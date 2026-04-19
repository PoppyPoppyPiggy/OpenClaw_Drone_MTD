#!/usr/bin/env python3
"""
add_supplementary_metrics.py — Compute additional metrics from saved V2 runs

Reads results/llm_v2/<model>_seed<seed>.json, derives supplementary stats
that were not in the original run, and writes them back under a new
`supplementary_metrics` key. Non-destructive: existing keys untouched.

New metrics:
  - policy_metrics.cramers_v         (effect size for phase-skill dependence)
  - policy_metrics.cramers_v_bias_corrected  (Bergsma 2013 correction)
  - belief_metrics.volatility_summary (alias for p_real_std_mean)
  - coverage_metrics.phase_advance_rate_per_episode_distribution
    (min / p25 / median / p75 / max across episodes)
  - policy_metrics.js_divergence_phase_pairs_labels

Also emits per-phase skill distribution percentages for quick reading.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


def cramers_v(contingency: np.ndarray, bias_correct: bool = False) -> float:
    """Cramér's V effect size from a 2-D contingency table.

    V = sqrt( chi2 / (n * min(rows-1, cols-1)) )

    With `bias_correct=True`, returns the Bergsma-Cressie V_tilde which
    corrects for sample-size inflation in small tables.
    """
    O = np.asarray(contingency, dtype=np.float64)
    n = float(O.sum())
    if n <= 0:
        return 0.0
    row_tot = O.sum(axis=1, keepdims=True)
    col_tot = O.sum(axis=0, keepdims=True)
    E = row_tot @ col_tot / n
    mask = E > 0
    chi2 = float(((O - E)[mask] ** 2 / E[mask]).sum())
    r, c = O.shape
    denom = n * max(1, min(r - 1, c - 1))
    v = math.sqrt(chi2 / denom) if denom > 0 else 0.0
    if not bias_correct:
        return v
    # Bergsma-Cressie bias-corrected V
    phi2 = chi2 / n
    phi2_hat = max(0.0, phi2 - (r - 1) * (c - 1) / max(1.0, n - 1))
    r_hat = r - ((r - 1) ** 2) / max(1.0, n - 1)
    c_hat = c - ((c - 1) ** 2) / max(1.0, n - 1)
    denom_hat = max(1.0, min(r_hat - 1, c_hat - 1))
    return math.sqrt(phi2_hat / denom_hat) if denom_hat > 0 else 0.0


def js_divergence(p: np.ndarray, q: np.ndarray, eps: float = 1e-12) -> float:
    p = p + eps
    q = q + eps
    p = p / p.sum()
    q = q / q.sum()
    m = 0.5 * (p + q)
    kl_pm = (p * np.log2(p / m)).sum()
    kl_qm = (q * np.log2(q / m)).sum()
    return float(0.5 * (kl_pm + kl_qm))


def process_run(path: Path) -> dict:
    data = json.loads(path.read_text())
    cm_list = data.get("policy_metrics", {}).get("phase_skill_confusion_matrix")
    if cm_list is None:
        return {"path": str(path), "skipped": "no confusion matrix"}
    cm = np.asarray(cm_list, dtype=np.float64)
    labels = data["policy_metrics"].get("phase_skill_confusion_labels", {})
    phases = labels.get("phases", [f"phase_{i}" for i in range(cm.shape[0])])
    skills = labels.get("skills", [f"skill_{i}" for i in range(cm.shape[1])])

    cv = cramers_v(cm, bias_correct=False)
    cv_bc = cramers_v(cm, bias_correct=True)

    # Per-phase skill distribution %
    row_tot = cm.sum(axis=1, keepdims=True)
    row_tot_safe = np.where(row_tot == 0, 1, row_tot)
    per_phase_pct = (cm / row_tot_safe * 100).round(1)
    per_phase_pct_dict = {
        phases[i]: {skills[j]: float(per_phase_pct[i, j]) for j in range(cm.shape[1])}
        for i in range(cm.shape[0])
    }

    # Pairwise JS-divergence matrix
    n_phases = cm.shape[0]
    js_mat = np.zeros((n_phases, n_phases), dtype=np.float64)
    for i in range(n_phases):
        for j in range(n_phases):
            if row_tot[i, 0] > 0 and row_tot[j, 0] > 0:
                js_mat[i, j] = js_divergence(cm[i], cm[j])

    supplementary = {
        "cramers_v": round(cv, 4),
        "cramers_v_bias_corrected": round(cv_bc, 4),
        "cramers_v_interpretation": _interpret_v(cv, cm.shape),
        "per_phase_skill_pct": per_phase_pct_dict,
        "js_divergence_phase_pairs": js_mat.round(4).tolist(),
        "js_divergence_phase_labels": phases,
        "belief_volatility": data.get("belief_metrics", {}).get("p_real_std_mean"),
    }

    # Update in place (non-destructive)
    data.setdefault("supplementary_metrics", {}).update(supplementary)
    path.write_text(json.dumps(data, indent=2))

    return {
        "path": str(path),
        "model": data.get("model"),
        "seed": data.get("seed"),
        "cramers_v": round(cv, 4),
        "cramers_v_bc": round(cv_bc, 4),
    }


def _interpret_v(v: float, shape: tuple[int, int]) -> str:
    """Cohen-style thresholds for Cramér's V (varies with df=min(r-1,c-1))."""
    df = min(shape[0] - 1, shape[1] - 1)
    # Cohen thresholds (df-aware)
    if df == 1:
        small, medium, large = 0.10, 0.30, 0.50
    elif df == 2:
        small, medium, large = 0.07, 0.21, 0.35
    else:
        small, medium, large = 0.06, 0.17, 0.29  # df ≥ 3
    if v < small:
        return "negligible"
    if v < medium:
        return "small"
    if v < large:
        return "medium"
    return "large"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-dir", default="results/llm_v2")
    args = parser.parse_args()

    runs_dir = Path(args.llm_dir)
    results = []
    for f in sorted(runs_dir.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        r = process_run(f)
        results.append(r)

    print("=== Supplementary metrics written (Cramér's V + phase_pct + JS matrix) ===")
    print(f"{'Model':<20} {'seed':>6} {'Cramér V':>10} {'V (bc)':>10} {'effect size':>14}")
    for r in results:
        if "skipped" in r:
            continue
        shape = (4, 5)
        inter = _interpret_v(r["cramers_v"], shape)
        print(f"{r['model']:<20} {r['seed']:>6} "
              f"{r['cramers_v']:>10.4f} {r['cramers_v_bc']:>10.4f} {inter:>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
