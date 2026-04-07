#!/usr/bin/env python3
"""
obs_packet_flow.py — Terminal-based OMNeT++ style packet flow animation.

Visualises the MIRAGE-UAS network topology and animates packet movement
in real time using ANSI escape codes.  Two modes:

  --replay   (default) Replay attacker_log.jsonl at configurable speed
  --live     Listen on UDP 19996-19999 for a running experiment

Requires Python 3.9+, stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import signal
import socket
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── ANSI helpers ─────────────────────────────────────────────────────────────

ESC = "\033["
HIDE_CURSOR = f"{ESC}?25l"
SHOW_CURSOR = f"{ESC}?25h"
CLEAR_SCREEN = f"{ESC}2J"
RST = f"{ESC}0m"
BOLD = f"{ESC}1m"
DIM = f"{ESC}2m"
BLINK = f"{ESC}5m"

FG_RED     = f"{ESC}31m"
FG_GREEN   = f"{ESC}32m"
FG_YELLOW  = f"{ESC}33m"
FG_BLUE    = f"{ESC}34m"
FG_MAGENTA = f"{ESC}35m"
FG_CYAN    = f"{ESC}36m"
FG_WHITE   = f"{ESC}37m"
FG_BR_RED    = f"{ESC}91m"
FG_BR_GREEN  = f"{ESC}92m"
FG_BR_YELLOW = f"{ESC}93m"
FG_BR_CYAN   = f"{ESC}96m"
FG_BR_WHITE  = f"{ESC}97m"
FG_ORANGE    = f"{ESC}38;5;208m"
BG_RED  = f"{ESC}41m"
BG_CYAN = f"{ESC}46m"
FG_BLACK = f"{ESC}30m"


def goto(r: int, c: int) -> str:
    return f"{ESC}{r};{c}H"


def clrline() -> str:
    return f"{ESC}2K"


# ── Protocol classification ──────────────────────────────────────────────────

PROTO_COLOR: Dict[str, str] = {
    "mavlink":    FG_CYAN,
    "http":       FG_GREEN,
    "websocket":  FG_YELLOW,
    "rtsp":       FG_MAGENTA,
    "ssh":        FG_RED,
    "ghost":      DIM + FG_WHITE,
    "gps":        FG_RED + BLINK,
    "breadcrumb": FG_BR_GREEN,
    "lateral":    FG_BR_RED,
    "scan":       FG_ORANGE,
    "login":      FG_BR_YELLOW,
    "other":      FG_WHITE,
}

PROTO_LABEL: Dict[str, str] = {
    "mavlink": "MAVLink", "http": "HTTP", "websocket": "WS",
    "rtsp": "RTSP", "ssh": "SSH", "ghost": "Ghost", "gps": "GPS",
    "breadcrumb": "Bread", "lateral": "Lateral", "scan": "Scan",
    "login": "Login", "other": "?",
}

# Arrow display row index per protocol (0-4, maps to 5 arrow lines)
PROTO_ARROW_IDX: Dict[str, int] = {
    "mavlink": 0, "gps": 0,
    "http": 1, "login": 1, "breadcrumb": 1,
    "websocket": 2,
    "rtsp": 3, "ssh": 3, "ghost": 3,
    "scan": 0, "lateral": 2,
    "other": 4,
}


def classify_action(action: str) -> Tuple[str, str]:
    """Map attacker_log action → (proto, display_label)."""
    a = action.lower()

    # Scan / Recon
    if a.startswith("tcp_scan"):
        return "scan", "TCP_SCAN"
    if a.startswith("udp_") and "probe" in a:
        return "scan", "UDP_PROBE"
    if a.startswith("recon"):
        return "scan", "RECON"

    # MAVLink
    if a.startswith("heartbeat"):
        return "mavlink", "HEARTBEAT"
    if a.startswith("request_data"):
        return "mavlink", "DATA_STREAM"
    if "param_request" in a:
        return "mavlink", "PARAM_REQ"
    if "param_read" in a:
        name = a.replace("param_read_", "").replace("_timeout", "")
        return "mavlink", f"PARAM:{name[:8]}"
    if "param_set" in a or "fence_disabled" in a:
        return "mavlink", "PARAM_SET"
    if "set_mode" in a:
        return "mavlink", "SET_MODE"
    if a.startswith("arm_"):
        return "mavlink", "ARM"
    if "takeoff" in a:
        return "mavlink", "TAKEOFF"
    if "mission" in a:
        return "mavlink", "MISSION"
    if "file_transfer" in a:
        return "mavlink", "FILE_XFER"

    # HTTP
    if a.startswith("http_get"):
        path = a.replace("http_get_", "").replace("_fail", "")
        return "http", f"GET {path[:12]}"
    if a.startswith("authenticated_"):
        return "http", "AUTH_REQ"
    if a.startswith("follow_endpoint"):
        return "http", "FOLLOW_EP"

    # Login
    if a.startswith("http_login") or a.startswith("login_"):
        return "login", "LOGIN"
    if "credential_reuse" in a:
        return "login", "CRED_REUSE"

    # WebSocket
    if a.startswith("ws_"):
        tag = a[3:].upper()
        if len(tag) > 14:
            tag = tag[:14]
        return "websocket", f"WS:{tag}"

    # RTSP
    if a.startswith("rtsp_"):
        return "rtsp", a.upper()

    # GPS
    if a.startswith("gps_"):
        return "gps", "GPS_SPOOF"

    # SSH
    if a.startswith("ssh_"):
        return "ssh", "SSH"

    # Ghost
    if a.startswith("ghost_"):
        return "ghost", "GHOST"

    # Breadcrumb
    if a.startswith("breadcrumb_"):
        return "breadcrumb", "BREADCRUMB"

    # Lateral
    if a.startswith("lateral_"):
        return "lateral", "LATERAL"

    # Backup/C2
    if a.startswith("backup_") or a.startswith("c2_"):
        return "lateral", a[:12].upper()

    # Intel/campaign summary — skip animation
    if a in ("intel_summary", "campaign_complete", "final_score"):
        return "other", a.upper()

    return "other", a[:14].upper()


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class PacketEvent:
    ts: float
    proto: str
    msg_type: str
    target_ip: str       # drone IP (or url-extracted IP)
    drone_idx: int       # 0, 1, 2
    direction: str       # "fwd" (attacker→drone) or "rev" (response)
    level: int
    extra: str
    duration_ms: float
    has_response: bool   # True if attacker got a response


@dataclass
class AnimPacket:
    event: PacketEvent
    frame: int
    total_frames: int


@dataclass
class Stats:
    total: int = 0
    by_proto: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    current_level: int = 0
    level_start_ts: float = 0.0
    mtd_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    responses: int = 0
    timeouts: int = 0


# ── Constants ────────────────────────────────────────────────────────────────

DRONE_IPS = ["172.40.0.10", "172.40.0.11", "172.40.0.12"]
DRONE_NAMES = ["DRONE_0", "DRONE_1", "DRONE_2"]
ATTACKER_IP = "172.40.0.200"
FPS = 10
FRAME_DT = 1.0 / FPS
ANIM_FRAMES = 4
TICKER_SIZE = 14
REPLAY_SPEED = 10.0

# Layout (1-based)
ATK_ROW = 3
ATK_COL = 2
ATK_W = 17

ARROW_COL_START = ATK_COL + ATK_W + 1
ARROW_LEN = 20
ARROW_COL_END = ARROW_COL_START + ARROW_LEN

HN_COL = ARROW_COL_END + 1
HN_W = 45

DRONE_BOX_ROW = ATK_ROW + 3
DRONE_BOX_W = 12
DRONE_COLS = [HN_COL + 2, HN_COL + 15, HN_COL + 29]

STATS_COL = HN_COL + HN_W + 2
STATS_ROW = ATK_ROW
TICKER_ROW = ATK_ROW + 18
TICKER_COL = ATK_COL

# Arrow rows for 5 protocol lines
ARROW_ROWS = [ATK_ROW + 3, ATK_ROW + 4, ATK_ROW + 5, ATK_ROW + 6, ATK_ROW + 7]


# ── IP extraction ────────────────────────────────────────────────────────────

_IP_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+)")


def extract_target_ip(target: str) -> str:
    """Extract IP from target string (ip:port, http://ip:port/path, ws://ip:port)."""
    m = _IP_RE.search(target)
    if m:
        return m.group(1)
    return DRONE_IPS[0]


def drone_index(ip: str) -> int:
    """Map IP to drone index 0/1/2."""
    if ip in DRONE_IPS:
        return DRONE_IPS.index(ip)
    # Match last octet
    try:
        last = ip.split(".")[-1]
        for i, dip in enumerate(DRONE_IPS):
            if dip.endswith("." + last):
                return i
    except (IndexError, ValueError):
        pass
    return 0


# ── Data loading ─────────────────────────────────────────────────────────────

def load_attacker_log(path: str) -> List[PacketEvent]:
    events: List[PacketEvent] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = float(rec.get("timestamp", 0))
            level = int(rec.get("level", -1))
            action = rec.get("action", "")
            target = rec.get("target", "")
            duration = float(rec.get("duration_ms", 0))
            resp = rec.get("response_preview", "")

            # Skip summary records
            if level < 0:
                continue
            if action in ("intel_summary", "campaign_complete", "final_score",
                          "recon_summary", "breadcrumb_harvest"):
                continue

            proto, msg_type = classify_action(action)
            ip = extract_target_ip(target)
            didx = drone_index(ip)

            # Determine if we got a response
            has_response = bool(resp) and "_timeout" not in action and "_fail" not in action
            is_timeout = "_timeout" in action or "_fail" in action

            extra_text = ""
            if resp and len(resp) > 0:
                extra_text = resp[:50]
            ttp = rec.get("mitre_ttp", "")
            if ttp:
                extra_text = f"[{ttp}] {extra_text}"
            intel = rec.get("intel_gained", "")
            if intel:
                extra_text = f"{extra_text} +{intel[:20]}"

            # Forward packet (attacker → drone)
            events.append(PacketEvent(
                ts=ts, proto=proto, msg_type=msg_type,
                target_ip=ip, drone_idx=didx,
                direction="fwd", level=level,
                extra=extra_text[:60],
                duration_ms=duration,
                has_response=has_response,
            ))

            # Response packet (drone → attacker) if we got one
            if has_response and duration > 0:
                resp_ts = ts + (duration / 1000.0)
                resp_label = "ACK" if "ack" in action or "accept" in action else "RESP"
                events.append(PacketEvent(
                    ts=resp_ts, proto=proto, msg_type=resp_label,
                    target_ip=ip, drone_idx=didx,
                    direction="rev", level=level,
                    extra="",
                    duration_ms=0,
                    has_response=False,
                ))

    events.sort(key=lambda e: e.ts)
    return events


# ── Renderer ─────────────────────────────────────────────────────────────────

class Renderer:
    def __init__(self) -> None:
        self.buf: List[str] = []
        self.flash_nodes: Dict[str, float] = {}
        self.anim_packets: List[AnimPacket] = []
        self.ticker_lines: List[str] = []
        self.stats = Stats()

    def _w(self, s: str) -> None:
        self.buf.append(s)

    def _at(self, r: int, c: int, text: str) -> None:
        self.buf.append(goto(r, c) + text)

    def flush(self) -> None:
        sys.stdout.write("".join(self.buf))
        sys.stdout.flush()
        self.buf.clear()

    # ── Full static draw ─────────────────────────────────────────────────

    def draw_static(self) -> None:
        self._w(HIDE_CURSOR + CLEAR_SCREEN)
        self._draw_title()
        self._draw_attacker_box()
        self._draw_arrows_static()
        self._draw_honey_box()
        self._draw_engine_box()
        self._draw_stats_frame()
        self._draw_ticker_frame()
        self.flush()

    def _draw_title(self) -> None:
        self._at(1, ATK_COL,
                 f"{BOLD}{FG_BR_CYAN}  MIRAGE-UAS  Packet Flow Animation  "
                 f"{DIM}(Ctrl+C to exit){RST}")

    def _draw_attacker_box(self) -> None:
        r, c = ATK_ROW, ATK_COL
        box = [
            f"\u250c{'─' * 15}\u2510",
            f"\u2502{BOLD}   ATTACKER   {RST}{FG_RED}\u2502",
            f"\u2502 172.40.0.200 \u2502",
            f"\u2502              \u2502",
            f"\u2502 {FG_BR_RED}L? ------{RST}{FG_RED}    \u2502",
            f"\u2502              \u2502",
            f"\u2502              \u2502",
            f"\u2502              \u2502",
            f"\u2502              \u2502",
            f"\u2502              \u2502",
            f"\u2514{'─' * 15}\u2518",
        ]
        for i, line in enumerate(box):
            self._at(r + i, c, f"{FG_RED}{line}{RST}")

    def _draw_arrows_static(self) -> None:
        arrow_r = f"{'─' * (ARROW_LEN - 1)}\u25b6"
        arrow_l = f"\u25c0{'─' * (ARROW_LEN - 1)}"
        labels = [
            (FG_CYAN,    "MAVLink/GPS", arrow_r),
            (FG_GREEN,   "HTTP/Login ", arrow_r),
            (FG_YELLOW,  "WebSocket  ", arrow_r),
            (FG_MAGENTA, "RTSP/SSH   ", arrow_r),
            (FG_WHITE,   "Response   ", arrow_l),
        ]
        for i, (clr, lbl, arr) in enumerate(labels):
            row = ARROW_ROWS[i]
            self._at(row, ARROW_COL_START, f"{clr}{arr}{RST} {DIM}{lbl}{RST}")

    def _draw_honey_box(self) -> None:
        r, c, w = ATK_ROW, HN_COL, HN_W
        # Top border
        self._at(r, c, f"{FG_CYAN}\u250c{'─' * (w - 2)}\u2510{RST}")
        self._at(r + 1, c,
                 f"{FG_CYAN}\u2502{BOLD}{FG_BR_CYAN}{'MIRAGE-UAS HONEY NETWORK':^{w-2}}{RST}{FG_CYAN}\u2502{RST}")
        self._at(r + 2, c, f"{FG_CYAN}\u2502{' ' * (w - 2)}\u2502{RST}")

        # Drone sub-boxes
        for di in range(3):
            dc = DRONE_COLS[di]
            dr = DRONE_BOX_ROW
            name = DRONE_NAMES[di]
            ip_short = DRONE_IPS[di].split(".")[-1]
            lines = [
                f"\u250c{'─' * 10}\u2510",
                f"\u2502{BOLD} {name:<9}{RST}{FG_BR_WHITE}\u2502",
                f"\u2502 .{ip_short}:14550{' ' * max(0, 3 - len(ip_short))}\u2502",
                f"\u2502 HTTP  :80 \u2502",
                f"\u2502 WS:18789 \u2502",
                f"\u2502 RTSP:8554\u2502",
                f"\u2514{'─' * 10}\u2518",
            ]
            for i, line in enumerate(lines):
                self._at(dr + i, dc, f"{FG_BR_WHITE}{line}{RST}")

        # Side borders
        for ri in range(2, 17):
            self._at(r + ri, c, f"{FG_CYAN}\u2502{RST}")
            self._at(r + ri, c + w - 1, f"{FG_CYAN}\u2502{RST}")
        self._at(r + 17, c, f"{FG_CYAN}\u2514{'─' * (w - 2)}\u2518{RST}")

    def _draw_engine_box(self) -> None:
        r = DRONE_BOX_ROW + 8
        c = HN_COL + 3
        w = 38
        self._at(r, c,     f"{FG_BLUE}\u250c{'─' * (w-2)}\u2510{RST}")
        self._at(r + 1, c, f"{FG_BLUE}\u2502{BOLD}{'OpenClaw Agent + MTD Engine':^{w-2}}{RST}{FG_BLUE}\u2502{RST}")
        self._at(r + 2, c, f"{FG_BLUE}\u2502{'  :14551    :14552    :14553  ':^{w-2}}\u2502{RST}")
        self._at(r + 3, c, f"{FG_BLUE}\u2502{FG_BR_RED}{'PORT_ROT  IP_SHUF  KEY_ROT':^{w-2}}{RST}{FG_BLUE}\u2502{RST}")
        self._at(r + 4, c, f"{FG_BLUE}\u2514{'─' * (w-2)}\u2518{RST}")

    def _draw_stats_frame(self) -> None:
        r, c, w = STATS_ROW, STATS_COL, 24
        self._at(r, c, f"{BOLD}{FG_BR_WHITE}\u250c{'─' * (w-2)}\u2510{RST}")
        self._at(r + 1, c, f"{BOLD}{FG_BR_WHITE}\u2502{'  STATISTICS  ':^{w-2}}\u2502{RST}")
        for i in range(2, 18):
            self._at(r + i, c, f"{FG_WHITE}\u2502{' ' * (w-2)}\u2502{RST}")
        self._at(r + 18, c, f"{FG_WHITE}\u2514{'─' * (w-2)}\u2518{RST}")

    def _draw_ticker_frame(self) -> None:
        r = TICKER_ROW
        self._at(r, TICKER_COL,
                 f"{BOLD}{FG_BR_YELLOW}\u2500\u2500 Event Ticker {'─' * 70}{RST}")

    # ── Incremental updates ──────────────────────────────────────────────

    def update_attack_level(self, level: int) -> None:
        level_names = {0: "RECON", 1: "EXPLOIT", 2: "ENUM", 3: "DEEPEN", 4: "PERSIST"}
        name = level_names.get(level, "???")
        r = ATK_ROW + 4
        self._at(r, ATK_COL,
                 f"{FG_RED}\u2502 {FG_BR_RED}{BOLD}L{level} {name:<8}{RST}{FG_RED}   \u2502{RST}")

    def update_stats(self, stats: Stats, now: float) -> None:
        r, c = STATS_ROW, STATS_COL

        def _l(row: int, text: str) -> None:
            padded = f" {text}"[:22]
            self._at(row, c, f"{FG_WHITE}\u2502{padded:<22}\u2502{RST}")

        _l(r+2, f"{FG_BR_WHITE}total: {stats.total}{RST}")
        _l(r+3, f"{FG_CYAN}mavlink: {stats.by_proto.get('mavlink', 0)}{RST}")
        _l(r+4, f"{FG_GREEN}http: {stats.by_proto.get('http', 0) + stats.by_proto.get('login', 0)}{RST}")
        _l(r+5, f"{FG_YELLOW}ws: {stats.by_proto.get('websocket', 0)}{RST}")
        _l(r+6, f"{FG_MAGENTA}rtsp: {stats.by_proto.get('rtsp', 0)}{RST}")
        _l(r+7, f"{FG_ORANGE}scan: {stats.by_proto.get('scan', 0)}{RST}")
        _l(r+8, f"{FG_RED}ssh: {stats.by_proto.get('ssh', 0)}{RST}")
        _l(r+9, "")
        dur = int(now - stats.level_start_ts) if stats.level_start_ts > 0 else 0
        _l(r+10, f"{BOLD}ATTACK LEVEL{RST}")
        _l(r+11, f" current: L{stats.current_level}")
        _l(r+12, f" duration: {dur}s")
        _l(r+13, "")
        _l(r+14, f"{FG_BR_GREEN}responses: {stats.responses}{RST}")
        _l(r+15, f"{FG_RED}timeouts: {stats.timeouts}{RST}")
        _l(r+16, "")

        mtd_parts = [f"{k}:{v}" for k, v in stats.mtd_counts.items()]
        _l(r+17, f"MTD: {' '.join(mtd_parts) if mtd_parts else 'none'}")

    def update_ticker(self) -> None:
        r = TICKER_ROW
        shown = self.ticker_lines[-TICKER_SIZE:]
        for i, line in enumerate(shown):
            self._at(r + 1 + i, TICKER_COL, clrline() + line)
        for i in range(len(shown), TICKER_SIZE):
            self._at(r + 1 + i, TICKER_COL, clrline())

    def add_ticker(self, text: str) -> None:
        self.ticker_lines.append(text)
        if len(self.ticker_lines) > 200:
            self.ticker_lines = self.ticker_lines[-100:]

    # ── Packet animation ─────────────────────────────────────────────────

    def start_anim(self, ev: PacketEvent) -> None:
        self.anim_packets.append(AnimPacket(event=ev, frame=0, total_frames=ANIM_FRAMES))

    def tick_anims(self, now_mono: float) -> None:
        surviving: List[AnimPacket] = []
        for ap in self.anim_packets:
            self._draw_anim_frame(ap, now_mono)
            ap.frame += 1
            if ap.frame < ap.total_frames:
                surviving.append(ap)
            else:
                self._flash_dest(ap.event, now_mono)
        self.anim_packets = surviving

        # Expire flashes
        expired = [k for k, exp in self.flash_nodes.items() if now_mono >= exp]
        for k in expired:
            self._unflash(k)
            del self.flash_nodes[k]

    def _draw_anim_frame(self, ap: AnimPacket, now: float) -> None:
        ev = ap.event
        color = PROTO_COLOR.get(ev.proto, FG_WHITE)

        # Pick arrow row based on protocol
        arrow_idx = PROTO_ARROW_IDX.get(ev.proto, 4)
        # For response packets, always use the response arrow (row 4)
        if ev.direction == "rev":
            arrow_idx = 4
        row = ARROW_ROWS[arrow_idx]

        frac = ap.frame / max(ap.total_frames - 1, 1)

        if ev.direction == "fwd":
            col = ARROW_COL_START + int(frac * (ARROW_COL_END - ARROW_COL_START))
            self._at(row, col, f"{color}{BOLD}\u25cf{RST}")

            # Show label on first frame
            if ap.frame == 0:
                label_row = row - 1 if row > 2 else row + 1
                label = ev.msg_type[:16]
                self._at(label_row, ARROW_COL_START, f"{color}{DIM}{label:<{ARROW_LEN}}{RST}")
                # Flash attacker
                self.flash_nodes["attacker"] = now + 0.3
                self._flash_attacker()
        else:
            col = ARROW_COL_END - int(frac * (ARROW_COL_END - ARROW_COL_START))
            self._at(row, col, f"{color}{BOLD}\u25cf{RST}")
            if ap.frame == 0:
                self.flash_nodes[f"drone_{ev.drone_idx}"] = now + 0.5
                self._flash_drone(ev.drone_idx)

        # Erase previous position
        if ap.frame > 0:
            prev_frac = (ap.frame - 1) / max(ap.total_frames - 1, 1)
            if ev.direction == "fwd":
                prev_col = ARROW_COL_START + int(prev_frac * (ARROW_COL_END - ARROW_COL_START))
            else:
                prev_col = ARROW_COL_END - int(prev_frac * (ARROW_COL_END - ARROW_COL_START))
            if prev_col != col:
                self._at(row, prev_col, f"{DIM}─{RST}")

    def _flash_attacker(self) -> None:
        self._at(ATK_ROW + 1, ATK_COL,
                 f"{BG_RED}{BOLD}{FG_BR_WHITE}\u2502   ATTACKER   \u2502{RST}")

    def _flash_drone(self, idx: int) -> None:
        dc = DRONE_COLS[idx]
        dr = DRONE_BOX_ROW + 1
        name = DRONE_NAMES[idx]
        self._at(dr, dc,
                 f"{BG_CYAN}{FG_BLACK}{BOLD}\u2502 {name:<9}\u2502{RST}")

    def _flash_dest(self, ev: PacketEvent, now: float) -> None:
        if ev.direction == "fwd":
            key = f"drone_{ev.drone_idx}"
            self.flash_nodes[key] = now + 0.3
            self._flash_drone(ev.drone_idx)
        else:
            self.flash_nodes["attacker"] = now + 0.3
            self._flash_attacker()

    def _unflash(self, key: str) -> None:
        if key == "attacker":
            self._at(ATK_ROW + 1, ATK_COL,
                     f"{FG_RED}\u2502{BOLD}   ATTACKER   {RST}{FG_RED}\u2502{RST}")
        elif key.startswith("drone_"):
            di = int(key.split("_")[1])
            dc = DRONE_COLS[di]
            dr = DRONE_BOX_ROW + 1
            name = DRONE_NAMES[di]
            self._at(dr, dc,
                     f"{FG_BR_WHITE}\u2502{BOLD} {name:<9}{RST}{FG_BR_WHITE}\u2502{RST}")


# ── Ticker formatting ────────────────────────────────────────────────────────

def fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def ticker_for_packet(ev: PacketEvent) -> str:
    ts = fmt_ts(ev.ts)
    color = PROTO_COLOR.get(ev.proto, FG_WHITE)
    plabel = PROTO_LABEL.get(ev.proto, "?")
    drone = DRONE_NAMES[ev.drone_idx]

    if ev.direction == "fwd":
        arrow = f"──▶ {drone}"
        result = ""
        if ev.has_response:
            result = f" {FG_BR_GREEN}✓{RST}"
        elif "_timeout" in ev.msg_type.lower() or "_fail" in ev.msg_type.lower():
            result = f" {FG_RED}✗{RST}"
        return (
            f"{DIM}{ts}{RST} "
            f"{color}[L{ev.level}]{RST} "
            f"{color}{BOLD}{ev.msg_type}{RST} "
            f"{arrow}{result}"
            f" {DIM}{ev.extra[:35]}{RST}"
        )
    else:
        return (
            f"{DIM}{ts}{RST} "
            f"{color}[L{ev.level}]{RST} "
            f"{FG_BR_GREEN}◀── {ev.msg_type} from {drone}{RST}"
        )


# ── Replay engine ────────────────────────────────────────────────────────────

def run_replay(data_dir: str) -> None:
    log_path = os.path.join(data_dir, "results", "attacker_log.jsonl")
    if not os.path.exists(log_path):
        print(f"Error: {log_path} not found")
        return

    events = load_attacker_log(log_path)
    if not events:
        print("No events to replay.")
        return

    renderer = Renderer()
    stats = renderer.stats
    stats.level_start_ts = events[0].ts

    renderer.draw_static()

    sim_start = events[0].ts
    wall_start = time.monotonic()
    idx = 0

    try:
        while idx < len(events) or renderer.anim_packets:
            frame_start = time.monotonic()
            wall_elapsed = frame_start - wall_start
            sim_now = sim_start + wall_elapsed * REPLAY_SPEED

            # Fire events
            fired = 0
            while idx < len(events) and events[idx].ts <= sim_now and fired < 8:
                ev = events[idx]
                idx += 1
                fired += 1

                stats.total += 1
                stats.by_proto[ev.proto] += 1
                if ev.has_response:
                    stats.responses += 1
                if not ev.has_response and ev.direction == "fwd":
                    stats.timeouts += 1

                if ev.level != stats.current_level:
                    stats.current_level = ev.level
                    stats.level_start_ts = ev.ts

                renderer.start_anim(ev)
                renderer.add_ticker(ticker_for_packet(ev))

            renderer.tick_anims(time.monotonic())
            renderer.update_attack_level(stats.current_level)
            renderer.update_stats(stats, sim_now)
            renderer.update_ticker()
            renderer.flush()

            elapsed = time.monotonic() - frame_start
            rest = FRAME_DT - elapsed
            if rest > 0:
                time.sleep(rest)

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RST + "\n")
        sys.stdout.flush()

    print(f"\nReplay done. {stats.total} packets ({stats.responses} responses, {stats.timeouts} timeouts)")


# ── Live mode ────────────────────────────────────────────────────────────────

def run_live(data_dir: str) -> None:
    """Listen on UDP 19996-19999 and visualize events."""
    ports = [19996, 19997, 19998, 19999]
    socks: List[socket.socket] = []
    for port in ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.setblocking(False)
            socks.append(s)
        except OSError as e:
            print(f"Warning: port {port}: {e}")

    if not socks:
        print("No UDP ports available. Is obs_openclaw.py already running?")
        return

    renderer = Renderer()
    stats = renderer.stats
    stats.level_start_ts = time.time()

    renderer.draw_static()

    try:
        while True:
            frame_start = time.monotonic()
            now = time.time()

            readable, _, _ = select.select(socks, [], [], 0)
            for s in readable:
                try:
                    data, _ = s.recvfrom(65536)
                    port = s.getsockname()[1]
                    msg = json.loads(data.decode("utf-8", errors="replace"))
                    _handle_live_msg(renderer, stats, port, msg, now)
                except Exception:
                    pass

            renderer.tick_anims(time.monotonic())
            renderer.update_stats(stats, now)
            renderer.update_ticker()
            renderer.flush()

            rest = FRAME_DT - (time.monotonic() - frame_start)
            if rest > 0:
                time.sleep(rest)

    except KeyboardInterrupt:
        pass
    finally:
        for s in socks:
            s.close()
        sys.stdout.write(SHOW_CURSOR + RST + "\n")
        sys.stdout.flush()

    print(f"\nLive session ended. {stats.total} events.")


def _handle_live_msg(renderer: Renderer, stats: Stats,
                     port: int, msg: dict, now: float) -> None:
    """Route a live UDP message to the correct handler."""
    if port == 19999:
        # State snapshot from AgenticDecoyEngine
        drone_id = msg.get("drone_id", "drone_0")
        phase = msg.get("current_phase", "IDLE")
        level = msg.get("attacker_level", "---")
        ip = msg.get("attacker_ip", "")
        if ip:
            didx = drone_index(ip)
        else:
            # Extract index from drone_id
            try:
                didx = int(drone_id.split("_")[-1])
            except (ValueError, IndexError):
                didx = 0

        stats.current_level = _guess_level(phase)
        stats.level_start_ts = now

        last_action = msg.get("last_action", "")
        if last_action:
            proto, label = classify_action(last_action)
            ev = PacketEvent(
                ts=now, proto=proto, msg_type=label,
                target_ip=DRONE_IPS[min(didx, 2)], drone_idx=min(didx, 2),
                direction="fwd", level=stats.current_level,
                extra=msg.get("last_action_reason", "")[:40],
                duration_ms=0, has_response=True,
            )
            stats.total += 1
            stats.by_proto[proto] += 1
            renderer.start_anim(ev)
            renderer.add_ticker(ticker_for_packet(ev))
            renderer.update_attack_level(stats.current_level)

    elif port == 19998:
        # Decision/phase/level event from OpenClawAgent
        event_type = msg.get("event", "")
        drone_id = msg.get("drone_id", "drone_0")
        try:
            didx = int(drone_id.split("_")[-1])
        except (ValueError, IndexError):
            didx = 0

        if event_type == "agent_decision":
            behavior = msg.get("behavior", "?")
            color = FG_CYAN
            renderer.add_ticker(
                f"{DIM}{fmt_ts(now)}{RST} "
                f"{color}[AGENT]{RST} {BOLD}{behavior}{RST} "
                f"{drone_id} {DIM}{msg.get('decision', '')[:30]}{RST}"
            )

        elif event_type == "phase_transition":
            fp = msg.get("from_phase", "?")
            tp = msg.get("to_phase", "?")
            renderer.add_ticker(
                f"{DIM}{fmt_ts(now)}{RST} "
                f"{FG_BR_YELLOW}[PHASE]{RST} {drone_id} "
                f"{fp} → {tp}"
            )
            renderer.update_attack_level(_guess_level(tp))

        elif event_type == "level_reclassified":
            fl = msg.get("from_level", "?")
            tl = msg.get("to_level", "?")
            renderer.add_ticker(
                f"{DIM}{fmt_ts(now)}{RST} "
                f"{FG_MAGENTA}[LEVEL]{RST} {drone_id} "
                f"{fl} → {tl}"
            )

    elif port == 19997:
        # Internal state diff
        behavior = msg.get("behavior", "?")
        drone_id = msg.get("drone_id", "?")
        changes = msg.get("changes", [])
        change_strs = []
        for ch in changes[:2]:
            var = ch.get("variable", "?")
            before = str(ch.get("before", ""))[:8]
            after = str(ch.get("after", ""))[:8]
            change_strs.append(f"{var}: {before}→{after}")
        renderer.add_ticker(
            f"{DIM}{fmt_ts(now)}{RST} "
            f"{FG_BLUE}[DIFF]{RST} {drone_id} {behavior} "
            f"{DIM}{'; '.join(change_strs)}{RST}"
        )
        # Animate as internal action
        try:
            didx = int(drone_id.split("_")[-1])
        except (ValueError, IndexError):
            didx = 0
        ev = PacketEvent(
            ts=now, proto="mavlink", msg_type=behavior[:10],
            target_ip=DRONE_IPS[min(didx, 2)], drone_idx=min(didx, 2),
            direction="rev", level=0,
            extra="", duration_ms=0, has_response=False,
        )
        stats.total += 1
        renderer.start_anim(ev)

    elif port == 19996:
        # Packet-level diff from MavlinkResponseGenerator
        req_type = msg.get("request_type", "?")
        drone_id = msg.get("drone_id", "?")
        deception = msg.get("deception_applied", [])
        try:
            didx = int(drone_id.split("_")[-1])
        except (ValueError, IndexError):
            didx = 0
        dec_str = ",".join(deception[:2]) if deception else "none"

        ev = PacketEvent(
            ts=now, proto="mavlink", msg_type=req_type[:10],
            target_ip=DRONE_IPS[min(didx, 2)], drone_idx=min(didx, 2),
            direction="rev", level=0,
            extra=dec_str, duration_ms=0, has_response=False,
        )
        stats.total += 1
        stats.by_proto["mavlink"] += 1
        stats.responses += 1
        renderer.start_anim(ev)
        renderer.add_ticker(
            f"{DIM}{fmt_ts(now)}{RST} "
            f"{FG_CYAN}[PKT]{RST} {req_type} → {drone_id} "
            f"{DIM}deception:{dec_str}{RST}"
        )


def _guess_level(phase: str) -> int:
    """Map attack phase to approximate L-level."""
    phase = phase.lower()
    if phase in ("recon", "idle"):
        return 0
    if phase == "exploit":
        return 1
    if phase == "persist":
        return 3
    if phase == "exfil":
        return 4
    return 2


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MIRAGE-UAS OMNeT++ style packet flow animation"
    )
    parser.add_argument("--live", action="store_true",
                        help="Live mode: listen on UDP 19996-19999")
    parser.add_argument("--replay", action="store_true", default=True,
                        help="Replay mode (default)")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Project root directory")
    parser.add_argument("--speed", type=float, default=10.0,
                        help="Replay speed multiplier (default: 10)")
    args = parser.parse_args()

    global REPLAY_SPEED
    REPLAY_SPEED = args.speed

    data_dir = args.data_dir
    if data_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.normpath(os.path.join(script_dir, "..", ".."))
        if os.path.isfile(os.path.join(candidate, "results", "attacker_log.jsonl")):
            data_dir = candidate
        else:
            data_dir = os.getcwd()

    if hasattr(signal, "SIGWINCH"):
        signal.signal(signal.SIGWINCH, lambda *_: None)

    if args.live:
        run_live(data_dir)
    else:
        run_replay(data_dir)


if __name__ == "__main__":
    main()
