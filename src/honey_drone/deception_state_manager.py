#!/usr/bin/env python3
"""
deception_state_manager.py — 공격자 믿음 상태 추적기 (Deception State Manager)

Project  : MIRAGE-UAS
Module   : Honey Drone / Deception State Manager
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - MavlinkCaptureEvent       (공격자 행동 관측)
    - Breadcrumb 접촉 이벤트     (breadcrumb 사용 시 신호)
    - GhostService 접촉 이벤트   (ghost 서비스 접촉 시 신호)

[Outputs]
    - AttackerBeliefState        (공격자의 현재 믿음 상태)
    - DeceptionEffectiveness     (기만 효과 측정 지표)
    - MTD urgency 보정값          (믿음 상태 기반 urgency 가중치)

[Dependencies]
    - asyncio (stdlib)
    - math    (stdlib, 베이지안 갱신)

[설계 원칙]
    ① 공격자의 "믿음 상태"를 확률 분포로 추적 (Bayesian belief update)
    ② 기만 레이어별 효과 독립 측정: ghost 서비스 / breadcrumb / MTD 각각
    ③ 공격자가 진짜/가짜를 구분하기 시작하면 urgency 급상승 → MTD 트리거
    ④ 논문 DES(Eq.19) 지표의 deception_engagement 하위 성분 제공
    ⑤ 다수 공격자 동시 추적 (per-attacker 독립 상태)

[DATA FLOW]
    AgenticDecoyEngine / FakeServiceFactory / BreadcrumbPlanter
    ──▶ DeceptionStateManager.observe_*()
    ──▶ AttackerBeliefState (per attacker_ip)
    ──▶ DeceptionOrchestrator._assess_deception()
    ──▶ MTD urgency 보정 / 서비스 재배치 결정
"""

import asyncio
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from shared.logger import get_logger
from shared.models import AttackerLevel, DroneProtocol

logger = get_logger(__name__)


# ── 공격자가 믿는 "진짜"의 대상 ──────────────────────────────────────────────
class BeliefTarget(str, Enum):
    REAL_DRONE     = "real_drone"      # 진짜 드론이라고 믿음
    HONEYPOT       = "honeypot"        # 허니팟이라고 의심
    GHOST_SERVICE  = "ghost_service"   # ghost 서비스라고 의심
    UNKNOWN        = "unknown"         # 판단 불가


# ── 관측 이벤트 유형 ──────────────────────────────────────────────────────────
class ObservationType(str, Enum):
    PROTOCOL_INTERACT   = "protocol_interact"     # 프로토콜 상호작용
    BREADCRUMB_ACCESS   = "breadcrumb_access"     # breadcrumb 접근
    BREADCRUMB_USE      = "breadcrumb_use"        # breadcrumb 실제 사용 (인증 시도 등)
    GHOST_CONNECT       = "ghost_connect"         # ghost 서비스 접속
    GHOST_DEEP_INTERACT = "ghost_deep_interact"   # ghost 서비스 심층 상호작용
    RECONNECT_SAME      = "reconnect_same"        # 동일 서비스 재접속
    RECONNECT_DIFFERENT = "reconnect_different"   # 다른 서비스로 이동
    SCAN_DETECTED       = "scan_detected"         # 포트 스캔 탐지
    EXPLOIT_ATTEMPT     = "exploit_attempt"       # exploit 시도
    EVASION_BEHAVIOR    = "evasion_behavior"      # 탐지 회피 행동


# ── 관측 이벤트와 믿음 갱신 가중치 ────────────────────────────────────────────
# P(observation | belief=REAL_DRONE) 대비 P(observation | belief=HONEYPOT) 비율
# likelihood_ratio > 1.0 → 공격자가 "진짜"라고 더 믿게 됨
# likelihood_ratio < 1.0 → 공격자가 "가짜"라고 의심하게 됨
_LIKELIHOOD_RATIOS: dict[ObservationType, float] = {
    ObservationType.PROTOCOL_INTERACT:   1.2,   # 일반 상호작용 → 약간 진짜 쪽
    ObservationType.BREADCRUMB_ACCESS:   1.8,   # breadcrumb 발견 → 유인 성공
    ObservationType.BREADCRUMB_USE:      2.5,   # breadcrumb 사용 → 강하게 속음
    ObservationType.GHOST_CONNECT:       1.5,   # ghost 서비스 접속 → 유인 성공
    ObservationType.GHOST_DEEP_INTERACT: 2.0,   # ghost 심층 상호작용 → 강하게 속음
    ObservationType.RECONNECT_SAME:      1.3,   # 재접속 → 관심 유지
    ObservationType.RECONNECT_DIFFERENT: 0.7,   # 다른 서비스 탐색 → 약간 의심
    ObservationType.SCAN_DETECTED:       0.5,   # 포트 스캔 → 가짜 의심
    ObservationType.EXPLOIT_ATTEMPT:     1.1,   # exploit 시도 → 진짜로 판단
    ObservationType.EVASION_BEHAVIOR:    0.3,   # 회피 행동 → 강하게 의심
}


# ── AttackerBeliefState ───────────────────────────────────────────────────────
@dataclass
class AttackerBeliefState:
    """
    [ROLE] 특정 공격자의 현재 믿음 상태.
           베이지안 사후확률로 갱신되는 확률 분포.

    [REF] MIRAGE-UAS Eq.19 DES 하위 성분
    """
    attacker_ip        : str
    drone_id           : str
    # P(real_drone | observations) — 공격자가 진짜 드론이라고 믿을 확률
    p_believes_real    : float = 0.7    # 초기 prior: 70% "진짜"라고 믿음
    # 관측 이력
    total_observations : int   = 0
    breadcrumbs_seen   : int   = 0
    breadcrumbs_used   : int   = 0
    ghost_interactions : int   = 0
    scan_events        : int   = 0
    evasion_events     : int   = 0
    # 시간
    first_seen_ns      : int   = field(default_factory=lambda: time.time_ns())
    last_update_ns     : int   = field(default_factory=lambda: time.time_ns())

    @property
    def belief_target(self) -> BeliefTarget:
        """공격자의 주된 믿음 판단."""
        if self.p_believes_real >= 0.7:
            return BeliefTarget.REAL_DRONE
        if self.p_believes_real <= 0.3:
            return BeliefTarget.HONEYPOT
        return BeliefTarget.UNKNOWN

    @property
    def deception_success_score(self) -> float:
        """
        [ROLE] 기만 성공 점수 [0.0, 1.0].
               p_believes_real이 높을수록 기만 성공적.
        """
        return self.p_believes_real

    @property
    def dwell_time_sec(self) -> float:
        return (self.last_update_ns - self.first_seen_ns) / 1e9

    def __repr__(self) -> str:
        return (
            f"AttackerBeliefState(ip={self.attacker_ip}, "
            f"drone={self.drone_id}, "
            f"p_real={self.p_believes_real:.3f}, "
            f"belief={self.belief_target.value}, "
            f"obs={self.total_observations})"
        )


# ── DeceptionEffectiveness ────────────────────────────────────────────────────
@dataclass
class DeceptionEffectiveness:
    """
    [ROLE] 기만 효과 집계 지표.
           논문 DES(Eq.19) deception_engagement 성분의 근거 데이터.

    [DATA FLOW]
        DeceptionStateManager.get_effectiveness()
        ──▶ DeceptionEffectiveness
        ──▶ 논문 Table V (DES score)
    """
    drone_id               : str
    total_attackers        : int   = 0
    avg_p_believes_real    : float = 0.0   # 전체 공격자 평균 P(real)
    breadcrumb_hit_rate    : float = 0.0   # breadcrumb 접근/사용 비율
    ghost_engagement_rate  : float = 0.0   # ghost 서비스 상호작용 비율
    avg_dwell_time_sec     : float = 0.0   # 평균 체류 시간
    honeypot_detected_rate : float = 0.0   # 허니팟 탐지된 비율 (P(real)<0.3)

    def __repr__(self) -> str:
        return (
            f"DeceptionEffectiveness(drone={self.drone_id}, "
            f"attackers={self.total_attackers}, "
            f"avg_belief={self.avg_p_believes_real:.3f}, "
            f"bc_hit={self.breadcrumb_hit_rate:.2f}, "
            f"detected={self.honeypot_detected_rate:.2f})"
        )


# ── DeceptionStateManager ────────────────────────────────────────────────────
class DeceptionStateManager:
    """
    [ROLE] 모든 공격자의 믿음 상태를 베이지안 방식으로 추적.
           기만 효과를 실시간 측정하고 MTD urgency 보정값 제공.

    [DATA FLOW]
        관측 이벤트 (observe_*() 메서드)
        ──▶ 베이지안 사후확률 갱신 (_bayesian_update)
        ──▶ AttackerBeliefState 갱신
        ──▶ DeceptionOrchestrator 의사결정 지원
    """

    def __init__(self, drone_id: str) -> None:
        self._drone_id = drone_id
        # attacker_ip → AttackerBeliefState
        self._beliefs: dict[str, AttackerBeliefState] = {}
        self._lock = asyncio.Lock()

    def _get_or_create_belief(self, attacker_ip: str) -> AttackerBeliefState:
        """[ROLE] 공격자별 믿음 상태 조회/생성."""
        if attacker_ip not in self._beliefs:
            self._beliefs[attacker_ip] = AttackerBeliefState(
                attacker_ip=attacker_ip,
                drone_id=self._drone_id,
            )
        return self._beliefs[attacker_ip]

    async def observe_protocol_interaction(
        self, attacker_ip: str, protocol: DroneProtocol,
    ) -> AttackerBeliefState:
        """
        [ROLE] 프로토콜 상호작용 관측 시 믿음 갱신.

        [DATA FLOW]
            protocol interaction ──▶ Bayesian update ──▶ AttackerBeliefState
        """
        async with self._lock:
            state = self._get_or_create_belief(attacker_ip)
            self._bayesian_update(state, ObservationType.PROTOCOL_INTERACT)
            return state

    async def observe_breadcrumb_access(
        self, attacker_ip: str, breadcrumb_id: str,
    ) -> AttackerBeliefState:
        """
        [ROLE] 공격자가 breadcrumb을 발견(접근)했을 때 믿음 갱신.

        [DATA FLOW]
            breadcrumb access ──▶ Bayesian update ──▶ AttackerBeliefState
        """
        async with self._lock:
            state = self._get_or_create_belief(attacker_ip)
            state.breadcrumbs_seen += 1
            self._bayesian_update(state, ObservationType.BREADCRUMB_ACCESS)
            logger.info(
                "breadcrumb accessed — belief updated",
                attacker_ip=attacker_ip,
                breadcrumb_id=breadcrumb_id[:8],
                p_real=round(state.p_believes_real, 3),
            )
            return state

    async def observe_breadcrumb_use(
        self, attacker_ip: str, breadcrumb_id: str,
    ) -> AttackerBeliefState:
        """
        [ROLE] 공격자가 breadcrumb을 실제 사용(인증 시도 등)했을 때 믿음 갱신.
               가장 강력한 기만 성공 신호.

        [DATA FLOW]
            breadcrumb use ──▶ Bayesian update (강한 가중치)
            ──▶ AttackerBeliefState
        """
        async with self._lock:
            state = self._get_or_create_belief(attacker_ip)
            state.breadcrumbs_used += 1
            self._bayesian_update(state, ObservationType.BREADCRUMB_USE)
            logger.warning(
                "breadcrumb USED by attacker — strong deception signal",
                attacker_ip=attacker_ip,
                breadcrumb_id=breadcrumb_id[:8],
                p_real=round(state.p_believes_real, 3),
            )
            return state

    async def observe_ghost_interaction(
        self, attacker_ip: str, service_type: str, deep: bool = False,
    ) -> AttackerBeliefState:
        """
        [ROLE] ghost 서비스 접촉/심층 상호작용 관측.

        [DATA FLOW]
            ghost interaction ──▶ Bayesian update ──▶ AttackerBeliefState
        """
        obs_type = (
            ObservationType.GHOST_DEEP_INTERACT if deep
            else ObservationType.GHOST_CONNECT
        )
        async with self._lock:
            state = self._get_or_create_belief(attacker_ip)
            state.ghost_interactions += 1
            self._bayesian_update(state, obs_type)
            return state

    async def observe_scan(self, attacker_ip: str) -> AttackerBeliefState:
        """
        [ROLE] 포트 스캔 탐지 시 믿음 갱신 (가짜 의심 증가).

        [DATA FLOW]
            scan detection ──▶ Bayesian update (의심 방향) ──▶ AttackerBeliefState
        """
        async with self._lock:
            state = self._get_or_create_belief(attacker_ip)
            state.scan_events += 1
            self._bayesian_update(state, ObservationType.SCAN_DETECTED)
            return state

    async def observe_evasion(self, attacker_ip: str) -> AttackerBeliefState:
        """
        [ROLE] 탐지 회피 행동 관측 (공격자가 허니팟을 의심하는 강한 신호).

        [DATA FLOW]
            evasion behavior ──▶ Bayesian update (강한 의심)
            ──▶ AttackerBeliefState
        """
        async with self._lock:
            state = self._get_or_create_belief(attacker_ip)
            state.evasion_events += 1
            self._bayesian_update(state, ObservationType.EVASION_BEHAVIOR)
            logger.warning(
                "evasion behavior detected — attacker may suspect honeypot",
                attacker_ip=attacker_ip,
                p_real=round(state.p_believes_real, 3),
            )
            return state

    async def observe_exploit_attempt(
        self, attacker_ip: str,
    ) -> AttackerBeliefState:
        """
        [ROLE] exploit 시도 관측 (공격자가 진짜라고 믿고 있다는 신호).

        [DATA FLOW]
            exploit attempt ──▶ Bayesian update ──▶ AttackerBeliefState
        """
        async with self._lock:
            state = self._get_or_create_belief(attacker_ip)
            self._bayesian_update(state, ObservationType.EXPLOIT_ATTEMPT)
            return state

    def get_belief(self, attacker_ip: str) -> Optional[AttackerBeliefState]:
        """[ROLE] 특정 공격자의 현재 믿음 상태 조회."""
        return self._beliefs.get(attacker_ip)

    def get_all_beliefs(self) -> list[AttackerBeliefState]:
        """[ROLE] 모든 공격자의 현재 믿음 상태 반환."""
        return list(self._beliefs.values())

    def get_urgency_modifier(self, attacker_ip: str) -> float:
        """
        [ROLE] 공격자의 믿음 상태 기반 MTD urgency 보정 가중치.
               공격자가 가짜라고 의심할수록 urgency 상승.

        [DATA FLOW]
            AttackerBeliefState.p_believes_real
            ──▶ urgency_modifier float [0.0, 1.0]
            ──▶ DeceptionOrchestrator → MTD urgency 보정

        [보정 로직]
            - P(real) >= 0.7: modifier = 0.0 (기만 성공, 추가 MTD 불필요)
            - P(real) <= 0.3: modifier = 1.0 (기만 실패, 즉각 MTD 필요)
            - 중간: 선형 보간
        """
        state = self._beliefs.get(attacker_ip)
        if not state:
            return 0.0

        p = state.p_believes_real
        if p >= 0.7:
            return 0.0
        if p <= 0.3:
            return 1.0
        # [0.3, 0.7] → [1.0, 0.0] 선형 보간
        return (0.7 - p) / 0.4

    def get_effectiveness(self) -> DeceptionEffectiveness:
        """
        [ROLE] 전체 기만 효과 집계.
               논문 DES(Eq.19) deception_engagement 성분.

        [DATA FLOW]
            all AttackerBeliefStates ──▶ 집계 ──▶ DeceptionEffectiveness
        """
        states = list(self._beliefs.values())
        if not states:
            return DeceptionEffectiveness(drone_id=self._drone_id)

        n = len(states)
        avg_p = sum(s.p_believes_real for s in states) / n
        avg_dwell = sum(s.dwell_time_sec for s in states) / n

        bc_seen_total = sum(s.breadcrumbs_seen for s in states)
        bc_used_total = sum(s.breadcrumbs_used for s in states)
        ghost_total   = sum(s.ghost_interactions for s in states)
        detected      = sum(1 for s in states if s.belief_target == BeliefTarget.HONEYPOT)

        return DeceptionEffectiveness(
            drone_id=self._drone_id,
            total_attackers=n,
            avg_p_believes_real=avg_p,
            breadcrumb_hit_rate=bc_used_total / max(bc_seen_total, 1),
            ghost_engagement_rate=ghost_total / max(n, 1),
            avg_dwell_time_sec=avg_dwell,
            honeypot_detected_rate=detected / n,
        )

    def clear_attacker(self, attacker_ip: str) -> None:
        """[ROLE] 특정 공격자의 믿음 상태 제거 (세션 종료 시)."""
        self._beliefs.pop(attacker_ip, None)

    def clear_all(self) -> None:
        """[ROLE] 모든 믿음 상태 초기화 (MTD 로테이션 시)."""
        self._beliefs.clear()

    # ── 베이지안 갱신 ─────────────────────────────────────────────────────────

    def _bayesian_update(
        self, state: AttackerBeliefState, obs_type: ObservationType,
    ) -> None:
        """
        [ROLE] 베이지안 사후확률 갱신.
               P(real | obs) = P(obs|real) · P(real) / P(obs)

        [수식]
            prior = state.p_believes_real
            lr    = _LIKELIHOOD_RATIOS[obs_type]

            P(obs|real) ∝ lr
            P(obs|fake) ∝ 1.0

            posterior = (lr · prior) / (lr · prior + 1.0 · (1 - prior))

        [DATA FLOW]
            prior + likelihood_ratio ──▶ posterior ──▶ state.p_believes_real
        """
        lr = _LIKELIHOOD_RATIOS.get(obs_type, 1.0)
        prior = state.p_believes_real

        # 수치 안정성: prior를 [0.01, 0.99]로 클램프
        prior = max(0.01, min(0.99, prior))

        numerator   = lr * prior
        denominator = numerator + 1.0 * (1.0 - prior)
        posterior   = numerator / denominator

        state.p_believes_real = posterior
        state.total_observations += 1
        state.last_update_ns = time.time_ns()
