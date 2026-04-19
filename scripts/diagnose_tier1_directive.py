#!/usr/bin/env python3
"""
diagnose_tier1_directive.py — Tier 1 (GCS) → Tier 2 (drone LLM) integration test

[ROLE]
    Verifies that strategic_directive bias actually reshapes the tactical
    LLM's skill distribution. This is the central claim of the hierarchical
    MIRAGE-UAS architecture — without data here, Tier 1 is just decoration.

[TEST]
    For each phase-direct-ive combination we measure the skill the LLM picks:
      Baseline    : empty directive → LLM picks purely from context
      Bias(skill)  : directive carries strong bias toward a single skill
    Measured over multiple calls per condition; distribution difference is
    the integration signal.

[OUTPUT]
    results/diagnostics/tier1_directive.json
    Prints skill distribution per condition and chi-square-like divergence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "config" / ".env")

# Use the production LLMTacticalAgent (async) to match paper claims exactly.
import asyncio

from honey_drone.llm_agent import LLMTacticalAgent, SKILL_NAMES  # noqa: E402


BASELINE_CONTEXT = {
    "phase_val": 1,  # EXPLOIT
    "max_level": 3,
    "avg_p_real": 0.62,
    "avg_dwell_sec": 60.0,
    "avg_commands": 25,
    "services_touched": 3.0,
    "exploit_attempts": 5,
    "ghost_active": 1,
    "time_in_phase": 30.0,
    "evasion_signals": 0,
}


def make_biased_context(base: dict, bias_skill_idx: int, urgency: float = 0.85) -> dict:
    """Attach a strategic directive biasing toward `bias_skill_idx`."""
    ctx = dict(base)
    bias = {i: 0.05 for i in range(len(SKILL_NAMES))}
    bias[bias_skill_idx] = 0.75  # strong push
    ctx["strategic_directive"] = {
        "action": "escalate" if bias_skill_idx in (3, 4) else "deploy_decoy",
        "skill_bias": bias,
        "urgency": urgency,
        "reason": (
            f"GCS: focus on {SKILL_NAMES[bias_skill_idx]} due to observed "
            "attacker trajectory"
        ),
        "issued_at": time.time(),
        "ttl_sec": 30.0,
    }
    return ctx


async def test_condition(
    agent: LLMTacticalAgent,
    context_fn,
    label: str,
    n_calls: int,
) -> dict:
    counts: Counter = Counter()
    latencies = []
    for i in range(n_calls):
        ctx = context_fn() if callable(context_fn) else context_fn
        idx, name, debug = await agent.select_action(ctx)
        counts[name] += 1
        latencies.append(debug["latency_ms"])
    total = sum(counts.values()) or 1
    dist = {s: counts.get(s, 0) / total for s in SKILL_NAMES}
    return {
        "label": label,
        "n_calls": n_calls,
        "distribution": {k: round(v, 3) for k, v in dist.items()},
        "counts": dict(counts),
        "mean_latency_ms": round(sum(latencies) / max(1, len(latencies)), 1),
    }


def kl_divergence(p: dict, q: dict, eps: float = 1e-6) -> float:
    """KL(p || q) for skill distributions with Laplace smoothing."""
    total = 0.0
    for s in SKILL_NAMES:
        pv = p.get(s, 0.0) + eps
        qv = q.get(s, 0.0) + eps
        total += pv * np.log(pv / qv)
    return float(total)


async def main_async() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", default=os.environ.get("LLM_AGENT_MODEL", "llama3.1:8b"),
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("LLM_AGENT_OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument("--calls", type=int, default=10)
    parser.add_argument("--output", default="results/diagnostics/tier1_directive.json")
    args = parser.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Tier 1 directive bias diagnostic")
    print(f"  model     : {args.model}")
    print(f"  ollama    : {args.ollama_url}")
    print(f"  calls/cond: {args.calls}")
    print(f"  conditions: 1 baseline + 5 biased (one per skill)")

    agent = LLMTacticalAgent(
        drone_id="tier1_diag",
        model_name=args.model,
        ollama_base_url=args.ollama_url,
        timeout_sec=25.0,
        temperature=0.4,
    )

    results: list[dict] = []
    try:
        baseline = await test_condition(
            agent, BASELINE_CONTEXT, "baseline_no_directive", args.calls,
        )
        results.append(baseline)
        print(f"\n[baseline_no_directive]  latency={baseline['mean_latency_ms']}ms")
        for s, p in baseline["distribution"].items():
            bar = "█" * int(p * 40)
            print(f"  {s:<18} {p:.2%}  {bar}")

        for bias_idx in range(len(SKILL_NAMES)):
            label = f"bias_{SKILL_NAMES[bias_idx]}"
            r = await test_condition(
                agent,
                lambda i=bias_idx: make_biased_context(BASELINE_CONTEXT, i),
                label,
                args.calls,
            )
            results.append(r)
            print(f"\n[{label}]  latency={r['mean_latency_ms']}ms")
            for s, p in r["distribution"].items():
                bar = "█" * int(p * 40)
                flag = "  ← biased" if s == SKILL_NAMES[bias_idx] else ""
                print(f"  {s:<18} {p:.2%}  {bar}{flag}")
    finally:
        await agent.close()

    # Measure KL divergence baseline ↔ each biased condition
    comparison = []
    for r in results[1:]:
        kl = kl_divergence(r["distribution"], baseline["distribution"])
        bias_skill = r["label"][len("bias_"):]
        hit_rate = r["distribution"].get(bias_skill, 0.0)
        comparison.append({
            "bias_skill": bias_skill,
            "hit_rate": round(hit_rate, 3),
            "kl_div_from_baseline": round(kl, 3),
        })

    print("\n=== SUMMARY: Tier 1 directive effect on Tier 2 skill distribution ===")
    print(f"  {'Biased toward':<18} {'hit_rate':>10} {'KL from baseline':>20}")
    print(f"  {'-' * 18} {'-' * 10} {'-' * 20}")
    for c in comparison:
        print(f"  {c['bias_skill']:<18} {c['hit_rate']:>10.2%} {c['kl_div_from_baseline']:>20.3f}")

    doc = {
        "model": args.model,
        "conditions": results,
        "comparison": comparison,
        "interpretation": (
            "hit_rate > baseline + 0.2 or KL > 0.5 indicates Tier 1 directive "
            "successfully biases Tier 2 tactical choices (integration signal)."
        ),
    }
    out.write_text(json.dumps(doc, indent=2))
    print(f"\nSaved → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
