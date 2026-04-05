#!/usr/bin/env python3
"""
mtd_actions.py — MTD 액션 타입 정의 및 비용 계산

Project  : MIRAGE-UAS
Module   : MTD / Actions
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - constants.py (MTD_COST_SENSITIVITY_KAPPA, MTD_ALPHA_WEIGHTS)

[Outputs]
    - MTDActionType   → engagement_to_mtd.py
    - MTDAction       → mtd_executor.py
    - MTDResult       → mtd_executor.py 반환값
    - compute_action_cost() → r_mtd 비용 산출

[REF]
    MIRAGE-UAS Eq.17: C_mtd(a) = κ_ℓ · Σᵢ αᵢ · cost_i(a)
    α 인덱스 순서: [freq_hop(0), ip_shuffle(1), port_rotate(2),
                    proto_change(3), route_morph(4), key_rotate(5), service_migrate(6)]
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum

from shared.constants import MTD_ALPHA_WEIGHTS, MTD_COST_SENSITIVITY_KAPPA
from shared.logger import get_logger

logger = get_logger(__name__)


# ── MTDActionType ──────────────────────────────────────────────────────────────
# 인덱스 = MTD_ALPHA_WEIGHTS 내 위치 (Eq.17 αᵢ 매핑)
class MTDActionType(IntEnum):
    FREQ_HOP         = 0   # 주파수 채널 교체 (MAVLink RF 레이어)
    IP_SHUFFLE       = 1   # Docker 컨테이너 IP 재할당
    PORT_ROTATE      = 2   # MAVLink / HTTP 포트 변경
    PROTO_CHANGE     = 3   # MAVLink v1 ↔ v2 / UDP ↔ TCP 전환
    ROUTE_MORPH      = 4   # MAVLink Router 경로 재설정
    KEY_ROTATE       = 5   # MAVLink 서명 키 갱신
    SERVICE_MIGRATE  = 6   # CC 서비스 새 컨테이너로 마이그레이션


# ── 단위 비용 테이블 ───────────────────────────────────────────────────────────
# cost_i(a): 각 액션의 운영 비용 (정규화 [0,1])
# 논문 §4 Table I에서 정당화
_ACTION_BASE_COST: dict[MTDActionType, float] = {
    MTDActionType.FREQ_HOP:        0.10,
    MTDActionType.IP_SHUFFLE:      0.30,
    MTDActionType.PORT_ROTATE:     0.15,
    MTDActionType.PROTO_CHANGE:    0.25,
    MTDActionType.ROUTE_MORPH:     0.35,
    MTDActionType.KEY_ROTATE:      0.20,
    MTDActionType.SERVICE_MIGRATE: 0.80,   # 컨테이너 재시작 비용 최대
}


# ── MTDAction ──────────────────────────────────────────────────────────────────
@dataclass
class MTDAction:
    """
    [ROLE] 실행할 MTD 액션 단위 레코드.
           engagement_to_mtd.py가 생성하고 mtd_executor.py가 실행.

    [DATA FLOW]
        MTDTrigger ──▶ EngagementToMTDConverter ──▶ MTDAction list
        MTDAction ──▶ MTDExecutor.execute()
    """
    action_id      : str          = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ns   : int          = field(default_factory=time.time_ns)
    action_type    : MTDActionType = MTDActionType.PORT_ROTATE
    target_drone_id: str          = ""
    # 액션 파라미터 (타입별로 상이)
    parameters     : dict         = field(default_factory=dict)
    # Eq.17 비용 (executor가 실행 전 계산)
    cost           : float        = 0.0
    urgency        : float        = 0.0   # 트리거 urgency 상속

    def __repr__(self) -> str:
        return (
            f"MTDAction(id={self.action_id[:8]}, "
            f"type={self.action_type.name}, "
            f"drone={self.target_drone_id}, "
            f"cost={self.cost:.3f}, urgency={self.urgency:.2f})"
        )


# ── MTDResult ──────────────────────────────────────────────────────────────────
@dataclass
class MTDResult:
    """
    [ROLE] MTDExecutor.execute() 반환값. 논문 Table III 데이터 소스.

    [DATA FLOW]
        MTDExecutor.execute(action) ──▶ MTDResult
        MTDResult ──▶ MetricsCollector (Phase C2)
    """
    action_id       : str
    action_type     : MTDActionType
    target_drone_id : str
    success         : bool
    execution_time_ms: float = 0.0   # 실행 소요 시간 (논문 Table III)
    error_msg       : str   = ""
    new_surface     : dict  = field(default_factory=dict)  # 변경 후 attack surface 상태
    timestamp_ns    : int   = field(default_factory=time.time_ns)

    def __repr__(self) -> str:
        status = "OK" if self.success else f"FAIL({self.error_msg[:30]})"
        return (
            f"MTDResult(action={self.action_id[:8]}, "
            f"type={self.action_type.name}, "
            f"status={status}, "
            f"exec_ms={self.execution_time_ms:.1f})"
        )


# ── 비용 계산 함수 ─────────────────────────────────────────────────────────────

def compute_action_cost(action_type: MTDActionType) -> float:
    """
    [ROLE] 단일 MTD 액션의 Eq.17 비용 계산.
           C_mtd(a) = κ_ℓ · αᵢ · cost_i(a)

    [DATA FLOW]
        MTDActionType ──▶ MTD_ALPHA_WEIGHTS[i] * BASE_COST[i] * KAPPA ──▶ float

    [REF] MIRAGE-UAS Eq.17
    """
    idx       = int(action_type)
    alpha_i   = MTD_ALPHA_WEIGHTS[idx]
    base_cost = _ACTION_BASE_COST[action_type]
    cost      = MTD_COST_SENSITIVITY_KAPPA * alpha_i * base_cost
    return round(cost, 6)


def compute_batch_cost(action_types: list[MTDActionType]) -> float:
    """
    [ROLE] 복수 액션 배치의 총 비용 합산.
           논문 실험에서 액션 조합 비용 비교에 사용.

    [DATA FLOW]
        list[MTDActionType] ──▶ Σ compute_action_cost(a) ──▶ float
    """
    return sum(compute_action_cost(a) for a in action_types)


def build_action(
    action_type: MTDActionType,
    target_drone_id: str,
    urgency: float,
    **kwargs,
) -> MTDAction:
    """
    [ROLE] MTDAction 인스턴스 생성 헬퍼.
           비용을 자동 계산하여 포함.

    [DATA FLOW]
        MTDActionType + 파라미터 ──▶ MTDAction (cost 자동 산출)
    """
    return MTDAction(
        action_type=action_type,
        target_drone_id=target_drone_id,
        parameters=kwargs,
        cost=compute_action_cost(action_type),
        urgency=urgency,
    )
