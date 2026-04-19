#!/usr/bin/env python3
"""
aggregate_all_policies.py — Unified Policy Comparison Table

[ROLE]
    Aggregate results from
      - results/models/multi_seed_log.json       (DQN baseline, 3 seeds)
      - results/llm_multi_seed/<model>_seed*.json (LLM per-seed)
      - results/policy_comparison.json           (classic policy sweep if present)
    into a single Table VII-style JSON + pretty terminal table.

[USAGE]
    python scripts/aggregate_all_policies.py
    python scripts/aggregate_all_policies.py --output results/table_vii.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def mean_ci95(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return (float(values[0]), 0.0)
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    se = math.sqrt(var / n)
    return (m, 1.96 * se)


def load_dqn_baseline(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    per_seed = data.get("per_seed_dqn", {})
    rewards = [v["avg_reward"] for v in per_seed.values()]
    p_reals = [v["avg_p_real"] for v in per_seed.values()]
    r_m, r_h = mean_ci95(rewards)
    p_m, p_h = mean_ci95(p_reals)
    return {
        "policy": "DQN",
        "n_seeds": len(rewards),
        "avg_reward_mean": round(r_m, 3),
        "avg_reward_ci95": round(r_h, 3),
        "avg_p_real_mean": round(p_m, 4),
        "avg_p_real_ci95": round(p_h, 4),
        "survival_rate_mean": None,
        "survival_rate_ci95": None,
        "source": str(path),
    }


def _aggregate_by_key(dir_path: Path, key: str, label_prefix: str = "") -> list[dict]:
    """Aggregate per-seed JSONs by the given grouping key (e.g., 'model' or 'policy')."""
    if not dir_path.exists():
        return []
    by_key: dict[str, list[dict]] = {}
    for f in sorted(dir_path.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        k = data.get(key)
        if k is None:
            continue
        # Normalise "DQN-seed2024" → "DQN" so per-seed checkpoints aggregate
        if isinstance(k, str) and k.startswith("DQN-seed"):
            k = "DQN"
        by_key.setdefault(str(k), []).append(data)

    rows = []
    for k, runs in by_key.items():
        rewards = [r["avg_reward"] for r in runs]
        p_reals = [r["avg_p_real"] for r in runs]
        surv = [r["survival_rate"] for r in runs]
        r_m, r_h = mean_ci95(rewards)
        p_m, p_h = mean_ci95(p_reals)
        s_m, s_h = mean_ci95(surv)
        rows.append({
            "policy": f"{label_prefix}{k}",
            "n_seeds": len(rewards),
            "avg_reward_mean": round(r_m, 3),
            "avg_reward_ci95": round(r_h, 3),
            "avg_p_real_mean": round(p_m, 4),
            "avg_p_real_ci95": round(p_h, 4),
            "survival_rate_mean": round(s_m, 4),
            "survival_rate_ci95": round(s_h, 4),
            "source": str(dir_path),
        })
    return rows


def load_llm_runs(dir_path: Path) -> list[dict]:
    return _aggregate_by_key(dir_path, key="model", label_prefix="LLM-")


def load_matched_baselines(dir_path: Path) -> list[dict]:
    rows = _aggregate_by_key(dir_path, key="policy", label_prefix="")
    for r in rows:
        r["policy"] = r["policy"] + " (matched)"
    return rows


def load_classic_sweep(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    rows = []
    for r in data:
        # single-seed from classic compare_policies run
        rows.append({
            "policy": r.get("policy", "?"),
            "n_seeds": 1,
            "avg_reward_mean": r.get("avg_reward"),
            "avg_reward_ci95": 0.0,
            "avg_p_real_mean": r.get("avg_p_real"),
            "avg_p_real_ci95": 0.0,
            "survival_rate_mean": r.get("survival_rate"),
            "survival_rate_ci95": 0.0,
            "source": str(path),
        })
    return rows


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no rows)")
        return
    print()
    print(f"  {'Policy':<28} {'n':>3} {'avg_R':>10} {'±CI':>7}  "
          f"{'p_real':>8} {'±CI':>8}  {'survive':>8} {'±CI':>8}")
    print(f"  {'-' * 28} {'-' * 3} {'-' * 10} {'-' * 7}  "
          f"{'-' * 8} {'-' * 8}  {'-' * 8} {'-' * 8}")
    for r in rows:
        ar = r["avg_reward_mean"]
        ah = r["avg_reward_ci95"]
        pm = r["avg_p_real_mean"]
        ph = r["avg_p_real_ci95"]
        sv = r["survival_rate_mean"]
        sh = r["survival_rate_ci95"]
        ar_s = f"{ar:>10.3f}" if ar is not None else f"{'—':>10}"
        ah_s = f"{ah:>7.3f}" if ah is not None else f"{'—':>7}"
        pm_s = f"{pm:>8.4f}" if pm is not None else f"{'—':>8}"
        ph_s = f"{ph:>8.4f}" if ph is not None else f"{'—':>8}"
        sv_s = f"{sv:>8.4f}" if sv is not None else f"{'—':>8}"
        sh_s = f"{sh:>8.4f}" if sh is not None else f"{'—':>8}"
        print(f"  {r['policy']:<28} {r['n_seeds']:>3} "
              f"{ar_s} {ah_s}  {pm_s} {ph_s}  {sv_s} {sh_s}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dqn-log",
        default="results/models/multi_seed_log.json",
    )
    parser.add_argument(
        "--llm-dir",
        default="results/llm_multi_seed",
    )
    parser.add_argument(
        "--classic",
        default="results/policy_comparison.json",
    )
    parser.add_argument(
        "--matched-dir",
        default="results/baseline_matched",
        help="baseline runs at matched episode/step setup (run_baseline_matched.py output)",
    )
    parser.add_argument(
        "--output",
        default="results/table_vii.json",
    )
    args = parser.parse_args()

    rows: list[dict] = []
    dqn = load_dqn_baseline(Path(args.dqn_log))
    if dqn is not None:
        dqn["policy"] = "DQN (3-seed, 200-step)"
        rows.append(dqn)
    rows.extend(load_classic_sweep(Path(args.classic)))

    rows.extend(load_matched_baselines(Path(args.matched_dir)))

    rows.extend(load_llm_runs(Path(args.llm_dir)))

    rows.sort(key=lambda r: (
        not r["policy"].startswith("DQN"),
        not r["policy"].startswith("LLM-"),
        r["policy"],
    ))

    print_table(rows)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    print(f"\n  → saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
