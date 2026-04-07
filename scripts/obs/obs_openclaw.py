#!/usr/bin/env python3
"""
obs_openclaw.py — Real-time OpenClaw Agent Monitor

Listens on 4 UDP ports simultaneously:
  19999  state snapshots   (1/sec per drone from AgenticDecoyEngine)
  19998  decision events   (from OpenClawAgent)
  19997  internal diffs    (from OpenClawAgent)
  19996  packet-level      (from MavlinkResponseGenerator)

Renders a live terminal UI using ANSI escape codes.
"""
from __future__ import annotations

import json
import os
import select
import socket
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ── ANSI colors ──────────────────────────────────────────────────────────────
RST      = "\033[0m"
BOLD     = "\033[1m"
DIM      = "\033[2m"
WHITE    = "\033[97m"
RED      = "\033[91m"
GREEN    = "\033[92m"
YELLOW   = "\033[93m"
BLUE     = "\033[94m"
MAGENTA  = "\033[95m"
CYAN     = "\033[96m"
ORANGE   = "\033[38;5;208m"
BG_BLUE  = "\033[44m"
BG_RED   = "\033[41m"
BG_GREEN = "\033[42m"

# phase -> color
PHASE_COLOR = {
    "recon":   WHITE,
    "exploit": YELLOW,
    "persist": ORANGE,
    "exfil":   RED,
    "IDLE":    DIM,
}

STALE_TIMEOUT = 3.0  # seconds before showing "WAITING..."


@dataclass
class DroneState:
    drone_id: str = ""
    timestamp: float = 0.0
    attacker_ip: str = ""
    current_phase: str = "IDLE"
    attacker_level: str = "---"
    belief_score: float = 0.0
    active_behaviors: List[str] = field(default_factory=list)
    last_action: str = ""
    last_action_reason: str = ""
    dwell_seconds: float = 0.0
    commands_received: int = 0
    mtd_triggers_sent: int = 0
    confusion_delta: float = 0.0
    session_id: str = ""


@dataclass
class DiffEntry:
    timestamp: float = 0.0
    drone_id: str = ""
    behavior: str = ""
    changes: List[dict] = field(default_factory=list)


@dataclass
class DecisionEntry:
    timestamp: float = 0.0
    drone_id: str = ""
    event_type: str = ""
    summary: str = ""
    detail: str = ""
    color: str = WHITE


@dataclass
class PacketEntry:
    timestamp: float = 0.0
    drone_id: str = ""
    request_type: str = ""
    src_system: int = 0
    deception: List[str] = field(default_factory=list)
    jitter_m: float = 0.0


class AgentMonitor:
    def __init__(self) -> None:
        self.drones: Dict[str, DroneState] = {}
        self.decisions: deque = deque(maxlen=50)
        self.diffs: deque = deque(maxlen=30)
        self.packets: deque = deque(maxlen=20)
        self.last_reasoning: Dict[str, dict] = {}
        self.total_sessions: int = 0
        self.total_mtd: int = 0
        self.total_breaches: int = 0
        self.sockets: List[socket.socket] = []
        self.start_time: float = time.time()

    def setup_sockets(self) -> None:
        ports = [19999, 19998, 19997, 19996]
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                sock.setblocking(False)
                self.sockets.append(sock)
            except OSError as e:
                print(f"{YELLOW}Warning: port {port} unavailable: {e}{RST}")
                try:
                    sock.close()
                except Exception:
                    pass

        if not self.sockets:
            print(f"{RED}ERROR: No UDP ports available (19996-19999).{RST}")
            print(f"Another obs_openclaw.py may be running. Kill it first.")
            sys.exit(1)

    def poll(self) -> None:
        ready, _, _ = select.select(self.sockets, [], [], 0.05)
        for sock in ready:
            try:
                data, _ = sock.recvfrom(65536)
                port = sock.getsockname()[1]
                msg = json.loads(data.decode("utf-8", errors="ignore"))
                self._handle_message(port, msg)
            except Exception:
                pass

    def _handle_message(self, port: int, msg: dict) -> None:
        if port == 19999:
            self._handle_state(msg)
        elif port == 19998:
            self._handle_decision(msg)
        elif port == 19997:
            self._handle_diff(msg)
        elif port == 19996:
            self._handle_packet(msg)

    def _handle_state(self, msg: dict) -> None:
        drone_id = msg.get("drone_id", "?")
        state = self.drones.setdefault(drone_id, DroneState())
        state.drone_id = drone_id
        state.timestamp = msg.get("timestamp", time.time())
        state.attacker_ip = msg.get("attacker_ip", "")
        state.current_phase = msg.get("current_phase", "IDLE")
        state.attacker_level = msg.get("attacker_level", "---")
        state.belief_score = msg.get("belief_score", 0.0)
        state.active_behaviors = msg.get("active_behaviors", [])
        state.last_action = msg.get("last_action", "")
        state.last_action_reason = msg.get("last_action_reason", "")
        state.dwell_seconds = msg.get("dwell_seconds", 0.0)
        state.commands_received = msg.get("commands_received", 0)
        state.mtd_triggers_sent = msg.get("mtd_triggers_sent", 0)
        state.confusion_delta = msg.get("confusion_delta", 0.0)
        state.session_id = msg.get("session_id", "")

    def _handle_decision(self, msg: dict) -> None:
        event_type = msg.get("event", "")
        drone_id = msg.get("drone_id", "?")
        ts = msg.get("timestamp", time.time())

        if event_type == "agent_decision":
            behavior = msg.get("behavior", "?")
            decision = msg.get("decision", "")
            confidence = msg.get("confidence", 0.0)
            entry = DecisionEntry(
                timestamp=ts,
                drone_id=drone_id,
                event_type="decision",
                summary=f"BEHAVIOR {behavior}",
                detail=f"{decision[:40]}  conf:{confidence:.2f}",
                color=CYAN,
            )
            self.decisions.appendleft(entry)
            # store for reasoning panel
            self.last_reasoning[drone_id] = msg

        elif event_type == "phase_transition":
            from_p = msg.get("from_phase", "?")
            to_p = msg.get("to_phase", "?")
            trigger = msg.get("trigger_command", "?")
            entry = DecisionEntry(
                timestamp=ts,
                drone_id=drone_id,
                event_type="phase",
                summary=f"PHASE {from_p}\u2192{to_p}",
                detail=f"trigger: {trigger}",
                color=YELLOW,
            )
            self.decisions.appendleft(entry)

        elif event_type == "level_reclassified":
            from_l = msg.get("from_level", "?")
            to_l = msg.get("to_level", "?")
            evidence = msg.get("evidence", [])
            entry = DecisionEntry(
                timestamp=ts,
                drone_id=drone_id,
                event_type="level",
                summary=f"LEVEL {from_l}\u2192{to_l}",
                detail=", ".join(evidence[:3]),
                color=MAGENTA,
            )
            self.decisions.appendleft(entry)

    def _handle_diff(self, msg: dict) -> None:
        entry = DiffEntry(
            timestamp=msg.get("timestamp", time.time()),
            drone_id=msg.get("drone_id", "?"),
            behavior=msg.get("behavior", "?"),
            changes=msg.get("changes", []),
        )
        self.diffs.appendleft(entry)

    def _handle_packet(self, msg: dict) -> None:
        entry = PacketEntry(
            timestamp=msg.get("timestamp", time.time()),
            drone_id=msg.get("drone_id", "?"),
            request_type=msg.get("request_type", "?"),
            src_system=msg.get("key_fields", {}).get("srcSystem", 0),
            deception=msg.get("deception_applied", []),
            jitter_m=msg.get("vs_real_drone", {}).get("position_jitter_m", 0.0),
        )
        self.packets.appendleft(entry)

    # ── Rendering ────────────────────────────────────────────────────────────

    def render(self) -> str:
        now = time.time()
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
        lines: List[str] = []
        W = 100  # total width

        # ── Header ───────────────────────────────────────────────────────
        header = f"  MIRAGE-UAS  OpenClaw Agent Monitor  [live]  {ts_str} "
        lines.append(f"{BG_BLUE}{WHITE}{BOLD}{header:<{W}}{RST}")
        lines.append(self._hline(W))

        # ── Drone columns + Decision feed ────────────────────────────────
        drone_ids = sorted(self.drones.keys()) if self.drones else []
        # Ensure we show 3 slots
        for i in range(3):
            did = f"drone_{i}" if i < len(drone_ids) else None
            if did and did not in self.drones:
                did = drone_ids[i] if i < len(drone_ids) else None

        slots: List[Optional[DroneState]] = []
        shown_ids = set()
        for i in range(3):
            candidate = f"drone_{i}"
            # try numbered IDs first, then sorted
            if candidate in self.drones:
                slots.append(self.drones[candidate])
                shown_ids.add(candidate)
            elif i < len(drone_ids) and drone_ids[i] not in shown_ids:
                slots.append(self.drones[drone_ids[i]])
                shown_ids.add(drone_ids[i])
            else:
                slots.append(None)

        # Build drone column lines (left side, ~60 chars)
        dcol_lines = self._render_drone_columns(slots, now)
        # Build decision feed lines (right side, ~38 chars)
        dfeed_lines = self._render_decision_feed()

        # Merge side by side
        max_rows = max(len(dcol_lines), len(dfeed_lines), 12)
        for r in range(max_rows):
            left = dcol_lines[r] if r < len(dcol_lines) else " " * 60
            right = dfeed_lines[r] if r < len(dfeed_lines) else ""
            lines.append(f"{left} {DIM}\u2502{RST} {right}")

        lines.append(self._hline(W))

        # ── Internal state diff + Packet panel ──────────────────────────
        diff_lines = self._render_diff_panel()
        pkt_lines = self._render_packet_panel()

        max_rows2 = max(len(diff_lines), len(pkt_lines), 8)
        for r in range(max_rows2):
            left = diff_lines[r] if r < len(diff_lines) else " " * 60
            right = pkt_lines[r] if r < len(pkt_lines) else ""
            lines.append(f"{left} {DIM}\u2502{RST} {right}")

        lines.append(self._hline(W))

        # ── Reasoning + Stats ────────────────────────────────────────────
        reason_lines = self._render_reasoning()
        stats_lines = self._render_stats()

        max_rows3 = max(len(reason_lines), len(stats_lines), 4)
        for r in range(max_rows3):
            left = reason_lines[r] if r < len(reason_lines) else " " * 60
            right = stats_lines[r] if r < len(stats_lines) else ""
            lines.append(f"{left} {DIM}\u2502{RST} {right}")

        lines.append(self._hline(W))

        # ── Deception surface ────────────────────────────────────────────
        surface_lines = self._render_deception_surface()
        for line in surface_lines:
            lines.append(line)

        lines.append("")
        lines.append(f"{DIM}  UDP: 19996(pkt) 19997(diff) 19998(decision) 19999(state)  |  Ctrl+C to exit{RST}")

        return "\n".join(lines)

    def _hline(self, w: int) -> str:
        return f"{DIM}{'─' * w}{RST}"

    def _render_drone_columns(self, slots: List[Optional[DroneState]], now: float) -> List[str]:
        lines: List[str] = []
        col_w = 19

        # Header row
        row = ""
        for i, s in enumerate(slots):
            if s is None:
                name = f"DRONE_{i}"
                row += f"  {DIM}{name:<{col_w}}{RST}"
            else:
                name = s.drone_id.upper().replace("_", " ")
                row += f"  {BOLD}{name:<{col_w}}{RST}"
        lines.append(row)

        # Phase/Level
        row = ""
        for i, s in enumerate(slots):
            if s is None:
                row += f"  {DIM}{'IDLE':<{col_w}}{RST}"
            elif (now - s.timestamp) > STALE_TIMEOUT and s.timestamp > 0:
                row += f"  {YELLOW}{'WAITING...':<{col_w}}{RST}"
            else:
                phase = s.current_phase.upper()
                lvl = s.attacker_level
                pc = PHASE_COLOR.get(s.current_phase, WHITE)
                text = f"{phase}/{lvl}"
                row += f"  {pc}{text:<{col_w}}{RST}"
        lines.append(row)

        # Belief
        row = ""
        for i, s in enumerate(slots):
            if s is None:
                row += f"  {DIM}{'Belief: ---':<{col_w}}{RST}"
            else:
                row += f"  Belief:{GREEN}{s.belief_score:.2f}{RST}{'':>{col_w - 12}}"
        lines.append(row)

        # Dwell
        row = ""
        for i, s in enumerate(slots):
            if s is None:
                row += f"  {DIM}{'Dwell:   0s':<{col_w}}{RST}"
            else:
                row += f"  Dwell:{s.dwell_seconds:>5.0f}s{'':>{col_w - 13}}"
        lines.append(row)

        # Commands
        row = ""
        for i, s in enumerate(slots):
            if s is None:
                row += f"  {DIM}{'Cmds:    0':<{col_w}}{RST}"
            else:
                row += f"  Cmds: {s.commands_received:>4}{'':>{col_w - 11}}"
        lines.append(row)

        # MTD triggers
        row = ""
        for i, s in enumerate(slots):
            if s is None:
                row += f"  {DIM}{'MTD:     0':<{col_w}}{RST}"
            else:
                row += f"  MTD:  {RED}{s.mtd_triggers_sent:>4}{RST}{'':>{col_w - 11}}"
        lines.append(row)

        lines.append("")

        # Last action
        row = ""
        for i, s in enumerate(slots):
            if s is None:
                row += f"  {DIM}{'':<{col_w}}{RST}"
            else:
                row += f"  {DIM}LAST ACTION{RST}{'':>{col_w - 11}}"
        lines.append(row)

        row = ""
        for i, s in enumerate(slots):
            if s is None:
                row += f"  {'':<{col_w}}"
            else:
                act = (s.last_action or "---")[:col_w]
                row += f"  {CYAN}{act:<{col_w}}{RST}"
        lines.append(row)

        row = ""
        for i, s in enumerate(slots):
            if s is None:
                row += f"  {'':<{col_w}}"
            else:
                reason = (s.last_action_reason or "")[:col_w]
                row += f"  {DIM}{reason:<{col_w}}{RST}"
        lines.append(row)

        return lines

    def _render_decision_feed(self) -> List[str]:
        lines: List[str] = []
        lines.append(f"{BOLD}  DECISION FEED{RST}")
        lines.append("")

        for entry in list(self.decisions)[:10]:
            ts_short = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            lines.append(f"  {DIM}{ts_short}{RST} {BOLD}{entry.drone_id}{RST}")
            lines.append(f"  {entry.color}{entry.summary}{RST}")
            if entry.detail:
                lines.append(f"  {DIM}{entry.detail[:36]}{RST}")
            lines.append("")

        if not self.decisions:
            lines.append(f"  {DIM}No decisions yet...{RST}")
            lines.append(f"  {DIM}Waiting for engine{RST}")

        return lines

    def _render_diff_panel(self) -> List[str]:
        lines: List[str] = []
        lines.append(f"  {BOLD}INTERNAL STATE CHANGES (live diff){RST}")
        lines.append("")

        for entry in list(self.diffs)[:5]:
            ts_short = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            lines.append(f"  {DIM}{ts_short}{RST} {BOLD}{entry.drone_id}{RST} {CYAN}{entry.behavior}{RST}")
            for ch in entry.changes[:3]:
                var = ch.get("variable", "?")
                before = ch.get("before", "?")
                after = ch.get("after", "?")
                # Truncate display values
                before_s = str(before)[:12]
                after_s = str(after)[:12]
                lines.append(f"  {WHITE}{var:<28}{RST} {RED}{before_s:>12}{RST} \u2192 {GREEN}{after_s}{RST}")
                wire = ch.get("wire_level_change", "")
                if wire:
                    lines.append(f"  {CYAN}wire: {wire[:52]}{RST}")
                effect = ch.get("effect_on_attacker", "")
                if effect:
                    lines.append(f"  {YELLOW}{effect[:52]}{RST}")
            lines.append("")

        if not self.diffs:
            lines.append(f"  {DIM}No state changes yet...{RST}")

        return lines

    def _render_packet_panel(self) -> List[str]:
        lines: List[str] = []
        lines.append(f"  {BOLD}PACKET LEVEL (last 5){RST}")
        lines.append("")

        for entry in list(self.packets)[:5]:
            dec_str = ", ".join(entry.deception[:2]) if entry.deception else "none"
            lines.append(f"  {entry.request_type} \u2192 {entry.drone_id}")
            lines.append(f"  srcSys:{CYAN}0x{entry.src_system:02x}{RST} jitter:{entry.jitter_m:.1f}m")
            lines.append(f"  {DIM}deception: {dec_str}{RST}")
            lines.append("")

        if not self.packets:
            lines.append(f"  {DIM}No packets yet...{RST}")

        return lines

    def _render_reasoning(self) -> List[str]:
        lines: List[str] = []
        lines.append(f"  {BOLD}AGENT REASONING (last decision){RST}")
        lines.append("")

        if not self.last_reasoning:
            lines.append(f"  {DIM}No decisions yet...{RST}")
            return lines

        # Show the most recent reasoning
        latest_drone = None
        latest_ts = 0.0
        for did, msg in self.last_reasoning.items():
            ts = msg.get("timestamp", 0.0)
            if ts > latest_ts:
                latest_ts = ts
                latest_drone = did

        if latest_drone:
            msg = self.last_reasoning[latest_drone]
            behavior = msg.get("behavior", "?")
            decision = msg.get("decision", "?")
            confidence = msg.get("confidence", 0.0)
            inp = msg.get("input_state", {})
            effect = msg.get("expected_effect", "")

            lines.append(f"  {BOLD}{latest_drone}{RST} chose {CYAN}{behavior}{RST} because:")
            if inp:
                lines.append(f"  - phase={inp.get('phase', '?')} level={inp.get('level', '?')} dwell={inp.get('dwell', 0):.0f}s")
            lines.append(f"  - decision: {decision[:50]}")
            lines.append(f"  - expected: {YELLOW}{effect[:50]}{RST}")
            lines.append(f"  - confidence: {GREEN}{confidence:.2f}{RST}")

        return lines

    def _render_stats(self) -> List[str]:
        lines: List[str] = []
        lines.append(f"  {BOLD}SESSION STATS{RST}")
        lines.append("")

        active = sum(1 for s in self.drones.values()
                     if s.current_phase not in ("IDLE", "") and s.timestamp > 0)
        total = len(self.drones)
        total_mtd = sum(s.mtd_triggers_sent for s in self.drones.values())
        total_cmds = sum(s.commands_received for s in self.drones.values())
        avg_belief = 0.0
        if self.drones:
            avg_belief = sum(s.belief_score for s in self.drones.values()) / max(len(self.drones), 1)

        lines.append(f"  Active: {GREEN}{active}{RST}/{total} drones")
        lines.append(f"  Total cmds: {total_cmds}")
        lines.append(f"  MTD triggers: {RED}{total_mtd}{RST}")
        lines.append(f"  Avg belief: {GREEN}{avg_belief:.3f}{RST}")
        lines.append(f"  Decisions: {len(self.decisions)}")
        lines.append(f"  Diffs: {len(self.diffs)}")
        lines.append(f"  Breaches: {GREEN}0{RST}")

        return lines

    def _render_deception_surface(self) -> List[str]:
        lines: List[str] = []

        # Find the most active drone
        active_drone: Optional[DroneState] = None
        for s in self.drones.values():
            if s.current_phase not in ("IDLE", ""):
                if active_drone is None or s.dwell_seconds > active_drone.dwell_seconds:
                    active_drone = s

        if active_drone is None:
            if self.drones:
                # pick first
                active_drone = next(iter(self.drones.values()))

        if active_drone is None:
            lines.append(f"  {DIM}ENGINE OFFLINE \u2014 run: bash scripts/run_full.sh{RST}")
            return lines

        did = active_drone.drone_id
        lines.append(f"  {BOLD}CURRENT DECEPTION SURFACE ({did}){RST}")

        # Gather info from recent diffs
        sysid_sent = "?"
        gps_shown = "?"
        ghost_ports: List[str] = []
        credentials: List[str] = []
        param_drift = "unknown"
        false_flag = "NO"

        for entry in list(self.diffs):
            if entry.drone_id != did:
                continue
            for ch in entry.changes:
                var = ch.get("variable", "")
                if "_current_sysid" in var:
                    sysid_sent = str(ch.get("after", "?"))
                if "_current_gps" in var:
                    gps = ch.get("after", {})
                    if isinstance(gps, dict):
                        gps_shown = f"{gps.get('lat', '?')}, {gps.get('lon', '?')}, {gps.get('alt', '?')}m"
                if "ghost_port" in var:
                    after = ch.get("after", [])
                    if isinstance(after, list):
                        ghost_ports = [str(p) for p in after]
                if "false_flag_active" in var and ch.get("after") is True:
                    false_flag = "YES"
                if "planted_credentials" in var or "credential" in var:
                    credentials.append(var.split(".")[-1] if "." in var else var)
                if "param_values" in var:
                    param_drift = "active"

        lines.append(f"  sysid shown to attacker    : {CYAN}{sysid_sent}{RST}  (real: 1)")
        lines.append(f"  GPS position shown         : {gps_shown}")
        lines.append(f"  active ghost ports         : {', '.join(ghost_ports) if ghost_ports else 'none yet'}")
        lines.append(f"  planted credentials        : {', '.join(credentials) if credentials else 'none yet'}")
        lines.append(f"  param drift active         : {param_drift}")
        lines.append(f"  false_flag active           : {RED if false_flag == 'YES' else GREEN}{false_flag}{RST}")

        return lines

    def cleanup(self) -> None:
        for sock in self.sockets:
            try:
                sock.close()
            except Exception:
                pass


def main() -> None:
    os.chdir(Path(__file__).parent.parent.parent)

    monitor = AgentMonitor()
    monitor.setup_sockets()

    print(f"\033[2J\033[H", end="")  # clear screen
    print(f"{BG_BLUE}{WHITE}{BOLD}  OpenClaw Agent Monitor — starting...  {RST}")
    print(f"  Listening on UDP 19996-19999")
    print(f"  Real-time agent decisions via UDP:19998/19999")
    print(f"  Waiting for engine data...\n")

    try:
        while True:
            # Poll all sockets
            for _ in range(20):  # poll 20 times per render cycle
                monitor.poll()

            # Render
            output = monitor.render()
            # Move cursor to top-left and print (avoids flicker vs full clear)
            sys.stdout.write(f"\033[H\033[J{output}\n")
            sys.stdout.flush()

            time.sleep(0.05)  # ~1 sec total with 20 polls of 0.05s select timeout

    except KeyboardInterrupt:
        pass
    finally:
        monitor.cleanup()
        print(f"\n{RST}Monitor stopped.")


if __name__ == "__main__":
    main()
