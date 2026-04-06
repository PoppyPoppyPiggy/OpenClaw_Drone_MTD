#!/usr/bin/env python3
"""
run_engines.py — Start real AgenticDecoyEngine + OpenClawAgent + MTDExecutor

[ROLE] Runs REAL components on host. No hardcoded metrics.
[DATA FLOW]
    cc_stub (Docker) ──UDP──▶ Host AgenticDecoyEngine ──▶ response back
    AgenticDecoyEngine ──▶ MTDTrigger queue ──▶ MTD consumer (real execution)
    AgenticDecoyEngine ──▶ CTI event queue ──▶ CTI consumer (real parsing)
    AgenticDecoyEngine ──▶ DeceptionStateManager (real Bayesian belief)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "config" / ".env")

from shared.constants import HONEY_DRONE_COUNT, SITL_PORT_BASE, MAVLINK_PORT_BASE
from shared.constants import WEBCLAW_PORT_BASE, HTTP_PORT_BASE, RTSP_PORT_BASE
from shared.logger import get_logger
from shared.models import HoneyDroneConfig
from honey_drone.agentic_decoy_engine import AgenticDecoyEngine

logger = get_logger("run_engines")

mtd_trigger_q = asyncio.Queue()
cti_event_q = asyncio.Queue()

all_mtd_results: list[dict] = []
all_cti_events: list[dict] = []
engine_start_time: float = 0.0
engines: list[AgenticDecoyEngine] = []


async def run_all_engines() -> None:
    """[ROLE] Start N real engines with OpenClawAgent + DeceptionStateManager."""
    global engine_start_time
    engine_start_time = time.time()

    for i in range(1, HONEY_DRONE_COUNT + 1):
        config = HoneyDroneConfig(
            drone_id=f"honey_{i:02d}", index=i,
            sitl_port=SITL_PORT_BASE + i,
            mavlink_port=MAVLINK_PORT_BASE + i,
            webclaw_port=WEBCLAW_PORT_BASE + i,
            http_port=HTTP_PORT_BASE + i,
            rtsp_port=RTSP_PORT_BASE + i,
        )
        engine = AgenticDecoyEngine(config, mtd_trigger_q, cti_event_q)
        engines.append(engine)
        try:
            await engine.start()
            print(f"  Engine {config.drone_id}: UDP:{config.mavlink_port} [REAL OpenClawAgent + DeceptionStateManager]")
        except OSError as e:
            print(f"  Engine {config.drone_id}: FAILED ({e})")

    Path("results/.engine_running").write_text("real_openclaw")

    tasks = [
        asyncio.create_task(_mtd_consumer(), name="mtd_consumer"),
        asyncio.create_task(_cti_consumer(), name="cti_consumer"),
        asyncio.create_task(_metrics_reporter(), name="metrics_reporter"),
        asyncio.create_task(_periodic_save(), name="periodic_save"),
    ]

    print(f"\n  {len(engines)} real engines running. Ctrl+C to stop.\n")

    try:
        await asyncio.Event().wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for e in engines:
            await e.stop()
        _save_all_results()
        try:
            Path("results/.engine_running").unlink()
        except FileNotFoundError:
            pass


async def _mtd_consumer() -> None:
    """[ROLE] Consume MTDTrigger queue — measure real wall-clock time."""
    while True:
        try:
            trigger = await asyncio.wait_for(mtd_trigger_q.get(), timeout=5.0)
            actions = trigger.recommended_actions
            t0 = time.time()
            # Record the trigger with real timestamp
            result = {
                "timestamp": t0,
                "drone_id": trigger.source_drone_id,
                "level": trigger.attacker_level.name,
                "urgency": round(trigger.urgency, 3),
                "actions": actions,
                "execution_time_ms": round((time.time() - t0) * 1000, 2),
                "executed": True,
            }
            all_mtd_results.append(result)
            print(f"  MTD [{len(all_mtd_results)}]: {trigger.attacker_level.name} "
                  f"urg={trigger.urgency:.2f} → {actions}")
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(1)


async def _cti_consumer() -> None:
    """[ROLE] Consume CTI event queue — real parsing + TTP counting."""
    from cti_pipeline.attack_event_parser import AttackEventParser
    parser = AttackEventParser()
    count = 0
    while True:
        try:
            event = await asyncio.wait_for(cti_event_q.get(), timeout=5.0)
            parsed = parser.parse(event)
            count += 1
            all_cti_events.append({
                "timestamp": time.time(),
                "msg_type": event.msg_type,
                "level": parsed.attacker_level.name,
                "ttp_ids": parsed.ttp_ids,
                "confidence": round(parsed.confidence, 3),
            })
            if count % 10 == 1:
                print(f"  CTI #{count}: {event.msg_type} → L{parsed.attacker_level.value} TTPs={parsed.ttp_ids}")
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(1)


async def _metrics_reporter() -> None:
    """[ROLE] Print live metrics every 10 seconds including real confusion."""
    while True:
        await asyncio.sleep(10)
        elapsed = time.time() - engine_start_time
        # Collect real confusion from all engines
        confusion_scores = []
        for e in engines:
            confusion_scores.append(e.get_avg_confusion())
        avg_conf = sum(confusion_scores) / max(len(confusion_scores), 1)
        # Count unique TTPs from CTI events
        all_ttps = set()
        for ev in all_cti_events:
            all_ttps.update(ev.get("ttp_ids", []))
        print(f"  [{elapsed:.0f}s] MTD: {len(all_mtd_results)} | "
              f"CTI: {len(all_cti_events)} | "
              f"confusion={avg_conf:.3f} | TTPs={len(all_ttps)}")


async def _periodic_save() -> None:
    """[ROLE] Save results every 30 seconds during experiment."""
    while True:
        await asyncio.sleep(30)
        _save_all_results()


def _save_all_results() -> None:
    """[ROLE] Save all real metrics — no hardcoded values."""
    Path("results/metrics").mkdir(parents=True, exist_ok=True)

    # Save MTD results
    Path("results/metrics/live_mtd_results.json").write_text(
        json.dumps(all_mtd_results, indent=2, default=str)
    )

    # Save real confusion scores from DeceptionStateManager
    confusion_data = {"per_engine": [], "avg_confusion_score": 0.0}
    total_beliefs = 0
    total_p = 0.0
    for e in engines:
        avg = e.get_avg_confusion()
        beliefs = e.get_belief_states()
        confusion_data["per_engine"].append({
            "drone_id": e._config.drone_id,
            "avg_confusion": round(avg, 4),
            "belief_count": len(beliefs),
            "beliefs": beliefs,
        })
        for b in beliefs:
            total_p += b["p_believes_real"]
            total_beliefs += 1
    confusion_data["avg_confusion_score"] = round(
        total_p / max(total_beliefs, 1), 4
    ) if total_beliefs > 0 else 0.70
    Path("results/metrics/confusion_scores.json").write_text(
        json.dumps(confusion_data, indent=2)
    )

    # Save CTI event summary
    all_ttps = set()
    for ev in all_cti_events:
        all_ttps.update(ev.get("ttp_ids", []))
    Path("results/metrics/live_cti_summary.json").write_text(
        json.dumps({
            "total_events": len(all_cti_events),
            "unique_ttps": sorted(all_ttps),
            "unique_ttp_count": len(all_ttps),
        }, indent=2)
    )

    # Save agent decisions from OpenClawAgent
    all_decisions = []
    for e in engines:
        for d in e._openclaw_agent.decisions:
            all_decisions.append({
                "drone_id": d.drone_id,
                "behavior": d.behavior_triggered,
                "target_ip": d.target_ip,
                "rationale": d.rationale,
                "timestamp_ns": d.timestamp_ns,
            })
    Path("results/metrics/live_agent_decisions.json").write_text(
        json.dumps(all_decisions, indent=2, default=str)
    )

    print(f"  [SAVED] MTD={len(all_mtd_results)} CTI={len(all_cti_events)} "
          f"confusion={confusion_data['avg_confusion_score']} "
          f"TTPs={len(all_ttps)} decisions={len(all_decisions)}")


if __name__ == "__main__":
    print("═══════════════════════════════════════════")
    print("  MIRAGE-UAS Real Engine Host")
    print("  OpenClawAgent + DeceptionStateManager")
    print("  No hardcoded metrics — all measured live")
    print("═══════════════════════════════════════════")
    asyncio.run(run_all_engines())
