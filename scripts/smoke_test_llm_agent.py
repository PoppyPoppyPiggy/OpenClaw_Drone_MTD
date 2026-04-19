#!/usr/bin/env python3
"""
smoke_test_llm_agent.py — Quick end-to-end sanity check for Tier 2 LLM agent.

Runs LLMTacticalAgent.select_action() against N models for K calls each
with synthetic contexts covering all 4 attack phases. Measures:
  - Reachability of Windows-hosted Ollama from WSL
  - JSON-parse reliability (fallback rate)
  - Mean latency per model
  - Skill distribution sanity (not always picking same skill)

Usage:
    python scripts/smoke_test_llm_agent.py
    python scripts/smoke_test_llm_agent.py --models llama3.1:8b,qwen2.5:14b
    python scripts/smoke_test_llm_agent.py --calls 5
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "config" / ".env")

from honey_drone.llm_agent import LLMTacticalAgent, SKILL_NAMES  # noqa: E402


SYNTHETIC_CONTEXTS = [
    # (phase 0 RECON, low engagement)
    {"phase_val": 0, "max_level": 0, "avg_p_real": 0.78, "avg_dwell_sec": 5.0,
     "avg_commands": 3, "services_touched": 1.0, "exploit_attempts": 0,
     "ghost_active": 0, "time_in_phase": 5.0, "evasion_signals": 0},
    # (phase 1 EXPLOIT, metasploit)
    {"phase_val": 1, "max_level": 3, "avg_p_real": 0.62, "avg_dwell_sec": 40.0,
     "avg_commands": 18, "services_touched": 3.0, "exploit_attempts": 5,
     "ghost_active": 1, "time_in_phase": 22.0, "evasion_signals": 0},
    # (phase 2 PERSIST, PARAM_SET abuse)
    {"phase_val": 2, "max_level": 4, "avg_p_real": 0.55, "avg_dwell_sec": 120.0,
     "avg_commands": 55, "services_touched": 4.0, "exploit_attempts": 8,
     "ghost_active": 2, "time_in_phase": 60.0, "evasion_signals": 1},
    # (phase 3 EXFIL, log pull + file transfer)
    {"phase_val": 3, "max_level": 4, "avg_p_real": 0.48, "avg_dwell_sec": 200.0,
     "avg_commands": 110, "services_touched": 5.0, "exploit_attempts": 12,
     "ghost_active": 3, "time_in_phase": 80.0, "evasion_signals": 2},
    # (phase 1 EXPLOIT, attacker starting to suspect)
    {"phase_val": 1, "max_level": 2, "avg_p_real": 0.35, "avg_dwell_sec": 90.0,
     "avg_commands": 30, "services_touched": 2.0, "exploit_attempts": 4,
     "ghost_active": 1, "time_in_phase": 45.0, "evasion_signals": 3},
]


async def test_model(model: str, calls: int, base_url: str) -> dict:
    print(f"\n{'=' * 60}")
    print(f"Testing {model}")
    print(f"{'=' * 60}")
    agent = LLMTacticalAgent(
        drone_id=f"smoke_{model.replace(':', '_')}",
        model_name=model,
        ollama_base_url=base_url,
        timeout_sec=30.0,
    )
    latencies: list[float] = []
    skills: Counter = Counter()
    fallbacks = 0
    errors = 0
    reasons: list[str] = []
    t_start = time.perf_counter()

    try:
        for i in range(calls):
            ctx = SYNTHETIC_CONTEXTS[i % len(SYNTHETIC_CONTEXTS)]
            try:
                idx, name, debug = await agent.select_action(ctx)
                latencies.append(debug["latency_ms"])
                skills[name] += 1
                if debug.get("error_kind"):
                    fallbacks += 1
                reasons.append(debug.get("reasoning", "")[:80])
                phase = ["RECON", "EXPLOIT", "PERSIST", "EXFIL"][ctx["phase_val"]]
                print(f"  [{i + 1}/{calls}] phase={phase:<7} → {name:<17} "
                      f"({debug['latency_ms']:>6.0f} ms) {reasons[-1][:60]}")
            except Exception as e:
                errors += 1
                print(f"  [{i + 1}/{calls}] ERROR: {type(e).__name__}: {e}")
    finally:
        await agent.close()

    total_t = time.perf_counter() - t_start
    return {
        "model": model,
        "calls_attempted": calls,
        "calls_ok": calls - errors,
        "errors": errors,
        "fallbacks": fallbacks,
        "mean_latency_ms": sum(latencies) / max(1, len(latencies)),
        "max_latency_ms": max(latencies) if latencies else 0.0,
        "skill_dist": dict(skills),
        "total_wall_sec": round(total_t, 2),
    }


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Tier 2 LLM agent smoke test")
    parser.add_argument(
        "--models",
        default="llama3.1:8b,qwen2.5:14b,gemma2:9b",
        help="comma-separated Ollama model tags",
    )
    parser.add_argument(
        "--calls", type=int, default=5,
        help="decisions to make per model",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("LLM_AGENT_OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"Ollama URL : {args.ollama_url}")
    print(f"Models     : {models}")
    print(f"Calls each : {args.calls}")

    results: list[dict] = []
    for model in models:
        r = await test_model(model, args.calls, args.ollama_url)
        results.append(r)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        ok = r["calls_ok"] == r["calls_attempted"] and r["fallbacks"] == 0
        flag = "PASS" if ok else "CHECK"
        print(f"  [{flag}] {r['model']:<20}  "
              f"ok={r['calls_ok']}/{r['calls_attempted']}  "
              f"fallback={r['fallbacks']}  "
              f"err={r['errors']}  "
              f"mean={r['mean_latency_ms']:>6.0f}ms  "
              f"max={r['max_latency_ms']:>6.0f}ms  "
              f"skills={r['skill_dist']}")
    any_fail = any(r["errors"] > 0 or r["fallbacks"] > 0 for r in results)
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
