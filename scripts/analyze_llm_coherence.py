#!/usr/bin/env python3
"""
analyze_llm_coherence.py — Data integrity / coherence report for LLM runs

[ROLE]
    After `run_llm_experiments.py` produces per-seed JSONs, run this to
    check whether the LLM is actually exhibiting the properties we claim:
      (a) skill diversity (entropy of decision distribution)
      (b) phase-aware choice (distinct mode per phase)
      (c) reasoning quality (non-degenerate reason strings)
      (d) latency profile fits proactive-loop budget

[OUTPUT]
    results/diagnostics/llm_coherence.md  (human-readable per-model report)
    results/diagnostics/llm_coherence.json (machine-readable for table merge)

[USAGE]
    python scripts/analyze_llm_coherence.py
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def shannon_entropy_bits(dist: dict[str, float]) -> float:
    """Entropy in bits over a distribution dict (values summing to ~1 or pct)."""
    total = sum(dist.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for v in dist.values():
        p = v / total
        if p > 0:
            h -= p * math.log2(p)
    return h


def load_runs(dir_path: Path) -> list[dict]:
    runs = []
    for f in sorted(dir_path.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        try:
            runs.append(json.loads(f.read_text()))
        except Exception:
            pass
    return runs


def analyze_run(run: dict) -> dict:
    exec_dist = run.get("action_distribution", {})
    llm_sum = run.get("llm_summary", {})
    llm_dec_pct = llm_sum.get("skill_distribution_llm_decisions_pct", {})
    # Fallback to execution distribution for older runs w/o decision dist
    used_dist = llm_dec_pct or exec_dist

    exec_entropy = shannon_entropy_bits(exec_dist)
    dec_entropy = shannon_entropy_bits(used_dist)

    phase_pref = run.get("phase_preference", {})
    unique_phase_skills = len(set(phase_pref.values())) if phase_pref else 0

    sample_reasons = llm_sum.get("reason_samples", [])
    n_samples = len(sample_reasons)
    unique_reasons = len({r.get("reason", "") for r in sample_reasons})
    reason_diversity_ratio = unique_reasons / max(1, n_samples)

    lat = {
        "mean_ms": llm_sum.get("mean_latency_ms"),
        "p50_ms": llm_sum.get("p50_latency_ms"),
        "p95_ms": llm_sum.get("p95_latency_ms"),
    }

    # Coherence checks
    checks = {
        "skill_diversity_entropy_bits_ge_1_5": exec_entropy >= 1.5,
        "phase_discrimination_ge_3_unique": unique_phase_skills >= 3,
        "reason_diversity_ge_0_5": reason_diversity_ratio >= 0.5 if sample_reasons else None,
        "p95_latency_under_5000ms": (lat["p95_ms"] is not None and lat["p95_ms"] < 5000),
        "zero_fallback": llm_sum.get("fallback_rate", 1.0) == 0.0,
    }
    passed = sum(1 for v in checks.values() if v is True)
    total = sum(1 for v in checks.values() if v is not None)
    return {
        "model": run.get("model"),
        "seed": run.get("seed"),
        "avg_reward": run.get("avg_reward"),
        "avg_p_real": run.get("avg_p_real"),
        "survival_rate": run.get("survival_rate"),
        "exec_distribution": exec_dist,
        "llm_decision_distribution_pct": llm_dec_pct,
        "entropy_exec_bits": round(exec_entropy, 3),
        "entropy_llm_decisions_bits": round(dec_entropy, 3),
        "phase_preference": phase_pref,
        "unique_phase_skills": unique_phase_skills,
        "n_reason_samples": n_samples,
        "unique_reasons": unique_reasons,
        "reason_diversity_ratio": round(reason_diversity_ratio, 3),
        "latency_ms": lat,
        "checks": checks,
        "pass_rate": f"{passed}/{total}",
    }


def render_markdown(report: list[dict]) -> str:
    lines = [
        "# LLM Data Coherence Report",
        "",
        "Generated from `results/llm_multi_seed/*.json`.",
        "",
        "## Per-Run Summary",
        "",
        "| Model | seed | avg_R | p_real | surv | H_exec | H_dec | Ph-skills | Latency p95 | Pass |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report:
        lat = r["latency_ms"]
        p95 = lat.get("p95_ms")
        p95_s = f"{p95:.0f}ms" if p95 is not None else "—"
        lines.append(
            f"| `{r['model']}` | {r['seed']} | "
            f"{r['avg_reward']:.2f} | {r['avg_p_real']:.3f} | "
            f"{(r['survival_rate'] or 0)*100:.0f}% | "
            f"{r['entropy_exec_bits']:.2f} | {r['entropy_llm_decisions_bits']:.2f} | "
            f"{r['unique_phase_skills']}/4 | {p95_s} | {r['pass_rate']} |"
        )

    lines += [
        "",
        "## Coherence Checks",
        "",
        "- **H_exec / H_dec**: Shannon entropy (bits) of skill distribution.",
        "  Max = log2(5) ≈ 2.32 for uniform. Below 1.5 indicates lock-in.",
        "- **Ph-skills**: count of distinct skills appearing as phase_preference "
        "winner across 4 phases. Ideal = 4 (one per phase).",
        "- **Pass**: checks passed / total non-null checks.",
        "",
        "## Per-Run Distributions",
        "",
    ]

    for r in report:
        lines += [
            f"### {r['model']} (seed {r['seed']})",
            "",
            "**LLM decision distribution (%):**",
            "",
        ]
        llm_dec = r["llm_decision_distribution_pct"]
        if llm_dec:
            for skill, pct in sorted(llm_dec.items(), key=lambda kv: -kv[1]):
                bar = "█" * int(pct / 2)
                lines.append(f"- `{skill:<18}` {pct:>5.1f}%  {bar}")
        else:
            lines.append("- (no LLM-decision distribution — older run schema)")
        lines += ["", "**Phase preference:**", ""]
        for ph, skill in r["phase_preference"].items():
            lines.append(f"- `{ph}` → `{skill}`")
        lines += ["", "**Coherence checks:**", ""]
        for k, v in r["checks"].items():
            mark = "✓" if v is True else ("✗" if v is False else "—")
            lines.append(f"- [{mark}] {k}")
        lines += [""]

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-dir", default="results/llm_multi_seed")
    parser.add_argument(
        "--output-md", default="results/diagnostics/llm_coherence.md",
    )
    parser.add_argument(
        "--output-json", default="results/diagnostics/llm_coherence.json",
    )
    args = parser.parse_args()

    runs = load_runs(Path(args.llm_dir))
    if not runs:
        print(f"(no runs found in {args.llm_dir})")
        return 0
    report = [analyze_run(r) for r in runs]
    md = render_markdown(report)

    out_md = Path(args.output_md)
    out_json = Path(args.output_json)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md)
    out_json.write_text(json.dumps(report, indent=2))
    print(f"→ {out_md}")
    print(f"→ {out_json}")
    # Also print to stdout for quick glance
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
