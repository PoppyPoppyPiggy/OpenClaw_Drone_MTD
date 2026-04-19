#!/usr/bin/env python3
"""
run_baseline_matched.py — Evaluate classical baselines under LLM experiment setup

Runs Random / Greedy / DQN policies on the SAME episode/step budget as the
LLM experiments (default: 50 ep × 50 steps), so Table VII rows are directly
comparable. Output schema matches run_llm_experiments.py.

Usage:
    python scripts/run_baseline_matched.py --episodes 50 --max-steps 50 --seeds 42
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from honey_drone.deception_env import (  # noqa: E402
    DeceptionEnv,
    N_BASE_ACTIONS,
    BASE_ACTION_NAMES,
)

SKILL_NAMES = (
    "statustext", "flight_sim", "ghost_port", "reboot_sim", "credential_leak",
)


class RandomPolicy:
    name = "Random"
    def __init__(self, seed: int = 0) -> None:
        self._rng = np.random.default_rng(seed)
    def reset(self) -> None: pass
    def select(self, state: np.ndarray) -> int:
        return int(self._rng.integers(0, N_BASE_ACTIONS))


class GreedyPolicy:
    """Phase-indexed expert heuristic matching compare_policies.py:GreedyPolicy."""
    name = "Greedy"
    _phase_map = {0: 1, 1: 4, 2: 3, 3: 4}
    def reset(self) -> None: pass
    def select(self, state: np.ndarray) -> int:
        phase = int(round(float(state[0]) * 3))
        return self._phase_map.get(max(0, min(3, phase)), 1)


class DQNPolicy:
    """Loads the 3-seed trained DQN checkpoint (first available)."""
    name = "DQN"
    def __init__(self, seed: int) -> None:
        import importlib.util
        import torch
        spec = importlib.util.spec_from_file_location(
            "train_dqn",
            Path(__file__).resolve().parents[1] / "scripts" / "train_dqn.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        DQN = mod.DQN
        # Prefer seed-matched checkpoint if present
        ckpt_candidates = [
            Path("results/models") / f"dqn_deception_agent_seed{seed}.pt",
            Path("results/models/dqn_deception_agent.pt"),
        ]
        ckpt_path = next((p for p in ckpt_candidates if p.exists()), None)
        if ckpt_path is None:
            raise FileNotFoundError("No DQN checkpoint found under results/models/")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        ckpt = torch.load(ckpt_path, map_location=self.device, weights_only=False)
        sd = ckpt.get("policy_state_dict", ckpt)
        # Infer dims from weights
        in_dim = sd["feature.0.weight"].shape[1]
        out_dim = sd["advantage.2.weight"].shape[0]
        self._uses_vec = in_dim != 10
        self._uses_param = out_dim == 45
        self.net = DQN(in_dim, out_dim, hidden=128).to(self.device)
        self.net.load_state_dict(sd)
        self.net.eval()
        self._torch = torch
        self._in_dim = in_dim
        self._history: list[np.ndarray] = []
        self.name = f"DQN-seed{seed}"

    def reset(self) -> None:
        self._history = []

    def select(self, state: np.ndarray) -> int:
        s = np.asarray(state, dtype=np.float32)
        if self._uses_vec and s.shape[0] == 10:
            # Build 64-dim history-augmented observation
            # Format: one-hot phase (4) + state (10) + history (50)
            phase_oh = np.zeros(4, dtype=np.float32)
            pidx = int(round(float(s[0]) * 3))
            phase_oh[max(0, min(3, pidx))] = 1.0
            self._history.append(s.copy())
            hist_flat = np.concatenate(self._history[-5:])
            # Pad to 50
            if hist_flat.shape[0] < 50:
                hist_flat = np.concatenate([
                    hist_flat, np.zeros(50 - hist_flat.shape[0], dtype=np.float32)
                ])
            else:
                hist_flat = hist_flat[:50]
            obs = np.concatenate([phase_oh, s, hist_flat])
        else:
            obs = s
        with self._torch.no_grad():
            t = self._torch.from_numpy(obs[None, :]).to(self.device)
            q = self.net(t)
            a = int(q.argmax(dim=1).item())
        if self._uses_param:
            a = a // 9  # collapse 45→5
        return max(0, min(N_BASE_ACTIONS - 1, a))


def run_policy(policy, episodes: int, max_steps: int, seed: int, macro: int = 1) -> dict:
    rewards, lengths, p_reals, survivals = [], [], [], []
    action_counts = np.zeros(N_BASE_ACTIONS)
    t_start = time.perf_counter()
    env = DeceptionEnv(max_steps=max_steps, action_mode="base", seed=seed)

    for ep in range(episodes):
        state = env.reset(seed=seed + ep)
        policy.reset()
        total_r = 0.0
        steps = 0
        cached_action = 0
        ep_info: dict = {}
        while True:
            if steps % max(1, macro) == 0:
                cached_action = policy.select(state)
            action = cached_action
            action_counts[action] += 1
            state, reward, done, ep_info = env.step(action)
            total_r += float(reward)
            steps += 1
            if done or steps >= max_steps:
                break
        rewards.append(total_r)
        lengths.append(steps)
        p_reals.append(float(ep_info.get("p_real", 0.0)))
        survivals.append(steps >= max_steps)

    total_acts = max(action_counts.sum(), 1.0)
    return {
        "policy": policy.name,
        "seed": seed,
        "episodes": episodes,
        "max_steps": max_steps,
        "macro": macro,
        "avg_reward": round(float(np.mean(rewards)), 4),
        "std_reward": round(float(np.std(rewards)), 4),
        "median_reward": round(float(np.median(rewards)), 4),
        "avg_length": round(float(np.mean(lengths)), 2),
        "avg_p_real": round(float(np.mean(p_reals)), 4),
        "survival_rate": round(float(np.mean(survivals)), 4),
        "action_distribution": {
            BASE_ACTION_NAMES[i]: round(action_counts[i] / total_acts * 100, 1)
            for i in range(N_BASE_ACTIONS)
        },
        "rewards_raw": [round(r, 3) for r in rewards],
        "wall_sec": round(time.perf_counter() - t_start, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="42,1337,2024")
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--macro", type=int, default=1,
                        help="Macro-action stride for baselines; use 5 to mirror LLM setup")
    parser.add_argument("--output-dir", default="results/baseline_matched")
    parser.add_argument("--policies", default="random,greedy,dqn")
    args = parser.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    policy_names = [p.strip().lower() for p in args.policies.split(",")]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for seed in seeds:
        for p_name in policy_names:
            if p_name == "random":
                pol = RandomPolicy(seed=seed)
            elif p_name == "greedy":
                pol = GreedyPolicy()
            elif p_name == "dqn":
                try:
                    pol = DQNPolicy(seed=seed)
                except FileNotFoundError as e:
                    print(f"  SKIP DQN (seed={seed}): {e}")
                    continue
            else:
                print(f"  unknown policy '{p_name}' — skip")
                continue

            print(f"=== {pol.name}  seed={seed}  ep={args.episodes}  "
                  f"max_steps={args.max_steps}  macro={args.macro} ===")
            r = run_policy(pol, args.episodes, args.max_steps, seed, args.macro)
            tag = f"{pol.name.replace('/', '_')}_seed{seed}"
            out_file = out_dir / f"{tag}.json"
            to_save = {k: v for k, v in r.items() if k != "rewards_raw"}
            out_file.write_text(json.dumps(to_save, indent=2))
            print(f"  avg_R={r['avg_reward']:.2f}  avg_p_real={r['avg_p_real']:.3f}  "
                  f"survive={r['survival_rate']:.2%}  wall={r['wall_sec']:.1f}s  "
                  f"→ {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
