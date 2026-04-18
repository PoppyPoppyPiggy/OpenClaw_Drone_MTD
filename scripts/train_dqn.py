#!/usr/bin/env python3
"""
train_dqn.py — DQN Training for Deception Agent (GPU-accelerated)

[ALGORITHM]
    Double DQN with Experience Replay + Target Network
    (Mnih et al., 2015, Nature; van Hasselt et al., 2016, AAAI)

[USAGE]
    python3 scripts/train_dqn.py
    python3 scripts/train_dqn.py --episodes 5000 --target-reward 80

[OUTPUT]
    results/models/dqn_deception_agent.pt   — trained policy
    results/models/dqn_training_log.json    — training curves
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from honey_drone.deception_env import DeceptionEnv, STATE_DIM, N_ACTIONS, ACTION_NAMES


# ═══════════════════════════════════════════════════════════════
# DQN Network
# ═══════════════════════════════════════════════════════════════

class DQN(nn.Module):
    """Dueling DQN: separate value and advantage streams."""

    def __init__(self, state_dim: int, n_actions: int, hidden: int = 128):
        super().__init__()
        self.feature = nn.Sequential(
            nn.Linear(state_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        # Value stream
        self.value = nn.Sequential(
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, 1),
        )
        # Advantage stream
        self.advantage = nn.Sequential(
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, n_actions),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feat = self.feature(x)
        val = self.value(feat)
        adv = self.advantage(feat)
        # Q(s,a) = V(s) + A(s,a) - mean(A)
        return val + adv - adv.mean(dim=-1, keepdim=True)


# ═══════════════════════════════════════════════════════════════
# Experience Replay
# ═══════════════════════════════════════════════════════════════

class ReplayBuffer:
    def __init__(self, capacity: int = 50000):
        self.buffer = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done):
        self.buffer.append((state, action, reward, next_state, done))

    def sample(self, batch_size: int):
        indices = np.random.choice(len(self.buffer), batch_size, replace=False)
        batch = [self.buffer[i] for i in indices]
        states, actions, rewards, next_states, dones = zip(*batch)
        return (
            torch.FloatTensor(np.array(states)),
            torch.LongTensor(actions),
            torch.FloatTensor(rewards),
            torch.FloatTensor(np.array(next_states)),
            torch.BoolTensor(dones),
        )

    def __len__(self):
        return len(self.buffer)


# ═══════════════════════════════════════════════════════════════
# Training
# ═══════════════════════════════════════════════════════════════

def train(
    episodes: int = 3000,
    batch_size: int = 128,
    gamma: float = 0.99,
    lr: float = 3e-4,
    eps_start: float = 1.0,
    eps_end: float = 0.02,
    eps_decay: int = 500,
    target_update: int = 5,
    target_reward: float = 35.0,
    patience: int = 300,
    seed: int = 42,
    eval_episodes: int = 100,
    eval_seed: int = 1337,
):
    # Deterministic training: seed torch, numpy, and python's random
    import random as _rnd
    _rnd.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n  Device: {device}  seed={seed}  eval_seed={eval_seed}")
    if device.type == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")
        try:
            props = torch.cuda.get_device_properties(0)
            vram = getattr(props, 'total_memory', 0) or getattr(props, 'total_mem', 0)
            print(f"  VRAM: {vram / 1e9:.1f} GB")
        except Exception:
            pass
    print()

    env = DeceptionEnv(max_steps=200)
    # Use ENV's actual obs size — module-level STATE_DIM=64 is for VecDeceptionEnv;
    # single-agent DeceptionEnv._observe() returns a 10-dim vector.
    actual_state_dim = int(env.reset().shape[0])
    if actual_state_dim != STATE_DIM:
        print(f"  Note: env obs dim={actual_state_dim} (module STATE_DIM={STATE_DIM} "
              f"is for VecDeceptionEnv; single-agent mode uses {actual_state_dim}-dim)")
    policy_net = DQN(actual_state_dim, N_ACTIONS).to(device)
    target_net = DQN(actual_state_dim, N_ACTIONS).to(device)
    target_net.load_state_dict(policy_net.state_dict())
    target_net.eval()

    optimizer = optim.Adam(policy_net.parameters(), lr=lr)
    replay = ReplayBuffer(50000)

    # Logging
    episode_rewards = []
    episode_lengths = []
    episode_p_reals = []
    best_avg_reward = -float("inf")
    best_episode = 0
    no_improve_count = 0

    model_dir = Path("results/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    for ep in range(1, episodes + 1):
        state = env.reset()
        total_reward = 0.0
        steps = 0

        while True:
            # Epsilon-greedy
            eps = eps_end + (eps_start - eps_end) * np.exp(-ep / eps_decay)
            if np.random.random() < eps:
                action = np.random.randint(N_ACTIONS)
            else:
                with torch.no_grad():
                    s_tensor = torch.FloatTensor(state).unsqueeze(0).to(device)
                    q_values = policy_net(s_tensor)
                    action = q_values.argmax(dim=1).item()

            next_state, reward, done, info = env.step(action)
            replay.push(state, action, reward, next_state, done)
            state = next_state
            total_reward += reward
            steps += 1

            # Train
            if len(replay) >= batch_size:
                states, actions, rewards_b, next_states, dones = replay.sample(batch_size)
                states = states.to(device)
                actions = actions.to(device)
                rewards_b = rewards_b.to(device)
                next_states = next_states.to(device)
                dones = dones.to(device)

                # Double DQN: action selection from policy, evaluation from target
                current_q = policy_net(states).gather(1, actions.unsqueeze(1)).squeeze(1)
                with torch.no_grad():
                    next_actions = policy_net(next_states).argmax(dim=1)
                    next_q = target_net(next_states).gather(1, next_actions.unsqueeze(1)).squeeze(1)
                    expected_q = rewards_b + gamma * next_q * (~dones)

                loss = nn.SmoothL1Loss()(current_q, expected_q)
                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(policy_net.parameters(), 1.0)
                optimizer.step()

            if done:
                break

        episode_rewards.append(total_reward)
        episode_lengths.append(steps)
        episode_p_reals.append(info.get("p_real", 0))

        # Target network update
        if ep % target_update == 0:
            target_net.load_state_dict(policy_net.state_dict())

        # Rolling average
        window = min(100, len(episode_rewards))
        avg_reward = np.mean(episode_rewards[-window:])
        avg_len = np.mean(episode_lengths[-window:])
        avg_p_real = np.mean(episode_p_reals[-window:])

        # Check improvement
        if avg_reward > best_avg_reward:
            best_avg_reward = avg_reward
            best_episode = ep
            no_improve_count = 0
            # Save best model
            torch.save({
                "policy_state_dict": policy_net.state_dict(),
                "episode": ep,
                "avg_reward": avg_reward,
                "state_dim": actual_state_dim,
                "n_actions": N_ACTIONS,
            }, model_dir / "dqn_deception_agent.pt")
        else:
            no_improve_count += 1

        # Print progress
        if ep % 50 == 0 or ep == 1:
            elapsed = time.time() - t0
            eps_per_sec = ep / elapsed
            eta = (episodes - ep) / max(eps_per_sec, 0.1)

            # Action distribution in last 100 episodes
            bar = ""
            if ep >= 10:
                # Count which actions the greedy policy would pick
                test_states = [env.reset() for _ in range(50)]
                with torch.no_grad():
                    ts = torch.FloatTensor(np.array(test_states)).to(device)
                    acts = policy_net(ts).argmax(dim=1).cpu().numpy()
                counts = np.bincount(acts, minlength=N_ACTIONS)
                pcts = counts / max(counts.sum(), 1) * 100
                bar = " ".join(f"{ACTION_NAMES[i][:6]}={pcts[i]:.0f}%" for i in range(N_ACTIONS))

            converge_mark = ""
            if avg_reward >= target_reward:
                converge_mark = " ✓ TARGET"

            print(
                f"  ep {ep:5d}/{episodes} │ "
                f"R={avg_reward:7.2f} │ "
                f"len={avg_len:5.1f} │ "
                f"P(r)={avg_p_real:.3f} │ "
                f"ε={eps:.3f} │ "
                f"best={best_avg_reward:.2f}@{best_episode} │ "
                f"ETA {eta:.0f}s"
                f"{converge_mark}"
            )
            if bar:
                print(f"          policy: {bar}")

        # Early stopping
        if avg_reward >= target_reward and ep >= 500:
            print(f"\n  ✓ TARGET REWARD {target_reward} REACHED at episode {ep}")
            break
        if no_improve_count >= patience and ep >= 500:
            print(f"\n  ⚠ No improvement for {patience} episodes, stopping at ep {ep}")
            break

    # ═══════════════════════════════════════════════════════════════
    # Save training log
    # ═══════════════════════════════════════════════════════════════
    elapsed = time.time() - t0
    log = {
        "episodes": ep,
        "elapsed_sec": round(elapsed, 1),
        "best_avg_reward": round(best_avg_reward, 4),
        "best_episode": best_episode,
        "final_epsilon": round(eps, 4),
        "episode_rewards": [round(r, 4) for r in episode_rewards],
        "episode_lengths": episode_lengths,
        "device": str(device),
    }
    (model_dir / "dqn_training_log.json").write_text(json.dumps(log, indent=2))

    # ═══════════════════════════════════════════════════════════════
    # Final evaluation
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  ── Final Evaluation (100 episodes, greedy) ──")
    policy_net.eval()
    eval_rewards = []
    eval_p_reals = []
    action_counts = np.zeros(N_ACTIONS)

    for _ in range(100):
        state = env.reset()
        total_r = 0.0
        while True:
            with torch.no_grad():
                s_t = torch.FloatTensor(state).unsqueeze(0).to(device)
                action = policy_net(s_t).argmax(dim=1).item()
            action_counts[action] += 1
            state, reward, done, info = env.step(action)
            total_r += reward
            if done:
                break
        eval_rewards.append(total_r)
        eval_p_reals.append(info.get("p_real", 0))

    avg_eval = np.mean(eval_rewards)
    avg_p = np.mean(eval_p_reals)
    total_acts = action_counts.sum()

    print(f"  Avg reward:  {avg_eval:.2f}")
    print(f"  Avg P(real): {avg_p:.4f}")
    print(f"  Learned policy (action distribution):")
    for i in range(N_ACTIONS):
        pct = action_counts[i] / total_acts * 100
        bar = "█" * int(pct / 2.5)
        print(f"    {ACTION_NAMES[i]:22s} {pct:5.1f}%  {bar}")

    print(f"\n  Model: results/models/dqn_deception_agent.pt")
    print(f"  Log:   results/models/dqn_training_log.json")
    print(f"  Time:  {elapsed:.1f}s ({ep/elapsed:.0f} ep/s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=3000)
    parser.add_argument("--batch-size", type=int, default=256,
                        help="Larger batch leverages GPU (RTX 5090 handles 512+)")
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--target-reward", type=float, default=35.0)
    parser.add_argument("--patience", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42,
                        help="Deterministic training seed")
    parser.add_argument("--eval-episodes", type=int, default=100)
    parser.add_argument("--eval-seed", type=int, default=1337,
                        help="Held-out seed for final evaluation")
    args = parser.parse_args()

    print("╔═══════════════════════════════════════════════════╗")
    print("║  MIRAGE-UAS  DQN Deception Agent Training        ║")
    print("╠═══════════════════════════════════════════════════╣")
    print("║  Algorithm: Double DQN + Dueling + Exp Replay    ║")
    print(f"║  Episodes:  {args.episodes:<38}║")
    print(f"║  Batch:     {args.batch_size:<38}║")
    print(f"║  LR:        {args.lr:<38}║")
    print(f"║  Target:    avg_reward ≥ {args.target_reward:<25}║")
    print("╚═══════════════════════════════════════════════════╝")

    train(
        episodes=args.episodes,
        batch_size=args.batch_size,
        lr=args.lr,
        target_reward=args.target_reward,
        patience=args.patience,
        seed=args.seed,
        eval_episodes=args.eval_episodes,
        eval_seed=args.eval_seed,
    )
