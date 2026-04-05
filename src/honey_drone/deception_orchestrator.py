#!/usr/bin/env python3
"""
deception_orchestrator.py — 기만 레이어 통합 오케스트레이터

Project  : MIRAGE-UAS
Module   : Honey Drone / Deception Orchestrator
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - HoneyDroneConfig                    (드론 설정)
    - asyncio.Queue[MavlinkCaptureEvent]  (Track A 이벤트 스트림)
    - asyncio.Queue[MTDTrigger]           → Track A MTD Controller

[Outputs]
    - asyncio.Queue[MTDTrigger]           (urgency 보정된 MTD 트리거)
    - DeceptionEffectiveness               (논문 메트릭)

[Dependencies]
    - FakeServiceFactory    (ghost 서비스 생성/관리)
    - BreadcrumbPlanter     (breadcrumb 생성/추적)
    - DeceptionStateManager (공격자 믿음 상태 추적)
    - EngagementTracker     (기존 세션 메트릭 — 참조)

[설계 원칙]
    ① 3개 하위 모듈(Factory, Planter, StateManager)의 수명주기 통합 관리
    ② 이벤트 스트림을 감시하여 자동으로 ghost 서비스/breadcrumb 배치 결정
    ③ 공격자 믿음 상태에 따라 MTD urgency를 실시간 보정
    ④ MTD 로테이션 시 전체 기만 레이어 초기화 + 재배치
    ⑤ 논문 DES(Eq.19) deception_engagement 지표 실시간 제공

[DATA FLOW]
    AgenticDecoyEngine ──▶ MavlinkCaptureEvent 스트림
    ──▶ DeceptionOrchestrator._event_loop()
        ├──▶ DeceptionStateManager.observe_*()  (믿음 갱신)
        ├──▶ BreadcrumbPlanter.check_*()        (breadcrumb 사용 탐지)
        ├──▶ FakeServiceFactory (ghost 서비스 관리)
        └──▶ _assess_deception()
            ├──▶ MTDTrigger (urgency 보정) ──▶ mtd_trigger_q
            └──▶ DeceptionEffectiveness (메트릭)
"""

import asyncio
import time
import uuid
from typing import Optional

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
from honey_drone.fake_service_factory import (
    FakeServiceFactory,
    GhostService,
    GhostServiceStatus,
    GhostServiceType,
)
from honey_drone.breadcrumb_plant import (
    Breadcrumb,
    BreadcrumbPlanter,
    BreadcrumbType,
)
from honey_drone.deception_state_manager import (
    AttackerBeliefState,
    BeliefTarget,
    DeceptionEffectiveness,
    DeceptionStateManager,
)

logger = get_logger(__name__)

# ── 오케스트레이터 운영 파라미터 (인프라 — 기본값 허용) ────────────────────────
_GHOST_SERVICE_PORT_OFFSET : int   = 1000    # ghost 서비스 포트 오프셋 (기본 포트 + offset)
_BREADCRUMB_REFRESH_SEC    : float = 120.0   # breadcrumb 갱신 주기 (초)
_BELIEF_CHECK_INTERVAL_SEC : float = 5.0     # 믿음 상태 평가 주기 (초)
_URGENCY_BELIEF_WEIGHT     : float = 0.3     # 믿음 기반 urgency 보정 가중치 (MTD urgency에 가산)
_HONEYPOT_DETECTED_THRESHOLD: float = 0.3    # 공격자가 허니팟 탐지했다고 판단하는 P(real) 임계값
_GHOST_SPAWN_COOLDOWN_SEC  : float = 30.0    # ghost 서비스 재생성 쿨다운


class DeceptionOrchestrator:
    """
    [ROLE] FakeServiceFactory + BreadcrumbPlanter + DeceptionStateManager
           3개 모듈을 통합 운용하는 기만 레이어 오케스트레이터.

    [DATA FLOW]
        외부 입력:
          - event_input_q: AgenticDecoyEngine → 이 오케스트레이터 (이벤트 tap)

        외부 출력:
          - mtd_trigger_q: MTD Controller 소비 (urgency 보정된 트리거)

        내부 순환:
          - FakeServiceFactory ←→ BreadcrumbPlanter ←→ DeceptionStateManager
    """

    def __init__(
        self,
        config: HoneyDroneConfig,
        event_input_q: asyncio.Queue,
        mtd_trigger_q: asyncio.Queue,
    ) -> None:
        self._config         = config
        self._event_input_q  = event_input_q
        self._mtd_trigger_q  = mtd_trigger_q

        # 내부 이벤트 큐 (ghost 서비스 이벤트)
        self._ghost_event_q: asyncio.Queue = asyncio.Queue(maxsize=1000)

        # 하위 모듈 초기화
        self._factory = FakeServiceFactory(config, self._ghost_event_q)
        self._planter = BreadcrumbPlanter(config)
        self._state_mgr = DeceptionStateManager(config.drone_id)

        # 태스크 관리
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._last_ghost_spawn_ns: int = 0
        self._current_attacker_level = AttackerLevel.L0_SCRIPT_KIDDIE

    @property
    def effectiveness(self) -> DeceptionEffectiveness:
        return self._state_mgr.get_effectiveness()

    @property
    def active_ghost_services(self) -> list[GhostService]:
        return self._factory.active_services

    @property
    def breadcrumbs(self) -> list[Breadcrumb]:
        return self._planter.all_breadcrumbs

    async def start(self) -> None:
        """
        [ROLE] 오케스트레이터 시작: 기본 ghost 서비스 + breadcrumb 배치 + 이벤트 루프 시작.

        [DATA FLOW]
            start()
            ──▶ _spawn_initial_services()
            ──▶ _plant_initial_breadcrumbs()
            ──▶ _event_loop()       (이벤트 스트림 감시)
            ──▶ _ghost_event_loop() (ghost 서비스 이벤트 감시)
            ──▶ _belief_check_loop() (주기적 믿음 평가)
            ──▶ _breadcrumb_refresh_loop() (breadcrumb 갱신)
        """
        self._running = True

        await self._spawn_initial_services()
        self._plant_initial_breadcrumbs()

        self._tasks = [
            asyncio.create_task(
                self._event_loop(),
                name=f"deception_events_{self._config.drone_id}",
            ),
            asyncio.create_task(
                self._ghost_event_loop(),
                name=f"ghost_events_{self._config.drone_id}",
            ),
            asyncio.create_task(
                self._belief_check_loop(),
                name=f"belief_check_{self._config.drone_id}",
            ),
            asyncio.create_task(
                self._breadcrumb_refresh_loop(),
                name=f"bc_refresh_{self._config.drone_id}",
            ),
        ]

        logger.info(
            "deception_orchestrator started",
            drone_id=self._config.drone_id,
            ghost_services=len(self._factory.active_services),
            breadcrumbs=len(self._planter.all_breadcrumbs),
        )

    async def stop(self) -> None:
        """
        [ROLE] 오케스트레이터 종료: 모든 태스크/서비스/breadcrumb 정리.

        [DATA FLOW]
            stop() ──▶ task.cancel() ──▶ factory.destroy_all()
            ──▶ planter.clear_all() ──▶ state_mgr.clear_all()
        """
        self._running = False

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self._factory.destroy_all()
        self._planter.clear_all()
        self._state_mgr.clear_all()

        logger.info("deception_orchestrator stopped", drone_id=self._config.drone_id)

    async def handle_mtd_rotation(self) -> None:
        """
        [ROLE] MTD 로테이션 시 기만 레이어 전체 재초기화.
               기존 ghost/breadcrumb 파기 → 새 설정으로 재배치.

        [DATA FLOW]
            MTD rotate signal
            ──▶ factory.destroy_all()
            ──▶ planter.clear_all()
            ──▶ state_mgr.clear_all()
            ──▶ _spawn_initial_services()
            ──▶ _plant_initial_breadcrumbs()
        """
        logger.info(
            "mtd rotation → resetting deception layer",
            drone_id=self._config.drone_id,
        )
        await self._factory.destroy_all()
        self._planter.clear_all()
        self._state_mgr.clear_all()

        await self._spawn_initial_services()
        self._plant_initial_breadcrumbs()

        logger.info(
            "deception layer reset complete",
            drone_id=self._config.drone_id,
            ghost_services=len(self._factory.active_services),
            breadcrumbs=len(self._planter.all_breadcrumbs),
        )

    async def escalate_deception(self, attacker_level: AttackerLevel) -> None:
        """
        [ROLE] 공격자 레벨 상승 시 기만 복잡도 에스컬레이션.
               더 정교한 ghost 서비스 + breadcrumb 추가 배치.

        [DATA FLOW]
            AttackerLevel 상승
            ──▶ 추가 ghost 서비스 생성
            ──▶ 레벨 맞춤 breadcrumb 추가 생성
        """
        if attacker_level <= self._current_attacker_level:
            return

        old_level = self._current_attacker_level
        self._current_attacker_level = attacker_level

        logger.info(
            "escalating deception complexity",
            drone_id=self._config.drone_id,
            old_level=old_level.name,
            new_level=attacker_level.name,
        )

        # 레벨별 추가 서비스
        await self._spawn_level_services(attacker_level)

        # 레벨별 추가 breadcrumb
        ghost_ports = [s.port for s in self._factory.active_services]
        self._planter.generate_breadcrumbs(attacker_level, ghost_ports)

    # ── 이벤트 처리 루프 ──────────────────────────────────────────────────────

    async def _event_loop(self) -> None:
        """
        [ROLE] AgenticDecoyEngine에서 오는 이벤트 스트림 감시.
               각 이벤트를 기반으로 믿음 상태 갱신 + breadcrumb 탐지.

        [DATA FLOW]
            event_input_q ──▶ MavlinkCaptureEvent
            ──▶ _process_event()
        """
        while self._running:
            try:
                event: MavlinkCaptureEvent = await self._event_input_q.get()
                await self._process_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "deception event_loop error",
                    drone_id=self._config.drone_id,
                    error=str(e),
                )

    async def _ghost_event_loop(self) -> None:
        """
        [ROLE] ghost 서비스에서 오는 이벤트 스트림 감시.

        [DATA FLOW]
            ghost_event_q ──▶ MavlinkCaptureEvent
            ──▶ _process_ghost_event()
        """
        while self._running:
            try:
                event: MavlinkCaptureEvent = await self._ghost_event_q.get()
                await self._process_ghost_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "ghost event_loop error",
                    drone_id=self._config.drone_id,
                    error=str(e),
                )

    async def _belief_check_loop(self) -> None:
        """
        [ROLE] 주기적으로 모든 공격자의 믿음 상태를 평가.
               허니팟 탐지가 의심되면 즉각 MTD 트리거 발생.

        [DATA FLOW]
            asyncio.sleep(_BELIEF_CHECK_INTERVAL_SEC)
            ──▶ 전체 AttackerBeliefState 평가
            ──▶ 허니팟 탐지 시 MTDTrigger 생성
        """
        while self._running:
            try:
                await asyncio.sleep(_BELIEF_CHECK_INTERVAL_SEC)
                await self._assess_all_beliefs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("belief_check_loop error", error=str(e))

    async def _breadcrumb_refresh_loop(self) -> None:
        """
        [ROLE] breadcrumb 주기적 갱신 (stale detection 방지).

        [DATA FLOW]
            asyncio.sleep(_BREADCRUMB_REFRESH_SEC)
            ──▶ planter.clear_expired()
            ──▶ 부족 시 추가 생성
        """
        while self._running:
            try:
                await asyncio.sleep(_BREADCRUMB_REFRESH_SEC)
                expired = self._planter.clear_expired()
                if expired > 0:
                    ghost_ports = [s.port for s in self._factory.active_services]
                    self._planter.generate_breadcrumbs(
                        self._current_attacker_level, ghost_ports,
                    )
                    logger.debug(
                        "breadcrumbs refreshed",
                        expired=expired,
                        new_total=len(self._planter.all_breadcrumbs),
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("breadcrumb_refresh error", error=str(e))

    # ── 이벤트 처리 ───────────────────────────────────────────────────────────

    async def _process_event(self, event: MavlinkCaptureEvent) -> None:
        """
        [ROLE] AgenticDecoyEngine 이벤트를 기만 상태에 반영.

        [DATA FLOW]
            MavlinkCaptureEvent
            ──▶ 프로토콜 상호작용 관측
            ──▶ breadcrumb 사용 여부 확인
            ──▶ exploit/scan 패턴 확인
        """
        attacker_ip = event.src_ip
        if not attacker_ip:
            return

        # 프로토콜 상호작용 관측
        await self._state_mgr.observe_protocol_interaction(
            attacker_ip, event.protocol,
        )

        # breadcrumb 사용 탐지 (payload에서 breadcrumb 값 검색)
        await self._check_breadcrumb_in_event(event)

        # exploit 시도 관측
        if event.is_anomalous:
            await self._state_mgr.observe_exploit_attempt(attacker_ip)

    async def _process_ghost_event(self, event: MavlinkCaptureEvent) -> None:
        """
        [ROLE] ghost 서비스 이벤트를 기만 상태에 반영.

        [DATA FLOW]
            ghost MavlinkCaptureEvent
            ──▶ ghost 상호작용 관측
            ──▶ breadcrumb 사용 여부 확인
            ──▶ 심층 상호작용 판단
        """
        attacker_ip = event.src_ip
        if not attacker_ip:
            return

        # ghost 상호작용 레벨 판단
        deep = event.msg_type in (
            "GHOST_WS_MESSAGE", "GHOST_DEEP_INTERACT",
            "HTTP_REQUEST", "RTSP_DESCRIBE", "SSH_DATA",
        )
        await self._state_mgr.observe_ghost_interaction(
            attacker_ip,
            service_type=event.msg_type,
            deep=deep,
        )

        # breadcrumb 사용 탐지
        await self._check_breadcrumb_in_event(event)

    async def _check_breadcrumb_in_event(
        self, event: MavlinkCaptureEvent,
    ) -> None:
        """
        [ROLE] 이벤트 payload에서 breadcrumb 값 사용 여부 확인.

        [DATA FLOW]
            event.payload_hex ──▶ 디코드 ──▶ breadcrumb value/key 매칭
            ──▶ mark_used() + observe_breadcrumb_use()
        """
        if not event.payload_hex:
            return

        try:
            payload_str = bytes.fromhex(event.payload_hex).decode(
                "utf-8", errors="ignore",
            )
        except (ValueError, UnicodeDecodeError):
            return

        # value 매칭 (API 토큰, 비밀번호 등)
        for crumb in self._planter.all_breadcrumbs:
            if crumb.was_accessed or not crumb.value:
                continue
            if crumb.value in payload_str:
                self._planter.mark_used(crumb.breadcrumb_id, event.src_ip)
                await self._state_mgr.observe_breadcrumb_use(
                    event.src_ip, crumb.breadcrumb_id,
                )
                break

        # key 매칭 (username 등)
        for crumb in self._planter.all_breadcrumbs:
            if crumb.was_accessed or not crumb.key:
                continue
            if crumb.key in payload_str and crumb.crumb_type == BreadcrumbType.CREDENTIAL:
                # key 접근만 기록 (사용은 value 매칭 시)
                await self._state_mgr.observe_breadcrumb_access(
                    event.src_ip, crumb.breadcrumb_id,
                )
                break

    async def _assess_all_beliefs(self) -> None:
        """
        [ROLE] 모든 공격자의 믿음 상태를 평가하고 필요 시 MTD 트리거.

        [DATA FLOW]
            all AttackerBeliefStates
            ──▶ 허니팟 탐지 검사
            ──▶ MTDTrigger (urgency 보정)
        """
        beliefs = self._state_mgr.get_all_beliefs()

        for belief in beliefs:
            if belief.belief_target == BeliefTarget.HONEYPOT:
                # 공격자가 허니팟을 탐지 → 즉각 MTD
                urgency_mod = self._state_mgr.get_urgency_modifier(
                    belief.attacker_ip,
                )
                final_urgency = min(0.8 + urgency_mod * _URGENCY_BELIEF_WEIGHT, 1.0)

                trigger = MTDTrigger(
                    source_drone_id=self._config.drone_id,
                    attacker_level=AttackerLevel.L3_ADVANCED,
                    urgency=final_urgency,
                    recommended_actions=[
                        "ip_shuffle", "port_rotate", "service_migrate",
                    ],
                )
                await self._mtd_trigger_q.put(trigger)

                logger.warning(
                    "honeypot detection suspected → mtd trigger",
                    drone_id=self._config.drone_id,
                    attacker_ip=belief.attacker_ip,
                    p_real=round(belief.p_believes_real, 3),
                    urgency=round(final_urgency, 3),
                )

    # ── 서비스/breadcrumb 초기 배치 ───────────────────────────────────────────

    async def _spawn_initial_services(self) -> None:
        """
        [ROLE] 초기 ghost 서비스 세트 생성.
               기본: HTTP + RTSP (L0-L1 수준)

        [DATA FLOW]
            HoneyDroneConfig 포트 기반 ──▶ ghost 서비스 2개 생성
        """
        base_offset = _GHOST_SERVICE_PORT_OFFSET + self._config.index * 10

        initial_services = [
            (GhostServiceType.HTTP, self._config.http_port + base_offset),
            (GhostServiceType.RTSP, self._config.rtsp_port + base_offset),
        ]

        for svc_type, port in initial_services:
            try:
                await self._factory.create_service(
                    svc_type, port, self._current_attacker_level,
                )
            except Exception as e:
                logger.error(
                    "initial ghost service spawn failed",
                    service_type=svc_type.value,
                    port=port,
                    error=str(e),
                )

        self._last_ghost_spawn_ns = time.time_ns()

    async def _spawn_level_services(self, level: AttackerLevel) -> None:
        """
        [ROLE] 공격자 레벨에 따른 추가 ghost 서비스 생성.

        [DATA FLOW]
            AttackerLevel ──▶ 레벨별 서비스 타입 결정 ──▶ create_service()
        """
        # 쿨다운 검사
        elapsed = (time.time_ns() - self._last_ghost_spawn_ns) / 1e9
        if elapsed < _GHOST_SPAWN_COOLDOWN_SEC:
            return

        base_offset = _GHOST_SERVICE_PORT_OFFSET + self._config.index * 10
        new_services: list[tuple[GhostServiceType, int]] = []

        if level >= AttackerLevel.L2_INTERMEDIATE:
            new_services.append((
                GhostServiceType.MAVLINK,
                self._config.mavlink_port + base_offset,
            ))
            new_services.append((
                GhostServiceType.SSH,
                22000 + self._config.index,
            ))

        if level >= AttackerLevel.L3_ADVANCED:
            new_services.append((
                GhostServiceType.OPENCLAW,
                self._config.webclaw_port + base_offset,
            ))

        for svc_type, port in new_services:
            # 이미 해당 타입이 실행 중이면 건너뛰기
            existing = [
                s for s in self._factory.active_services
                if s.service_type == svc_type
            ]
            if existing:
                continue
            try:
                await self._factory.create_service(svc_type, port, level)
            except Exception as e:
                logger.debug(
                    "level service spawn failed",
                    service_type=svc_type.value,
                    port=port,
                    error=str(e),
                )

        self._last_ghost_spawn_ns = time.time_ns()

    def _plant_initial_breadcrumbs(self) -> None:
        """
        [ROLE] 초기 breadcrumb 세트 배치.

        [DATA FLOW]
            current_attacker_level ──▶ planter.generate_breadcrumbs()
        """
        ghost_ports = [s.port for s in self._factory.active_services]
        self._planter.generate_breadcrumbs(
            self._current_attacker_level, ghost_ports,
        )

    # ── 외부 인터페이스 ───────────────────────────────────────────────────────

    def get_http_breadcrumb_data(self) -> dict:
        """
        [ROLE] FakeServiceFactory HTTP 응답에 삽입할 breadcrumb 데이터.
               HTTP ghost 서비스가 응답 생성 시 호출.

        [DATA FLOW]
            planter.get_http_injection_data() ──▶ dict
        """
        return self._planter.get_http_injection_data()

    def get_belief_state(self, attacker_ip: str) -> Optional[AttackerBeliefState]:
        """[ROLE] 특정 공격자의 현재 믿음 상태 조회."""
        return self._state_mgr.get_belief(attacker_ip)

    def get_all_belief_states(self) -> list[AttackerBeliefState]:
        """[ROLE] 모든 공격자의 현재 믿음 상태 반환."""
        return self._state_mgr.get_all_beliefs()

    def get_urgency_modifier(self, attacker_ip: str) -> float:
        """
        [ROLE] 믿음 상태 기반 MTD urgency 보정값.
               AgenticDecoyEngine._assess_and_signal()에서 호출하여
               기존 urgency에 가산.

        [DATA FLOW]
            attacker_ip ──▶ state_mgr.get_urgency_modifier()
            ──▶ float [0.0, 1.0]
        """
        return self._state_mgr.get_urgency_modifier(attacker_ip)
