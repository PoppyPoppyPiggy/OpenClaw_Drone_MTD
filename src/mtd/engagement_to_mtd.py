#!/usr/bin/env python3
"""
engagement_to_mtd.py — Engagement 신호 → MTD 액션 변환기

Project  : MIRAGE-UAS
Module   : MTD / Engagement-to-MTD Converter
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - MTDTrigger (AgenticDecoyEngine → asyncio.Queue)
    - constants.py (MTD_ALPHA_WEIGHTS, MTD_BREACH_PREVENTION_BETA)

[Outputs]
    - list[MTDAction] → MTDExecutor

[설계 원칙]
    - urgency에 비례하여 투입 액션 수 결정
    - recommended_actions 힌트를 우선하되 비용/urgency 균형 반영
    - 동일 액션 중복 배제
    - Eq.17 비용 합이 urgency * MAX_BUDGET을 초과하지 않도록 제한

[REF] MIRAGE-UAS §4 MTD Surface Controller
      Eq.17: C_mtd(a) = κ_ℓ · Σᵢ αᵢ · cost_i(a)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from shared.constants import (
    MTD_ALPHA_WEIGHTS,
    MTD_BREACH_PREVENTION_BETA,
)
from shared.logger import get_logger
from shared.models import AttackerLevel, MTDTrigger
from mtd.mtd_actions import (
    MTDAction,
    MTDActionType,
    build_action,
    compute_batch_cost,
)

logger = get_logger(__name__)

# ── 비용 예산 한도 (인프라 운영값) ─────────────────────────────────────────────
# urgency=1.0일 때 허용 최대 총 비용
_MAX_COST_BUDGET: float = 0.5

# 공격자 레벨별 기본 액션 정책
# urgency 기반으로 최종 필터링
_LEVEL_ACTION_POLICY: dict[AttackerLevel, list[MTDActionType]] = {
    AttackerLevel.L0_SCRIPT_KIDDIE: [
        MTDActionType.PORT_ROTATE,
    ],
    AttackerLevel.L1_BASIC: [
        MTDActionType.PORT_ROTATE,
        MTDActionType.IP_SHUFFLE,
    ],
    AttackerLevel.L2_INTERMEDIATE: [
        MTDActionType.PORT_ROTATE,
        MTDActionType.IP_SHUFFLE,
        MTDActionType.KEY_ROTATE,
    ],
    AttackerLevel.L3_ADVANCED: [
        MTDActionType.IP_SHUFFLE,
        MTDActionType.PORT_ROTATE,
        MTDActionType.KEY_ROTATE,
        MTDActionType.PROTO_CHANGE,
    ],
    AttackerLevel.L4_APT: [
        MTDActionType.IP_SHUFFLE,
        MTDActionType.KEY_ROTATE,
        MTDActionType.PROTO_CHANGE,
        MTDActionType.SERVICE_MIGRATE,
        MTDActionType.ROUTE_MORPH,
        MTDActionType.FREQ_HOP,
    ],
}


class EngagementToMTDConverter:
    """
    [ROLE] MTDTrigger를 소비하는 비동기 변환기.
           engagment 지표 → MTDAction 리스트 변환.
           MTDExecutor와 함께 Track A의 제어 루프를 구성.

    [DATA FLOW]
        asyncio.Queue[MTDTrigger] (AgenticDecoyEngine 출력)
        ──▶ EngagementToMTDConverter.run()
        ──▶ list[MTDAction]
        ──▶ asyncio.Queue[MTDAction] (MTDExecutor 입력)
    """

    def __init__(
        self,
        trigger_queue: asyncio.Queue,
        action_queue: asyncio.Queue,
    ) -> None:
        self._trigger_q = trigger_queue
        self._action_q  = action_queue

    async def run(self) -> None:
        """
        [ROLE] MTDTrigger 큐를 지속 소비하며 MTDAction으로 변환.
               종료는 asyncio.CancelledError로 처리.

        [DATA FLOW]
            trigger_queue.get() ──▶ convert() ──▶ action_queue.put() × N
        """
        logger.info("engagement_to_mtd_converter started")
        while True:
            try:
                trigger: MTDTrigger = await self._trigger_q.get()
                actions = self.convert(trigger)
                for action in actions:
                    await self._action_q.put(action)
                    logger.debug(
                        "mtd_action_queued",
                        action_id=action.action_id[:8],
                        action_type=action.action_type.name,
                        drone_id=action.target_drone_id,
                        cost=action.cost,
                    )
                self._trigger_q.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("converter error", error=str(e))

    def convert(self, trigger: MTDTrigger) -> list[MTDAction]:
        """
        [ROLE] MTDTrigger 1개 → 실행할 MTDAction 리스트 변환.
               urgency에 비례하는 비용 예산 내에서 액션 선택.

        [DATA FLOW]
            MTDTrigger
            ──▶ 공격자 레벨 기반 후보 액션 목록 산출
            ──▶ recommended_actions 힌트 우선 정렬
            ──▶ 비용 예산 필터 (urgency * MAX_BUDGET)
            ──▶ list[MTDAction]
        """
        level   = trigger.attacker_level
        urgency = trigger.urgency
        drone   = trigger.source_drone_id

        # 1. 기본 정책에서 후보 목록 가져오기
        candidates = list(_LEVEL_ACTION_POLICY.get(level, [MTDActionType.PORT_ROTATE]))

        # 2. recommended_actions 힌트를 앞으로 재정렬
        hint_map = {
            "freq_hop":        MTDActionType.FREQ_HOP,
            "ip_shuffle":      MTDActionType.IP_SHUFFLE,
            "port_rotate":     MTDActionType.PORT_ROTATE,
            "proto_change":    MTDActionType.PROTO_CHANGE,
            "route_morph":     MTDActionType.ROUTE_MORPH,
            "key_rotate":      MTDActionType.KEY_ROTATE,
            "service_migrate": MTDActionType.SERVICE_MIGRATE,
        }
        hinted = [
            hint_map[h] for h in trigger.recommended_actions
            if h in hint_map and hint_map[h] in candidates
        ]
        ordered = hinted + [c for c in candidates if c not in hinted]

        # 3. urgency 기반 비용 예산 계산
        budget = urgency * _MAX_COST_BUDGET

        # 4. 예산 초과 없이 액션 선택
        selected: list[MTDAction] = []
        cumulative_cost = 0.0
        for action_type in ordered:
            action = build_action(
                action_type=action_type,
                target_drone_id=drone,
                urgency=urgency,
            )
            if cumulative_cost + action.cost <= budget:
                selected.append(action)
                cumulative_cost += action.cost
            else:
                break

        # 최소 1개 액션 보장 (예산 초과해도 최우선 액션 1개는 실행)
        if not selected and ordered:
            action = build_action(
                action_type=ordered[0],
                target_drone_id=drone,
                urgency=urgency,
            )
            selected.append(action)
            cumulative_cost = action.cost

        logger.info(
            "mtd_actions_converted",
            trigger_id=trigger.trigger_id[:8],
            drone_id=drone,
            level=level.name,
            urgency=round(urgency, 3),
            budget=round(budget, 4),
            actions=[a.action_type.name for a in selected],
            total_cost=round(cumulative_cost, 4),
        )
        return selected
