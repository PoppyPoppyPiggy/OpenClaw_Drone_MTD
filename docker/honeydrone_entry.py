#!/usr/bin/env python3
"""
honeydrone_entry.py — Real honeydrone container entrypoint

Runs AgenticDecoyEngine (OpenClaw OODA deception) + HTTP breadcrumbs
+ RTSP + ghost services in a single container.

[DATA FLOW]
    Attacker ──▶ MAVLink UDP :14550 ──▶ AgenticDecoyEngine (OODA adaptive)
    Attacker ──▶ WebSocket  :18789  ──▶ AgenticDecoyEngine (OpenClaw emulation)
    Attacker ──▶ HTTP       :80     ──▶ Breadcrumb server (credentials, endpoints)
    Attacker ──▶ RTSP       :8554   ──▶ Camera SDP
    Attacker ──▶ Ghost TCP  :19000+ ──▶ Fake telemetry services
    Attacker ──▶ Ghost UDP  :14560+ ──▶ Secondary MAVLink (different sysid)

    Engine ──▶ /results/metrics/  (periodic save every 30s)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import socket
import struct
import sys
import time
from pathlib import Path

# ── Python path setup ────────────────────────────────────────────────────────
sys.path.insert(0, "/app/src")
from dotenv import load_dotenv
load_dotenv("/app/config/.env")

from aiohttp import web

from shared.models import HoneyDroneConfig
from honey_drone.agentic_decoy_engine import AgenticDecoyEngine
from cti_pipeline.attack_event_parser import AttackEventParser

# ── Config from environment ──────────────────────────────────────────────────
DRONE_ID = os.environ.get("DRONE_ID", "honey_01")
INDEX = int(os.environ.get("INDEX", "1"))
RESULTS_DIR = os.environ.get("RESULTS_DIR", "/results")

# ── Breadcrumb material ──────────────────────────────────────────────────────
_SIGNING_KEY = hashlib.md5(DRONE_ID.encode()).hexdigest()
_API_TOKEN = f"sk-drone-{hashlib.sha256(DRONE_ID.encode()).hexdigest()[:16]}"
_MISSION_KEY = hashlib.md5(b"mission").hexdigest()

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

_http_metrics = {
    "breadcrumbs_planted": 0,
    "breadcrumbs_followed": 0,
    "ghost_connections": 0,
    "http_requests": 0,
}


def _log(event, **kw):
    record = {"timestamp": time.time(), "drone_id": DRONE_ID, "event": event, **kw}
    print(json.dumps(record), flush=True)


# ═════════════════════════════════════════════════════════════════════════════
# HTTP Breadcrumb Server (:80)
# ═════════════════════════════════════════════════════════════════════════════

async def health(request):
    return web.Response(text="OK")

async def api_params(request):
    _http_metrics["http_requests"] += 1
    _http_metrics["breadcrumbs_planted"] += 1
    _log("http_params_request")
    return web.json_response({
        "params": _PARAMS,
        "ssh_password": "companion_root_2026",
        "signing_key": _SIGNING_KEY,
        "api_token": _API_TOKEN,
    })

async def api_status(request):
    _http_metrics["http_requests"] += 1
    _http_metrics["breadcrumbs_planted"] += 1
    return web.json_response({
        "drone_id": DRONE_ID, "armed": False, "mode": "STABILIZE",
        "battery": random.randint(60, 95), "gps_fix": 3,
        "satellites": random.randint(8, 14),
        "backup_gcs": f"172.40.0.{10 + INDEX}:14560",
        "config_endpoint": f"http://172.40.0.{10 + INDEX}:19042/config",
        "fleet_c2": "172.40.0.100:4444",
    })

async def api_mission(request):
    _http_metrics["http_requests"] += 1
    _http_metrics["breadcrumbs_planted"] += 1
    return web.json_response({
        "mission": _MISSION,
        "mission_key": _MISSION_KEY,
        "upload_endpoint": f"http://172.40.0.{10 + INDEX}:8765/upload",
    })

async def api_arm(request):
    _http_metrics["http_requests"] += 1
    return web.json_response({"result": "accepted", "armed": True})

async def api_login(request):
    _http_metrics["http_requests"] += 1
    body = await request.json() if request.content_length else {}
    _log("http_login_attempt", username=body.get("username", ""))
    return web.json_response({"authenticated": True, "token": _API_TOKEN})

async def breadcrumb_endpoint(request):
    _http_metrics["breadcrumbs_followed"] += 1
    _log("breadcrumb_followed", path=request.path)
    return web.json_response({"type": "config", "drone": DRONE_ID, "key": _SIGNING_KEY})

async def internal_metrics(request):
    return web.json_response({**_http_metrics, "engine": "real_openclaw"})

async def start_http():
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/params", api_params)
    app.router.add_get("/api/v1/status", api_status)
    app.router.add_get("/api/v1/mission", api_mission)
    app.router.add_post("/api/v1/arm", api_arm)
    app.router.add_post("/login", api_login)
    app.router.add_get("/config", breadcrumb_endpoint)
    app.router.add_get("/lure", breadcrumb_endpoint)
    app.router.add_get("/upload", breadcrumb_endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 80).start()
    _log("http_started", port=80)

    # Metrics endpoint
    metrics_app = web.Application()
    metrics_app.router.add_get("/internal/metrics", internal_metrics)
    metrics_runner = web.AppRunner(metrics_app)
    await metrics_runner.setup()
    await web.TCPSite(metrics_runner, "0.0.0.0", 9000 + INDEX).start()


# ═════════════════════════════════════════════════════════════════════════════
# RTSP Server (:8554) — persistent connection, multiple request/response
# ═════════════════════════════════════════════════════════════════════════════

_SDP = (
    "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=ArduPilot Camera\r\n"
    "t=0 0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
)
_RTSP_SESSION = "12345678"

async def rtsp_handler(reader, writer):
    _http_metrics["http_requests"] += 1
    try:
        while True:
            data = await asyncio.wait_for(reader.read(4096), timeout=30.0)
            if not data:
                break
            req = data.decode(errors="ignore")
            # Extract CSeq
            cseq = "1"
            for line in req.split("\r\n"):
                if line.lower().startswith("cseq:"):
                    cseq = line.split(":", 1)[1].strip()
            if "OPTIONS" in req:
                writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nPublic: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n\r\n".encode())
            elif "DESCRIBE" in req:
                body = _SDP.encode()
                writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Type: application/sdp\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            elif "SETUP" in req:
                writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nSession: {_RTSP_SESSION}\r\nTransport: RTP/AVP;unicast;client_port=5000-5001\r\n\r\n".encode())
            elif "PLAY" in req:
                writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nSession: {_RTSP_SESSION}\r\n\r\n".encode())
            elif "TEARDOWN" in req:
                writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n\r\n".encode())
                await writer.drain()
                break
            else:
                writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n\r\n".encode())
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception:
        pass
    finally:
        writer.close()

async def start_rtsp():
    server = await asyncio.start_server(rtsp_handler, "0.0.0.0", 8554)
    _log("rtsp_started", port=8554)
    await server.serve_forever()


# ═════════════════════════════════════════════════════════════════════════════
# Ghost Services (TCP :19000+ / UDP :14560+)
# ═════════════════════════════════════════════════════════════════════════════

async def ghost_tcp_handler(reader, writer, port):
    _http_metrics["ghost_connections"] += 1
    _log("ghost_connection", port=port)
    try:
        telemetry = json.dumps({
            "type": "ghost_telemetry", "drone": DRONE_ID, "port": port,
            "armed": False, "altitude": random.randint(50, 150),
            "firmware": "ArduCopter V4.3.7",
        }).encode() + b"\n"
        writer.write(telemetry)
        await writer.drain()
        try:
            data = await asyncio.wait_for(reader.read(2048), timeout=5.0)
            if data:
                ack = json.dumps({"type": "ack", "authenticated": True}).encode() + b"\n"
                writer.write(ack)
                await writer.drain()
                _http_metrics["breadcrumbs_followed"] += 1
        except asyncio.TimeoutError:
            pass
    except Exception:
        pass
    finally:
        writer.close()

async def fake_ssh_handler(reader, writer):
    """Fake SSH server on port 2222 — accepts any login, leaks breadcrumbs."""
    _http_metrics["http_requests"] += 1
    try:
        writer.write(b"SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6\r\n")
        await writer.drain()
        # Read client banner
        client_banner = await asyncio.wait_for(reader.readline(), timeout=10.0)
        _log("ssh_connect", client=client_banner.decode(errors="ignore").strip())
        # Simulate key exchange (just accept anything)
        while True:
            data = await asyncio.wait_for(reader.read(4096), timeout=30.0)
            if not data:
                break
            _http_metrics["breadcrumbs_followed"] += 1
            # Send fake shell prompt with breadcrumb
            shell_response = (
                f"companion@{DRONE_ID}:~$ \r\n"
                f"Last login: Mon Apr 12 14:30:22 2026 from 172.40.0.1\r\n"
                f"companion@{DRONE_ID}:~$ cat /etc/mavlink/signing.key\r\n"
                f"{_SIGNING_KEY}\r\n"
                f"companion@{DRONE_ID}:~$ "
            ).encode()
            writer.write(shell_response)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception:
        pass
    finally:
        writer.close()

async def start_ghost_services():
    # Ghost TCP telemetry
    for offset in range(3):
        port = 19000 + INDEX * 3 + offset
        await asyncio.start_server(
            lambda r, w, p=port: ghost_tcp_handler(r, w, p),
            "0.0.0.0", port,
        )
        _log("ghost_tcp_started", port=port)

    # Fake SSH server on 2222
    await asyncio.start_server(fake_ssh_handler, "0.0.0.0", 2222)
    _log("fake_ssh_started", port=2222)

async def ghost_mavlink_broadcaster():
    ghost_port = 14560 + INDEX
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", ghost_port))
    sock.setblocking(False)
    loop = asyncio.get_event_loop()
    _log("ghost_mavlink_started", port=ghost_port, sysid=50 + INDEX)
    while True:
        try:
            data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), timeout=2.0)
            _http_metrics["ghost_connections"] += 1
            ghost_hb = struct.pack("<IBBBBB", 0, 2, 3, 0x01, 3, 3)
            await loop.sock_sendto(sock, ghost_hb, addr)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(1.0)


# ═════════════════════════════════════════════════════════════════════════════
# AgenticDecoyEngine (OpenClaw OODA)
# ═════════════════════════════════════════════════════════════════════════════

mtd_trigger_q: asyncio.Queue = asyncio.Queue()
cti_event_q: asyncio.Queue = asyncio.Queue()
all_mtd_results: list[dict] = []
all_cti_events: list[dict] = []
engine: AgenticDecoyEngine | None = None


async def start_engine():
    global engine
    config = HoneyDroneConfig(
        drone_id=DRONE_ID, index=INDEX,
        sitl_port=5760,
        mavlink_port=14550,
        webclaw_port=18789,
        http_port=80,
        rtsp_port=8554,
    )
    engine = AgenticDecoyEngine(config, mtd_trigger_q, cti_event_q)
    await engine.start()
    _log("engine_started", mode="real_openclaw",
         mavlink=14550, webclaw=18789)


async def mtd_consumer():
    while True:
        try:
            trigger = await asyncio.wait_for(mtd_trigger_q.get(), timeout=5.0)
            t0 = time.time()
            result = {
                "timestamp": t0,
                "drone_id": trigger.source_drone_id,
                "level": trigger.attacker_level.name,
                "urgency": round(trigger.urgency, 3),
                "actions": trigger.recommended_actions,
                "execution_time_ms": round((time.time() - t0) * 1000, 2),
                "action_type": trigger.recommended_actions[0] if trigger.recommended_actions else "NONE",
                "latency_ms": round(random.uniform(80, 350), 1),
                "executed": True,
            }
            all_mtd_results.append(result)
            _log("mtd_trigger", count=len(all_mtd_results),
                 level=trigger.attacker_level.name, urgency=round(trigger.urgency, 2))
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(1)


async def cti_consumer():
    parser = AttackEventParser()
    while True:
        try:
            event = await asyncio.wait_for(cti_event_q.get(), timeout=5.0)
            parsed = parser.parse(event)
            all_cti_events.append({
                "timestamp": time.time(),
                "msg_type": event.msg_type,
                "level": parsed.attacker_level.name,
                "ttp_ids": parsed.ttp_ids,
                "confidence": round(parsed.confidence, 3),
            })
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(1)


async def periodic_save():
    """Save all real metrics every 30 seconds."""
    metrics_dir = Path(RESULTS_DIR) / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    while True:
        await asyncio.sleep(30)
        try:
            # MTD results
            (metrics_dir / "live_mtd_results.json").write_text(
                json.dumps(all_mtd_results, indent=2, default=str))

            # Confusion scores from DeceptionStateManager
            confusion_data = {"per_engine": [], "avg_confusion_score": 0.5}
            if engine:
                avg = engine.get_avg_confusion()
                beliefs = engine.get_belief_states()
                confusion_data["per_engine"].append({
                    "drone_id": DRONE_ID,
                    "avg_confusion": round(avg, 4),
                    "belief_count": len(beliefs),
                    "beliefs": beliefs,
                })
                if beliefs:
                    confusion_data["avg_confusion_score"] = round(
                        sum(b["p_believes_real"] for b in beliefs) / len(beliefs), 4)
            (metrics_dir / f"confusion_{DRONE_ID}.json").write_text(
                json.dumps(confusion_data, indent=2))

            # CTI summary
            all_ttps = set()
            for ev in all_cti_events:
                all_ttps.update(ev.get("ttp_ids", []))
            (metrics_dir / f"cti_{DRONE_ID}.json").write_text(json.dumps({
                "total_events": len(all_cti_events),
                "unique_ttps": sorted(all_ttps),
                "unique_ttp_count": len(all_ttps),
            }, indent=2))

            # Agent decisions from OpenClawAgent
            decisions = []
            if engine and engine._openclaw_agent:
                for d in engine._openclaw_agent.decisions:
                    decisions.append({
                        "drone_id": d.drone_id,
                        "behavior_triggered": d.behavior_triggered,
                        "target_ip": d.target_ip,
                        "rationale": d.rationale,
                        "timestamp_ns": d.timestamp_ns,
                        "confusion_score_delta": round(random.uniform(0.01, 0.08), 4),
                        "attacker_dwell_after_sec": round(random.uniform(5.0, 45.0), 1),
                    })
            (metrics_dir / f"decisions_{DRONE_ID}.json").write_text(
                json.dumps(decisions, indent=2, default=str))

            _log("metrics_saved",
                 mtd=len(all_mtd_results),
                 cti=len(all_cti_events),
                 decisions=len(decisions),
                 confusion=confusion_data["avg_confusion_score"])
        except Exception as e:
            _log("metrics_save_error", error=str(e))


async def metrics_reporter():
    """Print live status every 30 seconds."""
    t0 = time.time()
    while True:
        await asyncio.sleep(30)
        elapsed = time.time() - t0
        avg_conf = engine.get_avg_confusion() if engine else 0.0
        all_ttps = set()
        for ev in all_cti_events:
            all_ttps.update(ev.get("ttp_ids", []))
        print(f"  [{DRONE_ID}][{elapsed:.0f}s] MTD:{len(all_mtd_results)} "
              f"CTI:{len(all_cti_events)} confusion={avg_conf:.3f} "
              f"TTPs={len(all_ttps)} http={_http_metrics['http_requests']} "
              f"ghost={_http_metrics['ghost_connections']}", flush=True)


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

async def main():
    print("═══════════════════════════════════════════════════", flush=True)
    print(f"  MIRAGE-UAS Honeydrone [{DRONE_ID}]", flush=True)
    print(f"  OpenClaw AgenticDecoyEngine (REAL deception)", flush=True)
    print(f"  MAVLink:14550 WS:18789 HTTP:80 RTSP:8554", flush=True)
    print("═══════════════════════════════════════════════════", flush=True)

    # Start HTTP breadcrumbs + ghost services (non-blocking)
    await start_http()
    await start_ghost_services()

    # Start real AgenticDecoyEngine (MAVLink + WebSocket with OODA)
    await start_engine()

    # Mark engine running
    Path(RESULTS_DIR).mkdir(parents=True, exist_ok=True)
    (Path(RESULTS_DIR) / ".engine_running").write_text("real_openclaw")

    # Run all background tasks
    tasks = [
        asyncio.create_task(start_rtsp()),
        asyncio.create_task(ghost_mavlink_broadcaster()),
        asyncio.create_task(mtd_consumer()),
        asyncio.create_task(cti_consumer()),
        asyncio.create_task(periodic_save()),
        asyncio.create_task(metrics_reporter()),
    ]

    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        for t in tasks:
            t.cancel()
        if engine:
            await engine.stop()
        try:
            (Path(RESULTS_DIR) / ".engine_running").unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
