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


class HDQNPolicy:
    """Hierarchical DQN — MetaController + Controller on 45 parameterized actions."""
    name = "h-DQN"
    def __init__(self, model_path: str, device: str = "cuda"):
        from honey_drone.hierarchical_agent import (
            HierarchicalDQN, MetaController, Controller, N_STRATEGIES,
            STATE_DIM, CONTROLLER_INPUT_DIM, N_ACTIONS,
        )
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        ckpt = torch.load(model_path, map_location=self.device, weights_only=False)
        # Auto-detect network size from checkpoint weights
        meta_h = ckpt["meta_state_dict"]["feature.0.weight"].shape[0]
        ctrl_h = ckpt["controller_state_dict"]["feature.0.weight"].shape[0]
        self.hdqn = HierarchicalDQN.__new__(HierarchicalDQN)
        torch.nn.Module.__init__(self.hdqn)
        self.hdqn.meta = MetaController(hidden=meta_h)
        self.hdqn.controller = Controller(hidden=ctrl_h)
        self.hdqn.meta.load_state_dict(ckpt["meta_state_dict"])
        self.hdqn.controller.load_state_dict(ckpt["controller_state_dict"])
        self.hdqn = self.hdqn.to(self.device)
        self.hdqn.eval()
        self._strategy = None
        self._strategy_steps = 0
        self._horizon = 10
        self._train_ep = ckpt.get("episode", "?")

    def select(self, state: np.ndarray) -> int:
        """Returns flat 45-action index. Caller must use param-mode env."""
        self._strategy_steps += 1
        if self._strategy is None or self._strategy_steps >= self._horizon:
            with torch.no_grad():
                s_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                self._strategy = self.hdqn.meta(s_t).argmax(dim=1).item()
            self._strategy_steps = 0
        with torch.no_grad():
            s_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            action_q = self.hdqn.select_action(s_t, self._strategy)
            return action_q.argmax(dim=1).item()

    @property
    def uses_param_env(self):
        return True


# ═══════════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════════

def evaluate_policy(policy, env: DeceptionEnv, n_episodes: int, seed: int = 42):
    """Run n_episodes and collect metrics."""
    np.random.seed(seed)

    # h-DQN uses param-mode env (45 actions with intensity/variant)
    use_param = getattr(policy, "uses_param_env", False)
    if use_param:
        eval_env = DeceptionEnv(max_steps=env.max_steps, action_mode="param")
    else:
        eval_env = env

    rewards = []
    lengths = []
    p_reals = []
    survivals = []
    action_counts = np.zeros(N_ACTIONS)
    phase_actions = {p: np.zeros(N_ACTIONS) for p in range(4)}

    for ep in range(n_episodes):
        state = eval_env.reset()
        total_r = 0.0
        steps = 0
        while True:
            action = policy.select(state)
            # Map 45-action to base 5 for stats tracking
            base_action = action // 9 if use_param else action
            base_action = min(base_action, N_ACTIONS - 1)
            action_counts[base_action] += 1
            phase = int(round(state[0] * 3))
            phase_actions[phase][base_action] += 1

            state, reward, done, info = eval_env.step(action)
            total_r += reward
            steps += 1
            if done:
                break

        rewards.append(total_r)
        lengths.append(steps)
        p_reals.append(info.get("p_real", 0))
        survivals.append(steps >= eval_env.max_steps)

    total_acts = max(action_counts.sum(), 1)

    # F1: deception effectiveness classification
    tp = sum(1 for r in rewards if r >= 40)
    fp = sum(1 for r in rewards if 10 <= r < 40)
    fn = sum(1 for r in rewards if r < 10)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-8)

    return {
        "policy": policy.name,
        "episodes": n_episodes,
        "avg_reward": round(float(np.mean(rewards)), 4),
        "std_reward": round(float(np.std(rewards)), 4),
        "avg_length": round(float(np.mean(lengths)), 1),
        "avg_p_real": round(float(np.mean(p_reals)), 4),
        "survival_rate": round(float(np.mean(survivals)), 4),
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "tp": tp, "fp": fp, "fn": fn,
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
    print("  ╔════════════════╦══════════╦════════╦═════════╦══════════╦══════════╦════════╦════════╦════════╗")
    print("  ║ Policy         ║ Avg R    ║ Std R  ║ P(real) ║ Survive% ║ Avg Len  ║   P    ║   R    ║  F1    ║")
    print("  ╠════════════════╬══════════╬════════╬═════════╬══════════╬══════════╬════════╬════════╬════════╣")
    for r in results:
        name = r["policy"]
        print(f"  ║ {name:14s} ║ {r['avg_reward']:8.2f} ║ {r['std_reward']:6.2f} ║ "
              f"{r['avg_p_real']:7.4f} ║ {r['survival_rate']*100:7.1f}% ║ {r['avg_length']:8.1f} ║"
              f" {r['precision']:.4f} ║ {r['recall']:.4f} ║ {r['f1']:.4f} ║")
    print("  ╚════════════════╩══════════╩════════╩═════════╩══════════╩══════════╩════════╩════════╩════════╝")

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
    print("║  Policies:  Random / Greedy / DQN / h-DQN        ║")
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
        print(f"  DQN model: {model_path}")
    else:
        print(f"  DQN model not found: {model_path}")
        print(f"    Run: python3 scripts/train_dqn.py --episodes 3000")

    hdqn_path = Path("results/models/hdqn_deception_agent.pt")
    if hdqn_path.exists():
        policies.append(HDQNPolicy(str(hdqn_path), device))
        print(f"  h-DQN model: {hdqn_path}")
    else:
        print(f"  h-DQN model not found: {hdqn_path}")
        print(f"    Run: python3 scripts/train_hdqn.py --episodes 3000")

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
