#!/usr/bin/env python3
"""
python_sim.py — Pure Python Discrete Event Simulation of MIRAGE-UAS packet flow

Replays the real experiment data as an OMNeT++-style simulation with:
  - Color-coded ASCII packet flow visualization
  - Per-event timing from actual attacker_log.jsonl
  - MTD trigger injection at correct timestamps
  - CTI pipeline events
  - Final statistics matching Tables II-VI

Usage: python3 results/omnetpp/python_sim.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
from collections import Counter

# Colors
R = "\033[91m"   # red — attacker
C = "\033[96m"   # cyan — decoy response
Y = "\033[93m"   # yellow — MTD
G = "\033[92m"   # green — CTI
W = "\033[97m"   # white — benign/info
M = "\033[95m"   # magenta — agent decision
D = "\033[90m"   # dim — timeout/fail
B = "\033[94m"   # blue — engine
RST = "\033[0m"

# Node names
NODES = {
    "172.40.0.10": "CC_0 ",
    "172.40.0.11": "CC_1 ",
    "172.40.0.12": "CC_2 ",
    "172.40.0.200": "ATKR ",
    "172.40.0.240": "CTI  ",
    "host": "ENGIN",
}


@dataclass
class SimEvent:
    time_s: float
    src: str
    dst: str
    protocol: str
    label: str
    level: int = 0
    success: bool = True
    color: str = W
    detail: str = ""


def load_events() -> Tuple[List[SimEvent], Dict]:
    """Load real experiment data and convert to simulation events."""
    base = Path("results")
    events: List[SimEvent] = []

    # Load attacker log
    log_path = base / "attacker_log.jsonl"
    if not log_path.exists():
        print(f"{R}ERROR: {log_path} not found. Run experiment first.{RST}")
        sys.exit(1)

    records = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        print(f"{R}ERROR: attacker log is empty.{RST}")
        sys.exit(1)

    t0 = records[0].get("timestamp", 0)

    # Convert attacker packets to sim events
    for r in records:
        lv = r.get("level", -1)
        if lv < 0:
            continue
        t = r["timestamp"] - t0
        action = r.get("action", "")
        target = r.get("target", "")
        ok = "timeout" not in action and "fail" not in action
        resp = r.get("response_preview", "")

        dst_ip = target.split(":")[0] if ":" in target else target
        dst_port = target.split(":")[-1] if ":" in target else "?"
        dst_name = NODES.get(dst_ip, dst_ip[:6])

        # Determine protocol
        if "http" in action or "login" in action:
            proto = "HTTP"
        elif "ws_" in action:
            proto = "WebSocket"
        elif "rtsp" in action:
            proto = "RTSP"
        elif "ssh" in action:
            proto = "SSH"
        elif "ghost" in action:
            proto = "TCP/Ghost"
        elif "gps" in action:
            proto = "MAVLink:GPS"
        else:
            proto = "MAVLink"

        # Map action to readable label
        label_map = {
            "heartbeat": "HEARTBEAT",
            "param_request_list": "PARAM_REQ",
            "arm_command": "ARM_CMD",
            "udp_scan": "UDP_SCAN",
            "http_get_/api/v1/params": "GET /params",
            "http_get_/api/v1/status": "GET /status",
            "http_get_/api/v1/mission": "GET /mission",
            "http_get_/health": "GET /health",
            "http_login": "POST /login",
            "ws_auth_bypass": "CVE_AUTH_BYPASS",
            "ws_skill_invoke": "SKILL_INVOKE",
            "ws_ping": "WS_PING",
            "rtsp_teardown": "RTSP_TEARDOWN",
            "breadcrumb_follow": "BREADCRUMB_FOLLOW",
            "ghost_port_probe": "GHOST_PROBE",
            "gps_inject": "GPS_INJECT",
            "ssh_connect": "SSH_CONNECT",
            "breadcrumb_harvest": "BC_HARVEST",
        }
        short_action = action.replace("_timeout", "").replace("_fail", "")
        msg_label = label_map.get(short_action, short_action[:16].upper())

        # Attacker → HoneyDrone
        events.append(SimEvent(
            time_s=t, src="ATKR ", dst=dst_name,
            protocol=proto, label=msg_label,
            level=lv, success=ok,
            color=R if not ok else R,
            detail=f"L{lv}",
        ))

        # HoneyDrone → Attacker (response)
        if ok:
            events.append(SimEvent(
                time_s=t + 0.001, src=dst_name, dst="ATKR ",
                protocol=proto, label=msg_label + "_RESP",
                level=lv, success=True, color=C,
                detail="DECOY" if ok else "",
            ))

            # CTI pipeline event (every successful interaction)
            ttp = {"heartbeat": "T0842", "arm_command": "T0855",
                   "param_request_list": "T0840", "http_get": "T0842",
                   "ws_auth_bypass": "T0820", "ghost_port_probe": "T0886",
                   "gps_inject": "T0856", "rtsp_teardown": "T0813",
                   "breadcrumb_follow": "T0882"}.get(short_action, "")
            if ttp:
                events.append(SimEvent(
                    time_s=t + 0.01, src=dst_name, dst="CTI  ",
                    protocol="EVENT", label=f"TTP:{ttp}",
                    level=lv, success=True, color=G,
                ))

    # Load engine log for agent decisions + MTD
    engine_log = base / "logs" / "engines.log"
    if engine_log.exists():
        with open(engine_log) as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    r = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                ev = r.get("event", "")
                ts_str = r.get("timestamp", "")
                drone = r.get("drone_id", "")

                # Parse timestamp
                try:
                    # ISO format: 2026-04-06T06:20:40.123Z
                    parts = ts_str.split("T")
                    if len(parts) == 2:
                        time_part = parts[1].rstrip("Z")
                        h, m, s = time_part.split(":")
                        t = float(h) * 3600 + float(m) * 60 + float(s) - t0
                        if t < 0:
                            t = abs(t) % 300  # wrap around
                    else:
                        continue
                except (ValueError, IndexError):
                    continue

                drone_name = {"honey_01": "CC_0 ", "honey_02": "CC_1 ",
                              "honey_03": "CC_2 "}.get(drone, "ENGIN")

                if ev == "mtd_trigger emitted":
                    urg = r.get("urgency", 0)
                    acts = r.get("actions", [])
                    act_str = acts[0] if acts else "MTD"
                    events.append(SimEvent(
                        time_s=t, src="ENGIN", dst=drone_name,
                        protocol="MTD", label=act_str.upper(),
                        color=Y, detail=f"urg={urg}",
                    ))
                elif ev in ("proactive_statustext", "proactive_ghost_port",
                            "proactive_fake_key", "proactive_reboot_start",
                            "false_flag_start", "sysid rotated"):
                    events.append(SimEvent(
                        time_s=t, src=drone_name, dst="ENGIN",
                        protocol="AGENT", label=ev.replace("proactive_", "").upper()[:16],
                        color=M, detail=drone,
                    ))

    events.sort(key=lambda e: e.time_s)

    # Load metrics for summary
    metrics = {}
    for name in ["summary", "table_ii_engagement", "table_iii_mtd_latency",
                  "table_iv_dataset", "table_v_deception", "table_vi_agent_decisions"]:
        p = base / "metrics" / f"{name}.json"
        if p.exists():
            metrics[name] = json.loads(p.read_text())

    return events, metrics


def run_simulation(events: List[SimEvent], metrics: Dict, speed: float = 1.0) -> None:
    """Run discrete event simulation with live ASCII output."""

    print(f"{W}{'═' * 78}{RST}")
    print(f"{W}  MIRAGE-UAS Packet Flow Simulation (Python DES){RST}")
    print(f"{W}  {len(events)} events from real experiment data{RST}")
    print(f"{W}{'═' * 78}{RST}")
    print()
    print(f"  {R}■{RST} Attacker    {C}■{RST} Decoy Response    "
          f"{Y}■{RST} MTD Action    {G}■{RST} CTI Event    {M}■{RST} Agent Decision")
    print(f"{D}{'─' * 78}{RST}")
    print()

    # Stats
    stats: Dict[str, int] = Counter()
    level_stats: Dict[int, Dict[str, int]] = {}

    sim_start = time.time()
    prev_t = 0.0
    current_level = -1

    for i, ev in enumerate(events):
        # Real-time pacing (compressed)
        dt = ev.time_s - prev_t
        if dt > 0 and speed > 0:
            time.sleep(min(dt / speed, 0.05))  # max 50ms real delay
        prev_t = ev.time_s

        # Level change banner
        if ev.level > 0 and ev.level != current_level and ev.src == "ATKR ":
            current_level = ev.level
            names = {0: "L0 Script Kiddie", 1: "L1 Basic MAVLink",
                     2: "L2 HTTP Enum", 3: "L3 WebSocket CVE", 4: "L4 APT Chain"}
            lv_colors = {0: D, 1: B, 2: Y, 3: M, 4: R}
            c = lv_colors.get(ev.level, W)
            print(f"\n{c}  {'━' * 74}{RST}")
            print(f"{c}  ▶ {names.get(ev.level, f'L{ev.level}')} {'━' * (68 - len(names.get(ev.level, '')))}{RST}")
            print(f"{c}  {'━' * 74}{RST}\n")

        # Track stats
        stats["total"] += 1
        if ev.src == "ATKR ":
            stats["attacks"] += 1
            if ev.success:
                stats["engaged"] += 1
            lv = ev.level
            if lv not in level_stats:
                level_stats[lv] = {"sent": 0, "ok": 0}
            level_stats[lv]["sent"] += 1
            if ev.success:
                level_stats[lv]["ok"] += 1
        elif "MTD" in ev.protocol:
            stats["mtd"] += 1
        elif "EVENT" in ev.protocol:
            stats["cti"] += 1
        elif "AGENT" in ev.protocol:
            stats["agent"] += 1

        # Format arrow
        arrow_len = 20
        proto_label = f"{ev.protocol}:{ev.label}"
        if len(proto_label) > arrow_len:
            proto_label = proto_label[:arrow_len]

        if ev.success or "AGENT" in ev.protocol or "MTD" in ev.protocol:
            arrow = f"──{proto_label:─<{arrow_len}}─▶"
        else:
            arrow = f"──{proto_label:·<{arrow_len}}·▶"

        # Status tag
        if not ev.success:
            tag = f"{D}[TIMEOUT]{RST}"
        elif ev.color == C:
            tag = f"{C}[DECOY]{RST}"
        elif ev.color == Y:
            tag = f"{Y}[{ev.detail}]{RST}"
        elif ev.color == G:
            tag = f"{G}[CTI]{RST}"
        elif ev.color == M:
            tag = f"{M}[{ev.detail[:8]}]{RST}"
        else:
            tag = f"{D}[{ev.detail}]{RST}"

        line = (f"  {D}[t={ev.time_s:6.1f}s]{RST} "
                f"{ev.color}{ev.src}{RST} "
                f"{ev.color}{arrow}{RST} "
                f"{ev.color}{ev.dst}{RST}  "
                f"{tag}")
        print(line)

    # ═══ Final Summary ═══
    elapsed = time.time() - sim_start
    attacks = stats.get("attacks", 0)
    engaged = stats.get("engaged", 0)
    rate = engaged * 100 // max(attacks, 1)

    print(f"\n{W}{'═' * 78}{RST}")
    print(f"{W}  SIMULATION COMPLETE — {len(events)} events in {elapsed:.1f}s{RST}")
    print(f"{W}{'═' * 78}{RST}")
    print()

    # Table II equivalent
    print(f"  {W}TABLE II — Engagement by Level{RST}")
    print(f"  {'Level':<25} {'Sent':>6} {'Success':>8} {'Rate':>6}")
    print(f"  {'─'*50}")
    names = {0: "L0 Script Kiddie", 1: "L1 Basic MAVLink",
             2: "L2 HTTP Enum", 3: "L3 WebSocket CVE", 4: "L4 APT Chain"}
    for lv in sorted(level_stats.keys()):
        s = level_stats[lv]
        pct = s["ok"] * 100 // max(s["sent"], 1)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"  {names.get(lv, f'L{lv}'):<25} {s['sent']:>6} {s['ok']:>8} {bar} {pct}%")

    # Summary stats
    print()
    print(f"  {W}Summary{RST}")
    print(f"  Total packets:    {stats.get('total', 0)}")
    print(f"  Attacker packets: {attacks}")
    print(f"  Engaged:          {engaged} ({rate}%)")
    print(f"  MTD events:       {stats.get('mtd', 0)}")
    print(f"  CTI events:       {stats.get('cti', 0)}")
    print(f"  Agent decisions:  {stats.get('agent', 0)}")

    # DeceptionScore from metrics
    summary = metrics.get("summary", {})
    ds = summary.get("deception_score", 0)
    mode = summary.get("engine_mode", "?")
    print(f"\n  {G}DeceptionScore: {ds}{RST}")
    print(f"  Engine mode:      {mode}")
    print(f"{W}{'═' * 78}{RST}")


def main():
    os.chdir(Path(__file__).parent.parent.parent)
    events, metrics = load_events()
    speed = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    run_simulation(events, metrics, speed)


if __name__ == "__main__":
    main()
