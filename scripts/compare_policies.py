#!/usr/bin/env python3
"""
compare_policies.py — 4-Policy Comparison Experiment

Compares: Random / Greedy / DQN / Full Agent
on identical DeceptionEnv episodes for fair evaluation.

Usage:
    python3 scripts/compare_policies.py
    python3 scripts/compare_policies.py --episodes 1000 --output results/policy_comparison.json

Output:
    Table + JSON with per-policy metrics for paper Table VII
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from honey_drone.deception_env import (
    DeceptionEnv, STATE_DIM, N_ACTIONS, ACTION_NAMES,
)

# ═══════════════════════════════════════════════════════════════
# 4 Policies
# ═══════════════════════════════════════════════════════════════

class RandomPolicy:
    """Uniform random — baseline."""
    name = "Random"
    def select(self, state: np.ndarray) -> int:
        return np.random.randint(N_ACTIONS)


class GreedyPolicy:
    """Rule-based heuristic — best action per phase (expert knowledge)."""
    name = "Greedy"
    # phase → best action index (domain expert handpicked)
    _phase_map = {
        0: 1,  # RECON  → flight_sim (highest base effect 0.05)
        1: 4,  # EXPLOIT → fake_key (highest base effect 0.06)
        2: 3,  # PERSIST → reboot (highest base effect 0.05)
        3: 4,  # EXFIL  → fake_key (highest base effect 0.04)
    }
    def select(self, state: np.ndarray) -> int:
        phase = int(round(state[0] * 3))  # denormalize
        return self._phase_map.get(phase, 1)


class DQNPolicy:
    """DQN-trained policy — loaded from checkpoint."""
    name = "DQN"
    def __init__(self, model_path: str, device: str = "cuda"):
        # Import DQN from train_dqn (same directory)
        import importlib.util
        spec = importlib.util.spec_from_file_location("train_dqn", Path(__file__).parent / "train_dqn.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        DQN = mod.DQN
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.net = DQN(STATE_DIM, N_ACTIONS).to(self.device)
        ckpt = torch.load(model_path, map_location=self.device, weights_only=False)
        self.net.load_state_dict(ckpt["policy_state_dict"])
        self.net.eval()
        self._train_ep = ckpt.get("episode", "?")
        self._train_reward = ckpt.get("avg_reward", "?")

    def select(self, state: np.ndarray) -> int:
        with torch.no_grad():
            t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            return self.net(t).argmax(dim=1).item()


class FullAgentPolicy:
    """DQN + ε-exploration with phase-aware diversity."""
    name = "Full Agent"
    def __init__(self, model_path: str, device: str = "cuda"):
        self._dqn = DQNPolicy(model_path, device)
        self._step = 0

    def select(self, state: np.ndarray) -> int:
        self._step += 1
        # 90% DQN, 10% phase-aware exploration (not random, strategic)
        if np.random.random() < 0.1:
            phase = int(round(state[0] * 3))
            # Explore the second-best action per phase for diversity
            explore_map = {0: 0, 1: 3, 2: 0, 3: 3}  # statustext/reboot
            return explore_map.get(phase, 0)
        return self._dqn.select(state)


# ═══════════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════════

def evaluate_policy(policy, env: DeceptionEnv, n_episodes: int, seed: int = 42):
    """Run n_episodes and collect metrics."""
    np.random.seed(seed)
    rewards = []
    lengths = []
    p_reals = []
    survivals = []  # did agent survive full episode?
    action_counts = np.zeros(N_ACTIONS)
    phase_actions = {p: np.zeros(N_ACTIONS) for p in range(4)}

    for ep in range(n_episodes):
        state = env.reset()
        total_r = 0.0
        steps = 0
        while True:
            action = policy.select(state)
            action_counts[action] += 1
            phase = int(round(state[0] * 3))
            phase_actions[phase][action] += 1

            state, reward, done, info = env.step(action)
            total_r += reward
            steps += 1
            if done:
                break

        rewards.append(total_r)
        lengths.append(steps)
        p_reals.append(info.get("p_real", 0))
        survivals.append(steps >= env.max_steps)

    total_acts = max(action_counts.sum(), 1)
    return {
        "policy": policy.name,
        "episodes": n_episodes,
        "avg_reward": round(float(np.mean(rewards)), 4),
        "std_reward": round(float(np.std(rewards)), 4),
        "avg_length": round(float(np.mean(lengths)), 1),
        "avg_p_real": round(float(np.mean(p_reals)), 4),
        "survival_rate": round(float(np.mean(survivals)), 4),
        "action_distribution": {
            ACTION_NAMES[i]: round(action_counts[i] / total_acts * 100, 1)
            for i in range(N_ACTIONS)
        },
        "phase_preference": {
            f"phase_{p}": ACTION_NAMES[int(phase_actions[p].argmax())]
            for p in range(4)
        },
        "rewards_raw": [round(r, 2) for r in rewards],
    }


def print_comparison(results: list[dict]):
    """Pretty print comparison table."""
    print()
    print("  ╔════════════════╦══════════╦════════╦═════════╦══════════╦══════════╗")
    print("  ║ Policy         ║ Avg R    ║ Std R  ║ P(real) ║ Survive% ║ Avg Len  ║")
    print("  ╠════════════════╬══════════╬════════╬═════════╬══════════╬══════════╣")
    for r in results:
        name = r["policy"]
        print(f"  ║ {name:14s} ║ {r['avg_reward']:8.2f} ║ {r['std_reward']:6.2f} ║ "
              f"{r['avg_p_real']:7.4f} ║ {r['survival_rate']*100:7.1f}% ║ {r['avg_length']:8.1f} ║")
    print("  ╚════════════════╩══════════╩════════╩═════════╩══════════╩══════════╝")

    # Action distribution
    print()
    print("  Action Distribution (%):")
    print("  ┌────────────────┬──────────┬──────────┬──────────┬──────────┬──────────┐")
    print("  │ Policy         │ stxt     │ flight   │ ghost    │ reboot   │ fakekey  │")
    print("  ├────────────────┼──────────┼──────────┼──────────┼──────────┼──────────┤")
    for r in results:
        ad = r["action_distribution"]
        vals = [ad[a] for a in ACTION_NAMES]
        best_idx = np.argmax(vals)
        cells = []
        for i, v in enumerate(vals):
            marker = " ◀" if i == best_idx else "  "
            cells.append(f"{v:5.1f}%{marker}")
        print(f"  │ {r['policy']:14s} │ {cells[0]} │ {cells[1]} │ {cells[2]} │ {cells[3]} │ {cells[4]} │")
    print("  └────────────────┴──────────┴──────────┴──────────┴──────────┴──────────┘")

    # Phase preferences
    print()
    print("  Learned Phase Strategy:")
    print("  ┌────────────────┬──────────────┬──────────────┬──────────────┬──────────────┐")
    print("  │ Policy         │ RECON        │ EXPLOIT      │ PERSIST      │ EXFIL        │")
    print("  ├────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤")
    for r in results:
        pp = r["phase_preference"]
        p0 = pp.get("phase_0", "?")[:12]
        p1 = pp.get("phase_1", "?")[:12]
        p2 = pp.get("phase_2", "?")[:12]
        p3 = pp.get("phase_3", "?")[:12]
        print(f"  │ {r['policy']:14s} │ {p0:12s} │ {p1:12s} │ {p2:12s} │ {p3:12s} │")
    print("  └────────────────┴──────────────┴──────────────┴──────────────┴──────────────┘")

    # Statistical significance
    print()
    if len(results) >= 2:
        from scipy.stats import mannwhitneyu
        baseline = results[0]["rewards_raw"]
        print("  Statistical Significance (Mann-Whitney U vs Random):")
        for r in results[1:]:
            stat, p = mannwhitneyu(r["rewards_raw"], baseline, alternative="greater")
            sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
            cohen_d = (np.mean(r["rewards_raw"]) - np.mean(baseline)) / np.sqrt(
                (np.std(r["rewards_raw"])**2 + np.std(baseline)**2) / 2)
            print(f"    {r['policy']:14s} vs Random: U={stat:.0f}, p={p:.6f} {sig}, Cohen's d={cohen_d:.3f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=500)
    parser.add_argument("--model", type=str, default="results/models/dqn_deception_agent.pt")
    parser.add_argument("--output", type=str, default="results/policy_comparison.json")
    args = parser.parse_args()

    print("╔═══════════════════════════════════════════════════╗")
    print("║  MIRAGE-UAS  4-Policy Comparison                 ║")
    print("╠═══════════════════════════════════════════════════╣")
    print(f"║  Episodes:  {args.episodes:<38}║")
    print("║  Policies:  Random / Greedy / DQN / Full Agent   ║")
    print("╚═══════════════════════════════════════════════════╝")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n  Device: {device}")

    env = DeceptionEnv(max_steps=200)

    # Load policies
    policies = [RandomPolicy()]
    policies.append(GreedyPolicy())

    model_path = Path(args.model)
    if model_path.exists():
        policies.append(DQNPolicy(str(model_path), device))
        policies.append(FullAgentPolicy(str(model_path), device))
        print(f"  DQN model: {model_path}")
    else:
        print(f"  ⚠ DQN model not found: {model_path}")
        print(f"    Run: python3 scripts/train_dqn.py --episodes 3000")

    # Evaluate each policy
    results = []
    for policy in policies:
        t0 = time.time()
        print(f"\n  Evaluating {policy.name}...", end="", flush=True)
        r = evaluate_policy(policy, env, args.episodes, seed=42)
        elapsed = time.time() - t0
        print(f" done ({elapsed:.1f}s)")
        results.append(r)

    # Print comparison
    print_comparison(results)

    # Save
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove raw rewards for clean JSON (too large)
    save_results = [{k: v for k, v in r.items() if k != "rewards_raw"} for r in results]
    output_path.write_text(json.dumps(save_results, indent=2))
    print(f"\n  Results saved: {output_path}")


if __name__ == "__main__":
    main()
