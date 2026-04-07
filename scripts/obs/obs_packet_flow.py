#!/usr/bin/env python3
"""
obs_packet_flow.py — Terminal-based OMNeT++ style packet flow animation.

Visualises the MIRAGE-UAS network topology and animates packet movement
in real time using ANSI escape codes.  Two modes:

  --replay   (default) Replay attacker_log.jsonl + traffic_trace.csv at 10x
  --live     Listen on UDP 19996-19999 for a running experiment

Requires Python 3.9+, stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import select
import signal
import socket
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

# ── ANSI helpers ──────────────────────────────────────────────────────────────

ESC = "\033["
HIDE_CURSOR = f"{ESC}?25l"
SHOW_CURSOR = f"{ESC}?25h"
CLEAR_SCREEN = f"{ESC}2J"
RESET = f"{ESC}0m"
BOLD = f"{ESC}1m"
DIM = f"{ESC}2m"
BLINK = f"{ESC}5m"
REVERSE = f"{ESC}7m"

# Foreground colours
FG_BLACK   = f"{ESC}30m"
FG_RED     = f"{ESC}31m"
FG_GREEN   = f"{ESC}32m"
FG_YELLOW  = f"{ESC}33m"
FG_BLUE    = f"{ESC}34m"
FG_MAGENTA = f"{ESC}35m"
FG_CYAN    = f"{ESC}36m"
FG_WHITE   = f"{ESC}37m"
FG_BRIGHT_RED    = f"{ESC}91m"
FG_BRIGHT_GREEN  = f"{ESC}92m"
FG_BRIGHT_YELLOW = f"{ESC}93m"
FG_BRIGHT_CYAN   = f"{ESC}96m"
FG_BRIGHT_WHITE  = f"{ESC}97m"

BG_RED    = f"{ESC}41m"
BG_BLUE   = f"{ESC}44m"
BG_CYAN   = f"{ESC}46m"


def goto(row: int, col: int) -> str:
    """ANSI cursor positioning (1-based)."""
    return f"{ESC}{row};{col}H"


def clrline() -> str:
    return f"{ESC}2K"


# ── Protocol enum ─────────────────────────────────────────────────────────────

class Proto(Enum):
    MAVLINK   = "mavlink"
    HTTP      = "http"
    WEBSOCKET = "websocket"
    RTSP      = "rtsp"
    SSH       = "ssh"
    GHOST     = "ghost"
    GPS       = "gps"
    BREADCRUMB = "breadcrumb"
    LATERAL   = "lateral"
    SCAN      = "scan"
    OTHER     = "other"


PROTO_COLOR: Dict[Proto, str] = {
    Proto.MAVLINK:    FG_CYAN,
    Proto.HTTP:       FG_GREEN,
    Proto.WEBSOCKET:  FG_YELLOW,
    Proto.RTSP:       FG_MAGENTA,
    Proto.SSH:        FG_RED,
    Proto.GHOST:      DIM + FG_WHITE,
    Proto.GPS:        FG_RED + BLINK,
    Proto.BREADCRUMB: FG_BRIGHT_GREEN,
    Proto.LATERAL:    FG_BRIGHT_RED,
    Proto.SCAN:       DIM + FG_YELLOW,
    Proto.OTHER:      FG_WHITE,
}

PROTO_LABEL: Dict[Proto, str] = {
    Proto.MAVLINK:    "MAVLink",
    Proto.HTTP:       "HTTP",
    Proto.WEBSOCKET:  "WebSocket",
    Proto.RTSP:       "RTSP",
    Proto.SSH:        "SSH",
    Proto.GHOST:      "Ghost",
    Proto.GPS:        "GPS",
    Proto.BREADCRUMB: "Bread",
    Proto.LATERAL:    "Lateral",
    Proto.SCAN:       "Scan",
    Proto.OTHER:      "???",
}

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PacketEvent:
    """A single packet / action from any data source."""
    ts: float               # unix-epoch seconds
    proto: Proto
    msg_type: str           # e.g. HEARTBEAT, COMMAND_LONG
    src_ip: str
    dst_ip: str
    dst_port: int
    direction: str          # "fwd" = attacker→drone, "rev" = response
    level: int              # attack level 0-5
    extra: str              # free-form annotation
    drone_idx: int          # 0, 1, 2 — index into drone list


@dataclass
class MtdEvent:
    ts: float
    drone_id: str
    action_type: str
    old_port: int
    new_port: int
    old_ip: str
    new_ip: str
    latency_ms: float


@dataclass
class AnimPacket:
    """Packet currently being animated across the topology view."""
    event: PacketEvent
    frame: int              # current animation frame (0..ANIM_FRAMES-1)
    total_frames: int


@dataclass
class Stats:
    total: int = 0
    by_proto: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    current_level: int = 0
    level_start_ts: float = 0.0
    mtd_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))


# ── Constants ─────────────────────────────────────────────────────────────────

DRONE_IPS = ["172.40.0.10", "172.40.0.11", "172.40.0.12"]
DRONE_NAMES = ["DRONE_0", "DRONE_1", "DRONE_2"]
ATTACKER_IP = "172.40.0.200"
FPS = 10
FRAME_DT = 1.0 / FPS
ANIM_FRAMES = 4          # how many frames a packet takes to cross
TICKER_SIZE = 10          # last N events in the ticker
REPLAY_SPEED = 10.0       # 10x real-time

# ── Topology layout constants ────────────────────────────────────────────────
# All row/col values are 1-based for goto().

TOPO_TOP = 2
TOPO_LEFT = 2

# Attacker box
ATK_ROW = TOPO_TOP + 1
ATK_COL = TOPO_LEFT
ATK_W = 17
ATK_H = 11

# Arrow path
ARROW_COL_START = ATK_COL + ATK_W + 1
ARROW_COL_END = ARROW_COL_START + 18

# Honey network box
HN_COL = ARROW_COL_END + 1
HN_W = 45
HN_H = 16

# Drone sub-boxes inside honey network (relative positions)
DRONE_BOX_ROW = ATK_ROW + 2
DRONE_BOX_W = 12
DRONE_BOX_H = 6
DRONE_COLS = [HN_COL + 3, HN_COL + 16, HN_COL + 29]

# Stats sidebar
STATS_COL = HN_COL + HN_W + 3
STATS_ROW = TOPO_TOP + 1
STATS_W = 22

# Ticker
TICKER_ROW = TOPO_TOP + HN_H + 2
TICKER_COL = TOPO_LEFT

# Arrow row for each drone (the row where the animated packet travels)
ARROW_ROWS = [ATK_ROW + 3, ATK_ROW + 4, ATK_ROW + 5]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _term_size() -> Tuple[int, int]:
    """Return (cols, rows)."""
    try:
        cols, rows = os.get_terminal_size()
        return cols, rows
    except OSError:
        return 120, 40


def _drone_index(ip: str) -> int:
    """Map an IP to drone index 0/1/2 (default 0)."""
    if ip in DRONE_IPS:
        return DRONE_IPS.index(ip)
    # check last octet
    for i, dip in enumerate(DRONE_IPS):
        if ip.endswith(dip.split(".")[-1]):
            return i
    return 0


def _classify_action(action: str) -> Tuple[Proto, str]:
    """Map attacker_log action string → (Proto, display_msg_type)."""
    a = action.lower()
    if a.startswith("tcp_scan") or a.startswith("recon"):
        return Proto.SCAN, action.upper()
    if a.startswith("heartbeat"):
        return Proto.MAVLINK, "HEARTBEAT"
    if a.startswith("request_data_stream"):
        return Proto.MAVLINK, "DATA_STREAM"
    if a.startswith("param_request"):
        return Proto.MAVLINK, "PARAM_REQUEST"
    if a.startswith("param_read"):
        return Proto.MAVLINK, "PARAM_READ"
    if a.startswith("set_mode"):
        return Proto.MAVLINK, "SET_MODE"
    if a.startswith("arm_command"):
        return Proto.MAVLINK, "ARM"
    if a.startswith("mission"):
        return Proto.MAVLINK, "MISSION"
    if a.startswith("http_get") or a.startswith("http"):
        return Proto.HTTP, action.upper()
    if a.startswith("ws_"):
        return Proto.WEBSOCKET, action.upper()
    if a.startswith("rtsp_"):
        return Proto.RTSP, action.upper()
    if a.startswith("ghost_"):
        return Proto.GHOST, action.upper()
    if a.startswith("gps_"):
        return Proto.GPS, action.upper()
    if a.startswith("ssh_"):
        return Proto.SSH, action.upper()
    if a.startswith("breadcrumb_"):
        return Proto.BREADCRUMB, action.upper()
    if a.startswith("lateral_"):
        return Proto.LATERAL, action.upper()
    return Proto.OTHER, action.upper()


def _proto_from_str(s: str) -> Proto:
    s = s.lower().strip()
    for p in Proto:
        if p.value == s:
            return p
    return Proto.OTHER


# ── Data loading ──────────────────────────────────────────────────────────────

def load_attacker_log(path: str) -> List[PacketEvent]:
    events: List[PacketEvent] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = float(rec["timestamp"])
            level = int(rec.get("level", 0))
            target = rec.get("target", "")
            action = rec.get("action", "")
            proto, msg = _classify_action(action)
            # parse target ip:port
            dst_ip = DRONE_IPS[0]
            dst_port = 14550
            if ":" in target:
                parts = target.rsplit(":", 1)
                dst_ip = parts[0]
                try:
                    dst_port = int(parts[1])
                except ValueError:
                    pass
            didx = _drone_index(dst_ip)
            extra = rec.get("response_preview", "")
            if len(extra) > 60:
                extra = extra[:60] + "..."
            events.append(PacketEvent(
                ts=ts, proto=proto, msg_type=msg,
                src_ip=ATTACKER_IP, dst_ip=dst_ip, dst_port=dst_port,
                direction="fwd", level=level, extra=extra, drone_idx=didx,
            ))
    return events


def load_traffic_trace(path: str, base_ts: float) -> List[PacketEvent]:
    """Load traffic_trace.csv — timestamps are relative ms from base_ts."""
    events: List[PacketEvent] = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_ms = int(row["timestamp_ms"])
            ts = base_ts + ts_ms / 1000.0
            proto = _proto_from_str(row.get("protocol", "other"))
            msg = row.get("msg_type", "?")
            src_ip = row.get("src_ip", ATTACKER_IP)
            dst_ip = row.get("dst_ip", DRONE_IPS[0])
            dst_port = int(row.get("dst_port", 0))
            didx = _drone_index(dst_ip)
            lbl = int(row.get("label", 0))
            events.append(PacketEvent(
                ts=ts, proto=proto, msg_type=msg,
                src_ip=src_ip, dst_ip=dst_ip, dst_port=dst_port,
                direction="fwd", level=lbl, extra="",
                drone_idx=didx,
            ))
    return events


def load_mtd_events(path: str, base_ts: float) -> List[MtdEvent]:
    evts: List[MtdEvent] = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_ms_str = row.get("timestamp_ms", "0").strip()
            if not ts_ms_str:
                continue
            ts_ms = int(ts_ms_str)
            evts.append(MtdEvent(
                ts=base_ts + ts_ms / 1000.0,
                drone_id=row.get("drone_id", "?"),
                action_type=row.get("action_type", "?"),
                old_port=int(row.get("old_port", 0)),
                new_port=int(row.get("new_port", 0)),
                old_ip=row.get("old_ip", ""),
                new_ip=row.get("new_ip", ""),
                latency_ms=float(row.get("latency_ms", 0)),
            ))
    return evts


# ── Renderer ──────────────────────────────────────────────────────────────────

class Renderer:
    """Manages the terminal display — draws topology, animates packets."""

    def __init__(self) -> None:
        self.buf: List[str] = []
        self.flash_nodes: Dict[str, float] = {}   # node_key → expire_time
        self.anim_packets: List[AnimPacket] = []
        self.ticker_lines: List[str] = []
        self.stats = Stats()
        self._cols, self._rows = _term_size()

    # ── low-level buffer ──

    def _w(self, s: str) -> None:
        self.buf.append(s)

    def _at(self, row: int, col: int, text: str) -> None:
        self.buf.append(goto(row, col) + text)

    def flush(self) -> None:
        sys.stdout.write("".join(self.buf))
        sys.stdout.flush()
        self.buf.clear()

    # ── full redraw (called once at start) ──

    def draw_static(self) -> None:
        """Draw the full static topology."""
        self._w(HIDE_CURSOR + CLEAR_SCREEN)
        self._draw_title()
        self._draw_attacker_box()
        self._draw_arrows_static()
        self._draw_honey_box()
        self._draw_stats_frame()
        self._draw_ticker_frame()
        self.flush()

    def _draw_title(self) -> None:
        title = f"{BOLD}{FG_BRIGHT_CYAN}  MIRAGE-UAS  Packet Flow Observer{RESET}"
        self._at(1, TOPO_LEFT, title)

    def _draw_attacker_box(self) -> None:
        r = ATK_ROW
        c = ATK_COL
        self._at(r, c,     f"{FG_RED}{'':>1}\u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510{RESET}")
        self._at(r+1, c,   f"{FG_RED}{'':>1}\u2502{BOLD}  ATTACKER   {RESET}{FG_RED}\u2502{RESET}")
        self._at(r+2, c,   f"{FG_RED}{'':>1}\u2502{FG_WHITE} 172.40.0.200{FG_RED}\u2502{RESET}")
        self._at(r+3, c,   f"{FG_RED}{'':>1}\u2502{FG_WHITE}             {FG_RED}\u2502{RESET}")
        self._at(r+4, c,   f"{FG_RED}{'':>1}\u2502{FG_WHITE} L? EXPLOIT  {FG_RED}\u2502{RESET}")
        self._at(r+5, c,   f"{FG_RED}{'':>1}\u2502{FG_WHITE}             {FG_RED}\u2502{RESET}")
        self._at(r+6, c,   f"{FG_RED}{'':>1}\u2502{FG_WHITE}  nmap       {FG_RED}\u2502{RESET}")
        self._at(r+7, c,   f"{FG_RED}{'':>1}\u2502{FG_WHITE}             {FG_RED}\u2502{RESET}")
        self._at(r+8, c,   f"{FG_RED}{'':>1}\u2502             \u2502{RESET}")
        self._at(r+9, c,   f"{FG_RED}{'':>1}\u2502             \u2502{RESET}")
        self._at(r+10, c,  f"{FG_RED}{'':>1}\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518{RESET}")

    def _draw_arrows_static(self) -> None:
        labels = ["MAVLink", "HTTP   ", "WebSock", "RTSP   ", "Respond"]
        colors = [FG_CYAN, FG_GREEN, FG_YELLOW, FG_MAGENTA, FG_WHITE]
        arrows = ["\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u25b6",
                  "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u25b6",
                  "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u25b6",
                  "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u25b6",
                  "\u25c0\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"]
        for i, (lbl, clr, arr) in enumerate(zip(labels, colors, arrows)):
            row = ATK_ROW + 3 + i
            col = ARROW_COL_START
            self._at(row, col, f"{clr}{arr} {DIM}{lbl}{RESET}")

    def _draw_honey_box(self) -> None:
        r = ATK_ROW
        c = HN_COL
        w = HN_W
        # Top border
        self._at(r, c,   f"{FG_CYAN}\u250c{'':─<{w-2}}\u2510{RESET}")
        self._at(r+1, c, f"{FG_CYAN}\u2502{BOLD}{FG_BRIGHT_CYAN}{'MIRAGE-UAS HONEY NETWORK':^{w-2}}{RESET}{FG_CYAN}\u2502{RESET}")
        self._at(r+2, c, f"{FG_CYAN}\u2502{' '*(w-2)}\u2502{RESET}")
        # Drone sub-boxes
        for di in range(3):
            dc = DRONE_COLS[di]
            dr = DRONE_BOX_ROW
            name = DRONE_NAMES[di]
            ip_end = DRONE_IPS[di].split(".")[-1]
            self._at(dr,   dc, f"{FG_BRIGHT_WHITE}\u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510{RESET}")
            self._at(dr+1, dc, f"{FG_BRIGHT_WHITE}\u2502{BOLD} {name:<9}{RESET}{FG_BRIGHT_WHITE}\u2502{RESET}")
            self._at(dr+2, dc, f"{FG_BRIGHT_WHITE}\u2502{FG_WHITE} .{ip_end}:14550{' '*(3-len(ip_end))}{RESET}{FG_BRIGHT_WHITE}\u2502{RESET}")
            self._at(dr+3, dc, f"{FG_BRIGHT_WHITE}\u2502{FG_WHITE} HTTP  :80{' ':>1}{RESET}{FG_BRIGHT_WHITE}\u2502{RESET}")
            self._at(dr+4, dc, f"{FG_BRIGHT_WHITE}\u2502{FG_WHITE} WS:18789 {RESET}{FG_BRIGHT_WHITE}\u2502{RESET}")
            self._at(dr+5, dc, f"{FG_BRIGHT_WHITE}\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518{RESET}")

        # OpenClaw row
        orow = DRONE_BOX_ROW + DRONE_BOX_H + 1
        oc = HN_COL + 4
        self._at(orow,   oc, f"{FG_BLUE}\u250c{'':─<36}\u2510{RESET}")
        self._at(orow+1, oc, f"{FG_BLUE}\u2502{BOLD}{'OpenClaw Agent Engine':^36}{RESET}{FG_BLUE}\u2502{RESET}")
        self._at(orow+2, oc, f"{FG_BLUE}\u2502{'  :14551    :14552    :14553':^36}\u2502{RESET}")
        self._at(orow+3, oc, f"{FG_BLUE}\u2514{'':─<36}\u2518{RESET}")

        # MTD row
        mrow = orow + 4
        self._at(mrow,   oc, f"{FG_BRIGHT_RED}\u250c{'':─<36}\u2510{RESET}")
        self._at(mrow+1, oc, f"{FG_BRIGHT_RED}\u2502{BOLD}{'MTD Controller':^36}{RESET}{FG_BRIGHT_RED}\u2502{RESET}")
        self._at(mrow+2, oc, f"{FG_BRIGHT_RED}\u2502{'PORT_ROTATE  IP_SHUFFLE  KEY_ROTATE':^36}\u2502{RESET}")
        self._at(mrow+3, oc, f"{FG_BRIGHT_RED}\u2514{'':─<36}\u2518{RESET}")

        # Honey network side/bottom borders
        for ri in range(2, HN_H - 1):
            self._at(r + ri, c, f"{FG_CYAN}\u2502")
            self._at(r + ri, c + w - 1, f"{FG_CYAN}\u2502{RESET}")
        self._at(r + HN_H - 1, c, f"{FG_CYAN}\u2514{'':─<{w-2}}\u2518{RESET}")

    def _draw_stats_frame(self) -> None:
        r = STATS_ROW
        c = STATS_COL
        self._at(r, c,   f"{BOLD}{FG_BRIGHT_WHITE}\u250c{'':─<{STATS_W-2}}\u2510{RESET}")
        self._at(r+1, c, f"{BOLD}{FG_BRIGHT_WHITE}\u2502{'PACKETS':^{STATS_W-2}}\u2502{RESET}")
        for i in range(2, 14):
            self._at(r+i, c, f"{FG_WHITE}\u2502{' '*(STATS_W-2)}\u2502{RESET}")
        self._at(r+14, c, f"{FG_WHITE}\u2514{'':─<{STATS_W-2}}\u2518{RESET}")

    def _draw_ticker_frame(self) -> None:
        r = TICKER_ROW
        c = TICKER_COL
        self._at(r, c, f"{BOLD}{FG_BRIGHT_YELLOW}\u2500\u2500 Event Ticker {'':─<60}{RESET}")
        for i in range(TICKER_SIZE):
            self._at(r + 1 + i, c, clrline())

    # ── incremental update methods (called each frame) ──

    def update_attack_level(self, level: int) -> None:
        r = ATK_ROW + 4
        c = ATK_COL
        self._at(r, c, f"{FG_RED}{'':>1}\u2502{BOLD}{FG_BRIGHT_RED} L{level} EXPLOIT  {RESET}{FG_RED}\u2502{RESET}")

    def update_stats(self, stats: Stats, now: float) -> None:
        r = STATS_ROW
        c = STATS_COL
        sw = STATS_W - 4

        def _line(row: int, text: str) -> None:
            padded = f" {text:<{STATS_W-3}}"[:STATS_W - 2]
            self._at(row, c, f"{FG_WHITE}\u2502{padded}\u2502{RESET}")

        _line(r+2, f"{FG_BRIGHT_WHITE}total: {stats.total}{RESET}")
        _line(r+3, f"{FG_CYAN}mavlink: {stats.by_proto.get('mavlink', 0)}{RESET}")
        _line(r+4, f"{FG_GREEN}http: {stats.by_proto.get('http', 0)}{RESET}")
        _line(r+5, f"{FG_YELLOW}ws: {stats.by_proto.get('websocket', 0)}{RESET}")
        _line(r+6, f"{FG_MAGENTA}rtsp: {stats.by_proto.get('rtsp', 0)}{RESET}")

        _line(r+7, "")
        dur = int(now - stats.level_start_ts) if stats.level_start_ts > 0 else 0
        _line(r+8, f"{BOLD}ATTACK LEVEL{RESET}")
        _line(r+9, f" current: L{stats.current_level}")
        _line(r+10, f" duration: {dur}s")

        _line(r+11, "")
        _line(r+12, f"{BOLD}MTD EVENTS{RESET}")
        mtd_str_parts: List[str] = []
        for k, v in stats.mtd_counts.items():
            mtd_str_parts.append(f"{k}:{v}")
        mtd_str = " ".join(mtd_str_parts) if mtd_str_parts else "(none)"
        _line(r+13, f" {mtd_str}")

    def update_ticker(self) -> None:
        r = TICKER_ROW
        c = TICKER_COL
        for i, line in enumerate(self.ticker_lines[-TICKER_SIZE:]):
            self._at(r + 1 + i, c, clrline() + line)
        # clear remaining rows if fewer events
        shown = len(self.ticker_lines[-TICKER_SIZE:])
        for i in range(shown, TICKER_SIZE):
            self._at(r + 1 + i, c, clrline())

    def add_ticker_event(self, text: str) -> None:
        self.ticker_lines.append(text)
        if len(self.ticker_lines) > 200:
            self.ticker_lines = self.ticker_lines[-100:]

    # ── packet animation ──

    def start_anim(self, event: PacketEvent) -> None:
        self.anim_packets.append(AnimPacket(
            event=event, frame=0, total_frames=ANIM_FRAMES,
        ))

    def tick_anims(self, now: float) -> None:
        surviving: List[AnimPacket] = []
        for ap in self.anim_packets:
            self._draw_anim_frame(ap, now)
            ap.frame += 1
            if ap.frame < ap.total_frames:
                surviving.append(ap)
            else:
                # flash destination
                self._flash_dest(ap.event, now)
        self.anim_packets = surviving

        # Handle flash expiry
        expired_keys: List[str] = []
        for key, expire in self.flash_nodes.items():
            if now >= expire:
                expired_keys.append(key)
                self._unflash_node(key)
        for key in expired_keys:
            del self.flash_nodes[key]

    def _draw_anim_frame(self, ap: AnimPacket, now: float) -> None:
        ev = ap.event
        color = PROTO_COLOR.get(ev.proto, FG_WHITE)
        frac = ap.frame / max(ap.total_frames - 1, 1)

        # Determine arrow row — map drone_idx to one of the arrow rows
        row_idx = min(ev.drone_idx, len(ARROW_ROWS) - 1)
        row = ARROW_ROWS[row_idx]

        col_start = ARROW_COL_START
        col_end = ARROW_COL_END

        if ev.direction == "fwd":
            col = col_start + int(frac * (col_end - col_start))
            symbol = "\u25cf"  # ●
            label = ev.msg_type[:10]
            self._at(row, col, f"{color}{BOLD}{symbol}{RESET}")
            # show label above if first frame
            if ap.frame == 0:
                label_row = row - 1 if row > TOPO_TOP else row + 1
                trunc = label[:14]
                self._at(label_row, col_start, f"{color}{DIM}{trunc:<14}{RESET}")
                # flash source (attacker)
                self.flash_nodes["attacker"] = now + 0.3
                self._flash_attacker()
        else:
            col = col_end - int(frac * (col_end - col_start))
            symbol = "\u25cf"
            self._at(row, col, f"{color}{BOLD}{symbol}{RESET}")

        # Erase previous frame position (draw the normal arrow char back)
        if ap.frame > 0:
            prev_frac = (ap.frame - 1) / max(ap.total_frames - 1, 1)
            if ev.direction == "fwd":
                prev_col = col_start + int(prev_frac * (col_end - col_start))
            else:
                prev_col = col_end - int(prev_frac * (col_end - col_start))
            if prev_col != col:
                self._at(row, prev_col, f"{DIM}\u2500{RESET}")

    def _flash_attacker(self) -> None:
        self._at(ATK_ROW + 1, ATK_COL + 1, f"{BG_RED}{BOLD}{FG_WHITE}  ATTACKER   {RESET}")

    def _unflash_attacker(self) -> None:
        self._at(ATK_ROW + 1, ATK_COL + 1, f"{FG_RED}\u2502{BOLD}  ATTACKER   {RESET}")

    def _flash_dest(self, ev: PacketEvent, now: float) -> None:
        di = ev.drone_idx
        key = f"drone_{di}"
        self.flash_nodes[key] = now + 0.3
        dc = DRONE_COLS[di]
        dr = DRONE_BOX_ROW
        name = DRONE_NAMES[di]
        self._at(dr + 1, dc, f"{BG_CYAN}{BOLD}{FG_BLACK}\u2502 {name:<9}{RESET}{BG_CYAN}\u2502{RESET}")

    def _unflash_node(self, key: str) -> None:
        if key == "attacker":
            self._unflash_attacker()
            return
        if key.startswith("drone_"):
            di = int(key.split("_")[1])
            dc = DRONE_COLS[di]
            dr = DRONE_BOX_ROW
            name = DRONE_NAMES[di]
            self._at(dr + 1, dc, f"{FG_BRIGHT_WHITE}\u2502{BOLD} {name:<9}{RESET}{FG_BRIGHT_WHITE}\u2502{RESET}")


# ── Replay engine ─────────────────────────────────────────────────────────────

def _format_ts(ts: float) -> str:
    t = time.localtime(ts)
    return time.strftime("%H:%M:%S", t)


def _ticker_for_packet(ev: PacketEvent) -> str:
    ts_s = _format_ts(ev.ts)
    color = PROTO_COLOR.get(ev.proto, FG_WHITE)
    proto_lbl = PROTO_LABEL.get(ev.proto, "?")
    arrow = "\u2500\u2500\u25b6" if ev.direction == "fwd" else "\u25c0\u2500\u2500"
    drone = DRONE_NAMES[ev.drone_idx]
    extra_str = ""
    if ev.extra:
        extra_str = f" {DIM}({ev.extra[:40]}){RESET}"
    return (
        f"{FG_WHITE}{ts_s} "
        f"{color}[L{ev.level}] {ev.msg_type} {arrow} {drone}{RESET}"
        f"{extra_str}"
    )


def _ticker_for_mtd(me: MtdEvent) -> str:
    ts_s = _format_ts(me.ts)
    return (
        f"{FG_WHITE}{ts_s} "
        f"{FG_BRIGHT_RED}[MTD] {me.action_type} {me.drone_id} "
        f"{me.old_port}\u2192{me.new_port} "
        f"({me.old_ip}\u2192{me.new_ip}){RESET}"
    )


def run_replay(data_dir: str) -> None:
    attacker_log = os.path.join(data_dir, "results", "attacker_log.jsonl")
    trace_path = os.path.join(data_dir, "omnetpp_trace", "traffic_trace.csv")
    mtd_path = os.path.join(data_dir, "omnetpp_trace", "mtd_events.csv")

    # Load attacker log
    atk_events = load_attacker_log(attacker_log)
    base_ts = atk_events[0].ts if atk_events else time.time()

    # Load traffic trace
    traf_events: List[PacketEvent] = []
    if os.path.exists(trace_path):
        traf_events = load_traffic_trace(trace_path, base_ts)

    # Load MTD events
    mtd_events: List[MtdEvent] = []
    if os.path.exists(mtd_path):
        mtd_events = load_mtd_events(mtd_path, base_ts)

    # Merge all packet events and sort
    all_events: List[PacketEvent] = sorted(atk_events + traf_events, key=lambda e: e.ts)

    # Also merge MTD into a unified timeline
    # We'll handle them separately keyed by time.
    mtd_iter_idx = 0
    mtd_events_sorted = sorted(mtd_events, key=lambda m: m.ts)

    if not all_events:
        print("No events found. Check data paths.")
        return

    renderer = Renderer()
    stats = renderer.stats
    stats.level_start_ts = all_events[0].ts

    renderer.draw_static()

    sim_start = all_events[0].ts
    wall_start = time.monotonic()
    evt_idx = 0

    try:
        while evt_idx < len(all_events) or renderer.anim_packets:
            frame_start = time.monotonic()
            wall_elapsed = frame_start - wall_start
            sim_now = sim_start + wall_elapsed * REPLAY_SPEED

            # Fire events whose sim-time has arrived
            fired = 0
            while evt_idx < len(all_events) and all_events[evt_idx].ts <= sim_now:
                ev = all_events[evt_idx]
                evt_idx += 1
                fired += 1

                stats.total += 1
                stats.by_proto[ev.proto.value] += 1
                if ev.level != stats.current_level:
                    stats.current_level = ev.level
                    stats.level_start_ts = ev.ts

                renderer.start_anim(ev)
                renderer.add_ticker_event(_ticker_for_packet(ev))

                # cap to avoid flooding frames
                if fired > 5:
                    break

            # Fire MTD events
            while (mtd_iter_idx < len(mtd_events_sorted)
                   and mtd_events_sorted[mtd_iter_idx].ts <= sim_now):
                me = mtd_events_sorted[mtd_iter_idx]
                mtd_iter_idx += 1
                stats.mtd_counts[me.action_type] += 1
                renderer.add_ticker_event(_ticker_for_mtd(me))

            # Tick animations
            renderer.tick_anims(time.monotonic())

            # Update stats
            renderer.update_attack_level(stats.current_level)
            renderer.update_stats(stats, sim_now)
            renderer.update_ticker()

            renderer.flush()

            # Sleep to maintain FPS
            elapsed = time.monotonic() - frame_start
            sleep_time = FRAME_DT - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET + "\n")
        sys.stdout.flush()

    print(f"\nReplay complete. {stats.total} packets visualised.")


# ── Live mode ─────────────────────────────────────────────────────────────────

def run_live(data_dir: str) -> None:
    """Listen on UDP 19996-19999 for JSON event packets."""
    ports = [19996, 19997, 19998, 19999]
    socks: List[socket.socket] = []
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.setblocking(False)
        socks.append(s)

    renderer = Renderer()
    stats = renderer.stats
    stats.level_start_ts = time.time()

    renderer.draw_static()

    try:
        while True:
            frame_start = time.monotonic()
            now = time.time()

            # Poll sockets
            readable, _, _ = select.select(socks, [], [], 0)
            for s in readable:
                try:
                    data, addr = s.recvfrom(4096)
                    msg = json.loads(data.decode("utf-8", errors="replace"))
                    action = msg.get("action", "")
                    proto, msg_type = _classify_action(action)
                    target = msg.get("target", "")
                    dst_ip = DRONE_IPS[0]
                    dst_port = 14550
                    if ":" in target:
                        parts = target.rsplit(":", 1)
                        dst_ip = parts[0]
                        try:
                            dst_port = int(parts[1])
                        except ValueError:
                            pass
                    didx = _drone_index(dst_ip)
                    level = int(msg.get("level", 0))
                    ev = PacketEvent(
                        ts=now, proto=proto, msg_type=msg_type,
                        src_ip=ATTACKER_IP, dst_ip=dst_ip,
                        dst_port=dst_port, direction="fwd",
                        level=level,
                        extra=str(msg.get("response_preview", ""))[:40],
                        drone_idx=didx,
                    )
                    stats.total += 1
                    stats.by_proto[ev.proto.value] += 1
                    if ev.level != stats.current_level:
                        stats.current_level = ev.level
                        stats.level_start_ts = now
                    renderer.start_anim(ev)
                    renderer.add_ticker_event(_ticker_for_packet(ev))
                except Exception:
                    pass

            renderer.tick_anims(time.monotonic())
            renderer.update_attack_level(stats.current_level)
            renderer.update_stats(stats, time.time())
            renderer.update_ticker()
            renderer.flush()

            elapsed = time.monotonic() - frame_start
            sleep_time = FRAME_DT - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        pass
    finally:
        for s in socks:
            s.close()
        sys.stdout.write(SHOW_CURSOR + RESET + "\n")
        sys.stdout.flush()

    print(f"\nLive session ended. {stats.total} packets received.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MIRAGE-UAS OMNeT++ style packet flow animation",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Live mode: listen on UDP 19996-19999",
    )
    parser.add_argument(
        "--replay", action="store_true", default=True,
        help="Replay mode (default): read from attacker_log.jsonl",
    )
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help="Root directory of the mirage-uas project",
    )
    parser.add_argument(
        "--speed", type=float, default=10.0,
        help="Replay speed multiplier (default: 10)",
    )
    args = parser.parse_args()

    global REPLAY_SPEED
    REPLAY_SPEED = args.speed

    # Resolve data directory
    data_dir = args.data_dir
    if data_dir is None:
        # Try to find it relative to this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.normpath(os.path.join(script_dir, "..", ".."))
        if os.path.isfile(os.path.join(candidate, "results", "attacker_log.jsonl")):
            data_dir = candidate
        else:
            data_dir = os.getcwd()

    # Install SIGWINCH handler for terminal resize
    def _on_resize(signum: int, frame: object) -> None:
        pass  # renderer will re-query on next frame if needed

    if hasattr(signal, "SIGWINCH"):
        signal.signal(signal.SIGWINCH, _on_resize)

    if args.live:
        run_live(data_dir)
    else:
        run_replay(data_dir)


if __name__ == "__main__":
    main()
