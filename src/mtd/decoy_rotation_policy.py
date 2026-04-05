#!/usr/bin/env python3
"""
decoy_rotation_policy.py — 허니드론 로테이션(MTD rotate) 시점 결정 정책

Project  : MIRAGE-UAS
Module   : MTD / Decoy Rotation Policy
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - MTDTrigger           (현재 트리거 컨텍스트)
    - HoneyDroneInstance   (해당 드론 상태)
    - MTDResult list       (최근 실행 결과)

[Outputs]
    - bool: True = HoneyDroneManager.rotate() 호출 필요

[설계 원칙]
    경량 MTD(port/ip/key)로 충분한 경우 rotate 안 함.
    완전 교체(SERVICE_MIGRATE)가 필요한 조건:
      1. L4 APT + exploit 성공
      2. 동일 드론에 로테이션 쿨다운 이후 L3+ 재공격
      3. max_dwell_sec 초과
    논문 §4: "MTD + Decoy Rotation as defense layer"

[REF] MIRAGE-UAS §4 MTD Surface Controller
"""

from __future__ import annotations

from shared.logger import get_logger
from shared.models import AttackerLevel, HoneyDroneInstance, MTDTrigger
from mtd.mtd_actions import MTDActionType, MTDResult

logger = get_logger(__name__)

# 로테이션 쿨다운 (초) — 연속 rotate 방지 (인프라 운영값)
_ROTATION_COOLDOWN_SEC: float = 60.0
# L3+ 재공격 시 로테이션 트리거 임계 명령 수
_REATTACK_CMD_THRESHOLD: int = 15


class DecoyRotationPolicy:
    """
    [ROLE] MTDTrigger와 드론 상태를 분석하여
           경량 MTD로 충분한지 vs 전체 컨테이너 교체가 필요한지 판정.

    [DATA FLOW]
        MTDTrigger + HoneyDroneInstance + list[MTDResult]
        ──▶ should_rotate() ──▶ bool
        True  → HoneyDroneManager.rotate(drone_id)
        False → MTDExecutor에 경량 액션만 실행
    """

    def __init__(self) -> None:
        # drone_id → 마지막 rotate 시각 (time.time())
        self._last_rotation: dict[str, float] = {}

    def should_rotate(
        self,
        trigger: MTDTrigger,
        instance: HoneyDroneInstance,
        recent_results: list[MTDResult],
    ) -> bool:
        """
        [ROLE] 허니드론 전체 교체 여부 판정.
               아래 조건 중 하나 충족 시 True 반환.

        [DATA FLOW]
            MTDTrigger.urgency + HoneyDroneInstance + recent_results
            ──▶ 조건 평가 ──▶ bool
        """
        drone_id = trigger.source_drone_id
        metrics  = trigger.engagement
        level    = trigger.attacker_level

        # 조건 1: L4 APT + exploit 성공
        if (
            level >= AttackerLevel.L4_APT
            and metrics is not None
            and metrics.exploit_attempts > 0
        ):
            return self._check_cooldown_and_record(drone_id, reason="L4_exploit")

        # 조건 2: max_dwell_sec 초과 (공격자가 너무 오래 머뭄)
        if (
            metrics is not None
            and metrics.dwell_time_sec > instance.config.max_dwell_sec
        ):
            return self._check_cooldown_and_record(drone_id, reason="max_dwell_exceeded")

        # 조건 3: SERVICE_MIGRATE 액션이 최근 결과에 포함
        migrate_requested = any(
            r.action_type == MTDActionType.SERVICE_MIGRATE
            and r.new_surface.get("migrate_requested")
            for r in recent_results
        )
        if migrate_requested:
            return self._check_cooldown_and_record(drone_id, reason="service_migrate_requested")

        # 조건 4: L3+ 공격자가 대량 명령 후 재접속 (같은 드론에 재공격)
        if (
            level >= AttackerLevel.L3_ADVANCED
            and metrics is not None
            and metrics.commands_issued >= _REATTACK_CMD_THRESHOLD
            and instance.rotation_count > 0  # 이미 한 번 rotate 경험
        ):
            return self._check_cooldown_and_record(drone_id, reason="L3_reattack")

        return False

    def _check_cooldown_and_record(self, drone_id: str, reason: str) -> bool:
        """
        [ROLE] 쿨다운 확인 후 rotate 여부 반환.
               쿨다운 미경과 시 False → 불필요한 rotate 방지.

        [DATA FLOW]
            drone_id ──▶ 마지막 rotate 시각 확인
            ──▶ 쿨다운 경과 시 True + 시각 갱신
            ──▶ 미경과 시 False
        """
        import time
        now      = time.time()
        last     = self._last_rotation.get(drone_id, 0.0)
        elapsed  = now - last

        if elapsed < _ROTATION_COOLDOWN_SEC:
            logger.debug(
                "rotation_cooldown_active",
                drone_id=drone_id,
                reason=reason,
                remaining_sec=round(_ROTATION_COOLDOWN_SEC - elapsed, 1),
            )
            return False

        self._last_rotation[drone_id] = now
        logger.info(
            "rotation_decision_true",
            drone_id=drone_id,
            reason=reason,
        )
        return True
