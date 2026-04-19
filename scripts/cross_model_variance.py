#!/usr/bin/env python3
"""
cross_model_variance.py — Cross-model comparison across V2 runs

Aggregates supplementary_metrics (Cramér's V) along with headline metrics
(entropy, avg_p_real, avg_R) per model, reports within-model variance
(across 3 seeds) and between-model variance.

Output: docs/cross_model_variance.md + JSON
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


METRICS = [
    ("avg_reward",                     ["avg_reward"],                                         ".3f"),
    ("avg_p_real",                     ["avg_p_real"],                                          ".4f"),
    ("survival_rate",                  ["survival_rate"],                                        ".3f"),
    ("skill_entropy_bits",             ["policy_metrics", "skill_entropy_bits"],                ".3f"),
    ("cramers_v",                      ["supplementary_metrics", "cramers_v"],                   ".3f"),
    ("cramers_v_bias_corrected",       ["supplementary_metrics", "cramers_v_bias_corrected"],    ".3f"),
    ("chi_square_pvalue",              ["policy_metrics", "chi_square_pvalue"],                 ".6f"),
    ("misbelief_duration_ratio_mean",  ["belief_metrics", "misbelief_duration_ratio_mean"],     ".3f"),
    ("p_real_std_mean",                ["belief_metrics", "p_real_std_mean"],                   ".4f"),
    ("phase_advance_rate_mean",        ["coverage_metrics", "phase_advance_rate_mean"],         ".3f"),
    ("max_phase_reached_mean",         ["coverage_metrics", "max_phase_reached_mean"],          ".2f"),
    ("mean_latency_ms",                ["llm_summary", "mean_latency_ms"],                      ".0f"),
    ("fallback_rate",                  ["llm_summary", "fallback_rate"],                        ".4f"),
]


def dig(d: dict, path: list[str]):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-dir", default="results/llm_v2")
    parser.add_argument("--output-md", default="docs/cross_model_variance.md")
    parser.add_argument("--output-json", default="results/llm_v2/cross_model_variance.json")
    args = parser.parse_args()

    runs_dir = Path(args.llm_dir)
    by_model: dict[str, list[dict]] = {}
    for f in sorted(runs_dir.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        m = d.get("model")
        if m:
            by_model.setdefault(m, []).append(d)

    if not by_model:
        print(f"(no runs in {runs_dir})")
        return 0

    # Build per-model stats
    per_model: dict[str, dict] = {}
    for m, rs in by_model.items():
        stats: dict = {"n_seeds": len(rs), "seeds": [r.get("seed") for r in rs]}
        for name, path, _fmt in METRICS:
            vals = [dig(r, path) for r in rs]
            vals = [float(v) for v in vals if v is not None]
            if not vals:
                stats[name] = {"mean": None, "std": None, "min": None, "max": None}
                continue
            stats[name] = {
                "mean": float(np.mean(vals)),
                "std":  float(np.std(vals, ddof=1) if len(vals) > 1 else 0.0),
                "min":  float(np.min(vals)),
                "max":  float(np.max(vals)),
            }
        per_model[m] = stats

    # Between-model variance — std of per-model means for each metric
    between: dict[str, dict] = {}
    for name, _p, _f in METRICS:
        model_means = [per_model[m][name]["mean"] for m in per_model
                        if per_model[m][name]["mean"] is not None]
        if not model_means:
            continue
        between[name] = {
            "model_means": {m: per_model[m][name]["mean"] for m in per_model},
            "between_std":  float(np.std(model_means, ddof=1) if len(model_means) > 1 else 0.0),
            "between_mean": float(np.mean(model_means)),
            "range":        float(max(model_means) - min(model_means)),
        }

    # ── Markdown output ──
    lines = [
        "# Cross-Model Variance (V2, 3 seeds per model)",
        "",
        "Within-model `mean ± std` computed over 3 seeds (42 / 1337 / 2024).",
        "Between-model line reports std of the three per-model means.",
        "",
        "## §1 Headline metrics",
        "",
        "| Model | avg_R | avg_p_real | survival | H(skill) | Cramér V | V (bc) | χ² p | misbelief | latency (ms) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m, s in per_model.items():
        def cell(name: str, fmt: str = ".3f") -> str:
            v = s.get(name, {})
            mean = v.get("mean")
            sd = v.get("std")
            if mean is None:
                return "—"
            if sd is None or sd == 0.0:
                return f"{mean:{fmt}}"
            return f"{mean:{fmt}} ± {sd:{fmt}}"
        lines.append(
            f"| `{m}` | "
            f"{cell('avg_reward', '.2f')} | "
            f"{cell('avg_p_real', '.4f')} | "
            f"{cell('survival_rate', '.3f')} | "
            f"{cell('skill_entropy_bits', '.3f')} | "
            f"{cell('cramers_v', '.3f')} | "
            f"{cell('cramers_v_bias_corrected', '.3f')} | "
            f"{cell('chi_square_pvalue', '.4f')} | "
            f"{cell('misbelief_duration_ratio_mean', '.3f')} | "
            f"{cell('mean_latency_ms', '.0f')} |"
        )

    lines += [
        "",
        "## §2 Between-model variance (std of per-model means across models)",
        "",
        "Interpretation: high between-model std = models differ more than seeds do.",
        "",
        "| Metric | mean across models | between-model std | range |",
        "|---|---|---|---|",
    ]
    for name, b in between.items():
        lines.append(
            f"| `{name}` | {b['between_mean']:.4g} | "
            f"{b['between_std']:.4g} | {b['range']:.4g} |"
        )

    lines += [
        "",
        "## §3 Cross-model Cramér's V ranking",
        "",
        "Cramér's V quantifies the strength of phase-skill dependence. "
        "Higher = more phase-aware. Cohen thresholds (df=3): "
        "V<0.06 negligible; 0.06–0.17 small; 0.17–0.29 medium; ≥0.29 large.",
        "",
    ]
    cv_pairs = [(m, per_model[m].get("cramers_v", {}).get("mean", 0.0))
                for m in per_model]
    cv_pairs.sort(key=lambda x: -(x[1] or 0))
    lines += ["| Rank | Model | Cramér V mean | effect |",
              "|---|---|---|---|"]
    for rank, (m, v) in enumerate(cv_pairs, 1):
        if v is None:
            continue
        effect = ("large" if v >= 0.29 else "medium" if v >= 0.17
                  else "small" if v >= 0.06 else "negligible")
        lines.append(f"| {rank} | `{m}` | {v:.3f} | {effect} |")

    md_path = Path(args.output_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines))

    json_path = Path(args.output_json)
    json_path.write_text(json.dumps({
        "per_model": per_model,
        "between_model": between,
    }, indent=2, default=str))

    print(f"→ {md_path}")
    print(f"→ {json_path}")
    print()
    print("\n".join(lines[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
