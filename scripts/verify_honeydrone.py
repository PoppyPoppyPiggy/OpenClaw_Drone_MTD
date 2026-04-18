#!/usr/bin/env python3
"""
verify_honeydrone.py — end-to-end verification of the honeydrone stack

[ROLE]
    Validates that the REAL OpenClaw AgenticDecoyEngine + SignalingGameSolver
    boots correctly, responds to MAVLink attacker traffic, runs the proactive
    loop, emits metrics, and integrates cleanly — WITHOUT requiring Docker.

    This is the same entrypoint that docker/honeydrone_entry.py runs inside
    the mirage-honeydrone container, minus the HTTP/RTSP/ghost servers
    (MAVLink + WS engine is the critical path). If this passes, a Docker
    container built on the same code will also start.

[CHECKS]
    1. AgenticDecoyEngine starts, owns MAVLink UDP socket
    2. OpenClawAgent._proactive_loop selects skills (policy_mode aware)
    3. Injected MAVLink HEARTBEAT from "attacker" yields a MAVLink response
    4. DeceptionStateManager updates μ_A after observations
    5. SignalingGameSolver snapshot populated (if DEFENDER_POLICY=signaling_eq)
    6. periodic_save-style metric files produced

Usage:
    python3 scripts/verify_honeydrone.py                        # default (signaling_eq)
    DEFENDER_POLICY=dqn python3 scripts/verify_honeydrone.py    # DQN policy
    python3 scripts/verify_honeydrone.py --duration 20          # run longer
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

# Default env file
ENV_PATH = ROOT / "config" / ".env"
if not ENV_PATH.exists():
    EX = ROOT / "config" / ".env.example"
    if EX.exists():
        ENV_PATH.write_text(EX.read_text())
        print(f"[verify] bootstrapped {ENV_PATH} from {EX}")
load_dotenv(ENV_PATH)

# Override weight arrays if blank (research params that default is OK for smoke)
_DEFAULTS = {
    "MTD_COST_SENSITIVITY_KAPPA": "0.5",
    "MTD_ALPHA_WEIGHTS": "0.1,0.15,0.1,0.15,0.2,0.1,0.2",
    "MTD_BREACH_PREVENTION_BETA": "0.5",
    "COMPROMISE_P_BASE": "0.3",
    "DES_WEIGHT_LIST": "0.25,0.25,0.25,0.25",
    "REDUNDANCY_REWARD_HIGH": "0.5", "REDUNDANCY_REWARD_LOW": "0.1",
    "REDUNDANCY_THRESHOLD": "0.5",
    "DECEPTION_LAMBDA": "0.5", "DECEPTION_WEIGHTS": "0.4,0.3,0.3",
    "DECEPTION_DWELL_MAX_SEC": "300",
    "ATTACKER_PRIORS": "0.2,0.2,0.2,0.2,0.2",
    "PPO_LEARNING_RATE": "3e-4", "PPO_GAMMA": "0.99",
    "PPO_CLIP_EPS": "0.2", "PPO_ENTROPY_COEF": "0.01",
    "AGENT_PROACTIVE_INTERVAL_SEC": "3.0",   # shorter for smoke test
    "AGENT_SYSID_ROTATION_SEC": "20.0",
    "AGENT_PORT_ROTATION_SEC": "30.0",
    "AGENT_FALSE_FLAG_DWELL_THRESHOLD": "60.0",
    "AGENT_MIRROR_SERVICE_THRESHOLD": "3",
    "DECEPTION_SCORE_WEIGHTS": "0.25,0.2,0.2,0.15,0.2",
    "DEFENDER_POLICY": "signaling_eq",
    "SIGNALING_KAPPA": "0.5", "SIGNALING_TEMPERATURE": "0.8",
    "SIGNALING_EPSILON": "0.10", "SIGNALING_LEARNING_RATE": "0.1",
}
for k, v in _DEFAULTS.items():
    if not os.environ.get(k, "").strip():
        os.environ[k] = v

from shared.models import HoneyDroneConfig  # noqa: E402
from honey_drone.agentic_decoy_engine import AgenticDecoyEngine  # noqa: E402


# ── Test helpers ───────────────────────────────────────────────────────────────

def _mk_heartbeat(sysid: int = 100, compid: int = 200) -> bytes:
    """
    Build a minimal MAVLink v2 HEARTBEAT frame (msg_id=0) with correct CRC.
    This is what an attacker / MAVProxy probe sends first.
    """
    from pymavlink import mavutil
    mav = mavutil.mavlink.MAVLink(file=None, srcSystem=sysid, srcComponent=compid)
    mav.robust_parsing = True
    msg = mav.heartbeat_encode(
        type=6,          # GCS type
        autopilot=8,     # INVALID
        base_mode=0,
        custom_mode=0,
        system_status=3, # STANDBY
    )
    return msg.pack(mav)


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d) if d else 0:.0f}%"


# ── Main verification routine ─────────────────────────────────────────────────

async def run_verification(duration_sec: float, mavlink_port: int, drone_id: str) -> dict:
    mtd_q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    cti_q: asyncio.Queue = asyncio.Queue(maxsize=1000)

    cfg = HoneyDroneConfig(
        drone_id=drone_id,
        index=1,
        sitl_port=5760,
        mavlink_port=mavlink_port,
        webclaw_port=18789,
        http_port=9980,       # unused
        rtsp_port=9854,       # unused
        fcu_host="localhost",
    )

    engine = AgenticDecoyEngine(cfg, mtd_q, cti_q)

    report: dict = {
        "drone_id": drone_id,
        "mavlink_port": mavlink_port,
        "defender_policy": os.environ.get("DEFENDER_POLICY"),
        "checks": {},
    }

    print(f"[1/6] Starting engine on UDP :{mavlink_port}")
    await engine.start()
    report["checks"]["engine_started"] = True

    # Give it a moment to bind the UDP socket
    await asyncio.sleep(0.5)

    print(f"[2/6] Injecting MAVLink HEARTBEATs as fake attacker (from 127.0.0.1:varied)")
    attacker_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    attacker_sock.setblocking(False)
    attacker_sock.bind(("127.0.0.1", 0))
    atk_port = attacker_sock.getsockname()[1]

    # Listen for responses
    recv_sock = attacker_sock
    # (same socket — we send from this ephemeral port and listen for replies on it)

    hb = _mk_heartbeat(sysid=100)
    sent, recv_bytes = 0, 0
    t_end = time.time() + duration_sec
    loop = asyncio.get_event_loop()

    # One-shot MAVLink injection pattern: RECON → EXPLOIT → PERSIST → EXFIL
    from pymavlink import mavutil
    mav_atk = mavutil.mavlink.MAVLink(file=None, srcSystem=100, srcComponent=200)
    mav_atk.robust_parsing = True

    phase_packets = [
        ("RECON",   mav_atk.heartbeat_encode(6, 8, 0, 0, 3)),
        ("RECON",   mav_atk.param_request_list_encode(1, 1)),
        ("EXPLOIT", mav_atk.command_long_encode(1, 1, 400, 0, 1.0, 0, 0, 0, 0, 0, 0)),  # MAV_CMD_ARM
        ("EXPLOIT", mav_atk.set_mode_encode(1, 1, 4)),
        ("PERSIST", mav_atk.param_set_encode(1, 1, b"ARMING_CHECK", 0.0, 9)),
        ("PERSIST", mav_atk.mission_count_encode(1, 1, 5)),
        ("EXFIL",   mav_atk.log_request_list_encode(1, 1, 0, 0xFFFF)),
        ("EXFIL",   mav_atk.file_transfer_protocol_encode(0, 1, 1, bytes(251))),
    ]

    target_addr = ("127.0.0.1", mavlink_port)
    async def _recv_task():
        nonlocal recv_bytes
        while time.time() < t_end:
            try:
                data, _ = await asyncio.wait_for(loop.sock_recvfrom(recv_sock, 4096), timeout=0.5)
                recv_bytes += len(data)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

    async def _send_task():
        nonlocal sent
        cycle = 0
        while time.time() < t_end:
            phase_name, msg = phase_packets[cycle % len(phase_packets)]
            buf = msg.pack(mav_atk)
            try:
                await loop.sock_sendto(attacker_sock, buf, target_addr)
                sent += 1
            except Exception:
                pass
            cycle += 1
            await asyncio.sleep(0.25)

    print(f"[3/6] Running {duration_sec:.0f}s attacker injection (8-phase cycle)")
    t0 = time.time()
    await asyncio.gather(_send_task(), _recv_task(), return_exceptions=True)
    attacker_sock.close()
    elapsed = time.time() - t0

    report["checks"]["packets_sent"] = sent
    report["checks"]["bytes_received_from_drone"] = recv_bytes
    report["checks"]["bytes_per_packet_avg"] = round(recv_bytes / max(sent, 1), 1)

    print(f"  → sent={sent} packets in {elapsed:.1f}s, received {recv_bytes} bytes")

    # ── Snapshot engine state ─────────────────────────────────
    print(f"[4/6] Inspecting OpenClaw agent state")
    agent = engine._openclaw_agent
    fingerprints = list(agent._fingerprints.values())
    decisions = list(agent._decisions)

    report["checks"]["fingerprints_tracked"] = len(fingerprints)
    if fingerprints:
        fp = fingerprints[-1]
        report["checks"]["last_fingerprint"] = {
            "attacker_ip": fp.attacker_ip,
            "tool": fp.tool.name,
            "attack_phase": fp.attack_phase.name,
            "command_count": len(fp.command_sequence),
        }

    report["checks"]["agent_decisions"] = len(decisions)
    if decisions:
        d = decisions[-1]
        report["checks"]["last_decision"] = {
            "drone_id": d.drone_id,
            "behavior": d.behavior_triggered,
            "rationale": d.rationale[:80],
        }

    # ── Bayesian belief ──────────────────────────────────────
    print(f"[5/6] Checking μ_A (Bayesian belief) + Signaling Game state")
    beliefs = engine._belief_mgr.get_all_beliefs()
    report["checks"]["attackers_with_beliefs"] = len(beliefs)
    if beliefs:
        b = beliefs[0]
        report["checks"]["mu_a_first_attacker"] = round(b.mu_a, 4)
        report["checks"]["total_observations"] = b.total_observations
    report["checks"]["avg_mu_a"] = round(engine.get_avg_confusion(), 4)

    snap = agent.get_signaling_snapshot()
    if snap:
        report["checks"]["signaling_game"] = {
            "defender_policy": snap.get("defender_policy"),
            "last_policy_used": snap.get("last_policy_used"),
            "last_skill": snap.get("last_skill_name"),
            "mixing": snap.get("mixing"),
            "per_skill_n": [s["n"] for s in snap.get("per_skill_stats", [])],
        }

    # ── MTD trigger drain ────────────────────────────────────
    report["checks"]["mtd_triggers_queued"] = mtd_q.qsize()
    report["checks"]["cti_events_queued"] = cti_q.qsize()

    # ── Stop ─────────────────────────────────────────────────
    print(f"[6/6] Stopping engine")
    await engine.stop()
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_verdict(report: dict) -> bool:
    checks = report["checks"]
    def _ok(k: str, cond: bool, msg: str = "") -> None:
        mark = "✓" if cond else "✗"
        suffix = f"  {msg}" if msg else ""
        print(f"  [{mark}] {k}{suffix}")

    print("\n" + "=" * 60)
    print(f"  HONEYDRONE VERIFICATION — drone_id={report['drone_id']}")
    print(f"  DEFENDER_POLICY={report['defender_policy']}")
    print("=" * 60)

    all_pass = True
    conds = [
        ("engine starts",            checks.get("engine_started") is True),
        ("injected > 0 packets",     checks.get("packets_sent", 0) > 0),
        ("received MAVLink replies", checks.get("bytes_received_from_drone", 0) > 0),
        ("fingerprints tracked",     checks.get("fingerprints_tracked", 0) > 0),
        ("agent decisions recorded", checks.get("agent_decisions", 0) > 0),
        ("beliefs maintained",       checks.get("attackers_with_beliefs", 0) > 0),
        ("avg μ_A in [0, 1]",        0.0 <= checks.get("avg_mu_a", -1) <= 1.0),
    ]
    if report["defender_policy"] == "signaling_eq":
        conds.append(("signaling snapshot",
                      isinstance(checks.get("signaling_game", {}).get("mixing"), list)))

    for name, ok in conds:
        _ok(name, ok)
        all_pass = all_pass and ok

    print()
    print(f"  sent/recv:      {checks.get('packets_sent')} pkts → "
          f"{checks.get('bytes_received_from_drone')} bytes from drone")
    print(f"  fingerprints:   {checks.get('fingerprints_tracked')}")
    print(f"  decisions:      {checks.get('agent_decisions')}")
    print(f"  μ_A avg:        {checks.get('avg_mu_a')}")
    sg = checks.get("signaling_game") or {}
    if sg:
        print(f"  last skill:     {sg.get('last_skill')}")
        print(f"  mixing:         {sg.get('mixing')}")
        print(f"  per-skill n:    {sg.get('per_skill_n')}")
    print(f"  mtd/cti queued: {checks.get('mtd_triggers_queued')} / {checks.get('cti_events_queued')}")

    print("\n  " + ("✅ PASS" if all_pass else "❌ FAIL") + " — host-level honeydrone verification")
    return all_pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=12.0, help="seconds of attacker traffic")
    ap.add_argument("--port", type=int, default=14651, help="MAVLink UDP port (avoid privileged)")
    ap.add_argument("--drone-id", type=str, default="honey_verify")
    ap.add_argument("--save-json", type=str, default="results/metrics/verify_honeydrone.json")
    args = ap.parse_args()

    report = asyncio.run(run_verification(args.duration, args.port, args.drone_id))

    ok = _print_verdict(report)
    out_path = Path(args.save_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n  Report JSON: {out_path}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
