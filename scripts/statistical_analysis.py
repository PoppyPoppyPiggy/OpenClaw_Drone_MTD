#!/usr/bin/env python3
"""
statistical_analysis.py — N=30 experiment results statistical analysis.

Runs:
  1. Shapiro-Wilk normality
  2. Wilcoxon signed-rank (non-parametric)
  3. Cohen's d effect size
  4. Holm-Bonferroni correction
  5. Bootstrap BCa 95% CI
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from evaluation.statistical_test import full_statistical_evaluation


def load_scores(cond_dir: str, metric: str = "deception_score") -> list[float]:
    scores: list[float] = []
    for f in sorted(glob.glob(f"{cond_dir}/trial_*.json")):
        try:
            with open(f) as fp:
                data = json.load(fp)
            scores.append(data.get(metric, 0.5))
        except (json.JSONDecodeError, KeyError):
            continue
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description="N=30 statistical analysis")
    parser.add_argument("--n30-dir", default="results/n30")
    parser.add_argument("--output", default="results/metrics/statistics.json")
    parser.add_argument("--metric", default="deception_score")
    args = parser.parse_args()

    proposed = load_scores(f"{args.n30_dir}/mirage_full", args.metric)

    baselines = {}
    for name, subdir in [
        ("No defense", "no_defense"),
        ("MTD only", "mtd_only"),
        ("Deception only", "deception_only"),
    ]:
        scores = load_scores(f"{args.n30_dir}/{subdir}", args.metric)
        if scores:
            baselines[name] = scores

    if len(proposed) < 5:
        print(f"ERROR: only {len(proposed)} proposed scores (need >= 5)", file=sys.stderr)
        sys.exit(1)

    # Truncate to equal sizes
    min_n = min(len(proposed), *(len(v) for v in baselines.values())) if baselines else len(proposed)
    proposed = proposed[:min_n]
    baselines = {k: v[:min_n] for k, v in baselines.items()}

    result = full_statistical_evaluation(proposed, baselines, args.metric)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n=== Statistical Results (N={min_n}) ===")
    print(f"  Metric:  {result['metric']}")
    print(f"  Mean:    {result['mean']:.4f} [{result['ci_low']:.4f}, {result['ci_high']:.4f}] 95% CI")
    norm = result.get("normality", {})
    print(f"  Normal:  {'Yes' if norm.get('normal') else 'No'} (Shapiro p={norm.get('p_value')})")
    for name, cmp in result["comparisons"].items():
        sig = "Y" if cmp.get("significant_corrected") else "N"
        d_val = cmp.get("cohens_d", cmp.get("d", 0))
        print(
            f"  vs {name:20s}: sig={sig} p_adj={cmp.get('p_adjusted', '?'):.6f} "
            f"d={d_val:.3f} ({cmp['magnitude']})"
        )
    print(f"  All significant: {'YES' if result['all_significant'] else 'NO'}")


if __name__ == "__main__":
    main()
