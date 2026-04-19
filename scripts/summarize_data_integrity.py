#!/usr/bin/env python3
"""
summarize_data_integrity.py — unified data integrity report across all 3 tiers

[ROLE]
    Compiles the available data into a single markdown report answering
    "do the LLM and OpenClaw characteristics show up well in the data?"

    Sources consumed (tolerant of missing files):
      - results/baseline_matched/*.json       (Random / Greedy / DQN)
      - results/llm_multi_seed/*.json         (current Tier 2 run)
      - results/llm_multi_seed_v1/*.json      (Tier 2 prompt v1 snapshot)
      - results/llm_multi_seed_v2/*.json      (Tier 2 prompt v2 snapshot)
      - results/diagnostics/llm_coherence*.json
      - results/diagnostics/tier1_directive.json
      - results/diagnostics/tier3_lure.json

[OUTPUT]
    docs/DATA_INTEGRITY.md
"""
from __future__ import annotations

import json
import math
from pathlib import Path


def safe_read(p: Path) -> object | None:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def shannon_bits(dist: dict[str, float]) -> float:
    tot = sum(dist.values())
    if tot <= 0:
        return 0.0
    h = 0.0
    for v in dist.values():
        p = v / tot
        if p > 0:
            h -= p * math.log2(p)
    return h


def load_runs(dir_path: Path) -> list[dict]:
    if not dir_path.exists():
        return []
    runs = []
    for f in sorted(dir_path.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        try:
            runs.append(json.loads(f.read_text()))
        except Exception:
            pass
    return runs


def tier2_summary(runs: list[dict], label: str) -> list[str]:
    if not runs:
        return [f"*No runs found for {label}.*"]
    lines = [f"### {label}", "", "| Model | seed | avg_R | p_real | surv | H(skill) | ph-uniq | p95 lat |"]
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in runs:
        dist = r.get("action_distribution", {})
        h = shannon_bits(dist)
        phase_pref = r.get("phase_preference", {})
        uniq = len(set(phase_pref.values())) if phase_pref else 0
        llm = r.get("llm_summary", {})
        p95 = llm.get("p95_latency_ms")
        p95_s = f"{p95:.0f}ms" if p95 is not None else "—"
        lines.append(
            f"| `{r.get('model')}` | {r.get('seed')} | "
            f"{r.get('avg_reward', 0):.2f} | {r.get('avg_p_real', 0):.3f} | "
            f"{(r.get('survival_rate') or 0)*100:.0f}% | {h:.2f} | {uniq}/4 | {p95_s} |"
        )
    return lines


def baseline_summary(runs: list[dict]) -> list[str]:
    if not runs:
        return ["*No baseline_matched runs.*"]
    by_policy: dict[str, list[dict]] = {}
    for r in runs:
        pol = r.get("policy", "?")
        # normalise DQN-seedX → DQN
        if pol.startswith("DQN-seed"):
            pol = "DQN"
        by_policy.setdefault(pol, []).append(r)
    lines = ["| Policy | seeds | avg_R | p_real | survive |"]
    lines.append("|---|---|---|---|---|")
    for pol, pol_runs in sorted(by_policy.items()):
        rs = [r["avg_reward"] for r in pol_runs]
        ps = [r["avg_p_real"] for r in pol_runs]
        ss = [r["survival_rate"] for r in pol_runs]
        lines.append(
            f"| {pol} | {len(pol_runs)} | {mean(rs):.2f} | {mean(ps):.3f} | {mean(ss)*100:.0f}% |"
        )
    return lines


def tier1_summary(doc: dict | None) -> list[str]:
    if doc is None:
        return ["*Tier 1 directive diagnostic not yet run.*"]
    comp = doc.get("comparison", [])
    if not comp:
        return ["*Tier 1 diagnostic JSON has no comparison rows.*"]
    lines = ["### Tier 1 directive → Tier 2 skill bias", "",
             f"Model: `{doc.get('model')}`", "",
             "| biased toward | hit rate | KL from baseline |", "|---|---|---|"]
    for c in comp:
        lines.append(f"| `{c['bias_skill']}` | {c['hit_rate']*100:.0f}% | {c['kl_div_from_baseline']:.2f} |")
    return lines


def tier3_summary(doc: list[dict] | None) -> list[str]:
    if doc is None:
        return ["*Tier 3 lure diagnostic not yet run.*"]
    if not isinstance(doc, list):
        doc = [doc]
    lines = ["| Attacker IP | Breadcrumbs leaked | Records |", "|---|---|---|"]
    for s in doc:
        bc = s.get("unique_breadcrumbs_seen", [])
        n = len(s.get("records", []))
        lines.append(f"| {s.get('attacker_ip')} | {', '.join(bc) or '—'} | {n} |")
    return lines


def main() -> int:
    out = Path("docs/DATA_INTEGRITY.md")
    out.parent.mkdir(parents=True, exist_ok=True)

    baseline = load_runs(Path("results/baseline_matched"))
    tier2_live = load_runs(Path("results/llm_v2"))  # primary source post-V2
    tier2_v1 = load_runs(Path("results/llm_multi_seed_v1"))
    tier2_v2 = load_runs(Path("results/llm_v2"))
    tier1 = safe_read(Path("results/diagnostics/tier1_directive.json"))
    tier3 = safe_read(Path("results/diagnostics/tier3_lure.json"))
    htur = safe_read(Path("results/diagnostics/htur.json"))

    lines = [
        "# MIRAGE-UAS — Data Integrity Report",
        "",
        "Automatically generated by `scripts/summarize_data_integrity.py`.",
        "",
        "## §1 Baseline policies (matched setup — 50 ep × 50 steps × macro=5)",
        "",
    ]
    lines.extend(baseline_summary(baseline))

    lines += [
        "",
        "## §2 Tier 2 — LLM Tactical Policy",
        "",
        "Entropy H(skill) max ≈ 2.32 bits (uniform over 5 skills). "
        "Below 1.5 indicates lock-in on 1–2 skills. `ph-uniq` is the "
        "count of distinct skills appearing as phase_preference winners "
        "across the four attack phases (ideal = 4).",
        "",
    ]
    lines.extend(tier2_summary(tier2_live, "Latest run (`results/llm_multi_seed/`)"))
    lines += [""]
    if tier2_v1:
        lines.extend(tier2_summary(tier2_v1, "Prompt v1 snapshot"))
        lines += [""]
    if tier2_v2:
        lines.extend(tier2_summary(tier2_v2, "Prompt v2 snapshot"))
        lines += [""]

    lines += [
        "",
        "## §3 Tier 1 — GCS Strategic Directive Integration",
        "",
    ]
    lines.extend(tier1_summary(tier1))

    lines += [
        "",
        "## §4 Tier 3 — Attacker-Facing OpenClaw SDK Lure",
        "",
    ]
    lines.extend(tier3_summary(tier3))

    lines += [
        "",
        "## §4b Honey-Token Uptake (HTUR / CPR / FSR)",
        "",
        "Primary observed-behaviour metric that does NOT depend on the "
        "simulated DeceptionEnv. HTUR = unique honey-tokens reused by an "
        "attacker / unique honey-tokens issued to that attacker.",
        "",
    ]
    if htur is None:
        lines.append("*HTUR diagnostic not yet run. Expected at `results/diagnostics/htur.json`.*")
    else:
        agg = htur.get("aggregate_stats", {})
        per = htur.get("per_attacker_htur", {})
        lines += [
            f"- Aggregate HTUR: **{agg.get('htur', 0):.3f}** "
            f"({agg.get('reused_unique', 0)} reused / "
            f"{agg.get('issued_unique', 0)} issued)",
            f"- FSR (fake-service return rate): {agg.get('fsr', 0):.3f}",
            f"- Auth-endpoint reuse events: {agg.get('auth_reuses', 0)}",
            "",
            "| Attacker | issued | reused | HTUR |",
            "|---|---|---|---|",
        ]
        for ip, v in per.items():
            lines.append(
                f"| {ip} | {v.get('issued_unique', 0)} | "
                f"{v.get('reused_unique', 0)} | {v.get('htur', 0):.2f} |"
            )

    lines += [
        "",
        "## §5 Coherence checklist",
        "",
    ]
    verdicts: list[tuple[str, str, str]] = []  # (check, status, note)

    # Tier 2 checks
    if tier2_live:
        worst_uniq = min(
            len(set(r.get("phase_preference", {}).values()))
            for r in tier2_live
        )
        mean_uniq = sum(
            len(set(r.get("phase_preference", {}).values()))
            for r in tier2_live
        ) / len(tier2_live)
        worst_H = min(
            shannon_bits(r.get("action_distribution", {}))
            for r in tier2_live
        )
        mean_H = sum(
            shannon_bits(r.get("action_distribution", {}))
            for r in tier2_live
        ) / len(tier2_live)
        verdicts.append((
            "Tier 2 phase-discrimination (mean ph-uniq ≥ 2.5)",
            "PASS" if mean_uniq >= 2.5 else "FAIL",
            f"mean ph-uniq = {mean_uniq:.2f}/4, worst = {worst_uniq}/4",
        ))
        verdicts.append((
            "Tier 2 skill-diversity (H ≥ 1.5 bits)",
            "PASS" if worst_H >= 1.5 else "FAIL",
            f"worst-case H = {worst_H:.2f}, mean = {mean_H:.2f}",
        ))
        # Add chi-square p-value check — strong evidence for phase-aware skill choice
        worst_chi_p = max(
            (r.get("policy_metrics", {}).get("chi_square_pvalue", 1.0))
            for r in tier2_live
        )
        verdicts.append((
            "Tier 2 phase-skill independence REJECTED (worst χ² p < 0.05)",
            "PASS" if worst_chi_p < 0.05 else "FAIL",
            f"worst χ² p = {worst_chi_p:.4f}",
        ))
        worst_p95 = max(
            (r.get("llm_summary", {}).get("p95_latency_ms") or 0.0)
            for r in tier2_live
        )
        verdicts.append((
            "Tier 2 latency (p95 < 5000ms)",
            "PASS" if worst_p95 < 5000 else "FAIL",
            f"worst p95 = {worst_p95:.0f}ms",
        ))
        worst_fb = max(
            (r.get("llm_summary", {}).get("fallback_rate") or 0.0)
            for r in tier2_live
        )
        verdicts.append((
            "Tier 2 fallback rate (< 1%)",
            "PASS" if worst_fb < 0.01 else "FAIL",
            f"worst fallback = {worst_fb:.2%}",
        ))

    # Tier 1 checks
    if tier1:
        comp = tier1.get("comparison", [])
        if comp:
            hits = [c["hit_rate"] for c in comp]
            kls = [c["kl_div_from_baseline"] for c in comp]
            verdicts.append((
                "Tier 1 directive hit-rate (any bias ≥ 40%)",
                "PASS" if hits and max(hits) >= 0.4 else "FAIL",
                f"max hit_rate = {max(hits)*100:.0f}%" if hits else "no data",
            ))
            verdicts.append((
                "Tier 1 directive KL divergence (max ≥ 0.5)",
                "PASS" if kls and max(kls) >= 0.5 else "FAIL",
                f"max KL = {max(kls):.2f}" if kls else "no data",
            ))

    # Tier 3 checks
    if tier3:
        sessions = tier3 if isinstance(tier3, list) else [tier3]
        min_bc = min(len(s.get("unique_breadcrumbs_seen", [])) for s in sessions) if sessions else 0
        verdicts.append((
            "Tier 3 breadcrumbs leaked per session (≥ 3 types)",
            "PASS" if min_bc >= 3 else "FAIL",
            f"worst session leaked {min_bc} types",
        ))

    # HTUR checks
    if htur:
        agg = htur.get("aggregate_stats", {})
        aggregate_htur = float(agg.get("htur", 0))
        verdicts.append((
            "HTUR aggregate (≥ 0.50 indicates effective lure)",
            "PASS" if aggregate_htur >= 0.5 else "FAIL",
            f"HTUR = {aggregate_htur:.2f}",
        ))

    if verdicts:
        lines.append("| Check | Status | Detail |")
        lines.append("|---|---|---|")
        for name, st, detail in verdicts:
            mark = "✅" if st == "PASS" else ("❌" if st == "FAIL" else "—")
            lines.append(f"| {name} | {mark} {st} | {detail} |")
    else:
        lines.append("*No coherence data yet.*")

    out.write_text("\n".join(lines))
    print(f"→ {out}")
    print("\n" + "\n".join(lines[:80]))
    print("\n... (truncated in console; full report in " + str(out) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
