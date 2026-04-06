#!/usr/bin/env python3
"""
run_engines.py — Start real AgenticDecoyEngine + OpenClawAgent on host

[ROLE] Runs the REAL OpenClaw agent instances on the host machine.
       Each engine binds a UDP port (14551-14553) and processes MAVLink
       packets forwarded from Docker cc_stub containers.

[DATA FLOW]
    cc_stub (Docker) ──UDP──▶ Host AgenticDecoyEngine ──▶ response back
    AgenticDecoyEngine ──▶ MTDTrigger queue ──▶ MTD consumer
    AgenticDecoyEngine ──▶ CTI event queue ──▶ CTI consumer
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "config" / ".env")

from shared.constants import HONEY_DRONE_COUNT, SITL_PORT_BASE, MAVLINK_PORT_BASE
from shared.constants import WEBCLAW_PORT_BASE, HTTP_PORT_BASE, RTSP_PORT_BASE
from shared.logger import get_logger
from shared.models import HoneyDroneConfig
from honey_drone.agentic_decoy_engine import AgenticDecoyEngine

logger = get_logger("run_engines")

# Queues shared across all engines
mtd_trigger_q = asyncio.Queue()
cti_event_q   = asyncio.Queue()

# Results collection
all_mtd_results: list = []
engine_start_time: float = 0.0


async def run_all_engines() -> None:
    """
    [ROLE] Start N AgenticDecoyEngine instances, each with real OpenClawAgent.

    [DATA FLOW]
        config per drone ──▶ AgenticDecoyEngine.start()
        ──▶ UDP bind :14551-14553
        ──▶ OpenClawAgent 5 async tasks running
    """
    global engine_start_time
    engine_start_time = time.time()

    engines: list[AgenticDecoyEngine] = []
    for i in range(1, HONEY_DRONE_COUNT + 1):
        config = HoneyDroneConfig(
            drone_id=f"honey_{i:02d}",
            index=i,
            sitl_port=SITL_PORT_BASE + i,
            mavlink_port=MAVLINK_PORT_BASE + i,  # 14551, 14552, 14553
            webclaw_port=WEBCLAW_PORT_BASE + i,
            http_port=HTTP_PORT_BASE + i,
            rtsp_port=RTSP_PORT_BASE + i,
        )
        engine = AgenticDecoyEngine(config, mtd_trigger_q, cti_event_q)
        engines.append(engine)

        try:
            await engine.start()
            print(f"  Engine honey_{i:02d}: UDP:{config.mavlink_port} WS:{config.webclaw_port} [REAL OpenClawAgent]")
        except OSError as e:
            print(f"  Engine honey_{i:02d}: FAILED to bind port ({e})")

    # Write flag file indicating real engines are running
    Path("results/.engine_running").write_text("real_openclaw")

    # Start background consumers
    tasks = [
        asyncio.create_task(_mtd_consumer(), name="mtd_consumer"),
        asyncio.create_task(_cti_consumer(), name="cti_consumer"),
        asyncio.create_task(_metrics_reporter(), name="metrics_reporter"),
    ]

    print(f"\n  {len(engines)} real engines running. Ctrl+C to stop.\n")

    try:
        await asyncio.Event().wait()  # block forever
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for e in engines:
            await e.stop()
        _save_results()
        try:
            Path("results/.engine_running").unlink()
        except FileNotFoundError:
            pass


async def _mtd_consumer() -> None:
    """
    [ROLE] Consume MTDTrigger queue — log actions (DRY_RUN on host).

    [DATA FLOW]
        mtd_trigger_q ──▶ log + save result
    """
    while True:
        try:
            trigger = await asyncio.wait_for(mtd_trigger_q.get(), timeout=5.0)
            actions = trigger.recommended_actions
            print(f"  MTD: {trigger.attacker_level.name} urgency={trigger.urgency:.2f} → {actions}")
            all_mtd_results.append({
                "timestamp": time.time(),
                "drone_id": trigger.source_drone_id,
                "level": trigger.attacker_level.name,
                "urgency": round(trigger.urgency, 3),
                "actions": actions,
            })
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(1)  # avoid tight error loop


async def _cti_consumer() -> None:
    """
    [ROLE] Consume CTI event queue — parse and log.

    [DATA FLOW]
        cti_event_q ──▶ AttackEventParser.parse() ──▶ log
    """
    from cti_pipeline.attack_event_parser import AttackEventParser
    parser = AttackEventParser()
    count = 0
    while True:
        try:
            event = await asyncio.wait_for(cti_event_q.get(), timeout=5.0)
            parsed = parser.parse(event)
            count += 1
            if count % 10 == 1:
                print(f"  CTI #{count}: {event.msg_type} → L{parsed.attacker_level.value} TTPs={parsed.ttp_ids}")
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(1)


async def _metrics_reporter() -> None:
    """
    [ROLE] Print live metrics every 10 seconds.

    [DATA FLOW]
        all_mtd_results + queue depths ──▶ stdout
    """
    while True:
        await asyncio.sleep(10)
        elapsed = time.time() - engine_start_time
        print(f"  [{elapsed:.0f}s] MTD triggers: {len(all_mtd_results)} | CTI queue: {cti_event_q.qsize()}")


def _save_results() -> None:
    """
    [ROLE] Save engine results to JSON.

    [DATA FLOW]
        all_mtd_results ──▶ results/metrics/live_mtd_results.json
    """
    Path("results/metrics").mkdir(parents=True, exist_ok=True)
    Path("results/metrics/live_mtd_results.json").write_text(
        json.dumps(all_mtd_results, indent=2, default=str)
    )
    print(f"  Saved {len(all_mtd_results)} MTD results to results/metrics/live_mtd_results.json")


if __name__ == "__main__":
    print("═══════════════════════════════════════════")
    print("  MIRAGE-UAS Real Engine Host")
    print("  OpenClawAgent + EngagementTracker + CTI")
    print("═══════════════════════════════════════════")
    asyncio.run(run_all_engines())
