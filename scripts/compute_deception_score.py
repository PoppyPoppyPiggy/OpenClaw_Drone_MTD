#!/usr/bin/env python3
"""
compute_deception_score.py — Apply DeceptionScore v2 to every V2 run

Loads each results/llm_v2/<model>_seed<seed>.json, extracts the five L1-L5
inputs (HTUR, avg_p_real, survival_rate, phase_advance_rate,
skill_entropy_bits), computes the composite score under uniform weights and
under a sensitivity-analysis grid.

[OUTPUT]
    docs/deception_score_v2_analysis.md
    results/llm_v2/deception_score_per_run.json

[USAGE]
    python scripts/compute_deception_score.py
    python scripts/compute_deception_score.py --htur-source results/diagnostics/htur.json
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metrics.deception_score_v2 import (  # noqa: E402
    compute_deception_score_v2,
    DEFAULT_WEIGHTS,
    WEIGHT_KEYS,
)


def htur_from_diag(path: Path) -> float:
    """Aggregate HTUR from offline synthetic diagnostic (single global number)."""
    if not path.exists():
        return 0.0
    try:
        d = json.loads(path.read_text())
        return float(d.get("aggregate_stats", {}).get("htur", 0.0))
    except Exception:
        return 0.0


def extract_inputs(run: dict, htur: float) -> dict:
    cov = run.get("coverage_metrics", {})
    pol = run.get("policy_metrics", {})
    return {
        "htur": float(htur),
        "avg_p_real": float(run.get("avg_p_real", 0.0)),
        "survival_rate": float(run.get("survival_rate", 0.0)),
        "phase_advance_rate": float(cov.get("phase_advance_rate_mean", 0.0)),
        "skill_entropy_bits": float(pol.get("skill_entropy_bits", 0.0)),
    }


def sensitivity_grid() -> list[dict[str, float]]:
    """Grid of corner-case weight profiles.

    Each entry sums to 1. Useful for the paper's sensitivity analysis.
    """
    grids = [
        {"w_htur": 0.4, "w_belief": 0.15, "w_eng": 0.15, "w_cov": 0.15, "w_pol": 0.15},
        {"w_htur": 0.15, "w_belief": 0.4, "w_eng": 0.15, "w_cov": 0.15, "w_pol": 0.15},
        {"w_htur": 0.15, "w_belief": 0.15, "w_eng": 0.4, "w_cov": 0.15, "w_pol": 0.15},
        {"w_htur": 0.15, "w_belief": 0.15, "w_eng": 0.15, "w_cov": 0.4, "w_pol": 0.15},
        {"w_htur": 0.15, "w_belief": 0.15, "w_eng": 0.15, "w_cov": 0.15, "w_pol": 0.4},
        dict(DEFAULT_WEIGHTS),  # uniform
    ]
    return grids


GRID_LABELS = [
    "HTUR-heavy", "belief-heavy", "engagement-heavy",
    "coverage-heavy", "policy-heavy", "uniform",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-dir", default="results/llm_v2")
    parser.add_argument(
        "--htur-source", default="results/diagnostics/htur.json",
        help="HTUR is a system-level metric, applied uniformly to all runs",
    )
    parser.add_argument(
        "--output-md", default="docs/deception_score_v2_analysis.md",
    )
    parser.add_argument(
        "--output-json", default="results/llm_v2/deception_score_per_run.json",
    )
    args = parser.parse_args()

    htur = htur_from_diag(Path(args.htur_source))
    runs_dir = Path(args.llm_dir)
    runs: list[dict] = []
    for f in sorted(runs_dir.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        try:
            runs.append(json.loads(f.read_text()))
        except Exception:
            continue
    if not runs:
        print(f"(no runs in {runs_dir})")
        return 0

    grid = sensitivity_grid()

    # Per-run, per-weight-profile scores
    rows = []
    for r in runs:
        inputs = extract_inputs(r, htur)
        per_profile = {}
        for w, label in zip(grid, GRID_LABELS):
            s = compute_deception_score_v2(**inputs, weights=w)
            per_profile[label] = s.as_dict()
        rows.append({
            "model": r.get("model"),
            "seed": r.get("seed"),
            "inputs": inputs,
            "scores_by_profile": per_profile,
        })

    # Aggregate per model (mean across seeds) for uniform weights
    per_model: dict[str, list[float]] = {}
    for row in rows:
        per_model.setdefault(row["model"], []).append(
            row["scores_by_profile"]["uniform"]["score"]
        )

    # Markdown report
    lines = [
        "# DeceptionScore v2 — Applied to V2 experiment",
        "",
        f"HTUR source: `{args.htur_source}` → HTUR = **{htur:.3f}** (offline synthetic; applied uniformly).",
        "",
        "## §1 Per-run scores under uniform weights",
        "",
        "| Model | seed | HTUR | belief | engage | coverage | policy | **score** |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        u = row["scores_by_profile"]["uniform"]
        c = u["components"]
        lines.append(
            f"| `{row['model']}` | {row['seed']} | "
            f"{c['htur']:.3f} | {c['belief']:.3f} | {c['engagement']:.3f} | "
            f"{c['coverage']:.3f} | {c['policy']:.3f} | **{u['score']:.3f}** |"
        )

    lines += [
        "",
        "## §2 Per-model aggregate (mean across seeds, uniform weights)",
        "",
        "| Model | n_seeds | mean DeceptionScore |",
        "|---|---|---|",
    ]
    for m, scores in sorted(per_model.items()):
        lines.append(f"| `{m}` | {len(scores)} | **{sum(scores)/len(scores):.3f}** |")

    lines += [
        "",
        "## §3 Sensitivity analysis (first run, all 6 weight profiles)",
        "",
        "Interpretation: if the score ranking is stable across profiles, the "
        "conclusion is robust to weight choice. Profile names indicate which "
        "component is up-weighted to 0.40 (others 0.15 each); `uniform` is 0.20 each.",
        "",
        "| Profile | " + " | ".join(f"score ({r['model']}, s={r['seed']})" for r in rows[:3]) + " |",
        "|---|" + "---|" * len(rows[:3]),
    ]
    for label in GRID_LABELS:
        vals = [row["scores_by_profile"][label]["score"] for row in rows[:3]]
        lines.append(
            f"| {label} | " + " | ".join(f"{v:.3f}" for v in vals) + " |"
        )

    lines += [
        "",
        "## §4 Weights used (uniform default)",
        "",
        "```json",
        json.dumps(DEFAULT_WEIGHTS, indent=2),
        "```",
        "",
    ]

    md_path = Path(args.output_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines))

    json_path = Path(args.output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(rows, indent=2))

    print(f"→ {md_path}")
    print(f"→ {json_path}")
    print()
    print("\n".join(lines[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
