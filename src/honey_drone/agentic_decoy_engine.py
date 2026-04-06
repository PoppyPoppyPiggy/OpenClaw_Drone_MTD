#!/usr/bin/env python3
"""
agentic_decoy_engine.py — OpenClaw-inspired Agentic Decoy Engine

Project  : MIRAGE-UAS
Module   : Honey Drone / Agentic Decoy Engine
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.2.0

[Inputs]
    - HoneyDroneConfig     (드론 설정)
    - asyncio.Queue[MavlinkCaptureEvent]  (MAVLink 이벤트 수신)

[Outputs]
    - asyncio.Queue[MTDTrigger]           → Track A MTD Controller
    - asyncio.Queue[MavlinkCaptureEvent]  → Track B CTI Pipeline

[Dependencies]
    - websockets >= 12.0
    - asyncio (stdlib)

[OpenClaw 역전용 설계]
    OpenClaw는 공격자가 노리는 취약한 agentic AI이다.
    MIRAGE-UAS는 그 취약성 패턴을 허니드론에 의도적으로 탑재한다:

    1. WebSocket :18789 (OpenClaw 기본 포트) → L3-L4 고급 공격자 유인
    2. Skill 실행 체인 emulation           → MAVLink 명령에 맥락 응답
    3. CVE-2026-25253 의도 노출 (미패치)   → exploit 시도 = 즉각 MTD
    4. 세션 지속성 유지                    → dwell time 연장 → MTD 실행 시간 확보
    5. OpenClawAgent 자율 기만             → 공격 단계별 적응 응답 + 능동적 기만

[DATA FLOW]
    공격자 (UDP MAVLink / TCP WebSocket)
    ──▶ AgenticDecoyEngine._receive_loop() / _websocket_handler()
    ──▶ OpenClawAgent.observe() (공격자 지문 + 단계 갱신)
    ──▶ EngagementTracker.update_session()
    ──▶ OpenClawAgent.generate_response() → 적응형 응답 우선
    ──▶ MavlinkResponseGenerator.generate() → 폴백 기본 응답
    ──▶ _assess_and_signal()
        ├──▶ asyncio.Queue[MTDTrigger]           (Track A)
        └──▶ asyncio.Queue[MavlinkCaptureEvent]  (Track B)
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from typing import Optional

import websockets
from websockets.server import WebSocketServerProtocol

from shared.constants import (
    DECEPTION_DWELL_MAX_SEC,
    ENGAGEMENT_EXPLOIT_THRESHOLD,
)
from shared.logger import get_logger
from shared.models import (
    AttackerLevel,
    DroneProtocol,
    DroneStatus,
    EngagementMetrics,
    HoneyDroneConfig,
    MavlinkCaptureEvent,
    MTDTrigger,
)
from honey_drone.engagement_tracker import EngagementTracker
from honey_drone.mavlink_response_gen import MavlinkResponseGenerator
from honey_drone.openclaw_agent import OpenClawAgent

logger = get_logger(__name__)

# ── MTD 트리거 임계값 ──────────────────────────────────────────────────────────
# 이 값들은 인프라 운영값 (RL 탐색 제외)
_URGENCY_TRIGGER_THRESHOLD : float = 0.3   # MTD 큐에 push하는 최소 urgency
_TELEMETRY_INTERVAL_SEC    : float = 1.0   # 주기적 텔레메트리 브로드캐스트 간격


class AgenticDecoyEngine:
    """
    [ROLE] 허니드론 1개 인스턴스의 agentic deception 레이어.
           MAVLink UDP 응답과 OpenClaw WebSocket 에뮬레이션을 동시 운용.
           OpenClawAgent를 통해 자율 적응형 기만을 수행.

    [DATA FLOW]
        외부 입력:
          - capture_event_input_q: MavlinkInterceptor → 이 엔진 (수동 tap 이벤트)
          - 직접 수신: UDP MAVLink (응답 전용), TCP WebSocket (OpenClaw emulation)

        외부 출력:
          - mtd_trigger_q:     MTD Controller 소비
          - cti_event_output_q: CTI Pipeline 소비
    """

    def __init__(
        self,
        config: HoneyDroneConfig,
        mtd_trigger_q: asyncio.Queue,
        cti_event_output_q: asyncio.Queue,
    ) -> None:
        self._config             = config
        self._mtd_trigger_q      = mtd_trigger_q
        self._cti_event_output_q = cti_event_output_q
        self._tracker            = EngagementTracker()
        self._response_gen       = MavlinkResponseGenerator(config)
        self._openclaw_agent     = OpenClawAgent(config, mtd_trigger_q, cti_event_output_q)
        self._status             = DroneStatus.IDLE
        self._tasks: list[asyncio.Task] = []
        # UDP 소켓 (MAVLink 응답용)
        self._udp_sock: Optional[socket.socket] = None
        # WebSocket 서버
        self._ws_server = None

    @property
    def status(self) -> DroneStatus:
        return self._status

    async def start(self) -> None:
        """
        [ROLE] 엔진 시작: EngagementTracker + OpenClawAgent + UDP 수신 루프
               + WebSocket 서버 동시 기동.

        [DATA FLOW]
            start()
            ──▶ tracker.start()
            ──▶ openclaw_agent.start()
            ──▶ _receive_loop() (UDP)
            ──▶ _websocket_server() (TCP :webclaw_port)
            ──▶ _telemetry_broadcast_loop()
        """
        await self._tracker.start()
        await self._openclaw_agent.start()
        self._setup_udp_socket()

        self._tasks = [
            asyncio.create_task(self._receive_loop(),       name=f"recv_{self._config.drone_id}"),
            asyncio.create_task(self._websocket_server(),   name=f"ws_{self._config.drone_id}"),
            asyncio.create_task(self._telemetry_loop(),     name=f"tele_{self._config.drone_id}"),
        ]
        self._status = DroneStatus.IDLE
        logger.info(
            "agentic_decoy_engine started",
            drone_id=self._config.drone_id,
            mavlink_port=self._config.mavlink_port,
            webclaw_port=self._config.webclaw_port,
        )

    async def stop(self) -> None:
        """
        [ROLE] 엔진 종료: 모든 태스크 취소 + 소켓 닫기 + 에이전트/트래커 종료.

        [DATA FLOW]
            stop() ──▶ task.cancel() ──▶ socket.close()
            ──▶ openclaw_agent.stop() ──▶ tracker.stop()
        """
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._udp_sock:
            self._udp_sock.close()
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()

        await self._openclaw_agent.stop()
        await self._tracker.stop()
        self._status = DroneStatus.TERMINATED
        logger.info("agentic_decoy_engine stopped", drone_id=self._config.drone_id)

    def ingest_captured_event(self, event: MavlinkCaptureEvent) -> None:
        """
        [ROLE] 외부(MavlinkInterceptor)에서 캡처된 이벤트를 엔진에 주입.
               CTI Pipeline(Track B)으로 전달 + engagement 추적에 사용.

        [DATA FLOW]
            MavlinkInterceptor ──▶ ingest_captured_event()
            ──▶ cti_event_output_q (Track B)
            ──▶ tracker.update_session() (비동기 스케줄)
        """
        asyncio.get_event_loop().create_task(
            self._process_injected_event(event)
        )

    # ── 내부 루프 ──────────────────────────────────────────────────────────────

    def _setup_udp_socket(self) -> None:
        """
        [ROLE] MAVLink UDP 응답용 소켓 초기화.
               허니드론 CC가 공격자에게 직접 응답.

        [DATA FLOW]
            HoneyDroneConfig.mavlink_port ──▶ UDP socket bind (0.0.0.0)
        """
        self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_sock.settimeout(2.0)  # blocking with timeout for run_in_executor
        self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._udp_sock.bind(("0.0.0.0", self._config.mavlink_port))
            logger.debug(
                "udp socket bound",
                drone_id=self._config.drone_id,
                port=self._config.mavlink_port,
            )
        except OSError as e:
            logger.warning(
                "udp bind failed (likely already bound by DVD CC)",
                drone_id=self._config.drone_id,
                error=str(e),
            )

    async def _udp_recvfrom(self) -> tuple[bytes, tuple[str, int]]:
        """
        [ROLE] Python 3.9 호환 비동기 UDP recvfrom.

        [DATA FLOW]
            self._udp_sock.recvfrom() ──▶ (data, addr)
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._udp_sock.recvfrom, 2048)

    async def _udp_sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        """
        [ROLE] Python 3.9 호환 비동기 UDP sendto.

        [DATA FLOW]
            data ──▶ self._udp_sock.sendto(data, addr)
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._udp_sock.sendto, data, addr)

    async def _receive_loop(self) -> None:
        """
        [ROLE] MAVLink UDP 패킷 수신 루프.
               패킷을 MavlinkCaptureEvent로 변환하여 처리.

        [DATA FLOW]
            UDP recv ──▶ MavlinkCaptureEvent 생성
            ──▶ _process_mavlink_event()
        """
        while True:
            try:
                data, addr = await self._udp_recvfrom()
                session_id = self._get_or_create_session_id(addr[0])
                event = MavlinkCaptureEvent(
                    drone_id=self._config.drone_id,
                    src_ip=addr[0],
                    src_port=addr[1],
                    protocol=DroneProtocol.MAVLINK,
                    payload_hex=data.hex(),
                    session_id=session_id,
                )
                await self._process_mavlink_event(event, addr)
            except asyncio.CancelledError:
                break
            except socket.timeout:
                # Normal — no packet within timeout, just loop again
                continue
            except OSError as e:
                if "timed out" in str(e):
                    continue
                logger.error(
                    "receive_loop error",
                    drone_id=self._config.drone_id,
                    error=str(e),
                )
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(
                    "receive_loop error",
                    drone_id=self._config.drone_id,
                    error=str(e),
                )
                await asyncio.sleep(0.1)

    async def _process_mavlink_event(
        self,
        event: MavlinkCaptureEvent,
        addr: tuple[str, int],
    ) -> None:
        """
        [ROLE] MAVLink 이벤트 처리: 에이전트 관찰 → engagement 갱신
               → 적응형 응답 생성 → 신호 평가.

        [DATA FLOW]
            MavlinkCaptureEvent
            ──▶ openclaw_agent.observe()       (공격자 지문 갱신)
            ──▶ tracker.update_session()       ──▶ EngagementMetrics
            ──▶ openclaw_agent.generate_response() (적응형 응답 우선)
            ──▶ response_gen.generate()        (폴백)
            ──▶ UDP 응답 전송
            ──▶ _assess_and_signal()           ──▶ MTDTrigger / CTI event
        """
        # 에이전트에게 관찰 기회 제공 (지문/단계 갱신)
        self._openclaw_agent.observe(event)

        metrics = await self._tracker.update_session(event)

        # 적응형 응답 우선, 없으면 기본 응답 폴백
        response_bytes = self._openclaw_agent.generate_response(event)
        if response_bytes is None:
            response_bytes = self._response_gen.generate(event)

        if response_bytes and self._udp_sock:
            try:
                await self._udp_sendto(response_bytes, addr)
            except Exception as e:
                logger.debug("response send failed", error=str(e))

        # Track B: CTI 이벤트 큐에 push
        await self._cti_event_output_q.put(event)

        # Track A: MTD 신호 평가
        await self._assess_and_signal(metrics, event)

    async def _process_injected_event(self, event: MavlinkCaptureEvent) -> None:
        """
        [ROLE] MavlinkInterceptor에서 주입된 이벤트 처리.
               응답 없이 추적만 수행 (passive tap 이벤트).

        [DATA FLOW]
            ingest_captured_event() ──▶ openclaw_agent.observe()
            ──▶ tracker.update_session()
            ──▶ cti_event_output_q (Track B)
            ──▶ _assess_and_signal() (Track A)
        """
        self._openclaw_agent.observe(event)
        metrics = await self._tracker.update_session(event)
        await self._cti_event_output_q.put(event)
        await self._assess_and_signal(metrics, event)

    async def _websocket_server(self) -> None:
        """
        [ROLE] OpenClaw WebSocket API 에뮬레이션 서버.
               포트: WEBCLAW_PORT_BASE + drone.index (기본 18789+N)
               L3-L4 공격자가 탐지 후 연결 시도 → 고신뢰 유인.

        [DATA FLOW]
            websockets.serve() ──▶ _websocket_handler() per connection
        """
        try:
            self._ws_server = await websockets.serve(
                self._websocket_handler,
                "0.0.0.0",
                self._config.webclaw_port,
                ping_interval=None,   # 실제 OpenClaw: ping 비활성화
                max_size=1_048_576,   # 1MB (OpenClaw 기본값)
            )
            logger.info(
                "openclaw websocket server started",
                drone_id=self._config.drone_id,
                port=self._config.webclaw_port,
            )
            await self._ws_server.wait_closed()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(
                "websocket server error",
                drone_id=self._config.drone_id,
                error=str(e),
            )

    async def _websocket_handler(
        self, websocket: WebSocketServerProtocol, path: str
    ) -> None:
        """
        [ROLE] OpenClaw WebSocket 연결 핸들러.
               공격자가 보내는 JSON 명령에 OpenClaw API 형식으로 응답.
               OpenClawAgent의 적응형 응답을 우선 사용.

        [DATA FLOW]
            WebSocket 연결 수립
            ──▶ tracker.record_websocket_connect()
            ──▶ 메시지 수신 루프
            ──▶ openclaw_agent.observe_ws() + generate_ws_response()
            ──▶ _generate_openclaw_response() (폴백)
            ──▶ JSON 응답 전송
        """
        attacker_ip   = websocket.remote_address[0]
        attacker_port = websocket.remote_address[1]
        session_id    = self._get_or_create_session_id(attacker_ip)

        await self._tracker.record_websocket_connect(attacker_ip, self._config.drone_id)
        self._status = DroneStatus.ENGAGED

        logger.info(
            "openclaw websocket connected",
            drone_id=self._config.drone_id,
            attacker_ip=attacker_ip,
            path=path,
        )

        try:
            async for raw_msg in websocket:
                event = MavlinkCaptureEvent(
                    drone_id=self._config.drone_id,
                    src_ip=attacker_ip,
                    src_port=attacker_port,
                    protocol=DroneProtocol.WEBSOCKET,
                    msg_type="WS_MESSAGE",
                    payload_hex=raw_msg.encode().hex()
                    if isinstance(raw_msg, str)
                    else raw_msg.hex(),
                    session_id=session_id,
                )

                # 에이전트 관찰 + 적응형 응답
                self._openclaw_agent.observe_ws(raw_msg, attacker_ip)

                metrics = await self._tracker.update_session(event)
                await self._cti_event_output_q.put(event)
                await self._assess_and_signal(metrics, event)

                # 적응형 WS 응답 우선, 없으면 기본 응답
                agent_response = self._openclaw_agent.generate_ws_response(
                    raw_msg, attacker_ip
                )
                if agent_response is not None:
                    response = agent_response
                else:
                    response = self._generate_openclaw_response(raw_msg)

                await websocket.send(json.dumps(response))

        except websockets.exceptions.ConnectionClosed:
            logger.debug(
                "websocket connection closed",
                drone_id=self._config.drone_id,
                attacker_ip=attacker_ip,
            )
        finally:
            if self._status == DroneStatus.ENGAGED:
                self._status = DroneStatus.IDLE

    async def _telemetry_loop(self) -> None:
        """
        [ROLE] 주기적 MAVLink 텔레메트리 브로드캐스트.
               드론이 살아있는 것처럼 보이게 하여 공격자 체류 유도.

        [DATA FLOW]
            asyncio.sleep(_TELEMETRY_INTERVAL_SEC)
            ──▶ response_gen.get_telemetry_packet()
            ──▶ UDP broadcast (all active attacker IPs)
        """
        while True:
            try:
                await asyncio.sleep(_TELEMETRY_INTERVAL_SEC)
                if not self._udp_sock:
                    continue

                tele_bytes = self._response_gen.get_telemetry_packet()
                # 현재 활성 세션의 모든 공격자에게 텔레메트리 전송
                all_metrics = self._tracker.get_all_active_metrics(
                    self._config.drone_id
                )
                for m in all_metrics:
                    try:
                        await self._udp_sendto(
                            tele_bytes,
                            (m.attacker_ip, self._config.mavlink_port),
                        )
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("telemetry loop error", error=str(e))

    # ── 신호 평가 ─────────────────────────────────────────────────────────────

    async def _assess_and_signal(
        self,
        metrics: EngagementMetrics,
        event: MavlinkCaptureEvent,
    ) -> None:
        """
        [ROLE] EngagementMetrics 기반으로 MTDTrigger를 생성하여 큐에 push.
               urgency가 임계값 이상이거나 exploit 시도 시 즉각 트리거.

        [DATA FLOW]
            EngagementMetrics
            ──▶ tracker.compute_urgency()  ──▶ urgency float
            ──▶ tracker.classify_attacker() ──▶ AttackerLevel
            ──▶ MTDTrigger 생성
            ──▶ mtd_trigger_q.put() (Track A)
        """
        urgency = self._tracker.compute_urgency(metrics)
        level   = self._tracker.classify_attacker(metrics)

        # exploit 시도는 즉각 트리거 (urgency 임계값 무관)
        should_trigger = (
            urgency >= _URGENCY_TRIGGER_THRESHOLD
            or metrics.exploit_attempts >= ENGAGEMENT_EXPLOIT_THRESHOLD
        )
        if not should_trigger:
            return

        actions = self._tracker.recommend_mtd_actions(metrics, level)
        trigger = MTDTrigger(
            source_drone_id=self._config.drone_id,
            attacker_level=level,
            engagement=metrics,
            urgency=urgency,
            recommended_actions=actions,
        )

        await self._mtd_trigger_q.put(trigger)
        self._status = (
            DroneStatus.UNDER_ATTACK
            if metrics.exploit_attempts >= ENGAGEMENT_EXPLOIT_THRESHOLD
            else DroneStatus.ENGAGED
        )

        logger.info(
            "mtd_trigger emitted",
            trigger_id=trigger.trigger_id[:8],
            drone_id=self._config.drone_id,
            level=level.name,
            urgency=round(urgency, 3),
            actions=actions,
        )

    # ── OpenClaw API 에뮬레이션 (폴백) ───────────────────────────────────────

    def _generate_openclaw_response(self, raw_msg: str | bytes) -> dict:
        """
        [ROLE] OpenClaw API 형식의 JSON 응답 생성 (폴백).
               OpenClawAgent가 맥락 응답을 생성하지 못할 때 사용.

        [DATA FLOW]
            raw_msg (공격자 명령) ──▶ 파싱 ──▶ OpenClaw 스타일 JSON 응답
        """
        # OpenClaw JSON 메시지 파싱 시도
        try:
            if isinstance(raw_msg, bytes):
                raw_msg = raw_msg.decode("utf-8", errors="ignore")
            msg = json.loads(raw_msg)
            msg_type = msg.get("type", "")
        except (json.JSONDecodeError, AttributeError):
            msg_type = ""

        # OpenClaw API 응답 패턴 (실제 v2026.1.x 형식 모방)
        base_response: dict = {
            "version": "2026.1.28",   # CVE 패치 직전 버전 (취약 버전)
            "timestamp": time.time(),
            "session": str(uuid.uuid4())[:8],
        }

        if msg_type == "ping":
            return {**base_response, "type": "pong", "status": "ok"}

        if msg_type == "skill_invoke":
            # MAVLink skill 실행 응답
            return {
                **base_response,
                "type": "skill_result",
                "skill": msg.get("skill", "mavlink_telemetry"),
                "result": {
                    "status": "success",
                    "data": {
                        "drone_id": self._config.drone_id,
                        "altitude": 100.0,
                        "battery": 78,
                        "gps_fix": 3,
                        "mode": "STABILIZE",
                    },
                },
            }

        if msg_type == "auth":
            # CVE-2026-25253: 인증 없이 성공 응답 (취약점 의도 노출)
            return {
                **base_response,
                "type": "auth_result",
                "authenticated": True,   # 인증 항상 성공 (취약)
                "permissions": ["skill_invoke", "config_read", "config_write"],
            }

        # 기본 응답: 연결 확인
        return {
            **base_response,
            "type": "ack",
            "message": "Connected to OpenClaw gateway",
        }

    def _get_or_create_session_id(self, attacker_ip: str) -> str:
        """
        [ROLE] 공격자 IP 기반 세션 ID 생성/재사용.
               동일 IP의 이벤트는 동일 session_id로 묶임.

        [DATA FLOW]
            attacker_ip ──▶ hash 기반 deterministic UUID ──▶ session_id str
        """
        import hashlib
        key = f"{attacker_ip}:{self._config.drone_id}"
        return str(uuid.UUID(hashlib.md5(key.encode()).hexdigest()))
