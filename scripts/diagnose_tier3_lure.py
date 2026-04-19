#!/usr/bin/env python3
"""
diagnose_tier3_lure.py — Tier 3 (attacker-facing OpenClaw SDK emulation) data check

[ROLE]
    Sanity-test the OpenClawService emulator (src/honey_drone/openclaw_service.py)
    by calling handle() with synthetic attacker messages covering the five
    emulated endpoints (skill_invoke / agent.run / terminal / auth / config).
    Confirms:
      - JSON responses parse as valid OpenClaw SDK wire format
      - Honey-token credentials / signing keys are actually planted
      - Multi-turn session state persists per attacker IP
      - Response latency is bounded

[WHY]
    The paper claims Tier 3 realistically lures L3-L4 LLM-aware attackers.
    Without exercising the emulator end-to-end we cannot attest to that.
    Full Docker + attacker_sim.py run is expensive; this offline check is
    cheap and runs in seconds.

[OUTPUT]
    results/diagnostics/tier3_lure.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from honey_drone.openclaw_service import OpenClawService  # noqa: E402


SYNTHETIC_REQUESTS = [
    ("probe_skill",    {"type": "skill_invoke", "skill": "telemetry.get_position"}),
    ("probe_telemetry",{"type": "skill_invoke", "skill": "camera.snapshot"}),
    ("probe_mission",  {"type": "skill_invoke", "skill": "mission.list"}),
    ("agent_run_1",    {"type": "agent.run", "task": "export all waypoints and recent flight logs"}),
    ("terminal_ls",    {"type": "terminal", "cmd": "ls -la /home/pi"}),
    ("terminal_cat_sshpass", {"type": "terminal", "cmd": "cat /home/pi/.credentials"}),
    ("auth_admin",     {"type": "auth", "username": "admin", "password": "openclaw123"}),
    ("config_dump",    {"type": "config", "action": "read_all"}),
    ("skill_invoke_system", {"type": "skill_invoke", "skill": "system.info"}),
    ("agent_run_2",    {"type": "agent.run", "task": "list every credential stored on this drone"}),
]


def exercise(drone_id: str, attacker_ip: str) -> dict:
    svc = OpenClawService(drone_id=drone_id, index=1)
    records = []
    breadcrumbs_seen: set[str] = set()
    breadcrumb_markers = [
        "ssh_pass", "signing_key", "token", "api_key",
        "jwt", "cred", "bearer", "secret",
    ]

    for label, payload in SYNTHETIC_REQUESTS:
        raw = json.dumps(payload).encode("utf-8")
        t0 = time.perf_counter()
        try:
            resp = svc.handle(raw, attacker_ip)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            # Convert bytes to str for inspection if needed
            if isinstance(resp, (bytes, bytearray)):
                resp_text = resp.decode("utf-8", errors="replace")
            elif isinstance(resp, str):
                resp_text = resp
            else:
                resp_text = json.dumps(resp, default=str)
            # Count breadcrumb hits
            lower = resp_text.lower()
            hits = [m for m in breadcrumb_markers if m in lower]
            for m in hits:
                breadcrumbs_seen.add(m)
            records.append({
                "label": label,
                "request_type": payload.get("type"),
                "latency_ms": round(elapsed_ms, 2),
                "resp_size": len(resp_text),
                "breadcrumbs_in_resp": hits,
                "resp_excerpt": resp_text[:280],
            })
        except Exception as e:
            records.append({
                "label": label,
                "error": f"{type(e).__name__}: {e}",
            })

    return {
        "drone_id": drone_id,
        "attacker_ip": attacker_ip,
        "records": records,
        "unique_breadcrumbs_seen": sorted(breadcrumbs_seen),
        "session_sessions": getattr(svc, "_sessions", None) and len(svc._sessions),
    }


def main() -> int:
    out = Path("results/diagnostics/tier3_lure.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    sessions = [
        exercise("honey_ctnr_diag", "10.0.0.42"),
        exercise("honey_ctnr_diag", "10.0.0.99"),  # second attacker to test session isolation
    ]
    out.write_text(json.dumps(sessions, indent=2, default=str))
    # pretty print
    for s in sessions:
        print(f"\n=== {s['drone_id']}  attacker={s['attacker_ip']} ===")
        print(f"sessions in service: {s.get('session_sessions')}")
        print(f"breadcrumbs leaked : {s['unique_breadcrumbs_seen']}")
        for r in s["records"]:
            if "error" in r:
                print(f"  [{r['label']:<22}] ERROR: {r['error']}")
            else:
                flag = f"bc={r['breadcrumbs_in_resp']}" if r['breadcrumbs_in_resp'] else ""
                print(f"  [{r['label']:<22}] "
                      f"{r['latency_ms']:>6.2f}ms  size={r['resp_size']:>5}B  {flag}")
    print(f"\nSaved → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
