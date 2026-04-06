#!/usr/bin/env python3
"""
cc_stub.py — Stub DVD Companion Computer (breadcrumbs + ghost services + WebSocket)

[ROLE] Full honey drone emulation for test harness:
    MAVLink UDP :14550 — responder + breadcrumb STATUSTEXT injection
    HTTP :80           — fake ArduPilot Web UI with embedded breadcrumbs
    RTSP :8554         — camera stream SDP
    WebSocket :18789   — OpenClaw gateway emulation (CVE-2026-25253)
    Ghost TCP :19000+  — 3 ghost telemetry services
    Ghost UDP :14560+  — secondary MAVLink ghost (different sysid)
    Metrics :9000+     — /internal/metrics for DeceptionMonitor

[DATA FLOW]
    Attacker → any port → respond with breadcrumbs → track metrics
"""
import asyncio
import base64
import hashlib
import json
import os
import random
import socket
import struct
import time

from aiohttp import web

DRONE_ID = os.environ.get("DRONE_ID", "honey_01")
_INDEX = int(DRONE_ID.split("_")[-1]) if "_" in DRONE_ID else 1
_INTERCEPT_PORT = 19551
_ENGINE_HOST = os.environ.get("ENGINE_HOST", "")  # host.docker.internal if set
_ENGINE_PORT = int(os.environ.get("ENGINE_PORT", "0"))  # 14551/52/53 per drone

# ── Breadcrumb generation ─────────────────────────────────────────────────────
_SIGNING_KEY = hashlib.md5(DRONE_ID.encode()).hexdigest()
_API_TOKEN = f"sk-drone-{hashlib.sha256(DRONE_ID.encode()).hexdigest()[:16]}"
_MISSION_KEY = hashlib.md5(b"mission").hexdigest()

# ── Metrics tracking ──────────────────────────────────────────────────────────
_metrics = {
    "breadcrumbs_planted": 0,
    "breadcrumbs_followed": 0,
    "ghost_connections": 0,
    "ghost_services_active": 3,
    "total_connections": 0,
    "ws_connections": 0,
    "mavlink_responses": 0,
    "http_requests": 0,
}

_PARAMS = [
    {"name": "ARMING_CHECK", "value": 1.0},
    {"name": "RTL_ALT", "value": 1500.0},
    {"name": "BATT_CAPACITY", "value": 5200.0},
    {"name": "WPNAV_SPEED", "value": 500.0},
    {"name": "COMPASS_USE", "value": 1.0},
    {"name": "GPS_TYPE", "value": 1.0},
    {"name": "FENCE_ENABLE", "value": 1.0},
    {"name": "SYSID_MYGCS", "value": 255.0},
]

_MISSION = [
    {"seq": 0, "command": 22, "lat": 37.5665, "lon": 126.978, "alt": 50.0},
    {"seq": 1, "command": 16, "lat": 37.5670, "lon": 126.979, "alt": 80.0},
    {"seq": 2, "command": 16, "lat": 37.5680, "lon": 126.977, "alt": 100.0},
    {"seq": 3, "command": 21, "lat": 37.5665, "lon": 126.978, "alt": 0.0},
]

_armed = False
_mavlink_counter = 0
_STATUSTEXT_BREADCRUMBS = [
    f"[CFG] admin_pass=dr0ne@dm1n2026",
    f"[CFG] gcs_backup=172.40.0.{10 + _INDEX}:14560",
    f"[KEY] signing={_SIGNING_KEY[:8]}",
]


def _log(event, **kw):
    record = {"timestamp": time.time(), "drone_id": DRONE_ID, "event": event, **kw}
    print(json.dumps(record), flush=True)


def _heartbeat_bytes():
    base_mode = 0x01 | (0x80 if _armed else 0x00)
    return struct.pack("<IBBBBB", 0, 2, 3, base_mode, 3 if not _armed else 4, 3)


def _statustext_bytes(text):
    """[ROLE] MAVLink STATUSTEXT payload: severity(u8) + text(50 bytes)."""
    text_b = text.encode("ascii")[:50].ljust(50, b"\x00")
    return struct.pack("<B", 6) + text_b  # severity=INFO


# ── MAVLink UDP :14550 ────────────────────────────────────────────────────────

async def _forward_to_engine(loop, data, engine_host, engine_port):
    """[ROLE] Forward MAVLink to real OpenClawAgent on host, return response or None."""
    try:
        fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        fwd.setblocking(False)
        await loop.sock_sendto(fwd, data, (engine_host, engine_port))
        response = await asyncio.wait_for(loop.sock_recv(fwd, 4096), timeout=1.0)
        fwd.close()
        return response
    except Exception:
        try:
            fwd.close()
        except Exception:
            pass
        return None


async def mavlink_responder():
    """[ROLE] MAVLink UDP handler — forwards to real engine if available, else stub."""
    global _mavlink_counter
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 14550))
    sock.setblocking(False)
    loop = asyncio.get_event_loop()

    engine_mode = "real" if _ENGINE_HOST and _ENGINE_PORT else "stub"
    _log("mavlink_started", port=14550, engine_mode=engine_mode,
         engine=f"{_ENGINE_HOST}:{_ENGINE_PORT}" if engine_mode == "real" else "none")

    fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fwd_sock.setblocking(False)

    while True:
        try:
            data, addr = await loop.sock_recvfrom(sock, 2048)
            _metrics["total_connections"] += 1
            _metrics["mavlink_responses"] += 1

            # Forward copy to CTI interceptor (best-effort)
            try:
                await loop.sock_sendto(fwd_sock, data, ("cti-interceptor", _INTERCEPT_PORT))
            except OSError:
                pass

            # ── TRY REAL ENGINE FIRST ──
            response = None
            if engine_mode == "real":
                response = await _forward_to_engine(loop, data, _ENGINE_HOST, _ENGINE_PORT)

            if response:
                # Real OpenClawAgent responded
                await loop.sock_sendto(sock, response, addr)
                _metrics["engine_responses"] = _metrics.get("engine_responses", 0) + 1
            else:
                # ── STUB FALLBACK ──
                await loop.sock_sendto(sock, _heartbeat_bytes(), addr)

                # Every 10th packet: breadcrumb STATUSTEXT
                _mavlink_counter += 1
                if _mavlink_counter % 10 == 0:
                    msg = _STATUSTEXT_BREADCRUMBS[(_mavlink_counter // 10) % len(_STATUSTEXT_BREADCRUMBS)]
                    await loop.sock_sendto(sock, _statustext_bytes(msg), addr)
                    _metrics["breadcrumbs_planted"] += 1

                # PARAM_REQUEST → send params
                if len(data) <= 8:
                    for i, p in enumerate(_PARAMS[:3]):
                        name_b = p["name"].encode()[:16].ljust(16, b"\x00")
                        pv = struct.pack("<f", p["value"]) + name_b + struct.pack("<BHH", 9, len(_PARAMS), i)
                        await loop.sock_sendto(sock, pv, addr)

                # COMMAND_LONG → send ACK
                if len(data) >= 14:
                    try:
                        cmd = struct.unpack_from("<H", data, 0)[0]
                        await loop.sock_sendto(sock, struct.pack("<HB", cmd, 0), addr)
                    except struct.error:
                        pass

        except asyncio.CancelledError:
            break
        except Exception as e:
            _log("mavlink_error", error=str(e))
            await asyncio.sleep(0.1)


# ── HTTP :80 (with breadcrumbs) ───────────────────────────────────────────────

async def health(request):
    return web.Response(text="OK")


async def api_params(request):
    _metrics["http_requests"] += 1
    _metrics["breadcrumbs_planted"] += 1
    _log("http_params_request")
    return web.json_response({
        "params": _PARAMS,
        "ssh_password": "companion_root_2026",
        "signing_key": _SIGNING_KEY,
        "api_token": _API_TOKEN,
    })


async def api_status(request):
    _metrics["http_requests"] += 1
    _metrics["breadcrumbs_planted"] += 1
    _log("http_status_request")
    return web.json_response({
        "drone_id": DRONE_ID, "armed": _armed, "mode": "STABILIZE",
        "battery": random.randint(60, 95), "gps_fix": 3,
        "satellites": random.randint(8, 14),
        "backup_gcs": f"172.40.0.{10 + _INDEX}:14560",
        "config_endpoint": f"http://172.40.0.{10 + _INDEX}:19042/config",
        "fleet_c2": "172.40.0.100:4444",
    })


async def api_mission(request):
    _metrics["http_requests"] += 1
    _metrics["breadcrumbs_planted"] += 1
    _log("http_mission_request")
    return web.json_response({
        "mission": _MISSION,
        "mission_key": _MISSION_KEY,
        "upload_endpoint": f"http://172.40.0.{10 + _INDEX}:8765/upload",
    })


async def api_arm(request):
    global _armed
    _armed = True
    _metrics["http_requests"] += 1
    _log("http_arm_request", armed=True)
    return web.json_response({"result": "accepted", "armed": True})


async def api_login(request):
    _metrics["http_requests"] += 1
    body = await request.json() if request.content_length else {}
    _log("http_login_attempt", username=body.get("username", ""))
    return web.json_response({"authenticated": True, "token": _API_TOKEN})


async def breadcrumb_endpoint(request):
    """[ROLE] Breadcrumb lure — tracks when attacker follows planted endpoints."""
    _metrics["breadcrumbs_followed"] += 1
    _log("breadcrumb_followed", path=request.path)
    return web.json_response({"type": "config", "drone": DRONE_ID, "key": _SIGNING_KEY})


async def internal_metrics(request):
    """[ROLE] Internal metrics for DeceptionMonitor."""
    return web.json_response(_metrics)


async def start_http():
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/params", api_params)
    app.router.add_get("/api/v1/status", api_status)
    app.router.add_get("/api/v1/mission", api_mission)
    app.router.add_post("/api/v1/arm", api_arm)
    app.router.add_post("/login", api_login)
    # Breadcrumb lure endpoints
    app.router.add_get("/config", breadcrumb_endpoint)
    app.router.add_get("/lure", breadcrumb_endpoint)
    app.router.add_get("/upload", breadcrumb_endpoint)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 80).start()
    _log("http_started", port=80)

    # Metrics endpoint on 9000+index
    metrics_app = web.Application()
    metrics_app.router.add_get("/internal/metrics", internal_metrics)
    metrics_runner = web.AppRunner(metrics_app)
    await metrics_runner.setup()
    metrics_port = 9000 + _INDEX
    await web.TCPSite(metrics_runner, "0.0.0.0", metrics_port).start()
    _log("metrics_started", port=metrics_port)


# ── RTSP :8554 ────────────────────────────────────────────────────────────────

_SDP = (
    "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=ArduPilot Camera\r\n"
    "t=0 0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
)

async def rtsp_handler(reader, writer):
    try:
        data = await asyncio.wait_for(reader.read(2048), timeout=5.0)
        _metrics["total_connections"] += 1
        req = data.decode(errors="ignore")
        if "OPTIONS" in req:
            writer.write(b"RTSP/1.0 200 OK\r\nPublic: OPTIONS, DESCRIBE, SETUP, PLAY\r\n\r\n")
        elif "DESCRIBE" in req:
            body = _SDP.encode()
            writer.write(f"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        else:
            writer.write(b"RTSP/1.0 200 OK\r\n\r\n")
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()

async def start_rtsp():
    server = await asyncio.start_server(rtsp_handler, "0.0.0.0", 8554)
    _log("rtsp_started", port=8554)
    await server.serve_forever()


# ── WebSocket :18789 (raw TCP handshake — no websockets lib needed) ───────────

async def ws_client_handler(reader, writer):
    """[ROLE] OpenClaw WebSocket emulation with CVE-2026-25253 detection."""
    _metrics["total_connections"] += 1
    _metrics["ws_connections"] += 1
    try:
        req_data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        req = req_data.decode(errors="ignore")

        if "Upgrade: websocket" not in req and "upgrade: websocket" not in req:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        # Extract Sec-WebSocket-Key for handshake
        ws_key = ""
        cve_detected = False
        for line in req.split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                ws_key = line.split(":", 1)[1].strip()
            if "origin: null" in line.lower() or "127.0.0.1" in line.lower():
                cve_detected = True

        # Compute accept key (RFC 6455)
        magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        accept_raw = hashlib.sha1((ws_key + magic).encode()).digest()
        accept_key = base64.b64encode(accept_raw).decode()

        # Send 101 Switching Protocols
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_key}\r\n\r\n"
        )
        writer.write(response.encode())
        await writer.drain()
        _log("ws_connected", cve_detected=cve_detected)

        # Build response payload
        if cve_detected:
            payload = json.dumps({
                "type": "auth_result", "authenticated": True,
                "version": "2026.1.28",
                "permissions": ["shell_execute", "config_write", "mission_upload"],
                "signing_key_fragment": _SIGNING_KEY[:16],
                "token": _API_TOKEN,
            })
        else:
            payload = json.dumps({
                "type": "ack", "version": "2026.1.28",
                "message": "OpenClaw gateway ready",
                "drone_id": DRONE_ID,
            })

        # Send WebSocket text frame (opcode=0x81)
        payload_bytes = payload.encode()
        if len(payload_bytes) < 126:
            frame = bytes([0x81, len(payload_bytes)]) + payload_bytes
        else:
            frame = bytes([0x81, 126]) + struct.pack(">H", len(payload_bytes)) + payload_bytes
        writer.write(frame)
        await writer.drain()
        _metrics["breadcrumbs_planted"] += 1

        # Read any further frames (attacker may send skill_invoke etc.)
        try:
            while True:
                frame_data = await asyncio.wait_for(reader.read(4096), timeout=10.0)
                if not frame_data:
                    break
                _metrics["total_connections"] += 1
                # Send ack for any message
                ack = json.dumps({"type": "skill_result", "status": "success",
                                  "data": {"altitude": random.randint(50, 150),
                                           "battery": random.randint(40, 90),
                                           "signing_key": _SIGNING_KEY}}).encode()
                if len(ack) < 126:
                    writer.write(bytes([0x81, len(ack)]) + ack)
                else:
                    writer.write(bytes([0x81, 126]) + struct.pack(">H", len(ack)) + ack)
                await writer.drain()
                _metrics["breadcrumbs_planted"] += 1
        except (asyncio.TimeoutError, ConnectionResetError):
            pass

    except Exception as e:
        _log("ws_error", error=str(e))
    finally:
        writer.close()

async def start_websocket():
    server = await asyncio.start_server(ws_client_handler, "0.0.0.0", 18789)
    _log("websocket_started", port=18789)
    await server.serve_forever()


# ── Ghost TCP services :19000+ ────────────────────────────────────────────────

async def ghost_tcp_handler(reader, writer, port):
    _metrics["ghost_connections"] += 1
    _metrics["total_connections"] += 1
    _log("ghost_connection", port=port)
    try:
        telemetry = json.dumps({
            "type": "ghost_telemetry", "drone": DRONE_ID, "port": port,
            "armed": False, "altitude": random.randint(50, 150),
            "firmware": "ArduCopter V4.3.7",
        }).encode() + b"\n"
        writer.write(telemetry)
        await writer.drain()

        # Wait for attacker data
        try:
            data = await asyncio.wait_for(reader.read(2048), timeout=5.0)
            if data:
                ack = json.dumps({
                    "type": "ack", "authenticated": True,
                    "permissions": ["read", "write"],
                }).encode() + b"\n"
                writer.write(ack)
                await writer.drain()
                _metrics["breadcrumbs_followed"] += 1
        except asyncio.TimeoutError:
            pass
    except Exception:
        pass
    finally:
        writer.close()

async def start_ghost_services():
    for offset in range(3):
        port = 19000 + _INDEX * 3 + offset
        server = await asyncio.start_server(
            lambda r, w, p=port: ghost_tcp_handler(r, w, p),
            "0.0.0.0", port,
        )
        _log("ghost_tcp_started", port=port)


# ── Ghost UDP MAVLink :14560+ (secondary sysid) ──────────────────────────────

async def ghost_mavlink_broadcaster():
    ghost_port = 14560 + _INDEX
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", ghost_port))
    sock.setblocking(False)
    loop = asyncio.get_event_loop()
    _log("ghost_mavlink_started", port=ghost_port, sysid=50 + _INDEX)

    while True:
        try:
            # Listen for probes and respond
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 2048), timeout=2.0
                )
                _metrics["ghost_connections"] += 1
                # Different sysid than main drone
                ghost_hb = struct.pack("<IBBBBB", 0, 2, 3, 0x01, 3, 3)
                await loop.sock_sendto(sock, ghost_hb, addr)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(1.0)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    _log("cc_stub_started", drone_id=DRONE_ID, index=_INDEX)
    await start_http()
    await start_ghost_services()
    await asyncio.gather(
        mavlink_responder(),
        start_rtsp(),
        start_websocket(),
        ghost_mavlink_broadcaster(),
    )


if __name__ == "__main__":
    asyncio.run(main())
