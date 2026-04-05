#!/usr/bin/env python3
"""
http_rtsp_capture.py — HTTP / RTSP 공격 트래픽 캡처

Project  : MIRAGE-UAS
Module   : CTI Pipeline / HTTP-RTSP Capture
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - DVD CC Web Interface (HTTP :8081+)
    - DVD CC RTSP stream   (RTSP :8554+)
    - HoneyDroneConfig list

[Outputs]
    - asyncio.Queue[MavlinkCaptureEvent] → AttackEventParser
      (DroneProtocol.HTTP / DroneProtocol.RTSP 이벤트)

[설계 원칙]
    - HTTP: aiohttp 기반 리버스 프록시 모드
      CC의 HTTP 포트 앞에 가로채기 레이어를 두어 요청 로깅
    - RTSP: TCP accept 후 연결 시도 자체를 이벤트로 기록
      (실제 스트림 내용은 DVD CC가 처리)
    - 요청/연결만 기록, 응답은 CC로 pass-through
    - 논문 공격 표면 커버리지: MAVLink + HTTP + RTSP (3개 프로토콜)
"""

import asyncio
import time
import uuid
from typing import Optional

import aiohttp
from aiohttp import web

from shared.constants import HONEY_DRONE_COUNT, HTTP_PORT_BASE, RTSP_PORT_BASE
from shared.logger import get_logger
from shared.models import DroneProtocol, HoneyDroneConfig, MavlinkCaptureEvent

logger = get_logger(__name__)

# HTTP 캡처 포트 오프셋 (CC HTTP 포트 + 100 = 캡처 포트)
_HTTP_CAPTURE_PORT_OFFSET : int = 100
# RTSP 캡처 포트 오프셋 (CC RTSP 포트 + 100)
_RTSP_CAPTURE_PORT_OFFSET : int = 100


class HTTPRTSPCapture:
    """
    [ROLE] DVD CC의 HTTP/RTSP 공격 트래픽을 캡처하여 CTI 파이프라인에 전달.
           공격자가 CC Web UI(설정 탈취) 또는 RTSP(영상 감청)를 시도할 때
           이벤트를 MavlinkCaptureEvent로 변환하여 AttackEventParser에 전달.

    [DATA FLOW]
        공격자 ──▶ HTTP :8081+N  ──▶ HTTPCapture.handle_request()
                                      ──▶ MavlinkCaptureEvent (PROTOCOL.HTTP)
                                      ──▶ CC :8081+N (pass-through)
        공격자 ──▶ RTSP :8554+N  ──▶ RTSPCapture._accept_loop()
                                      ──▶ MavlinkCaptureEvent (PROTOCOL.RTSP)
    """

    def __init__(
        self,
        configs: list[HoneyDroneConfig],
        event_queue: asyncio.Queue,
    ) -> None:
        self._configs     = configs
        self._event_queue = event_queue
        self._http_apps: list[web.Application]   = []
        self._http_runners: list[web.AppRunner]  = []
        self._rtsp_servers: list[asyncio.Server] = []
        self._tasks: list[asyncio.Task]          = []

    async def start(self) -> None:
        """
        [ROLE] 모든 드론 인스턴스에 대해 HTTP 캡처 서버 + RTSP 캡처 서버 시작.

        [DATA FLOW]
            configs ──▶ _start_http_capture(config) × N
                    ──▶ _start_rtsp_capture(config) × N
        """
        for config in self._configs:
            await self._start_http_capture(config)
            self._tasks.append(
                asyncio.create_task(
                    self._start_rtsp_capture(config),
                    name=f"rtsp_cap_{config.drone_id}"
                )
            )
        logger.info(
            "http_rtsp_capture started",
            drone_count=len(self._configs),
        )

    async def stop(self) -> None:
        """[ROLE] 모든 HTTP 서버, RTSP 서버, 태스크 종료."""
        for runner in self._http_runners:
            await runner.cleanup()
        for srv in self._rtsp_servers:
            srv.close()
            await srv.wait_closed()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("http_rtsp_capture stopped")

    async def _start_http_capture(self, config: HoneyDroneConfig) -> None:
        """
        [ROLE] CC HTTP 포트 앞단에 aiohttp 캡처 서버 기동.
               모든 요청을 기록한 뒤 실제 CC Web UI로 pass-through.

        [DATA FLOW]
            aiohttp.web.Application ──▶ capture_port = http_port + 100
            요청 ──▶ handle_http_request() ──▶ event_queue + CC forward
        """
        capture_port = config.http_port + _HTTP_CAPTURE_PORT_OFFSET
        cc_base_url  = f"http://127.0.0.1:{config.http_port}"

        # aiohttp 캡처 핸들러 (클로저로 config 캡처)
        async def handle_http_request(request: web.Request) -> web.Response:
            # ── [ROLE] HTTP 요청을 이벤트로 기록 후 CC로 포워딩
            event = MavlinkCaptureEvent(
                drone_id=config.drone_id,
                src_ip=request.remote or "unknown",
                src_port=0,
                protocol=DroneProtocol.HTTP,
                msg_type="HTTP_REQUEST",
                http_method=request.method,
                http_path=request.path,
                payload_hex=(await request.read()).hex(),
                session_id=_session_id(request.remote or "", config.drone_id),
            )
            await self._event_queue.put(event)
            logger.debug(
                "http_captured",
                drone_id=config.drone_id,
                method=request.method,
                path=request.path,
                src=request.remote,
            )

            # CC Web UI로 pass-through
            try:
                async with aiohttp.ClientSession() as sess:
                    resp = await sess.request(
                        method=request.method,
                        url=f"{cc_base_url}{request.path_qs}",
                        headers={k: v for k, v in request.headers.items()
                                 if k.lower() not in ("host", "content-length")},
                        data=await request.read(),
                        allow_redirects=False,
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    )
                    return web.Response(
                        status=resp.status,
                        headers={k: v for k, v in resp.headers.items()
                                 if k.lower() not in ("transfer-encoding",)},
                        body=await resp.read(),
                    )
            except Exception:
                return web.Response(status=502, text="Bad Gateway")

        app    = web.Application()
        app.router.add_route("*", "/{path_info:.*}", handle_http_request)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", capture_port)
        await site.start()

        self._http_apps.append(app)
        self._http_runners.append(runner)
        logger.info(
            "http_capture server started",
            drone_id=config.drone_id,
            capture_port=capture_port,
            forwarding_to=config.http_port,
        )

    async def _start_rtsp_capture(self, config: HoneyDroneConfig) -> None:
        """
        [ROLE] RTSP 포트의 TCP 연결 시도를 캡처.
               실제 스트림 내용은 DVD CC가 처리하고
               연결 수립 시도 자체만 이벤트로 기록.

        [DATA FLOW]
            asyncio.start_server(:rtsp_port) ──▶ TCP accept
            ──▶ MavlinkCaptureEvent (PROTOCOL.RTSP) ──▶ event_queue
            ──▶ TCP data ──▶ CC RTSP :8554 forward
        """
        capture_port = config.rtsp_port + _RTSP_CAPTURE_PORT_OFFSET

        async def handle_rtsp(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            attacker_ip = writer.get_extra_info("peername", ("unknown", 0))[0]
            # 첫 패킷 수신 (RTSP OPTIONS 등)
            try:
                first_bytes = await asyncio.wait_for(reader.read(1024), timeout=3.0)
            except asyncio.TimeoutError:
                first_bytes = b""

            event = MavlinkCaptureEvent(
                drone_id=config.drone_id,
                src_ip=attacker_ip,
                src_port=writer.get_extra_info("peername", ("", 0))[1],
                protocol=DroneProtocol.RTSP,
                msg_type="RTSP_CONNECT",
                payload_hex=first_bytes.hex(),
                session_id=_session_id(attacker_ip, config.drone_id),
            )
            await self._event_queue.put(event)
            logger.debug(
                "rtsp_captured",
                drone_id=config.drone_id,
                src=attacker_ip,
            )
            writer.close()

        server = await asyncio.start_server(
            handle_rtsp, "0.0.0.0", capture_port
        )
        self._rtsp_servers.append(server)
        logger.info(
            "rtsp_capture server started",
            drone_id=config.drone_id,
            capture_port=capture_port,
        )
        async with server:
            await server.serve_forever()


def _session_id(attacker_ip: str, drone_id: str) -> str:
    """
    [ROLE] (attacker_ip, drone_id) 기반 결정론적 세션 ID 생성.
           동일 공격자의 HTTP/RTSP 이벤트를 동일 세션으로 묶음.

    [DATA FLOW]
        (attacker_ip, drone_id) ──▶ MD5 hash ──▶ UUID str
    """
    import hashlib
    key = f"{attacker_ip}:{drone_id}"
    return str(uuid.UUID(hashlib.md5(key.encode()).hexdigest()))
