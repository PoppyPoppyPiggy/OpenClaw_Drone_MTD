#!/usr/bin/env python3
"""
diagnose_env_coverage.py — Phase & state coverage diagnostic for DeceptionEnv

Runs N episodes without any LLM (using a fixed or random policy) and reports:
  - Phase visit distribution per episode
  - Average step index of each phase transition
  - State vector value distribution

Answers: "Does the attacker ever actually reach PERSIST/EXFIL in this setup?"
If coverage is poor, max_steps is too short or phase_advance_prob too low.

Usage:
    python scripts/diagnose_env_coverage.py --episodes 50 --max-steps 50
    python scripts/diagnose_env_coverage.py --episodes 50 --max-steps 150  # compare
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from honey_drone.deception_env import DeceptionEnv, N_BASE_ACTIONS  # noqa: E402


def run(episodes: int, max_steps: int, seed: int, policy: str) -> dict:
    env = DeceptionEnv(max_steps=max_steps, seed=seed)
    rng = np.random.default_rng(seed)
    # phase visit count per step index (across all episodes)
    phase_visits = [Counter() for _ in range(max_steps)]
    # first-step index at which each phase is reached (per episode)
    phase_first_reached: dict[int, list[int]] = {1: [], 2: [], 3: []}
    # terminal stats
    term_reasons = Counter()
    final_phases = Counter()
    p_real_values = []
    evasion_values = []

    for ep in range(episodes):
        state = env.reset(seed=seed + ep)
        reached = {1: False, 2: False, 3: False}
        steps = 0
        while True:
            phase_now = env.state.phase
            if steps < max_steps:
                phase_visits[steps][phase_now] += 1
            for ph in (1, 2, 3):
                if phase_now >= ph and not reached[ph]:
                    phase_first_reached[ph].append(steps)
                    reached[ph] = True
            # pick action
            if policy == "random":
                a = int(rng.integers(0, N_BASE_ACTIONS))
            elif policy == "flight_sim":
                a = 1
            elif policy == "reboot":
                a = 3
            else:
                a = 0  # statustext
            state, reward, done, info = env.step(a)
            steps += 1
            if done:
                # Categorise termination
                if info["p_real"] < 0.2:
                    term_reasons["detected"] += 1
                elif info["evasion"] >= 5:
                    term_reasons["evasion"] += 1
                else:
                    term_reasons["other_done"] += 1
                break
            if steps >= max_steps:
                term_reasons["max_steps"] += 1
                break
        final_phases[env.state.phase] += 1
        p_real_values.append(env.state.p_real)
        evasion_values.append(env.state.evasion_signals)

    # per-step phase distribution (across episodes)
    phase_per_step = []
    for ctr in phase_visits:
        total = sum(ctr.values()) or 1
        phase_per_step.append({p: ctr.get(p, 0) / total for p in range(4)})

    # overall phase time share
    overall_phase_share = Counter()
    for ctr in phase_visits:
        for p, n in ctr.items():
            overall_phase_share[p] += n
    total_steps = sum(overall_phase_share.values()) or 1
    overall_pct = {p: overall_phase_share[p] / total_steps * 100 for p in range(4)}

    return {
        "episodes": episodes,
        "max_steps": max_steps,
        "policy": policy,
        "seed": seed,
        "termination_reasons": dict(term_reasons),
        "final_phase_distribution": dict(final_phases),
        "overall_phase_time_share_pct": {f"phase_{k}": round(v, 2) for k, v in overall_pct.items()},
        "phase_first_reached_mean_step": {
            f"phase_{ph}": round(float(np.mean(steps)), 2) if steps else None
            for ph, steps in phase_first_reached.items()
        },
        "phase_first_reached_count": {
            f"phase_{ph}": len(steps) for ph, steps in phase_first_reached.items()
        },
        "final_p_real_mean": round(float(np.mean(p_real_values)), 4),
        "final_p_real_std": round(float(np.std(p_real_values)), 4),
        "final_evasion_mean": round(float(np.mean(evasion_values)), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--policy", default="random",
                        choices=["random", "flight_sim", "reboot", "statustext"])
    args = parser.parse_args()

    import json
    r = run(args.episodes, args.max_steps, args.seed, args.policy)
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
