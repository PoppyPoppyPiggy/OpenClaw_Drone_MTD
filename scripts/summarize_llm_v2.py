#!/usr/bin/env python3
"""
summarize_llm_v2.py — Aggregate V2 (prompt_v2 + L1-L5 metrics) experiment

[ROLE]
    Pull every results/llm_v2/<model>_seed<seed>.json, group by model,
    compute mean + 95% bootstrap CI (1000 resamples) for all L2-L5 metrics,
    and emit a markdown table ready for the paper.

[OUTPUT]
    results/llm_v2/summary.md
    results/llm_v2/summary.json

[USAGE]
    python scripts/summarize_llm_v2.py
    python scripts/summarize_llm_v2.py --runs-dir results/llm_v2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def bootstrap_ci(values: list[float], n_resamples: int = 1000,
                  confidence: float = 0.95, rng: np.random.Generator | None = None) -> tuple[float, float, float]:
    """Return (mean, ci_low, ci_high) via percentile bootstrap."""
    if not values:
        return (0.0, 0.0, 0.0)
    rng = rng or np.random.default_rng(0)
    arr = np.asarray(values, dtype=np.float64)
    if len(arr) == 1:
        m = float(arr[0])
        return (m, m, m)
    resamples = rng.choice(arr, size=(n_resamples, len(arr)), replace=True)
    means = resamples.mean(axis=1)
    lo = (1 - confidence) / 2 * 100
    hi = (1 + confidence) / 2 * 100
    return (float(arr.mean()), float(np.percentile(means, lo)),
            float(np.percentile(means, hi)))


def load_runs_by_model(dir_path: Path) -> dict[str, list[dict]]:
    by_model: dict[str, list[dict]] = {}
    for f in sorted(dir_path.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        model = d.get("model")
        if model is None:
            continue
        by_model.setdefault(model, []).append(d)
    return by_model


def fmt_ci(m: float, lo: float, hi: float, prec: int = 3) -> str:
    half = (hi - lo) / 2
    return f"{m:.{prec}f} ± {half:.{prec}f}"


def aggregate_runs(runs: list[dict]) -> dict:
    """Compute bootstrap-CI mean for every numeric metric across seeds."""
    rng = np.random.default_rng(42)

    def pick(metric_path: list[str]) -> list[float]:
        vals = []
        for r in runs:
            obj: object = r
            ok = True
            for k in metric_path:
                if isinstance(obj, dict) and k in obj:
                    obj = obj[k]
                else:
                    ok = False
                    break
            if ok and isinstance(obj, (int, float)):
                vals.append(float(obj))
        return vals

    def ci(path: list[str]) -> tuple[float, float, float]:
        return bootstrap_ci(pick(path), rng=rng)

    result: dict = {"n_seeds": len(runs), "seeds": [r.get("seed") for r in runs]}

    # Top-level
    for key in ["avg_reward", "avg_p_real", "survival_rate", "avg_length"]:
        m, lo, hi = ci([key])
        result[key] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}

    # Belief
    bm: dict = {}
    for key in ["avg_p_real_mean", "p_real_std_mean", "misbelief_duration_ratio_mean",
                "p_real_min_mean", "p_real_max_mean"]:
        m, lo, hi = ci(["belief_metrics", key])
        bm[key] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    result["belief_metrics"] = bm

    # Engagement
    em: dict = {}
    for key in ["mean_step_at_exit", "survival_rate"]:
        m, lo, hi = ci(["engagement_metrics", key])
        em[key] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    result["engagement_metrics"] = em

    # Coverage
    cm: dict = {}
    for key in ["phase_advance_rate_mean", "phase_reversal_count_mean", "max_phase_reached_mean"]:
        m, lo, hi = ci(["coverage_metrics", key])
        cm[key] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    # Per-phase time share averaged across seeds
    for ph in range(4):
        pkey = f"phase_{ph}"
        vals = [float(r.get("coverage_metrics", {}).get("time_share_per_phase", {}).get(pkey, 0.0))
                for r in runs]
        m, lo, hi = bootstrap_ci(vals, rng=rng)
        cm[f"time_share_{pkey}"] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    result["coverage_metrics"] = cm

    # Policy
    pm: dict = {}
    for key in ["skill_entropy_bits", "chi_square_stat", "chi_square_pvalue"]:
        m, lo, hi = ci(["policy_metrics", key])
        pm[key] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    # Mean JS divergence across all pairs of phases (excluding diag)
    js_pair_vals = []
    for r in runs:
        mat = r.get("policy_metrics", {}).get("js_divergence_phase_pairs")
        if mat:
            arr = np.asarray(mat, dtype=np.float64)
            n = arr.shape[0]
            mask = ~np.eye(n, dtype=bool)
            js_pair_vals.append(float(arr[mask].mean()))
    m, lo, hi = bootstrap_ci(js_pair_vals, rng=rng)
    pm["js_divergence_phase_mean"] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    result["policy_metrics"] = pm

    # Reward components (mean per-step contribution)
    rc: dict = {}
    for comp in ["r_belief", "r_engage", "r_dwell", "r_safety"]:
        m, lo, hi = ci(["reward_components_mean_per_step", comp])
        rc[comp] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    result["reward_components_mean_per_step"] = rc

    # LLM operational
    llm: dict = {}
    for key in ["mean_latency_ms", "p95_latency_ms", "fallback_rate", "calls"]:
        m, lo, hi = ci(["llm_summary", key])
        llm[key] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}
    result["llm_operational"] = llm

    # Phase-preference diversity count (how many distinct skills win across phases, avg across seeds)
    ph_uniq = []
    for r in runs:
        pp = r.get("phase_preference", {})
        ph_uniq.append(float(len(set(pp.values()))))
    m, lo, hi = bootstrap_ci(ph_uniq, rng=rng)
    result["phase_skill_uniqueness_4"] = {"mean": round(m, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4)}

    return result


def render_markdown(summaries: dict[str, dict]) -> str:
    lines = [
        "# LLM V2 — Experiment Summary",
        "",
        "Aggregated across 3 seeds per model (95 % percentile bootstrap CI, 1000 resamples).",
        "",
        "## §1 Headline metrics",
        "",
        "| Model | avg_R | avg_p_real | survival | H(skill, bits) | χ² p | phase-uniq /4 |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, s in summaries.items():
        r = s["avg_reward"]
        p = s["avg_p_real"]
        sv = s["survival_rate"]
        h = s["policy_metrics"]["skill_entropy_bits"]
        chi_p = s["policy_metrics"]["chi_square_pvalue"]
        phu = s["phase_skill_uniqueness_4"]
        lines.append(
            f"| `{model}` | {fmt_ci(r['mean'], r['ci_low'], r['ci_high'], 3)} | "
            f"{fmt_ci(p['mean'], p['ci_low'], p['ci_high'], 4)} | "
            f"{fmt_ci(sv['mean'], sv['ci_low'], sv['ci_high'], 3)} | "
            f"{fmt_ci(h['mean'], h['ci_low'], h['ci_high'], 3)} | "
            f"{chi_p['mean']:.4f} | "
            f"{fmt_ci(phu['mean'], phu['ci_low'], phu['ci_high'], 2)} |"
        )

    lines += [
        "",
        "χ² p < 0.05 indicates that skill choice is NOT independent of the "
        "attacker phase — i.e., the LLM is selecting phase-appropriately.",
        "phase-uniq = number of distinct skills appearing as the modal "
        "choice in each of the 4 phases (max = 4).",
        "",
        "## §2 L2 Belief metrics",
        "",
        "| Model | mean p_real | p_real std | misbelief duration ratio |",
        "|---|---|---|---|",
    ]
    for model, s in summaries.items():
        b = s["belief_metrics"]
        lines.append(
            f"| `{model}` | {fmt_ci(b['avg_p_real_mean']['mean'], b['avg_p_real_mean']['ci_low'], b['avg_p_real_mean']['ci_high'], 4)} | "
            f"{fmt_ci(b['p_real_std_mean']['mean'], b['p_real_std_mean']['ci_low'], b['p_real_std_mean']['ci_high'], 4)} | "
            f"{fmt_ci(b['misbelief_duration_ratio_mean']['mean'], b['misbelief_duration_ratio_mean']['ci_low'], b['misbelief_duration_ratio_mean']['ci_high'], 3)} |"
        )

    lines += [
        "",
        "## §3 L4 Coverage metrics (lower phase_advance_rate is better for defender)",
        "",
        "| Model | phase_advance_rate | max_phase_reached | reversals | time share (R/EXP/PRS/EXF) |",
        "|---|---|---|---|---|",
    ]
    for model, s in summaries.items():
        c = s["coverage_metrics"]
        ts = (f"{c['time_share_phase_0']['mean']:.2f} / "
              f"{c['time_share_phase_1']['mean']:.2f} / "
              f"{c['time_share_phase_2']['mean']:.2f} / "
              f"{c['time_share_phase_3']['mean']:.2f}")
        lines.append(
            f"| `{model}` | {fmt_ci(c['phase_advance_rate_mean']['mean'], c['phase_advance_rate_mean']['ci_low'], c['phase_advance_rate_mean']['ci_high'], 3)} | "
            f"{fmt_ci(c['max_phase_reached_mean']['mean'], c['max_phase_reached_mean']['ci_low'], c['max_phase_reached_mean']['ci_high'], 2)} | "
            f"{fmt_ci(c['phase_reversal_count_mean']['mean'], c['phase_reversal_count_mean']['ci_low'], c['phase_reversal_count_mean']['ci_high'], 2)} | "
            f"{ts} |"
        )

    lines += [
        "",
        "## §4 L5 Policy diversity (Phase × Skill)",
        "",
        "| Model | skill entropy (bits) | JS divergence phase-pair mean | phase-uniq |",
        "|---|---|---|---|",
    ]
    for model, s in summaries.items():
        p = s["policy_metrics"]
        phu = s["phase_skill_uniqueness_4"]
        lines.append(
            f"| `{model}` | {fmt_ci(p['skill_entropy_bits']['mean'], p['skill_entropy_bits']['ci_low'], p['skill_entropy_bits']['ci_high'], 3)} | "
            f"{fmt_ci(p['js_divergence_phase_mean']['mean'], p['js_divergence_phase_mean']['ci_low'], p['js_divergence_phase_mean']['ci_high'], 4)} | "
            f"{fmt_ci(phu['mean'], phu['ci_low'], phu['ci_high'], 2)} |"
        )

    lines += [
        "",
        "## §5 Reward decomposition (per-step contribution)",
        "",
        "Dominant contributor reveals what the LLM is actually optimising for. "
        "Negative r_safety indicates evasion pressure the defender is under.",
        "",
        "| Model | r_belief | r_engage | r_dwell | r_safety |",
        "|---|---|---|---|---|",
    ]
    for model, s in summaries.items():
        rc = s["reward_components_mean_per_step"]
        lines.append(
            f"| `{model}` | {fmt_ci(rc['r_belief']['mean'], rc['r_belief']['ci_low'], rc['r_belief']['ci_high'], 4)} | "
            f"{fmt_ci(rc['r_engage']['mean'], rc['r_engage']['ci_low'], rc['r_engage']['ci_high'], 4)} | "
            f"{fmt_ci(rc['r_dwell']['mean'], rc['r_dwell']['ci_low'], rc['r_dwell']['ci_high'], 4)} | "
            f"{fmt_ci(rc['r_safety']['mean'], rc['r_safety']['ci_low'], rc['r_safety']['ci_high'], 4)} |"
        )

    lines += [
        "",
        "## §6 Operational (LLM latency + fallback)",
        "",
        "| Model | mean latency (ms) | p95 latency (ms) | fallback rate | calls/seed |",
        "|---|---|---|---|---|",
    ]
    for model, s in summaries.items():
        lo = s["llm_operational"]
        lines.append(
            f"| `{model}` | {fmt_ci(lo['mean_latency_ms']['mean'], lo['mean_latency_ms']['ci_low'], lo['mean_latency_ms']['ci_high'], 0)} | "
            f"{fmt_ci(lo['p95_latency_ms']['mean'], lo['p95_latency_ms']['ci_low'], lo['p95_latency_ms']['ci_high'], 0)} | "
            f"{fmt_ci(lo['fallback_rate']['mean'], lo['fallback_rate']['ci_low'], lo['fallback_rate']['ci_high'], 4)} | "
            f"{fmt_ci(lo['calls']['mean'], lo['calls']['ci_low'], lo['calls']['ci_high'], 0)} |"
        )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default="results/llm_v2")
    parser.add_argument("--output-md", default=None,
                        help="default: <runs-dir>/summary.md")
    parser.add_argument("--output-json", default=None,
                        help="default: <runs-dir>/summary_ci.json")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    md_path = Path(args.output_md) if args.output_md else runs_dir / "summary.md"
    json_path = Path(args.output_json) if args.output_json else runs_dir / "summary_ci.json"

    by_model = load_runs_by_model(runs_dir)
    if not by_model:
        print(f"(no runs in {runs_dir})")
        return 0

    summaries = {m: aggregate_runs(runs) for m, runs in by_model.items()}
    md = render_markdown(summaries)

    md_path.write_text(md)
    json_path.write_text(json.dumps(summaries, indent=2))

    print(f"→ {md_path}")
    print(f"→ {json_path}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
