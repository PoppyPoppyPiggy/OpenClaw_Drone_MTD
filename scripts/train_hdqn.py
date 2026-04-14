#!/usr/bin/env python3
"""
train_hdqn.py — Hierarchical DQN Training for Deception Agent

[ALGORITHM]
    h-DQN (Kulkarni et al., 2016, NIPS):
    - MetaController: selects strategy every K steps (Double DQN)
    - Controller: selects parameterized action per step (Double DQN)
    - Intrinsic reward: strategy-specific sub-goals
    - Vectorized env: N parallel environments via numpy batch ops

[USAGE]
    python3 scripts/train_hdqn.py --max-time 3600
    python3 scripts/train_hdqn.py --n-envs 512 --batch-size 1024

[OUTPUT]
    results/models/hdqn_deception_agent.pt   — trained h-DQN
    results/models/hdqn_training_log.json    — training curves
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from honey_drone.deception_env import (
    VecDeceptionEnv, DeceptionEnv, STATE_DIM, N_ACTIONS_FLAT,
    N_BASE_ACTIONS, decode_action, BASE_ACTION_NAMES,
)
from honey_drone.hierarchical_agent import (
    HierarchicalDQN, MetaController, Controller,
    N_STRATEGIES, STRATEGY_NAMES, CONTROLLER_INPUT_DIM,
)


# ═══════════════════════════════════════════════════════════════
# Tensor Replay Buffer (GPU-friendly)
# ═══════════════════════════════════════════════════════════════

class TensorReplayBuffer:
    """Fixed-size ring buffer stored as contiguous tensors for fast sampling."""

    def __init__(self, capacity: int, state_dim: int, device: torch.device):
        self.capacity = capacity
        self.device = device
        self.idx = 0
        self.size = 0

        self.states = torch.zeros(capacity, state_dim, device=device)
        self.actions = torch.zeros(capacity, dtype=torch.long, device=device)
        self.rewards = torch.zeros(capacity, device=device)
        self.next_states = torch.zeros(capacity, state_dim, device=device)
        self.dones = torch.zeros(capacity, dtype=torch.bool, device=device)

    def push_batch(self, states, actions, rewards, next_states, dones):
        """Push a batch of N transitions."""
        n = states.shape[0]
        if self.idx + n <= self.capacity:
            sl = slice(self.idx, self.idx + n)
            self.states[sl] = states
            self.actions[sl] = actions
            self.rewards[sl] = rewards
            self.next_states[sl] = next_states
            self.dones[sl] = dones
        else:
            # Wrap around
            first = self.capacity - self.idx
            self.states[self.idx:] = states[:first]
            self.actions[self.idx:] = actions[:first]
            self.rewards[self.idx:] = rewards[:first]
            self.next_states[self.idx:] = next_states[:first]
            self.dones[self.idx:] = dones[:first]
            rest = n - first
            self.states[:rest] = states[first:]
            self.actions[:rest] = actions[first:]
            self.rewards[:rest] = rewards[first:]
            self.next_states[:rest] = next_states[first:]
            self.dones[:rest] = dones[first:]

        self.idx = (self.idx + n) % self.capacity
        self.size = min(self.size + n, self.capacity)

    def sample(self, batch_size: int):
        indices = torch.randint(0, self.size, (batch_size,), device=self.device)
        return (
            self.states[indices],
            self.actions[indices],
            self.rewards[indices],
            self.next_states[indices],
            self.dones[indices],
        )

    def __len__(self):
        return self.size


# ═══════════════════════════════════════════════════════════════
# Vectorized Intrinsic Reward
# ═══════════════════════════════════════════════════════════════

# Intrinsic reward coefficient table: (N_STRATEGIES, 5)
# columns: engaged_pos, engaged_neg, delta_p_scale, evasion_pen, engaged_base
_INTR_TABLE = np.array([
    # aggressive_engage
    [0.8,  -0.24,  1.0,   0.0,   0.0],
    # passive_monitor
    [0.0,   0.0,   2.5,  -0.5,   0.0],
    # identity_shift
    [0.0,   0.0,   3.0,   0.0,   0.12],
    # service_expansion
    [0.7,   0.0,   0.9,   0.0,   0.0],
    # credential_leak
    [0.0,   0.0,   4.0,   0.0,   0.25],
    # adaptive_response
    [0.3,   0.0,   2.0,  -0.15,  0.0],
], dtype=np.float32)


def intrinsic_reward_vec(
    strategies: np.ndarray,   # (N,) int
    p_real: np.ndarray,       # (N,)
    prev_p_real: np.ndarray,  # (N,)
    engaged: np.ndarray,      # (N,) bool
    evasion: np.ndarray,      # (N,) float
) -> np.ndarray:
    """Vectorized intrinsic reward for N envs."""
    delta_p = p_real - prev_p_real
    coeffs = _INTR_TABLE[strategies]  # (N, 5)

    r = np.zeros(len(strategies), dtype=np.float32)
    eng = engaged.astype(np.float32)
    r += coeffs[:, 0] * eng              # engaged positive
    r += coeffs[:, 1] * (1 - eng)        # engaged negative (for aggressive)
    r += coeffs[:, 2] * delta_p          # delta_p scale
    r += coeffs[:, 3] * (evasion > 0).astype(np.float32)  # evasion penalty
    r += coeffs[:, 4] * eng              # engaged base (for identity/cred)
    return np.clip(r, -1.0, 1.0)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _fmt_time(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))

def _bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


# ═══════════════════════════════════════════════════════════════
# Training
# ═══════════════════════════════════════════════════════════════

def train(
    n_envs: int = 256,
    total_steps: int = 5_000_000,
    batch_size: int = 1024,
    gamma: float = 0.99,
    meta_lr: float = 1e-4,
    ctrl_lr: float = 3e-4,
    eps_start: float = 1.0,
    eps_end: float = 0.02,
    eps_decay_steps: int = 300_000,
    strategy_horizon: int = 10,
    target_update: int = 2000,
    train_every: int = 4,
    max_time: int = 0,
):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n{'─' * 60}")
    print(f"  Device : {device}")
    if device.type == "cuda":
        print(f"  GPU    : {torch.cuda.get_device_name(0)}")
        cap = torch.cuda.get_device_capability(0)
        mem_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"  Arch   : sm_{cap[0]}{cap[1]}  |  VRAM {mem_gb:.1f} GB")
    print(f"  Envs   : {n_envs} parallel")
    print(f"{'─' * 60}")

    env = VecDeceptionEnv(n_envs=n_envs, max_steps=200)

    # Networks
    policy_hdqn = HierarchicalDQN().to(device)
    target_meta = MetaController().to(device)
    target_ctrl = Controller().to(device)
    target_meta.load_state_dict(policy_hdqn.meta.state_dict())
    target_ctrl.load_state_dict(policy_hdqn.controller.state_dict())
    target_meta.eval()
    target_ctrl.eval()

    meta_optimizer = optim.Adam(policy_hdqn.meta.parameters(), lr=meta_lr)
    ctrl_optimizer = optim.Adam(policy_hdqn.controller.parameters(), lr=ctrl_lr)

    ctrl_replay = TensorReplayBuffer(500_000, CONTROLLER_INPUT_DIM, device)
    meta_replay = TensorReplayBuffer(100_000, STATE_DIM, device)

    # Per-env tracking
    strategies = np.full(n_envs, -1, dtype=np.int32)
    strategy_steps = np.zeros(n_envs, dtype=np.int32)
    strategy_start_states = np.zeros((n_envs, STATE_DIM), dtype=np.float32)
    strategy_cum_rewards = np.zeros(n_envs, dtype=np.float32)
    env_rewards = np.zeros(n_envs, dtype=np.float32)
    env_lengths = np.zeros(n_envs, dtype=np.int32)
    prev_p_real = np.full(n_envs, 0.7, dtype=np.float32)

    # Logging
    completed_rewards = []
    completed_lengths = []
    strategy_counts = np.zeros(N_STRATEGIES, dtype=np.int64)
    best_avg_reward = -float("inf")
    best_step = 0
    recent_ctrl_loss = []
    recent_meta_loss = []
    saves = 0

    model_dir = Path("results/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    # Init
    states = env.reset_all()
    t0 = time.time()
    log_interval = 1000  # log every N global steps
    last_log_step = 0

    for step in range(1, total_steps + 1):
        # Epsilon schedule
        eps_val = eps_end + (eps_start - eps_end) * max(0, 1 - step / eps_decay_steps)

        # ── MetaController: select strategies ──
        need_strategy = (strategies < 0) | (strategy_steps >= strategy_horizon)

        if need_strategy.any():
            # Store meta transitions for envs that had a previous strategy
            had_prev = need_strategy & (strategies >= 0)
            if had_prev.any():
                idx_prev = np.where(had_prev)[0]
                meta_replay.push_batch(
                    torch.from_numpy(strategy_start_states[idx_prev]).to(device),
                    torch.from_numpy(strategies[idx_prev].astype(np.int64)).to(device),
                    torch.from_numpy(strategy_cum_rewards[idx_prev]).to(device),
                    torch.from_numpy(states[idx_prev]).to(device),
                    torch.zeros(len(idx_prev), dtype=torch.bool, device=device),
                )

            idx_need = np.where(need_strategy)[0]
            n_need = len(idx_need)

            # Epsilon-greedy strategy selection
            rand_mask = np.random.random(n_need) < eps_val
            if (~rand_mask).any():
                with torch.no_grad():
                    s_t = torch.from_numpy(states[idx_need[~rand_mask]]).to(device)
                    q = policy_hdqn.meta(s_t)
                    strategies[idx_need[~rand_mask]] = q.argmax(dim=1).cpu().numpy()
            if rand_mask.any():
                strategies[idx_need[rand_mask]] = np.random.randint(0, N_STRATEGIES, size=rand_mask.sum())

            for i in idx_need:
                strategy_counts[strategies[i]] += 1
            strategy_start_states[idx_need] = states[idx_need]
            strategy_cum_rewards[idx_need] = 0.0
            strategy_steps[idx_need] = 0

        # ── Controller: select actions (batched) ──
        rand_mask = np.random.random(n_envs) < eps_val
        actions = np.zeros(n_envs, dtype=np.int32)

        if (~rand_mask).any():
            greedy_idx = np.where(~rand_mask)[0]
            with torch.no_grad():
                s_t = torch.from_numpy(states[greedy_idx]).to(device)
                # Build per-env strategy one-hot
                strat_onehot = torch.zeros(len(greedy_idx), N_STRATEGIES, device=device)
                strat_idx = torch.from_numpy(strategies[greedy_idx].astype(np.int64)).to(device)
                strat_onehot.scatter_(1, strat_idx.unsqueeze(1), 1.0)
                ctrl_input = torch.cat([s_t, strat_onehot], dim=1)
                q = policy_hdqn.controller(ctrl_input)
                actions[greedy_idx] = q.argmax(dim=1).cpu().numpy()

        if rand_mask.any():
            actions[rand_mask] = np.random.randint(0, N_ACTIONS_FLAT, size=rand_mask.sum())

        # ── Step all envs ──
        next_states, extrinsic_rewards, dones, info = env.step(actions)

        # Intrinsic reward
        intr = intrinsic_reward_vec(
            strategies, info["p_real"], prev_p_real,
            info["engaged"], info["evasion"],
        )
        ctrl_rewards = 0.5 * extrinsic_rewards + 0.5 * intr

        # Build controller transitions
        strat_onehot_np = np.zeros((n_envs, N_STRATEGIES), dtype=np.float32)
        strat_onehot_np[np.arange(n_envs), strategies] = 1.0
        ctrl_states = np.concatenate([states, strat_onehot_np], axis=1)
        ctrl_next_states = np.concatenate([next_states, strat_onehot_np], axis=1)

        ctrl_replay.push_batch(
            torch.from_numpy(ctrl_states).to(device),
            torch.from_numpy(actions.astype(np.int64)).to(device),
            torch.from_numpy(ctrl_rewards).to(device),
            torch.from_numpy(ctrl_next_states).to(device),
            torch.from_numpy(dones).to(device),
        )

        strategy_cum_rewards += extrinsic_rewards
        strategy_steps += 1
        env_rewards += extrinsic_rewards
        env_lengths += 1
        prev_p_real = info["p_real"].copy()

        # ── Handle episode completions ──
        if dones.any():
            done_idx = np.where(dones)[0]
            # Meta transitions for done envs
            valid_done = dones & (strategies >= 0)
            if valid_done.any():
                idx_d = np.where(valid_done)[0]
                meta_replay.push_batch(
                    torch.from_numpy(strategy_start_states[idx_d]).to(device),
                    torch.from_numpy(strategies[idx_d].astype(np.int64)).to(device),
                    torch.from_numpy(strategy_cum_rewards[idx_d]).to(device),
                    torch.from_numpy(next_states[idx_d]).to(device),
                    torch.ones(len(idx_d), dtype=torch.bool, device=device),
                )

            completed_rewards.extend(env_rewards[done_idx].tolist())
            completed_lengths.extend(env_lengths[done_idx].tolist())

            # Reset per-env tracking for done envs
            strategies[done_idx] = -1
            strategy_steps[done_idx] = 0
            strategy_cum_rewards[done_idx] = 0.0
            env_rewards[done_idx] = 0.0
            env_lengths[done_idx] = 0
            prev_p_real[done_idx] = 0.7

        states = next_states

        # ── Train networks ──
        if step % train_every == 0 and len(ctrl_replay) >= batch_size:
            # Controller
            s_b, a_b, r_b, ns_b, d_b = ctrl_replay.sample(batch_size)
            current_q = policy_hdqn.controller(s_b).gather(1, a_b.unsqueeze(1)).squeeze(1)
            with torch.no_grad():
                next_a = policy_hdqn.controller(ns_b).argmax(dim=1)
                next_q = target_ctrl(ns_b).gather(1, next_a.unsqueeze(1)).squeeze(1)
                expected_q = r_b + gamma * next_q * (~d_b)
            loss_ctrl = nn.SmoothL1Loss()(current_q, expected_q)
            ctrl_optimizer.zero_grad()
            loss_ctrl.backward()
            nn.utils.clip_grad_norm_(policy_hdqn.controller.parameters(), 1.0)
            ctrl_optimizer.step()
            recent_ctrl_loss.append(loss_ctrl.item())

            # MetaController
            if len(meta_replay) >= batch_size:
                s_b, a_b, r_b, ns_b, d_b = meta_replay.sample(min(batch_size, len(meta_replay)))
                current_q = policy_hdqn.meta(s_b).gather(1, a_b.unsqueeze(1)).squeeze(1)
                with torch.no_grad():
                    next_a = policy_hdqn.meta(ns_b).argmax(dim=1)
                    next_q = target_meta(ns_b).gather(1, next_a.unsqueeze(1)).squeeze(1)
                    expected_q = r_b + (gamma ** strategy_horizon) * next_q * (~d_b)
                loss_meta = nn.SmoothL1Loss()(current_q, expected_q)
                meta_optimizer.zero_grad()
                loss_meta.backward()
                nn.utils.clip_grad_norm_(policy_hdqn.meta.parameters(), 1.0)
                meta_optimizer.step()
                recent_meta_loss.append(loss_meta.item())

        # Target network update
        if step % target_update == 0:
            target_meta.load_state_dict(policy_hdqn.meta.state_dict())
            target_ctrl.load_state_dict(policy_hdqn.controller.state_dict())

        # ── Logging ──
        if step - last_log_step >= log_interval and len(completed_rewards) >= 10:
            last_log_step = step
            elapsed = time.time() - t0
            sps = step * n_envs / elapsed  # env steps per second
            eta = (total_steps - step) * n_envs / max(sps, 1)
            pct = step / total_steps * 100

            window = min(200, len(completed_rewards))
            avg_reward = np.mean(completed_rewards[-window:])
            avg_len = np.mean(completed_lengths[-window:])
            avg_p = np.mean(prev_p_real)
            n_episodes = len(completed_rewards)

            avg_cl = np.mean(recent_ctrl_loss[-200:]) if recent_ctrl_loss else 0
            avg_ml = np.mean(recent_meta_loss[-200:]) if recent_meta_loss else 0

            total_strats = max(strategy_counts.sum(), 1)
            top3 = sorted(range(N_STRATEGIES),
                          key=lambda i: strategy_counts[i], reverse=True)[:3]
            strat_str = "  ".join(
                f"{STRATEGY_NAMES[i][:8]:8s} {strategy_counts[i]/total_strats*100:4.0f}%"
                for i in top3
            )

            gpu_str = ""
            if device.type == "cuda":
                mem_used = torch.cuda.memory_allocated(0) / 1024**2
                mem_peak = torch.cuda.max_memory_allocated(0) / 1024**2
                gpu_str = f"  VRAM {mem_used:.0f}/{mem_peak:.0f} MB"

            # Save best
            if avg_reward > best_avg_reward:
                best_avg_reward = avg_reward
                best_step = step
                saves += 1
                torch.save({
                    "meta_state_dict": policy_hdqn.meta.state_dict(),
                    "controller_state_dict": policy_hdqn.controller.state_dict(),
                    "step": step,
                    "avg_reward": float(avg_reward),
                    "n_strategies": N_STRATEGIES,
                    "n_actions": N_ACTIONS_FLAT,
                }, model_dir / "hdqn_deception_agent.pt")

            print(f"\n  {_bar(pct)} {pct:5.1f}%  step {step:>8d}/{total_steps}"
                  f"  [{_fmt_time(elapsed)} < {_fmt_time(eta)}]"
                  f"  {sps:,.0f} sps")
            print(f"  reward {avg_reward:+7.2f}  (best {best_avg_reward:+.2f} @{best_step})"
                  f"  len {avg_len:5.1f}  eps {eps_val:.3f}"
                  f"  ep {n_episodes}")
            print(f"  loss  ctrl {avg_cl:.4f}  meta {avg_ml:.4f}"
                  f"  p_real {avg_p:.3f}{gpu_str}")
            print(f"  strat  {strat_str}")

        # ── Time limit ──
        if max_time > 0 and (time.time() - t0) >= max_time:
            print(f"\n  TIME LIMIT {_fmt_time(max_time)} reached at step {step}")
            break

    # ═══════════════════════════════════════════════════════════════
    # Final evaluation (single env, greedy)
    # ═══════════════════════════════════════════════════════════════
    elapsed = time.time() - t0
    print(f"\n{'═' * 60}")
    print(f"  EVALUATION  (200 episodes, greedy policy)")
    print(f"{'═' * 60}")
    policy_hdqn.eval()
    eval_env = DeceptionEnv(max_steps=200, action_mode="param")
    eval_rewards = []
    eval_strategy_counts = np.zeros(N_STRATEGIES)
    eval_base_counts = np.zeros(N_BASE_ACTIONS)

    for _ in range(200):
        state = eval_env.reset()
        total_r = 0.0
        strat = None
        strat_steps = 0
        while True:
            if strat is None or strat_steps >= strategy_horizon:
                with torch.no_grad():
                    s_t = torch.from_numpy(state).unsqueeze(0).to(device)
                    strat = policy_hdqn.meta(s_t).argmax(dim=1).item()
                eval_strategy_counts[strat] += 1
                strat_steps = 0
            with torch.no_grad():
                s_t = torch.from_numpy(state).unsqueeze(0).to(device)
                action = policy_hdqn.select_action(s_t, strat).argmax(dim=1).item()
            base_a, _, _ = decode_action(action)
            eval_base_counts[base_a] += 1
            state, reward, done, info = eval_env.step(action)
            total_r += reward
            strat_steps += 1
            if done:
                break
        eval_rewards.append(total_r)

    avg_eval = np.mean(eval_rewards)
    std_eval = np.std(eval_rewards)
    print(f"  Avg reward : {avg_eval:.2f} +/- {std_eval:.2f}")
    print(f"  Min / Max  : {min(eval_rewards):.2f} / {max(eval_rewards):.2f}")

    print(f"\n  Strategy distribution:")
    total_s = max(eval_strategy_counts.sum(), 1)
    for i in range(N_STRATEGIES):
        pct_s = eval_strategy_counts[i] / total_s * 100
        bar = "█" * int(pct_s / 2.5)
        print(f"    {STRATEGY_NAMES[i]:22s} {pct_s:5.1f}%  {bar}")

    print(f"\n  Base action distribution:")
    total_a = max(eval_base_counts.sum(), 1)
    for i in range(N_BASE_ACTIONS):
        pct_a = eval_base_counts[i] / total_a * 100
        bar = "█" * int(pct_a / 2.5)
        print(f"    {BASE_ACTION_NAMES[i]:22s} {pct_a:5.1f}%  {bar}")

    # Save final checkpoint unconditionally
    saves += 1
    torch.save({
        "meta_state_dict": policy_hdqn.meta.state_dict(),
        "controller_state_dict": policy_hdqn.controller.state_dict(),
        "step": step,
        "avg_reward": float(avg_eval),
        "n_strategies": N_STRATEGIES,
        "n_actions": N_ACTIONS_FLAT,
    }, model_dir / "hdqn_deception_agent_final.pt")

    # Save log
    log = {
        "total_steps": step,
        "n_envs": n_envs,
        "episodes_completed": len(completed_rewards),
        "elapsed_sec": round(elapsed, 1),
        "best_avg_reward": round(best_avg_reward, 4),
        "best_step": best_step,
        "eval_avg_reward": round(float(avg_eval), 4),
        "eval_std_reward": round(float(std_eval), 4),
        "n_strategies": N_STRATEGIES,
        "n_actions": N_ACTIONS_FLAT,
        "strategy_horizon": strategy_horizon,
        "episode_rewards": [round(r, 4) for r in completed_rewards],
        "device": str(device),
    }
    (model_dir / "hdqn_training_log.json").write_text(json.dumps(log, indent=2))

    sps = step * n_envs / max(elapsed, 1)
    print(f"\n{'─' * 60}")
    print(f"  Best   : results/models/hdqn_deception_agent.pt")
    print(f"  Final  : results/models/hdqn_deception_agent_final.pt")
    print(f"  Log    : results/models/hdqn_training_log.json")
    print(f"  Time   : {_fmt_time(elapsed)}  ({sps:,.0f} sps, {len(completed_rewards)} episodes)")
    print(f"  Saves  : {saves} checkpoints")
    print(f"{'─' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="MIRAGE-UAS h-DQN Deception Agent Training (Vectorized)",
    )
    parser.add_argument("--n-envs", type=int, default=256,
                        help="Number of parallel environments")
    parser.add_argument("--total-steps", type=int, default=5_000_000,
                        help="Total env steps (across all envs)")
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--meta-lr", type=float, default=1e-4)
    parser.add_argument("--ctrl-lr", type=float, default=3e-4)
    parser.add_argument("--strategy-horizon", type=int, default=10)
    parser.add_argument("--train-every", type=int, default=4,
                        help="Train networks every N env steps")
    parser.add_argument("--max-time", type=int, default=0,
                        help="Max training time in seconds (0=unlimited)")
    args = parser.parse_args()

    print(f"\n{'═' * 60}")
    print(f"  MIRAGE-UAS  h-DQN Deception Agent Training")
    print(f"{'═' * 60}")
    print(f"  Algorithm  : h-DQN (MetaController + Controller)")
    print(f"  Strategies : {N_STRATEGIES} (6 deception strategies)")
    print(f"  Actions    : {N_ACTIONS_FLAT} (5 base x 3 intensity x 3 variant)")
    print(f"  Envs       : {args.n_envs} parallel (vectorized numpy)")
    print(f"  Steps      : {args.total_steps:,}")
    print(f"  Batch      : {args.batch_size}")
    print(f"  LR         : meta={args.meta_lr}  ctrl={args.ctrl_lr}")
    print(f"  Horizon    : {args.strategy_horizon} steps/strategy")
    print(f"  Train freq : every {args.train_every} steps")
    if args.max_time > 0:
        print(f"  Time Limit : {_fmt_time(args.max_time)}")
    print(f"{'═' * 60}")

    train(
        n_envs=args.n_envs,
        total_steps=args.total_steps,
        batch_size=args.batch_size,
        meta_lr=args.meta_lr,
        ctrl_lr=args.ctrl_lr,
        strategy_horizon=args.strategy_horizon,
        train_every=args.train_every,
        max_time=args.max_time,
    )
