#!/usr/bin/env python3
"""
tune_signaling.py — Grid search over SignalingGameSolver hyperparameters

[ROLE]
    Sweep κ (cost sensitivity) × τ (softmax temperature) on MarkovGameEnv
    against a mixed panel of attacker policies (random + greedy), pick the
    configuration that maximises defender payoff.

[USAGE]
    python3 scripts/tune_signaling.py
    python3 scripts/tune_signaling.py --episodes 300 --output results/signaling_tune.json

[OUTPUT]
    results/signaling_tune.json          — all grid-cell scores
    results/signaling_tune_best.json     — top 5 configs
    config/.env                          — (optional, with --apply) best κ, τ
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from honey_drone.markov_game_env import (
    MarkovGameEnv,
    N_ATTACKER_ACTIONS,
    RandomPolicy,
    GreedyAttackerPolicy,
)
from honey_drone.signaling_game_solver import SignalingGameSolver


def evaluate_config(
    kappa: float,
    temperature: float,
    epsilon: float,
    attacker_policies: list,
    episodes_per_attacker: int,
    max_steps: int = 200,
) -> dict:
    """Run `episodes_per_attacker` episodes for each attacker, return avg payoff."""
    per_atk = {}
    total_r_def = 0.0
    total_r_atk = 0.0
    total_dwell = 0.0
    total_mu = 0.0
    total_eps = 0

    for atk_name, atk_pol in attacker_policies:
        # Fresh solver each matchup (EMA starts cold — fair comparison)
        solver = SignalingGameSolver(
            cost_sensitivity_kappa=kappa,
            temperature=temperature,
            exploration_epsilon=epsilon,
            learning_rate=0.1,
        )

        env = MarkovGameEnv(max_steps=max_steps)
        r_def_sum, r_atk_sum, dwell_sum, mu_sum = 0.0, 0.0, 0.0, 0.0
        for _ in range(episodes_per_attacker):
            obs_d, obs_a = env.reset()
            ep_r_def, ep_r_atk = 0.0, 0.0
            for _ in range(max_steps):
                phase = int(round(float(obs_d[0]) * 3))
                mu = float(obs_d[2])
                d_act, _, _ = solver.select_skill(mu_a=mu, phase=max(0, min(3, phase)))
                a_act = atk_pol.select(obs_a)
                obs_d, obs_a, r_d, r_a, done, info = env.step(d_act, a_act)
                ep_r_def += r_d
                ep_r_atk += r_a
                if done:
                    break
            r_def_sum += ep_r_def
            r_atk_sum += ep_r_atk
            dwell_sum += env.state.dwell_sec
            mu_sum += env.state.mu_a
            total_eps += 1

        per_atk[atk_name] = {
            "avg_r_def": round(r_def_sum / episodes_per_attacker, 4),
            "avg_r_atk": round(r_atk_sum / episodes_per_attacker, 4),
            "avg_dwell": round(dwell_sum / episodes_per_attacker, 2),
            "avg_mu_a": round(mu_sum / episodes_per_attacker, 4),
        }
        total_r_def += r_def_sum
        total_r_atk += r_atk_sum
        total_dwell += dwell_sum
        total_mu += mu_sum

    return {
        "kappa": kappa,
        "temperature": temperature,
        "epsilon": epsilon,
        "avg_r_def": round(total_r_def / total_eps, 4),
        "avg_r_atk": round(total_r_atk / total_eps, 4),
        "avg_dwell": round(total_dwell / total_eps, 2),
        "avg_mu_a": round(total_mu / total_eps, 4),
        "per_attacker": per_atk,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=200,
                    help="Episodes per attacker per grid cell")
    ap.add_argument("--kappas", type=str, default="0.1,0.3,0.5,0.7,1.0",
                    help="Comma-separated κ values")
    ap.add_argument("--temperatures", type=str, default="0.3,0.5,0.8,1.2,2.0",
                    help="Comma-separated τ values")
    ap.add_argument("--epsilons", type=str, default="0.10",
                    help="Comma-separated ε values (usually fixed)")
    ap.add_argument("--output", type=str, default="results/signaling_tune.json")
    ap.add_argument("--apply", action="store_true",
                    help="Write best κ, τ back to config/.env (appends/overwrites SIGNALING_* lines)")
    args = ap.parse_args()

    kappas = [float(x) for x in args.kappas.split(",")]
    temps = [float(x) for x in args.temperatures.split(",")]
    epsilons = [float(x) for x in args.epsilons.split(",")]

    # Attacker panel — covers random + phase-optimal
    attackers = [
        ("Random", RandomPolicy(N_ATTACKER_ACTIONS)),
        ("Greedy", GreedyAttackerPolicy()),
    ]

    total_cells = len(kappas) * len(temps) * len(epsilons)
    print(f"\n  Grid: {len(kappas)} κ × {len(temps)} τ × {len(epsilons)} ε = {total_cells} cells")
    print(f"  Episodes/cell: {args.episodes} × {len(attackers)} attackers = {args.episodes * len(attackers)}")
    print(f"  Total episodes: {total_cells * args.episodes * len(attackers):,}\n")

    all_results = []
    t_start = time.time()

    cell = 0
    for k in kappas:
        for t in temps:
            for e in epsilons:
                cell += 1
                t0 = time.time()
                res = evaluate_config(k, t, e, attackers, args.episodes)
                elapsed = time.time() - t0
                all_results.append(res)
                print(f"  [{cell:2d}/{total_cells}] κ={k:.2f} τ={t:.2f} ε={e:.2f}  "
                      f"r_def={res['avg_r_def']:+7.3f}  "
                      f"μ_A={res['avg_mu_a']:.3f}  "
                      f"dwell={res['avg_dwell']:6.1f}s  ({elapsed:.1f}s)")

    elapsed = time.time() - t_start

    # Rank by defender payoff
    ranked = sorted(all_results, key=lambda r: r["avg_r_def"], reverse=True)
    top5 = ranked[:5]

    print(f"\n  ── Top 5 configurations (by avg_r_def) ──")
    print(f"  {'κ':>6s}  {'τ':>6s}  {'ε':>6s}  {'r_def':>8s}  {'μ_A':>6s}  {'dwell':>8s}")
    for r in top5:
        print(f"  {r['kappa']:6.2f}  {r['temperature']:6.2f}  {r['epsilon']:6.2f}  "
              f"{r['avg_r_def']:+8.3f}  {r['avg_mu_a']:6.3f}  {r['avg_dwell']:8.1f}")

    # Save
    output = {
        "episodes_per_attacker": args.episodes,
        "grid": {"kappas": kappas, "temperatures": temps, "epsilons": epsilons},
        "attackers": [name for name, _ in attackers],
        "elapsed_sec": round(elapsed, 1),
        "all_cells": all_results,
        "top5": top5,
        "best": ranked[0],
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\n  Full results: {out_path}")

    best_path = out_path.with_name(out_path.stem + "_best.json")
    best_path.write_text(json.dumps({"best": ranked[0], "top5": top5}, indent=2))
    print(f"  Best only:    {best_path}")

    # Optionally write back to .env
    if args.apply:
        env_path = Path("config/.env")
        if not env_path.exists():
            example_path = Path("config/.env.example")
            if example_path.exists():
                env_path.write_text(example_path.read_text())
                print(f"  Bootstrapped {env_path} from {example_path}")
            else:
                print(f"  --apply skipped: neither {env_path} nor {example_path} exists")
                return
        best = ranked[0]
        lines = env_path.read_text().splitlines()
        targets = {
            "SIGNALING_KAPPA": best["kappa"],
            "SIGNALING_TEMPERATURE": best["temperature"],
            "SIGNALING_EPSILON": best["epsilon"],
        }
        seen = set()
        new_lines = []
        for ln in lines:
            stripped = ln.strip()
            if "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=", 1)[0]
                if key in targets:
                    new_lines.append(f"{key}={targets[key]}")
                    seen.add(key)
                    continue
            new_lines.append(ln)
        # Append any missing keys
        for k, v in targets.items():
            if k not in seen:
                new_lines.append(f"{k}={v}")
        env_path.write_text("\n".join(new_lines) + "\n")
        print(f"  Applied best config → {env_path}")


if __name__ == "__main__":
    main()
