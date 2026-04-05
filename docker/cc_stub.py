#!/usr/bin/env python3
"""
cc_stub.py — Stub DVD Companion Computer

[ROLE] MAVLink UDP :14550 + HTTP :80 + RTSP :8554 for testing.
       Forwards MAVLink copy to CTI interceptor UDP :19551.
[DATA FLOW]
    Attacker UDP :14550 → respond + forward copy to :19551
    HTTP :80 → /api/v1/params, /status, /mission, /arm, /health
    RTSP :8554 → OPTIONS/DESCRIBE SDP response
"""
import asyncio
import json
import os
import random
import socket
import struct
import time

from aiohttp import web

DRONE_ID = os.environ.get("DRONE_ID", "honey_01")
_INTERCEPT_PORT = 19551  # CTI interceptor forward port

# MAVLink params for API responses
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


def _log(event, **kw):
    record = {"timestamp": time.time(), "drone_id": DRONE_ID, "event": event, **kw}
    print(json.dumps(record), flush=True)


def _heartbeat_bytes():
    """[ROLE] MAVLink v2 HEARTBEAT payload bytes."""
    base_mode = 0x01 | (0x80 if _armed else 0x00)
    return struct.pack("<IBBBBB", 0, 2, 3, base_mode, 3 if not _armed else 4, 3)


# ── MAVLink UDP :14550 ────────────────────────────────────────────────────────

async def mavlink_responder():
    """[ROLE] UDP MAVLink responder + forward copy to CTI interceptor."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 14550))
    sock.setblocking(False)
    loop = asyncio.get_event_loop()
    _log("mavlink_started", port=14550)

    # Forward socket to CTI interceptor
    fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fwd_sock.setblocking(False)

    while True:
        try:
            data, addr = await loop.sock_recvfrom(sock, 2048)
            _log("mavlink_recv", src=f"{addr[0]}:{addr[1]}", size=len(data),
                 hex=data[:16].hex())

            # Forward copy to CTI interceptor (best-effort)
            try:
                await loop.sock_sendto(fwd_sock, data, ("cti-interceptor", _INTERCEPT_PORT))
            except OSError:
                pass

            # Respond with heartbeat
            hb = _heartbeat_bytes()
            await loop.sock_sendto(sock, hb, addr)

            # If short payload (PARAM_REQUEST), send param values
            if len(data) <= 8:
                for i, p in enumerate(_PARAMS[:3]):
                    name_b = p["name"].encode()[:16].ljust(16, b"\x00")
                    pv = struct.pack("<f", p["value"]) + name_b + struct.pack(
                        "<BHH", 9, len(_PARAMS), i
                    )
                    await loop.sock_sendto(sock, pv, addr)

            # If looks like COMMAND_LONG (>=14 bytes), send ACK
            if len(data) >= 14:
                try:
                    cmd = struct.unpack_from("<H", data, 0)[0]
                    ack = struct.pack("<HB", cmd, 0)  # MAV_RESULT_ACCEPTED
                    await loop.sock_sendto(sock, ack, addr)
                except struct.error:
                    pass

        except asyncio.CancelledError:
            break
        except Exception as e:
            _log("mavlink_error", error=str(e))
            await asyncio.sleep(0.1)


# ── HTTP :80 ──────────────────────────────────────────────────────────────────

async def health(request):
    return web.Response(text="OK")


async def api_params(request):
    _log("http_params_request")
    return web.json_response({"params": _PARAMS})


async def api_status(request):
    _log("http_status_request")
    return web.json_response({
        "drone_id": DRONE_ID, "armed": _armed, "mode": "STABILIZE",
        "battery": random.randint(60, 95), "gps_fix": 3, "satellites": random.randint(8, 14),
    })


async def api_mission(request):
    _log("http_mission_request")
    return web.json_response({"mission": _MISSION})


async def api_arm(request):
    global _armed
    _armed = True
    _log("http_arm_request", armed=True)
    return web.json_response({"result": "accepted", "armed": True})


async def api_login(request):
    body = await request.json() if request.content_length else {}
    _log("http_login_attempt", username=body.get("username", ""))
    return web.json_response({"authenticated": True, "token": "fake-" + DRONE_ID})


async def start_http():
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/params", api_params)
    app.router.add_get("/api/v1/status", api_status)
    app.router.add_get("/api/v1/mission", api_mission)
    app.router.add_post("/api/v1/arm", api_arm)
    app.router.add_post("/login", api_login)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 80)
    await site.start()
    _log("http_started", port=80)


# ── RTSP :8554 ───���────────────────────────────────────────────────────────────

_SDP = (
    "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=ArduPilot Camera\r\n"
    "t=0 0\r\nm=video 0 RTP/AVP 96\r\n"
    "a=rtpmap:96 H264/90000\r\n"
)

async def rtsp_handler(reader, writer):
    try:
        data = await asyncio.wait_for(reader.read(2048), timeout=5.0)
        req = data.decode(errors="ignore")
        _log("rtsp_request", preview=req[:80])

        if "OPTIONS" in req:
            writer.write(b"RTSP/1.0 200 OK\r\nPublic: OPTIONS, DESCRIBE, SETUP, PLAY\r\n\r\n")
        elif "DESCRIBE" in req:
            writer.write(f"RTSP/1.0 200 OK\r\nContent-Type: application/sdp\r\nContent-Length: {len(_SDP)}\r\n\r\n{_SDP}".encode())
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


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    _log("cc_stub_started", drone_id=DRONE_ID)
    await start_http()
    await asyncio.gather(mavlink_responder(), start_rtsp())


if __name__ == "__main__":
    asyncio.run(main())
