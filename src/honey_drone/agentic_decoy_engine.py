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
from honey_drone.deception_state_manager import DeceptionStateManager, ObservationType

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
        self._belief_mgr         = DeceptionStateManager(config.drone_id)
        self._status             = DroneStatus.IDLE
        self._tasks: list[asyncio.Task] = []
        # UDP 소켓 (MAVLink 응답용)
        self._udp_sock: Optional[socket.socket] = None
        # WebSocket 서버
        self._ws_server = None
        # UDP 상태 브로드캐스트 소켓
        self._broadcast_sock: Optional[socket.socket] = None
        # MTD 트리거 카운트
        self._mtd_trigger_count: int = 0

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
        self._setup_udp_socket()

        # Transport 콜백을 에이전트에 연결 — 자율 행동에서 실제 패킷 전송 가능
        drone_id = self._config.drone_id

        def _get_attackers() -> list[tuple[str, int]]:
            return self._tracker.get_active_attacker_addrs(drone_id)

        self._openclaw_agent.set_transport(self._udp_sendto, _get_attackers)
        # Wire MAB to real DeceptionStateManager + EngagementTracker
        self._openclaw_agent._belief_mgr_ref = self._belief_mgr
        self._openclaw_agent._tracker_ref = self._tracker
        await self._openclaw_agent.start()

        # 상태 브로드캐스트 UDP 소켓 생성
        self._broadcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._broadcast_sock.setblocking(False)

        self._tasks = [
            asyncio.create_task(self._receive_loop(),       name=f"recv_{self._config.drone_id}"),
            asyncio.create_task(self._websocket_server(),   name=f"ws_{self._config.drone_id}"),
            asyncio.create_task(self._telemetry_loop(),     name=f"tele_{self._config.drone_id}"),
            asyncio.create_task(self._state_broadcast_loop(), name=f"bcast_{self._config.drone_id}"),
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
        if self._broadcast_sock:
            self._broadcast_sock.close()
            self._broadcast_sock = None
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()

        await self._openclaw_agent.stop()
        await self._tracker.stop()
        self._status = DroneStatus.TERMINATED
        logger.info("agentic_decoy_engine stopped", drone_id=self._config.drone_id)

    def get_avg_confusion(self) -> float:
        """
        [ROLE] 실제 베이지안 믿음 상태에서 평균 confusion score 반환.

        [DATA FLOW]
            DeceptionStateManager.get_all_beliefs() ──▶ avg P(real|obs)
        """
        beliefs = self._belief_mgr.get_all_beliefs()
        if not beliefs:
            return 0.70  # prior (아직 관측 없음)
        return sum(b.p_believes_real for b in beliefs) / len(beliefs)

    def get_belief_states(self) -> list:
        """
        [ROLE] 모든 공격자 믿음 상태 반환 (메트릭 저장용).

        [DATA FLOW]
            DeceptionStateManager ──▶ list[dict]
        """
        return [
            {
                "attacker_ip": b.attacker_ip,
                "p_believes_real": round(b.p_believes_real, 4),
                "total_observations": b.total_observations,
                "breadcrumbs_seen": b.breadcrumbs_seen,
                "ghost_interactions": b.ghost_interactions,
                "belief_target": b.belief_target.value,
            }
            for b in self._belief_mgr.get_all_beliefs()
        ]

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
                    msg_type=self._parse_msg_type(data),
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

        # 베이지안 믿음 상태 갱신 (실제 DeceptionStateManager)
        obs_type = ObservationType.PROTOCOL_INTERACT
        if metrics.exploit_attempts > 0:
            obs_type = ObservationType.EXPLOIT_ATTEMPT
        elif event.msg_type in ("LOG_REQUEST_LIST", "FILE_TRANSFER_PROTOCOL"):
            obs_type = ObservationType.EXPLOIT_ATTEMPT
        belief_state = await self._belief_mgr.observe_protocol_interaction(event.src_ip, event.protocol)

        # 적응형 응답 우선, 없으면 기본 응답 폴백
        response_bytes = self._openclaw_agent.generate_response(event)
        response_source = "agent"
        if response_bytes is None:
            response_bytes = self._response_gen.generate(event)
            response_source = "fallback"

        if response_bytes and self._udp_sock:
            try:
                await self._udp_sendto(response_bytes, addr)
            except Exception as e:
                logger.debug("response send failed", error=str(e))

        # Log the full OODA cycle for this packet
        fp = self._openclaw_agent._fingerprints.get(event.src_ip)
        logger.info(
            "ooda_cycle",
            drone_id=self._config.drone_id,
            attacker_ip=event.src_ip,
            msg_type=event.msg_type,
            tool=fp.tool.value if fp else "unknown",
            phase=fp.attack_phase.value if fp else "recon",
            response_source=response_source,
            response_len=len(response_bytes) if response_bytes else 0,
            p_real=round(belief_state.p_believes_real, 4) if belief_state else 0,
            dwell_sec=round(metrics.dwell_time_sec, 2),
            cmds=metrics.commands_issued,
            level=metrics.attacker_level.name,
        )

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
        except OSError as e:
            # Port already in use — non-fatal, skip WS server
            logger.warning(
                "websocket port unavailable (skipping)",
                drone_id=self._config.drone_id,
                port=self._config.webclaw_port,
                error=str(e),
            )
        except Exception as e:
            logger.error(
                "websocket server error",
                drone_id=self._config.drone_id,
                error=str(e),
            )

    async def _websocket_handler(
        self, websocket: WebSocketServerProtocol, path: str = "/"
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
        remote = websocket.remote_address if hasattr(websocket, 'remote_address') else ("0.0.0.0", 0)
        attacker_ip   = remote[0] if remote else "0.0.0.0"
        attacker_port = remote[1] if remote else 0
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
                try:
                    if isinstance(raw_msg, str):
                        payload_hex = raw_msg.encode("utf-8", errors="replace").hex()
                    elif isinstance(raw_msg, bytes):
                        payload_hex = raw_msg.hex()
                    else:
                        payload_hex = str(raw_msg).encode().hex()

                    event = MavlinkCaptureEvent(
                        drone_id=self._config.drone_id,
                        src_ip=attacker_ip,
                        src_port=attacker_port,
                        protocol=DroneProtocol.WEBSOCKET,
                        msg_type="WS_MESSAGE",
                        payload_hex=payload_hex,
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

                    # Log WS OODA cycle
                    fp = self._openclaw_agent._fingerprints.get(attacker_ip)
                    logger.info(
                        "ooda_ws_cycle",
                        drone_id=self._config.drone_id,
                        attacker_ip=attacker_ip,
                        tool=fp.tool.value if fp else "unknown",
                        phase=fp.attack_phase.value if fp else "recon",
                        response_type=response.get("type", "?"),
                        dwell_sec=round(metrics.dwell_time_sec, 2),
                        level=metrics.attacker_level.name,
                    )

                except Exception as e:
                    # Per-message error — don't kill the connection
                    logger.info(
                        "ws_message_error",
                        drone_id=self._config.drone_id,
                        error=str(e),
                    )
                    try:
                        fallback = {"type": "error", "message": "internal processing error"}
                        await websocket.send(json.dumps(fallback))
                    except Exception:
                        break

        except websockets.exceptions.ConnectionClosed:
            logger.debug(
                "websocket connection closed",
                drone_id=self._config.drone_id,
                attacker_ip=attacker_ip,
            )
        except Exception as e:
            logger.debug(
                "websocket handler error",
                drone_id=self._config.drone_id,
                error=str(e),
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
        self._mtd_trigger_count += 1
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

    # ── 상태 브로드캐스트 ──────────────────────────────────────────────────────

    async def _state_broadcast_loop(self) -> None:
        """
        [ROLE] 1초 주기로 엔진 상태 JSON 스냅샷을 UDP 브로드캐스트.
               외부 모니터/대시보드가 localhost:19999에서 수신.

        [DATA FLOW]
            openclaw_agent + tracker + belief_mgr
            ──▶ JSON snapshot ──▶ UDP sendto 127.0.0.1:19999
        """
        while True:
            try:
                await asyncio.sleep(1.0)

                if self._broadcast_sock is None:
                    continue

                # --- 최근 공격자 IP & 공격 단계 ---
                attacker_ip = ""
                current_phase = "IDLE"
                attacker_level = "---"
                fingerprints = getattr(self._openclaw_agent, "_fingerprints", {})
                if fingerprints:
                    # 가장 최근 지문 (dict 순서 보장 Python 3.7+)
                    last_key = list(fingerprints.keys())[-1]
                    fp = fingerprints[last_key]
                    attacker_ip = getattr(fp, "attacker_ip", "")
                    current_phase = getattr(fp, "attack_phase", "IDLE")
                    if hasattr(current_phase, "value"):
                        current_phase = current_phase.value
                    else:
                        current_phase = str(current_phase)

                # --- 최근 결정 ---
                last_action = ""
                last_action_reason = ""
                decisions = getattr(self._openclaw_agent, "_decisions", [])
                if decisions:
                    last_dec = decisions[-1]
                    last_action = getattr(last_dec, "behavior_triggered", "")
                    last_action_reason = getattr(last_dec, "rationale", "")

                # --- 활성 행동 목록 ---
                active_behaviors = ["proactive_loop", "sysid_rotation"]
                if getattr(self._openclaw_agent, "_false_flag_active", False):
                    active_behaviors.append("false_flag")
                if getattr(self._openclaw_agent, "_mirror_active", False):
                    active_behaviors.append("service_mirror")
                if getattr(self._openclaw_agent, "_silenced", False):
                    active_behaviors.append("reboot_sim")

                # --- 트래커 메트릭 ---
                dwell_seconds = 0.0
                commands_received = 0
                all_metrics = self._tracker.get_all_active_metrics(
                    self._config.drone_id
                )
                for m in all_metrics:
                    dwell_seconds += getattr(m, "dwell_time_sec", 0.0)
                    commands_received += getattr(m, "command_count", 0)
                    if not attacker_ip:
                        attacker_ip = getattr(m, "attacker_ip", "")

                # --- 공격자 수준 분류 ---
                if all_metrics:
                    lvl = self._tracker.classify_attacker(all_metrics[-1])
                    attacker_level = lvl.name if hasattr(lvl, "name") else str(lvl)

                # --- 믿음 점수 & confusion delta ---
                belief_score = self.get_avg_confusion()
                beliefs = self._belief_mgr.get_all_beliefs()
                confusion_delta = 0.0
                if beliefs:
                    confusion_delta = belief_score - 0.70  # delta from prior

                # --- 세션 ID ---
                session_id = ""
                if attacker_ip:
                    session_id = self._get_or_create_session_id(attacker_ip)

                snapshot = {
                    "drone_id": self._config.drone_id,
                    "timestamp": time.time(),
                    "attacker_ip": attacker_ip,
                    "current_phase": current_phase,
                    "attacker_level": attacker_level,
                    "belief_score": belief_score,
                    "active_behaviors": active_behaviors,
                    "last_action": last_action,
                    "last_action_reason": last_action_reason,
                    "dwell_seconds": dwell_seconds,
                    "commands_received": commands_received,
                    "mtd_triggers_sent": self._mtd_trigger_count,
                    "confusion_delta": confusion_delta,
                    "session_id": session_id,
                }

                payload = json.dumps(snapshot).encode("utf-8")
                self._broadcast_sock.sendto(payload, ("127.0.0.1", 19999))

            except asyncio.CancelledError:
                break
            except Exception:
                # Fire-and-forget: never crash the engine
                pass

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

    # ── Stub Receiver (Docker cc_stub → Host Engine) ────────────────────────

    async def start_stub_receiver(
        self, host: str = "0.0.0.0", port: int = 9090
    ) -> None:
        """
        [ROLE] cc_stub Docker 컨테이너로부터 포워딩된 이벤트 수신 (UDP).
               Docker 실험 경로에서 AgenticDecoyEngine을 활성화하는 진입점.

        Protocol:
          - MAVLink raw bytes → _process_mavlink_event()
          - JSON {"type": "http"|"ws"|"rtsp", ...} → _process_http_event()

        Response:
          - MAVLink: 응답 패킷 bytes 반환
          - JSON: {"response": ..., "drone_id": ...} JSON 반환

        [DATA FLOW]
            cc_stub._forward_to_engine() ──UDP──▶ start_stub_receiver()
            ──▶ _handle_stub_packet() ──▶ 응답 반환
        """
        loop = asyncio.get_event_loop()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.settimeout(2.0)

        logger.info(
            "stub_receiver started",
            drone_id=self._config.drone_id,
            host=host,
            port=port,
        )

        try:
            while True:
                try:
                    data, addr = await loop.run_in_executor(
                        None, sock.recvfrom, 65535,
                    )
                except (socket.timeout, OSError):
                    await asyncio.sleep(0.01)
                    continue

                asyncio.create_task(
                    self._handle_stub_packet(data, addr, sock)
                )
        except asyncio.CancelledError:
            pass
        finally:
            sock.close()
            logger.info(
                "stub_receiver stopped",
                drone_id=self._config.drone_id,
            )

    async def _handle_stub_packet(
        self,
        data: bytes,
        addr: tuple[str, int],
        sock: socket.socket,
    ) -> None:
        """
        [ROLE] cc_stub에서 포워딩된 단일 패킷 처리 + 응답 반환.
               JSON이면 HTTP/WS/RTSP 이벤트, 아니면 MAVLink raw bytes.

        [DATA FLOW]
            data ──▶ JSON? → _process_http_event()
                     bytes? → _process_mavlink_event() (기존 경로)
            ──▶ 응답 bytes → sock.sendto(addr)
        """
        try:
            resp_bytes: bytes = b""

            if data.startswith(b"{"):
                # JSON 이벤트 (HTTP/WS/RTSP 포워딩)
                event = json.loads(data.decode("utf-8"))
                response = self._process_http_event(event, addr)
                resp_bytes = json.dumps(response).encode("utf-8")
            else:
                # MAVLink raw bytes
                session_id = self._get_or_create_session_id(addr[0])
                event = MavlinkCaptureEvent(
                    drone_id=self._config.drone_id,
                    src_ip=addr[0],
                    src_port=addr[1],
                    protocol=DroneProtocol.MAVLINK,
                    msg_type=self._parse_msg_type(data),
                    payload_hex=data.hex(),
                    session_id=session_id,
                )

                # 에이전트 관찰 + 응답 생성
                self._openclaw_agent.observe(event)
                metrics = await self._tracker.update_session(event)

                # 베이지안 믿음 갱신
                obs_type = ObservationType.PROTOCOL_INTERACT
                if metrics.exploit_attempts > 0:
                    obs_type = ObservationType.EXPLOIT_ATTEMPT
                await self._belief_mgr.observe_protocol_interaction(
                    event.src_ip, event.protocol,
                )

                # 적응형 응답 우선 → 폴백
                response_bytes = self._openclaw_agent.generate_response(event)
                if response_bytes is None:
                    response_bytes = self._response_gen.generate(event)

                if response_bytes:
                    resp_bytes = response_bytes

                # Track B: CTI 큐
                await self._cti_event_output_q.put(event)
                # Track A: MTD 신호
                await self._assess_and_signal(metrics, event)

            if resp_bytes:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, sock.sendto, resp_bytes, addr,
                )
        except Exception as e:
            # 개별 패킷 오류가 수신 루프를 중단하면 안 됨
            logger.debug(
                "stub_packet_error",
                drone_id=self._config.drone_id,
                error=str(e),
            )

    def _process_http_event(
        self, event: dict, addr: tuple[str, int]
    ) -> dict:
        """
        [ROLE] cc_stub에서 포워딩된 HTTP/WS/RTSP 이벤트 처리.
               OpenClawAgent의 WS 응답 생성기를 활용.

        [DATA FLOW]
            {"type": "http"|"ws"|"rtsp", "data": {...}}
            ──▶ openclaw_agent.observe_ws() + generate_ws_response()
            ──▶ {"response": ..., "drone_id": ...}
        """
        event_type = event.get("type", "http")
        attacker_ip = addr[0]
        raw_msg = json.dumps(event.get("data", {}))

        # 에이전트에 WS 관찰 기회 제공 (HTTP/RTSP도 동일 경로)
        self._openclaw_agent.observe_ws(raw_msg, attacker_ip)

        # 에이전트 적응형 응답
        agent_resp = self._openclaw_agent.generate_ws_response(
            raw_msg, attacker_ip,
        )
        if agent_resp is not None:
            return agent_resp

        # 폴백
        return {
            "version": "2026.1.28",
            "type": "ack",
            "drone_id": self._config.drone_id,
            "message": f"{event_type} event received",
        }

    @staticmethod
    def _parse_msg_type(data: bytes) -> str:
        """
        [ROLE] MAVLink raw bytes에서 메시지 타입 이름 추출 (best-effort).
               파싱 실패 시 "UNKNOWN" 반환.
        """
        try:
            from pymavlink.dialects.v20 import ardupilotmega as apm

            # MAVLink v2: byte[7] = msgid low byte
            if len(data) >= 10 and data[0] == 0xFD:
                msgid = data[7] | (data[8] << 8) | (data[9] << 16)
            # MAVLink v1: byte[5] = msgid
            elif len(data) >= 6 and data[0] == 0xFE:
                msgid = data[5]
            else:
                return "UNKNOWN"

            # pymavlink ID→이름 역매핑
            id_to_name = {
                v.id: k
                for k, v in apm.mavlink_map.items()
            }
            return id_to_name.get(msgid, f"MSG_{msgid}")
        except Exception:
            return "UNKNOWN"

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
