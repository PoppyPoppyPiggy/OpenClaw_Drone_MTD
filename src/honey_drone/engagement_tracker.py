#!/usr/bin/env python3
"""
engagement_tracker.py — 공격자 세션 engagement 지표 추적기

Project  : MIRAGE-UAS
Module   : Honey Drone / Engagement Tracker
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - MavlinkCaptureEvent  (허니드론에 유입된 패킷 이벤트)
    - session_id / attacker_ip (세션 키)

[Outputs]
    - EngagementMetrics    (MTDTrigger urgency 계산 및 논문 Table II 데이터)

[Dependencies]
    - asyncio (stdlib)

[설계 원칙]
    - 세션 키: (attacker_ip, drone_id) 조합
    - 세션 타임아웃: SESSION_TIMEOUT_SEC 초과 시 자동 종료
    - exploit attempt 탐지: CVE-2026-25253 패턴 (WebSocket auth bypass)
    - 논문 메트릭 Table II: avg/max dwell_time_sec, cmd_count, exploit_attempts
"""

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from shared.constants import (
    ENGAGEMENT_EXPLOIT_THRESHOLD,
    ENGAGEMENT_URGENCY_L1_THRESHOLD,
    ENGAGEMENT_URGENCY_L2_THRESHOLD,
    ENGAGEMENT_URGENCY_L3_THRESHOLD,
    SESSION_CLEANUP_INTERVAL_SEC,
    SESSION_TIMEOUT_SEC,
)
from shared.logger import get_logger
from shared.models import (
    AttackerLevel,
    DroneProtocol,
    EngagementMetrics,
    MavlinkCaptureEvent,
    MTDTrigger,
)

logger = get_logger(__name__)

# ── CVE / exploit 탐지 패턴 ──────────────────────────────────────────────────
# CVE-2026-25253: WebSocket localhost bypass 시도 패턴
_CVE_PATTERNS: frozenset[str] = frozenset([
    "localhost",
    "127.0.0.1",
    "::1",
    "Origin: null",
    "X-Forwarded-For",
    "websocket upgrade",
])
# L3-L4 고급 공격 패턴 (MAVLink message type 기반)
_ADVANCED_MSG_TYPES: frozenset[str] = frozenset([
    "FILE_TRANSFER_PROTOCOL",
    "LOG_REQUEST_LIST",
    "LOG_REQUEST_DATA",
    "PARAM_SET",
    "MISSION_ITEM",
    "SET_POSITION_TARGET_LOCAL_NED",
    "SET_ACTUATOR_CONTROL_TARGET",
])


# ── AttackerSession (내부 추적용) ─────────────────────────────────────────────
@dataclass
class AttackerSession:
    """
    [ROLE] 공격자 1세션의 내부 상태 레코드.
           외부에는 EngagementMetrics 형태로 노출.
    """
    session_id          : str
    drone_id            : str
    attacker_ip         : str
    attacker_port       : int
    started_at_ns       : int  = field(default_factory=lambda: time.time_ns())
    last_activity_ns    : int  = field(default_factory=lambda: time.time_ns())
    commands            : list[str] = field(default_factory=list)
    protocols           : set[DroneProtocol] = field(default_factory=set)
    exploit_attempts    : int  = 0
    websocket_sessions  : int  = 0
    advanced_cmds       : int  = 0    # L3-L4 고급 명령 수
    real_drone_breached : bool = False

    @property
    def dwell_time_sec(self) -> float:
        return (self.last_activity_ns - self.started_at_ns) / 1e9

    @property
    def commands_issued(self) -> int:
        return len(self.commands)

    def is_timed_out(self) -> bool:
        elapsed = (time.time_ns() - self.last_activity_ns) / 1e9
        return elapsed > SESSION_TIMEOUT_SEC


# ── EngagementTracker ─────────────────────────────────────────────────────────
class EngagementTracker:
    """
    [ROLE] 모든 활성 공격자 세션을 추적하고 EngagementMetrics를 집계.
           AgenticDecoyEngine에 의해 생성/소유되며 MTDTrigger 신호의 근거 데이터 제공.

    [DATA FLOW]
        MavlinkCaptureEvent ──▶ update_session()
        ──▶ AttackerSession (내부)
        ──▶ get_metrics() ──▶ EngagementMetrics ──▶ MTDTrigger
    """

    def __init__(self) -> None:
        # (attacker_ip, drone_id) → AttackerSession
        self._sessions: dict[tuple[str, str], AttackerSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """
        [ROLE] 주기적 세션 정리 태스크 시작.

        [DATA FLOW]
            asyncio.create_task ──▶ _cleanup_loop() 백그라운드 실행
        """
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("engagement_tracker started")

    async def stop(self) -> None:
        """[ROLE] 정리 태스크 종료."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("engagement_tracker stopped")

    async def update_session(self, event: MavlinkCaptureEvent) -> EngagementMetrics:
        """
        [ROLE] MavlinkCaptureEvent로 세션 상태를 갱신하고
               최신 EngagementMetrics를 반환.

        [DATA FLOW]
            MavlinkCaptureEvent
            ──▶ 세션 생성 또는 기존 세션 갱신
            ──▶ exploit 패턴 탐지
            ──▶ EngagementMetrics 반환
        """
        key = (event.src_ip, event.drone_id)

        async with self._lock:
            if key not in self._sessions:
                session = AttackerSession(
                    session_id=event.session_id or str(uuid.uuid4()),
                    drone_id=event.drone_id,
                    attacker_ip=event.src_ip,
                    attacker_port=event.src_port,
                )
                self._sessions[key] = session
                logger.info(
                    "new attacker session",
                    session_id=session.session_id[:8],
                    drone_id=event.drone_id,
                    attacker_ip=event.src_ip,
                )
            else:
                session = self._sessions[key]

            # 세션 갱신
            session.last_activity_ns = time.time_ns()
            session.commands.append(event.msg_type)
            session.protocols.add(event.protocol)

            # exploit 탐지
            self._detect_exploits(session, event)

            return self._to_metrics(session)

    async def record_websocket_connect(self, attacker_ip: str, drone_id: str) -> None:
        """
        [ROLE] OpenClaw WebSocket 연결 시 세션 WebSocket 카운터 증가.
               L3-L4 공격자 식별의 핵심 신호.

        [DATA FLOW]
            attacker_ip, drone_id ──▶ 세션 websocket_sessions += 1
        """
        key = (attacker_ip, drone_id)
        async with self._lock:
            if key in self._sessions:
                self._sessions[key].websocket_sessions += 1

    def classify_attacker(self, metrics: EngagementMetrics) -> AttackerLevel:
        """
        [ROLE] EngagementMetrics 기반 L0-L4 공격자 분류 (rule-based).
               MIRAGE-UAS §3 Threat Model 기준.

        [DATA FLOW]
            EngagementMetrics ──▶ rule 매칭 ──▶ AttackerLevel
        """
        # L4: exploit 시도 + WebSocket + 다중 프로토콜 + 고급 명령
        if (
            metrics.exploit_attempts >= ENGAGEMENT_EXPLOIT_THRESHOLD
            and metrics.websocket_sessions > 0
            and len(metrics.protocols_used) >= 3
        ):
            return AttackerLevel.L4_APT

        # L3: exploit 시도 또는 WebSocket 연결
        if (
            metrics.exploit_attempts >= ENGAGEMENT_EXPLOIT_THRESHOLD
            or metrics.websocket_sessions > 0
        ):
            return AttackerLevel.L3_ADVANCED

        # L2: 장시간 체류 + 다수 명령
        if (
            metrics.dwell_time_sec >= ENGAGEMENT_URGENCY_L2_THRESHOLD
            and metrics.commands_issued >= 20
        ):
            return AttackerLevel.L2_INTERMEDIATE

        # L1: 기본 프로토콜 인식 + 일정 수 명령
        if (
            metrics.dwell_time_sec >= ENGAGEMENT_URGENCY_L1_THRESHOLD
            or metrics.commands_issued >= 5
        ):
            return AttackerLevel.L1_BASIC

        return AttackerLevel.L0_SCRIPT_KIDDIE

    def compute_urgency(self, metrics: EngagementMetrics) -> float:
        """
        [ROLE] MTDTrigger urgency 계산 [0.0, 1.0].
               공격자 레벨 + exploit 시도 수 기반 긴급도 산출.

        [DATA FLOW]
            EngagementMetrics ──▶ urgency float ──▶ MTDTrigger.urgency
        """
        level = self.classify_attacker(metrics)

        base_urgency = {
            AttackerLevel.L0_SCRIPT_KIDDIE: 0.1,
            AttackerLevel.L1_BASIC:         0.3,
            AttackerLevel.L2_INTERMEDIATE:  0.5,
            AttackerLevel.L3_ADVANCED:      0.75,
            AttackerLevel.L4_APT:           0.95,
        }[level]

        # exploit 시도가 있으면 최소 0.9
        if metrics.exploit_attempts >= ENGAGEMENT_EXPLOIT_THRESHOLD:
            base_urgency = max(base_urgency, 0.9)

        # real drone breach 시 즉각 1.0
        if metrics.real_drone_breached:
            return 1.0

        return min(base_urgency, 1.0)

    def recommend_mtd_actions(
        self, metrics: EngagementMetrics, level: AttackerLevel
    ) -> list[str]:
        """
        [ROLE] 공격자 레벨에 따른 MTD 액션 우선순위 추천.
               MTD Controller가 최종 결정권을 가지며 이는 힌트.

        [DATA FLOW]
            AttackerLevel ──▶ action 우선순위 list ──▶ MTDTrigger.recommended_actions
        """
        # MTD_ALPHA_WEIGHTS 순서:
        # [freq_hop, ip_shuffle, port_rotate, proto_change,
        #  route_morph, key_rotate, service_migrate]
        recommendations = {
            AttackerLevel.L0_SCRIPT_KIDDIE: ["port_rotate"],
            AttackerLevel.L1_BASIC:         ["port_rotate", "ip_shuffle"],
            AttackerLevel.L2_INTERMEDIATE:  ["ip_shuffle", "port_rotate", "key_rotate"],
            AttackerLevel.L3_ADVANCED:      ["ip_shuffle", "proto_change", "key_rotate", "service_migrate"],
            AttackerLevel.L4_APT:           ["ip_shuffle", "proto_change", "key_rotate",
                                             "service_migrate", "route_morph", "freq_hop"],
        }
        return recommendations.get(level, ["port_rotate"])

    def get_metrics(self, attacker_ip: str, drone_id: str) -> Optional[EngagementMetrics]:
        """
        [ROLE] 특정 (attacker_ip, drone_id) 세션의 최신 메트릭 반환.

        [DATA FLOW]
            (attacker_ip, drone_id) ──▶ session 조회 ──▶ EngagementMetrics
        """
        session = self._sessions.get((attacker_ip, drone_id))
        return self._to_metrics(session) if session else None

    def get_all_active_metrics(self, drone_id: str) -> list[EngagementMetrics]:
        """
        [ROLE] 특정 허니드론의 모든 활성 세션 메트릭 반환 (논문 집계용).

        [DATA FLOW]
            drone_id ──▶ 필터링 ──▶ list[EngagementMetrics]
        """
        return [
            self._to_metrics(s)
            for (ip, did), s in self._sessions.items()
            if did == drone_id
        ]

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

    def _detect_exploits(
        self, session: AttackerSession, event: MavlinkCaptureEvent
    ) -> None:
        """
        [ROLE] CVE-2026-25253 패턴 및 고급 명령 사용 여부 탐지.

        [DATA FLOW]
            MavlinkCaptureEvent.payload_hex ──▶ 패턴 매칭 ──▶ session.exploit_attempts++
        """
        # CVE 패턴 탐지 (WebSocket payload)
        if event.protocol == DroneProtocol.WEBSOCKET:
            payload_str = bytes.fromhex(event.payload_hex).decode("utf-8", errors="ignore").lower()
            if any(p.lower() in payload_str for p in _CVE_PATTERNS):
                session.exploit_attempts += 1
                logger.warning(
                    "cve exploit pattern detected",
                    session_id=session.session_id[:8],
                    attacker_ip=session.attacker_ip,
                    pattern_count=session.exploit_attempts,
                )

        # 고급 명령 탐지 (L3-L4 시그니처)
        if event.msg_type in _ADVANCED_MSG_TYPES:
            session.advanced_cmds += 1

    def _to_metrics(self, session: AttackerSession) -> EngagementMetrics:
        """
        [ROLE] AttackerSession → EngagementMetrics 변환 (외부 공개 인터페이스).

        [DATA FLOW]
            AttackerSession ──▶ EngagementMetrics (immutable 뷰)
        """
        level = self.classify_attacker(
            EngagementMetrics(
                session_id=session.session_id,
                drone_id=session.drone_id,
                attacker_ip=session.attacker_ip,
                attacker_level=AttackerLevel.L0_SCRIPT_KIDDIE,
                session_start_ns=session.started_at_ns,
                last_activity_ns=session.last_activity_ns,
                dwell_time_sec=session.dwell_time_sec,
                commands_issued=session.commands_issued,
                protocols_used=list(session.protocols),
                exploit_attempts=session.exploit_attempts,
                websocket_sessions=session.websocket_sessions,
                real_drone_breached=session.real_drone_breached,
            )
        )
        return EngagementMetrics(
            session_id=session.session_id,
            drone_id=session.drone_id,
            attacker_ip=session.attacker_ip,
            attacker_level=level,
            session_start_ns=session.started_at_ns,
            last_activity_ns=session.last_activity_ns,
            dwell_time_sec=session.dwell_time_sec,
            commands_issued=session.commands_issued,
            protocols_used=list(session.protocols),
            exploit_attempts=session.exploit_attempts,
            websocket_sessions=session.websocket_sessions,
            real_drone_breached=session.real_drone_breached,
        )

    async def _cleanup_loop(self) -> None:
        """
        [ROLE] 타임아웃된 세션을 주기적으로 정리.
               메모리 누수 방지 및 세션 수 안정화.

        [DATA FLOW]
            asyncio.sleep(SESSION_CLEANUP_INTERVAL_SEC)
            ──▶ 타임아웃 세션 탐지 ──▶ 삭제 + 로그
        """
        while True:
            await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SEC)
            async with self._lock:
                timed_out = [
                    key for key, s in self._sessions.items()
                    if s.is_timed_out()
                ]
                for key in timed_out:
                    session = self._sessions.pop(key)
                    logger.info(
                        "session timed out",
                        session_id=session.session_id[:8],
                        drone_id=session.drone_id,
                        attacker_ip=session.attacker_ip,
                        dwell_sec=round(session.dwell_time_sec, 1),
                        cmds=session.commands_issued,
                    )
