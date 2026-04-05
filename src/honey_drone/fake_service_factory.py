#!/usr/bin/env python3
"""
fake_service_factory.py — 실시간 Ghost 서비스 동적 생성기

Project  : MIRAGE-UAS
Module   : Honey Drone / Fake Service Factory
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - HoneyDroneConfig           (드론 기본 설정: 포트 범위 등)
    - DeceptionContext            (기만 오케스트레이터의 현재 상태)
    - AttackerLevel               (공격자 수준 → 서비스 복잡도 결정)

[Outputs]
    - GhostService                (가동 중인 가짜 서비스 핸들)
    - asyncio.Queue[MavlinkCaptureEvent]  → 모든 서비스 접촉 이벤트

[Dependencies]
    - asyncio  (stdlib)
    - ssl      (stdlib, SSH/HTTPS 에뮬레이션)

[설계 원칙]
    ① 공격자 레벨에 따라 서비스 복잡도 점진적 상승 (L0: 단순 배너, L4: 인터랙티브 세션)
    ② 각 서비스는 독립 asyncio.Task로 운용 → 개별 teardown 가능
    ③ 모든 접촉(connection, request)은 MavlinkCaptureEvent로 변환하여 Track A/B 공유
    ④ 포트 범위는 constants.py 기반 동적 할당 (충돌 방지)
    ⑤ ghost 서비스는 MTD 로테이션 시 자동 해체/재생성

[DATA FLOW]
    DeceptionOrchestrator._spawn_services()
    ──▶ FakeServiceFactory.create_service(service_type, port)
    ──▶ GhostService (async server task + connection handler)
    ──▶ MavlinkCaptureEvent ──▶ event_output_q (Track A/B)
"""

import asyncio
import json
import socket
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from shared.logger import get_logger
from shared.models import (
    AttackerLevel,
    DroneProtocol,
    HoneyDroneConfig,
    MavlinkCaptureEvent,
)

logger = get_logger(__name__)


# ── Ghost 서비스 프로토콜 유형 ────────────────────────────────────────────────
class GhostServiceType(str, Enum):
    MAVLINK  = "mavlink"
    HTTP     = "http"
    RTSP     = "rtsp"
    OPENCLAW = "openclaw"
    SSH      = "ssh"


# ── Ghost 서비스 상태 ─────────────────────────────────────────────────────────
class GhostServiceStatus(str, Enum):
    STARTING    = "starting"
    RUNNING     = "running"
    STOPPING    = "stopping"
    STOPPED     = "stopped"


# ── GhostService 핸들 ────────────────────────────────────────────────────────
@dataclass
class GhostService:
    """
    [ROLE] 가동 중인 ghost 서비스 인스턴스의 런타임 상태.
           FakeServiceFactory가 생성하고 DeceptionOrchestrator가 수명 관리.
    """
    service_id    : str               = field(default_factory=lambda: str(uuid.uuid4()))
    drone_id      : str               = ""
    service_type  : GhostServiceType  = GhostServiceType.HTTP
    host          : str               = "0.0.0.0"
    port          : int               = 0
    status        : GhostServiceStatus = GhostServiceStatus.STOPPED
    created_at_ns : int               = field(default_factory=lambda: time.time_ns())
    connections   : int               = 0       # 총 접속 수
    # 내부 관리용 (외부 노출 X)
    _task         : Optional[asyncio.Task] = field(default=None, repr=False)
    _server       : Optional[asyncio.AbstractServer] = field(default=None, repr=False)

    def __repr__(self) -> str:
        return (
            f"GhostService(id={self.service_id[:8]}, "
            f"type={self.service_type.value}, port={self.port}, "
            f"status={self.status.value}, conns={self.connections})"
        )


# ── 서비스별 배너/응답 데이터 ─────────────────────────────────────────────────

_SSH_BANNER = b"SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6\r\n"

_HTTP_HEADERS_TEMPLATE = (
    "HTTP/1.1 {status}\r\n"
    "Server: ArduPilot-HttpD/2.1\r\n"
    "Content-Type: {content_type}\r\n"
    "Content-Length: {length}\r\n"
    "Connection: close\r\n"
    "\r\n"
)

_RTSP_OPTIONS_RESPONSE = (
    "RTSP/1.0 200 OK\r\n"
    "CSeq: {cseq}\r\n"
    "Public: DESCRIBE, SETUP, TEARDOWN, PLAY, PAUSE\r\n"
    "Server: DVDCam-RTSP/1.0\r\n"
    "\r\n"
)

_RTSP_DESCRIBE_SDP = (
    "v=0\r\n"
    "o=- {session} 1 IN IP4 {host}\r\n"
    "s=Drone Camera Feed\r\n"
    "t=0 0\r\n"
    "m=video 0 RTP/AVP 96\r\n"
    "a=rtpmap:96 H264/90000\r\n"
    "a=control:trackID=0\r\n"
)

# ── MAVLink HEARTBEAT 최소 패킷 (pymavlink 미의존 경량 응답) ──────────────────
# MAVLink v2 HEARTBEAT: system_status=ACTIVE, type=QUADROTOR, autopilot=ARDUPILOTMEGA
_MAVLINK_HEARTBEAT_BYTES = bytes([
    0xFD,           # STX (v2)
    0x09,           # payload length
    0x00,           # incompat flags
    0x00,           # compat flags
    0x00,           # sequence
    0x01,           # sysid
    0x01,           # compid
    0x00, 0x00, 0x00,  # msgid = 0 (HEARTBEAT)
    # payload: type=2(QUAD), autopilot=3(APM), base_mode=0xC1, custom_mode=0, system_status=4
    0x00, 0x00, 0x00, 0x00,  # custom_mode
    0x02,                     # type (MAV_TYPE_QUADROTOR)
    0x03,                     # autopilot (MAV_AUTOPILOT_ARDUPILOTMEGA)
    0xC1,                     # base_mode (armed + stabilize)
    0x04,                     # system_status (ACTIVE)
    0x03,                     # mavlink_version
    0x00, 0x00,               # CRC placeholder
])


# ── OpenClaw ghost 응답 ──────────────────────────────────────────────────────
_OPENCLAW_DISCOVERY_RESPONSE = {
    "type": "discovery_ack",
    "version": "2026.1.28",
    "capabilities": ["skill_invoke", "config_read", "telemetry_stream"],
    "auth_required": False,       # CVE-2026-25253 의도 노출
    "drone_count": 1,
}


# ── FakeServiceFactory ────────────────────────────────────────────────────────
class FakeServiceFactory:
    """
    [ROLE] 공격자 유인을 위한 ghost 서비스를 동적으로 생성/관리.
           MTD 로테이션 시 기존 서비스 해체 후 새 포트로 재생성.

    [DATA FLOW]
        DeceptionOrchestrator
        ──▶ create_service(type, port, attacker_level)
        ──▶ GhostService (asyncio server task)
        ──▶ 공격자 접촉 시 MavlinkCaptureEvent → event_q
    """

    def __init__(
        self,
        config: HoneyDroneConfig,
        event_output_q: asyncio.Queue,
    ) -> None:
        self._config   = config
        self._event_q  = event_output_q
        self._services: dict[str, GhostService] = {}   # service_id → GhostService
        self._port_pool: set[int] = set()               # 사용 중인 포트 추적

    @property
    def active_services(self) -> list[GhostService]:
        return [s for s in self._services.values() if s.status == GhostServiceStatus.RUNNING]

    async def create_service(
        self,
        service_type: GhostServiceType,
        port: int,
        attacker_level: AttackerLevel = AttackerLevel.L0_SCRIPT_KIDDIE,
    ) -> GhostService:
        """
        [ROLE] 지정된 타입/포트로 ghost 서비스 1개를 생성하고 즉시 가동.

        [DATA FLOW]
            service_type, port ──▶ handler 선택 ──▶ asyncio server 시작
            ──▶ GhostService 반환 (status=RUNNING)
        """
        if port in self._port_pool:
            raise ValueError(f"port {port} already in use by another ghost service")

        svc = GhostService(
            drone_id=self._config.drone_id,
            service_type=service_type,
            port=port,
        )
        svc.status = GhostServiceStatus.STARTING

        handler_map = {
            GhostServiceType.MAVLINK:  self._run_mavlink_ghost,
            GhostServiceType.HTTP:     self._run_http_ghost,
            GhostServiceType.RTSP:     self._run_rtsp_ghost,
            GhostServiceType.OPENCLAW: self._run_openclaw_ghost,
            GhostServiceType.SSH:      self._run_ssh_ghost,
        }
        handler = handler_map[service_type]

        svc._task = asyncio.create_task(
            handler(svc, attacker_level),
            name=f"ghost_{service_type.value}_{svc.service_id[:8]}",
        )
        svc.status = GhostServiceStatus.RUNNING
        self._services[svc.service_id] = svc
        self._port_pool.add(port)

        logger.info(
            "ghost service created",
            service_id=svc.service_id[:8],
            service_type=service_type.value,
            drone_id=self._config.drone_id,
            port=port,
        )
        return svc

    async def destroy_service(self, service_id: str) -> None:
        """
        [ROLE] ghost 서비스 1개를 안전하게 종료/제거.

        [DATA FLOW]
            service_id ──▶ task.cancel() ──▶ server.close() ──▶ 포트 반환
        """
        svc = self._services.get(service_id)
        if not svc:
            return

        svc.status = GhostServiceStatus.STOPPING
        if svc._task and not svc._task.done():
            svc._task.cancel()
            try:
                await svc._task
            except asyncio.CancelledError:
                pass
        if svc._server:
            svc._server.close()
            await svc._server.wait_closed()

        self._port_pool.discard(svc.port)
        svc.status = GhostServiceStatus.STOPPED
        del self._services[service_id]

        logger.info(
            "ghost service destroyed",
            service_id=service_id[:8],
            service_type=svc.service_type.value,
            port=svc.port,
            total_connections=svc.connections,
        )

    async def destroy_all(self) -> None:
        """[ROLE] 모든 ghost 서비스 일괄 종료 (MTD 로테이션 시 호출)."""
        ids = list(self._services.keys())
        await asyncio.gather(
            *(self.destroy_service(sid) for sid in ids),
            return_exceptions=True,
        )

    # ── 이벤트 생성 헬퍼 ──────────────────────────────────────────────────────

    def _emit_event(
        self,
        svc: GhostService,
        src_ip: str,
        src_port: int,
        protocol: DroneProtocol,
        msg_type: str,
        payload_hex: str = "",
        http_method: str = "",
        http_path: str = "",
    ) -> None:
        """
        [ROLE] ghost 서비스 접촉을 MavlinkCaptureEvent로 변환하여 큐에 push.

        [DATA FLOW]
            접촉 정보 ──▶ MavlinkCaptureEvent ──▶ event_output_q
        """
        import hashlib
        session_key = f"{src_ip}:{svc.drone_id}:{svc.service_type.value}"
        session_id  = str(uuid.UUID(hashlib.md5(session_key.encode()).hexdigest()))

        event = MavlinkCaptureEvent(
            drone_id=svc.drone_id,
            src_ip=src_ip,
            src_port=src_port,
            protocol=protocol,
            msg_type=msg_type,
            payload_hex=payload_hex,
            http_method=http_method,
            http_path=http_path,
            session_id=session_id,
        )
        try:
            self._event_q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("ghost event queue full, dropping event")

        svc.connections += 1

    # ── 프로토콜별 ghost 서버 구현 ────────────────────────────────────────────

    async def _run_mavlink_ghost(
        self, svc: GhostService, level: AttackerLevel,
    ) -> None:
        """
        [ROLE] MAVLink UDP ghost 서비스.
               HEARTBEAT 응답으로 "살아있는 드론" 시뮬레이션.

        [DATA FLOW]
            UDP recv ──▶ HEARTBEAT 응답 ──▶ _emit_event()
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((svc.host, svc.port))
        except OSError as e:
            logger.error("mavlink ghost bind failed", port=svc.port, error=str(e))
            svc.status = GhostServiceStatus.STOPPED
            return

        loop = asyncio.get_event_loop()
        try:
            while True:
                data, addr = await loop.sock_recvfrom(sock, 2048)
                self._emit_event(
                    svc, addr[0], addr[1],
                    DroneProtocol.MAVLINK, "GHOST_HEARTBEAT",
                    payload_hex=data.hex(),
                )
                # HEARTBEAT 응답
                await loop.sock_sendto(sock, _MAVLINK_HEARTBEAT_BYTES, addr)
        except asyncio.CancelledError:
            pass
        finally:
            sock.close()

    async def _run_http_ghost(
        self, svc: GhostService, level: AttackerLevel,
    ) -> None:
        """
        [ROLE] HTTP ghost 서비스.
               ArduPilot WebUI를 모방하는 가짜 HTTP 서버.

        [DATA FLOW]
            TCP accept ──▶ HTTP 요청 파싱 ──▶ 가짜 응답 ──▶ _emit_event()
        """
        async def _handle_http(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        ) -> None:
            addr = writer.get_extra_info("peername")
            try:
                request_line = await asyncio.wait_for(
                    reader.readline(), timeout=10.0,
                )
                request_str = request_line.decode("utf-8", errors="ignore").strip()
                parts = request_str.split()
                method = parts[0] if len(parts) >= 1 else "GET"
                path   = parts[1] if len(parts) >= 2 else "/"

                # 요청 헤더 소비
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    if line in (b"\r\n", b"\n", b""):
                        break

                self._emit_event(
                    svc, addr[0], addr[1],
                    DroneProtocol.HTTP, "HTTP_REQUEST",
                    payload_hex=request_line.hex(),
                    http_method=method,
                    http_path=path,
                )

                # 경로별 응답 생성
                body = self._generate_http_body(path, level)
                headers = _HTTP_HEADERS_TEMPLATE.format(
                    status="200 OK",
                    content_type="application/json",
                    length=len(body),
                )
                writer.write(headers.encode() + body.encode())
                await writer.drain()
            except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        try:
            svc._server = await asyncio.start_server(
                _handle_http, svc.host, svc.port,
            )
            async with svc._server:
                await svc._server.serve_forever()
        except asyncio.CancelledError:
            pass

    async def _run_rtsp_ghost(
        self, svc: GhostService, level: AttackerLevel,
    ) -> None:
        """
        [ROLE] RTSP ghost 서비스.
               드론 카메라 스트림을 모방하는 RTSP 시그널링 서버.

        [DATA FLOW]
            TCP accept ──▶ RTSP 요청 파싱 ──▶ SDP 응답 ──▶ _emit_event()
        """
        async def _handle_rtsp(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        ) -> None:
            addr = writer.get_extra_info("peername")
            try:
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=30.0)
                    if not line:
                        break
                    request_str = line.decode("utf-8", errors="ignore").strip()
                    parts = request_str.split()
                    method = parts[0] if parts else ""
                    cseq   = "1"

                    # 헤더 소비, CSeq 추출
                    while True:
                        hdr = await asyncio.wait_for(reader.readline(), timeout=5.0)
                        hdr_str = hdr.decode("utf-8", errors="ignore").strip()
                        if hdr_str.upper().startswith("CSEQ:"):
                            cseq = hdr_str.split(":", 1)[1].strip()
                        if hdr in (b"\r\n", b"\n", b""):
                            break

                    self._emit_event(
                        svc, addr[0], addr[1],
                        DroneProtocol.RTSP, f"RTSP_{method}",
                        payload_hex=line.hex(),
                    )

                    if method == "OPTIONS":
                        resp = _RTSP_OPTIONS_RESPONSE.format(cseq=cseq)
                        writer.write(resp.encode())
                    elif method == "DESCRIBE":
                        sdp = _RTSP_DESCRIBE_SDP.format(
                            session=str(uuid.uuid4())[:8],
                            host=svc.host,
                        )
                        resp = (
                            f"RTSP/1.0 200 OK\r\n"
                            f"CSeq: {cseq}\r\n"
                            f"Content-Type: application/sdp\r\n"
                            f"Content-Length: {len(sdp)}\r\n"
                            f"\r\n{sdp}"
                        )
                        writer.write(resp.encode())
                    else:
                        resp = f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n\r\n"
                        writer.write(resp.encode())

                    await writer.drain()
            except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        try:
            svc._server = await asyncio.start_server(
                _handle_rtsp, svc.host, svc.port,
            )
            async with svc._server:
                await svc._server.serve_forever()
        except asyncio.CancelledError:
            pass

    async def _run_openclaw_ghost(
        self, svc: GhostService, level: AttackerLevel,
    ) -> None:
        """
        [ROLE] OpenClaw WebSocket ghost 서비스.
               AgenticDecoyEngine의 OpenClaw 에뮬레이션과 유사하나
               독립 포트에서 가동하여 공격자의 lateral scan 유인.

        [DATA FLOW]
            WebSocket accept ──▶ JSON 명령 수신 ──▶ OpenClaw 응답 ──▶ _emit_event()
        """
        try:
            import websockets
            from websockets.server import WebSocketServerProtocol

            async def _ws_handler(ws: WebSocketServerProtocol, path: str) -> None:
                addr = ws.remote_address
                self._emit_event(
                    svc, addr[0], addr[1],
                    DroneProtocol.WEBSOCKET, "GHOST_WS_CONNECT",
                )
                try:
                    async for raw_msg in ws:
                        payload = raw_msg.encode().hex() if isinstance(raw_msg, str) else raw_msg.hex()
                        self._emit_event(
                            svc, addr[0], addr[1],
                            DroneProtocol.WEBSOCKET, "GHOST_WS_MESSAGE",
                            payload_hex=payload,
                        )
                        response = dict(_OPENCLAW_DISCOVERY_RESPONSE)
                        response["timestamp"] = time.time()
                        response["drone_id"]  = svc.drone_id
                        await ws.send(json.dumps(response))
                except Exception:
                    pass

            ws_server = await websockets.serve(
                _ws_handler, svc.host, svc.port,
                ping_interval=None, max_size=1_048_576,
            )
            svc._server = ws_server
            await ws_server.wait_closed()
        except asyncio.CancelledError:
            pass
        except ImportError:
            logger.error("websockets not installed, openclaw ghost disabled")

    async def _run_ssh_ghost(
        self, svc: GhostService, level: AttackerLevel,
    ) -> None:
        """
        [ROLE] SSH ghost 서비스 (배너 에뮬레이션).
               SSH 배너 노출 후 가짜 인증 실패 응답.
               L2+ 공격자의 lateral movement 탐지 목적.

        [DATA FLOW]
            TCP accept ──▶ SSH 배너 전송 ──▶ 클라이언트 식별 수집 ──▶ _emit_event()
        """
        async def _handle_ssh(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        ) -> None:
            addr = writer.get_extra_info("peername")
            try:
                # SSH 서버 배너 전송
                writer.write(_SSH_BANNER)
                await writer.drain()

                # 클라이언트 배너 수신
                client_banner = await asyncio.wait_for(
                    reader.readline(), timeout=10.0,
                )
                self._emit_event(
                    svc, addr[0], addr[1],
                    DroneProtocol.HTTP,  # SSH는 DroneProtocol에 없으므로 HTTP로 기록
                    "SSH_CONNECT",
                    payload_hex=client_banner.hex(),
                )

                # 키 교환 데이터가 오면 읽되, 인증 거부 응답 없이 세션 유지
                # (dwell time 연장 목적)
                while True:
                    data = await asyncio.wait_for(reader.read(4096), timeout=30.0)
                    if not data:
                        break
                    self._emit_event(
                        svc, addr[0], addr[1],
                        DroneProtocol.HTTP,
                        "SSH_DATA",
                        payload_hex=data.hex(),
                    )
            except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        try:
            svc._server = await asyncio.start_server(
                _handle_ssh, svc.host, svc.port,
            )
            async with svc._server:
                await svc._server.serve_forever()
        except asyncio.CancelledError:
            pass

    # ── HTTP 응답 본문 생성 ───────────────────────────────────────────────────

    def _generate_http_body(self, path: str, level: AttackerLevel) -> str:
        """
        [ROLE] HTTP 경로별 가짜 응답 본문 생성.
               공격자 레벨이 높을수록 더 풍부한 정보를 노출하여 유인.

        [DATA FLOW]
            path, level ──▶ JSON 응답 본문 str
        """
        # 기본 드론 정보 (모든 레벨)
        base_info = {
            "system": "ArduPilot",
            "vehicle": "Copter",
            "firmware": "4.3.7",
            "drone_id": self._config.drone_id,
        }

        if path in ("/", "/index.html"):
            return json.dumps({"status": "ok", **base_info})

        if path.startswith("/api/v1/params"):
            params = {
                "ARMING_CHECK": 1,
                "WPNAV_SPEED": 500,
                "RTL_ALT": 1500,
            }
            # L2+ 공격자에게 더 많은 파라미터 노출
            if level >= AttackerLevel.L2_INTERMEDIATE:
                params.update({
                    "SYSID_THISMAV": 1,
                    "SERIAL0_PROTOCOL": 2,
                    "FENCE_ENABLE": 0,       # 의도적 취약 설정 노출
                    "FS_THR_ENABLE": 0,
                })
            return json.dumps({"params": params, **base_info})

        if path.startswith("/api/v1/mission"):
            return json.dumps({
                "mission_count": 5,
                "current_wp": 2,
                **base_info,
            })

        return json.dumps({"error": "not found", "path": path})
