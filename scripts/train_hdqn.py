#!/usr/bin/env python3
"""
train_hdqn.py — Hierarchical DQN Training for Deception Agent

[ALGORITHM]
    h-DQN (Kulkarni et al., 2016, NIPS):
    - MetaController: selects strategy every K steps (Double DQN)
    - Controller: selects parameterized action per step (Double DQN)
    - Intrinsic reward: strategy-specific sub-goals

[USAGE]
    python3 scripts/train_hdqn.py
    python3 scripts/train_hdqn.py --episodes 3000 --target-reward 50

[OUTPUT]
    results/models/hdqn_deception_agent.pt   — trained h-DQN
    results/models/hdqn_training_log.json    — training curves
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import timedelta
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from honey_drone.deception_env import (
    DeceptionEnv, STATE_DIM, N_ACTIONS_FLAT, ACTION_NAMES_45,
    N_BASE_ACTIONS, decode_action,
)
from honey_drone.hierarchical_agent import (
    HierarchicalDQN, MetaController, Controller,
    N_STRATEGIES, N_ACTIONS, STRATEGY_NAMES,
    CONTROLLER_INPUT_DIM,
)


# ═══════════════════════════════════════════════════════════════
# Replay Buffers
# ═══════════════════════════════════════════════════════════════

class ReplayBuffer:
    def __init__(self, capacity: int = 50000):
        self.buffer = deque(maxlen=capacity)

    def push(self, *args):
        self.buffer.append(args)

    def sample(self, batch_size: int):
        indices = np.random.choice(len(self.buffer), batch_size, replace=False)
        return [self.buffer[i] for i in indices]

    def __len__(self):
        return len(self.buffer)


# ═══════════════════════════════════════════════════════════════
# Intrinsic Reward (strategy-specific sub-goals)
# ═══════════════════════════════════════════════════════════════

def intrinsic_reward(strategy: int, info: dict, prev_info: dict) -> float:
    """
    Strategy-specific sub-goal reward for the Controller.

    Each strategy defines what "success" looks like for the Controller,
    creating temporal abstraction: MetaController picks the right strategy
    for the attack phase, Controller optimizes within that strategy.

    REF: Kulkarni et al. 2016 §3.2 — intrinsic reward as goal completion signal.
    Calibration: strategy rewards scaled to [-1, +1] to match extrinsic reward
    magnitude, ensuring neither dominates in the 50/50 combination.
    """
    p_real = info.get("p_real", 0.5)
    prev_p = prev_info.get("p_real", 0.5)
    engaged = info.get("engaged", False)
    evasion = info.get("evasion", 0)
    delta_p = p_real - prev_p

    if strategy == 0:  # aggressive_engage — maximize interaction volume
        return 0.8 * (1.0 if engaged else -0.3) + 0.2 * delta_p * 5
    elif strategy == 1:  # passive_monitor — observe without provoking suspicion
        return 0.5 * max(0, delta_p * 5) - 0.5 * (1.0 if evasion > 0 else 0.0)
    elif strategy == 2:  # identity_shift — disrupt attacker's tracking
        return 0.6 * delta_p * 5 + 0.4 * (0.3 if engaged else 0.0)
    elif strategy == 3:  # service_expansion — grow attack surface illusion
        return 0.7 * (1.0 if engaged else 0.0) + 0.3 * delta_p * 3
    elif strategy == 4:  # credential_leak — get attacker to use planted creds
        return 0.5 * delta_p * 8 + 0.5 * (0.5 if engaged else 0.0)
    elif strategy == 5:  # adaptive_response — balanced deception score maximization
        return 0.4 * delta_p * 5 + 0.3 * (1.0 if engaged else 0.0) - 0.3 * (0.5 if evasion > 0 else 0.0)
    return 0.0


# ═══════════════════════════════════════════════════════════════
# Training
# ═══════════════════════════════════════════════════════════════

def _fmt_time(seconds: float) -> str:
    """Format seconds → HH:MM:SS."""
    return str(timedelta(seconds=int(seconds)))


def _bar(pct: float, width: int = 20) -> str:
    """Compact progress bar."""
    filled = int(pct / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def train(
    episodes: int = 3000,
    batch_size: int = 128,
    gamma: float = 0.99,
    meta_lr: float = 1e-4,
    ctrl_lr: float = 3e-4,
    eps_start: float = 1.0,
    eps_end: float = 0.02,
    eps_decay: int = 600,
    strategy_horizon: int = 10,
    target_update: int = 5,
    target_reward: float = 50.0,
    patience: int = 400,
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
    print(f"{'─' * 60}")

    env = DeceptionEnv(max_steps=200, action_mode="param")

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

    meta_replay = ReplayBuffer(30000)
    ctrl_replay = ReplayBuffer(100000)

    # Logging
    episode_rewards = []
    episode_lengths = []
    strategy_counts = np.zeros(N_STRATEGIES)
    best_avg_reward = -float("inf")
    best_episode = 0
    no_improve_count = 0
    recent_ctrl_loss = deque(maxlen=100)
    recent_meta_loss = deque(maxlen=100)
    recent_p_real = deque(maxlen=100)
    saves = 0

    model_dir = Path("results/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    for ep in range(1, episodes + 1):
        state = env.reset()
        total_reward = 0.0
        total_extrinsic = 0.0
        steps = 0
        strategy = None
        strategy_start_state = None
        strategy_cumulative_reward = 0.0
        strategy_steps = 0
        prev_info = {"p_real": 0.7, "engaged": False, "evasion": 0}

        while True:
            eps = eps_end + (eps_start - eps_end) * np.exp(-ep / eps_decay)

            # ── MetaController: select strategy every K steps ──
            if strategy is None or strategy_steps >= strategy_horizon:
                # Store meta transition from previous strategy
                if strategy is not None and strategy_start_state is not None:
                    meta_replay.push(
                        strategy_start_state, strategy,
                        strategy_cumulative_reward, state, False,
                    )

                # Select new strategy
                if np.random.random() < eps:
                    strategy = np.random.randint(N_STRATEGIES)
                else:
                    with torch.no_grad():
                        s_t = torch.FloatTensor(state).unsqueeze(0).to(device)
                        strategy = policy_hdqn.meta(s_t).argmax(dim=1).item()

                strategy_counts[strategy] += 1
                strategy_start_state = state.copy()
                strategy_cumulative_reward = 0.0
                strategy_steps = 0

            # ── Controller: select parameterized action ──
            if np.random.random() < eps:
                action = np.random.randint(N_ACTIONS_FLAT)
            else:
                with torch.no_grad():
                    s_t = torch.FloatTensor(state).unsqueeze(0).to(device)
                    action_q = policy_hdqn.select_action(s_t, strategy)
                    action = action_q.argmax(dim=1).item()

            next_state, extrinsic_reward, done, info = env.step(action)

            # Intrinsic reward for controller
            intr = intrinsic_reward(strategy, info, prev_info)
            ctrl_reward = 0.5 * extrinsic_reward + 0.5 * intr

            # Build controller input (state + strategy one-hot)
            s_onehot = np.zeros(N_STRATEGIES, dtype=np.float32)
            s_onehot[strategy] = 1.0
            ctrl_state = np.concatenate([state, s_onehot])
            ctrl_next = np.concatenate([next_state, s_onehot])

            ctrl_replay.push(ctrl_state, action, ctrl_reward, ctrl_next, done)

            strategy_cumulative_reward += extrinsic_reward
            strategy_steps += 1
            total_reward += ctrl_reward
            total_extrinsic += extrinsic_reward
            state = next_state
            prev_info = info
            steps += 1

            # ── Train Controller ──
            if len(ctrl_replay) >= batch_size:
                batch = ctrl_replay.sample(batch_size)
                states_b, actions_b, rewards_b, next_states_b, dones_b = zip(*batch)
                states_b = torch.FloatTensor(np.array(states_b)).to(device)
                actions_b = torch.LongTensor(actions_b).to(device)
                rewards_b = torch.FloatTensor(rewards_b).to(device)
                next_states_b = torch.FloatTensor(np.array(next_states_b)).to(device)
                dones_b = torch.BoolTensor(dones_b).to(device)

                current_q = policy_hdqn.controller(states_b).gather(1, actions_b.unsqueeze(1)).squeeze(1)
                with torch.no_grad():
                    next_actions = policy_hdqn.controller(next_states_b).argmax(dim=1)
                    next_q = target_ctrl(next_states_b).gather(1, next_actions.unsqueeze(1)).squeeze(1)
                    expected_q = rewards_b + gamma * next_q * (~dones_b)

                loss_ctrl = nn.SmoothL1Loss()(current_q, expected_q)
                ctrl_optimizer.zero_grad()
                loss_ctrl.backward()
                nn.utils.clip_grad_norm_(policy_hdqn.controller.parameters(), 1.0)
                ctrl_optimizer.step()
                recent_ctrl_loss.append(loss_ctrl.item())

            # ── Train MetaController ──
            if len(meta_replay) >= min(batch_size, 64):
                meta_batch_size = min(batch_size, len(meta_replay))
                batch = meta_replay.sample(meta_batch_size)
                states_b, strategies_b, rewards_b, next_states_b, dones_b = zip(*batch)
                states_b = torch.FloatTensor(np.array(states_b)).to(device)
                strategies_b = torch.LongTensor(strategies_b).to(device)
                rewards_b = torch.FloatTensor(rewards_b).to(device)
                next_states_b = torch.FloatTensor(np.array(next_states_b)).to(device)
                dones_b = torch.BoolTensor(dones_b).to(device)

                current_q = policy_hdqn.meta(states_b).gather(1, strategies_b.unsqueeze(1)).squeeze(1)
                with torch.no_grad():
                    next_strategies = policy_hdqn.meta(next_states_b).argmax(dim=1)
                    next_q = target_meta(next_states_b).gather(1, next_strategies.unsqueeze(1)).squeeze(1)
                    expected_q = rewards_b + (gamma ** strategy_horizon) * next_q * (~dones_b)

                loss_meta = nn.SmoothL1Loss()(current_q, expected_q)
                meta_optimizer.zero_grad()
                loss_meta.backward()
                nn.utils.clip_grad_norm_(policy_hdqn.meta.parameters(), 1.0)
                meta_optimizer.step()
                recent_meta_loss.append(loss_meta.item())

            if done:
                # Final meta transition
                if strategy_start_state is not None:
                    meta_replay.push(
                        strategy_start_state, strategy,
                        strategy_cumulative_reward, state, True,
                    )
                break

        episode_rewards.append(total_extrinsic)
        episode_lengths.append(steps)
        recent_p_real.append(info.get("p_real", 0.0))

        # Target network update
        if ep % target_update == 0:
            target_meta.load_state_dict(policy_hdqn.meta.state_dict())
            target_ctrl.load_state_dict(policy_hdqn.controller.state_dict())

        # Rolling average
        window = min(100, len(episode_rewards))
        avg_reward = np.mean(episode_rewards[-window:])
        avg_len = np.mean(episode_lengths[-window:])

        if avg_reward > best_avg_reward:
            best_avg_reward = avg_reward
            best_episode = ep
            no_improve_count = 0
            saves += 1
            torch.save({
                "meta_state_dict": policy_hdqn.meta.state_dict(),
                "controller_state_dict": policy_hdqn.controller.state_dict(),
                "episode": ep,
                "avg_reward": float(avg_reward),
                "n_strategies": N_STRATEGIES,
                "n_actions": N_ACTIONS_FLAT,
            }, model_dir / "hdqn_deception_agent.pt")
        else:
            no_improve_count += 1

        if ep % 50 == 0 or ep == 1:
            elapsed = time.time() - t0
            eps_per_sec = ep / elapsed
            eta = (episodes - ep) / max(eps_per_sec, 0.1)
            pct = ep / episodes * 100

            avg_cl = np.mean(recent_ctrl_loss) if recent_ctrl_loss else 0
            avg_ml = np.mean(recent_meta_loss) if recent_meta_loss else 0
            avg_p = np.mean(recent_p_real) if recent_p_real else 0

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

            print(f"\n  {_bar(pct)} {pct:5.1f}%  ep {ep:>6d}/{episodes}"
                  f"  [{_fmt_time(elapsed)} < {_fmt_time(eta)}]"
                  f"  {eps_per_sec:.1f} ep/s")
            print(f"  reward {avg_reward:+7.2f}  (best {best_avg_reward:+.2f} @{best_episode})"
                  f"  len {avg_len:5.1f}  eps {eps:.3f}  saves {saves}")
            print(f"  loss  ctrl {avg_cl:.4f}  meta {avg_ml:.4f}"
                  f"  p_real {avg_p:.3f}{gpu_str}")
            print(f"  strat  {strat_str}")

        # ── Time limit ──
        if max_time > 0 and (time.time() - t0) >= max_time:
            print(f"\n  TIME LIMIT {_fmt_time(max_time)} reached at ep {ep}")
            break
        if avg_reward >= target_reward and ep >= 500:
            print(f"\n  TARGET REWARD {target_reward} reached at ep {ep}")
            break
        if no_improve_count >= patience and ep >= 500:
            print(f"\n  PATIENCE exhausted ({patience} ep no improvement), stopping at ep {ep}")
            break

    # ═══════════════════════════════════════════════════════════════
    # Final evaluation
    # ═══════════════════════════════════════════════════════════════
    elapsed = time.time() - t0
    print(f"\n{'═' * 60}")
    print(f"  EVALUATION  (100 episodes, greedy policy)")
    print(f"{'═' * 60}")
    policy_hdqn.eval()
    eval_rewards = []
    eval_strategy_counts = np.zeros(N_STRATEGIES)
    eval_base_counts = np.zeros(N_BASE_ACTIONS)

    for _ in range(100):
        state = env.reset()
        total_r = 0.0
        strat = None
        strat_steps = 0
        while True:
            if strat is None or strat_steps >= strategy_horizon:
                with torch.no_grad():
                    s_t = torch.FloatTensor(state).unsqueeze(0).to(device)
                    strat = policy_hdqn.meta(s_t).argmax(dim=1).item()
                eval_strategy_counts[strat] += 1
                strat_steps = 0
            with torch.no_grad():
                s_t = torch.FloatTensor(state).unsqueeze(0).to(device)
                action = policy_hdqn.select_action(s_t, strat).argmax(dim=1).item()
            base, _, _ = decode_action(action)
            eval_base_counts[base] += 1
            state, reward, done, info = env.step(action)
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
        pct = eval_strategy_counts[i] / total_s * 100
        bar = "█" * int(pct / 2.5)
        print(f"    {STRATEGY_NAMES[i]:22s} {pct:5.1f}%  {bar}")

    print(f"\n  Base action distribution:")
    total_a = max(eval_base_counts.sum(), 1)
    from honey_drone.deception_env import BASE_ACTION_NAMES
    for i in range(N_BASE_ACTIONS):
        pct = eval_base_counts[i] / total_a * 100
        bar = "█" * int(pct / 2.5)
        print(f"    {BASE_ACTION_NAMES[i]:22s} {pct:5.1f}%  {bar}")

    # Save log
    log = {
        "episodes": ep,
        "elapsed_sec": round(elapsed, 1),
        "best_avg_reward": round(best_avg_reward, 4),
        "best_episode": best_episode,
        "eval_avg_reward": round(float(avg_eval), 4),
        "eval_std_reward": round(float(std_eval), 4),
        "n_strategies": N_STRATEGIES,
        "n_actions": N_ACTIONS_FLAT,
        "strategy_horizon": strategy_horizon,
        "episode_rewards": [round(r, 4) for r in episode_rewards],
        "device": str(device),
    }
    (model_dir / "hdqn_training_log.json").write_text(json.dumps(log, indent=2))

    print(f"\n{'─' * 60}")
    print(f"  Model : results/models/hdqn_deception_agent.pt")
    print(f"  Log   : results/models/hdqn_training_log.json")
    print(f"  Time  : {_fmt_time(elapsed)}  ({ep/elapsed:.0f} ep/s, {ep} episodes)")
    print(f"  Saves : {saves} checkpoints")
    print(f"{'─' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="MIRAGE-UAS h-DQN Deception Agent Training",
    )
    parser.add_argument("--episodes", type=int, default=3000)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--meta-lr", type=float, default=1e-4)
    parser.add_argument("--ctrl-lr", type=float, default=3e-4)
    parser.add_argument("--strategy-horizon", type=int, default=10)
    parser.add_argument("--target-reward", type=float, default=50.0)
    parser.add_argument("--patience", type=int, default=400)
    parser.add_argument("--max-time", type=int, default=0,
                        help="Max training time in seconds (0=unlimited)")
    args = parser.parse_args()

    print(f"\n{'═' * 60}")
    print(f"  MIRAGE-UAS  h-DQN Deception Agent Training")
    print(f"{'═' * 60}")
    print(f"  Algorithm  : h-DQN (MetaController + Controller)")
    print(f"  Strategies : {N_STRATEGIES} (6 deception strategies)")
    print(f"  Actions    : {N_ACTIONS_FLAT} (5 base x 3 intensity x 3 variant)")
    print(f"  Episodes   : {args.episodes}")
    print(f"  Batch      : {args.batch_size}")
    print(f"  LR         : meta={args.meta_lr}  ctrl={args.ctrl_lr}")
    print(f"  Horizon    : {args.strategy_horizon} steps/strategy")
    print(f"  Target     : avg_reward >= {args.target_reward}")
    print(f"  Patience   : {args.patience} episodes")
    if args.max_time > 0:
        print(f"  Time Limit : {_fmt_time(args.max_time)}")
    print(f"{'═' * 60}")

    train(
        episodes=args.episodes,
        batch_size=args.batch_size,
        meta_lr=args.meta_lr,
        ctrl_lr=args.ctrl_lr,
        strategy_horizon=args.strategy_horizon,
        target_reward=args.target_reward,
        patience=args.patience,
        max_time=args.max_time,
    )
