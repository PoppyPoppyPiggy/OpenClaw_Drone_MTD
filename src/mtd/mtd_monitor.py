#!/usr/bin/env python3
"""
mtd_monitor.py — MTD 실행 상태 실시간 모니터

Project  : MIRAGE-UAS
Module   : MTD / Monitor
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - Docker SDK (docker stats --no-stream)
    - HoneyDroneInstance registry (active containers)

[Outputs]
    - MTDTrigger (CPU 과부하 시 자동 포트 로테이션 추천)
    - results/logs/surface_changes.jsonl (MTD surface 변경 이력)

[Dependencies]
    - docker >= 7.0
    - asyncio (stdlib)

[설계 원칙]
    ① 5초 주기 docker stats 폴링 — 컨테이너 건강 상태 추적
    ② CPU > 80%: MTDTrigger 자동 생성 (urgency=0.3, port_rotate)
    ③ 컨테이너 비정상 종료 감지: HoneyDroneManager.rotate() 자동 호출
    ④ 모든 surface 변경을 JSONL 감사 로그로 보존 (재현성)

[DATA FLOW]
    docker stats ──▶ _poll_loop()
    ──▶ CPU > 80%: MTDTrigger ──▶ mtd_trigger_q
    ──▶ container exited: drone_manager.rotate()
    ──▶ surface_changes.jsonl
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from shared.constants import RESULTS_DIR
from shared.logger import get_logger
from shared.models import (
    AttackerLevel,
    HoneyDroneInstance,
    MTDTrigger,
)

logger = get_logger(__name__)

_POLL_INTERVAL_SEC  : float = 5.0
_CPU_THRESHOLD_PCT  : float = 80.0
_SURFACE_LOG_PATH   = Path(RESULTS_DIR) / "logs" / "surface_changes.jsonl"


class MTDMonitor:
    """
    [ROLE] 허니드론 컨테이너 상태를 실시간 모니터링하고
           이상 감지 시 자동 MTD 트리거 생성 또는 컨테이너 복구 수행.

    [DATA FLOW]
        start()
        ──▶ _poll_loop() (5초 주기)
            ──▶ docker stats 파싱
            ──▶ CPU 과부하: MTDTrigger push
            ──▶ container exit: drone_manager.rotate()
        ──▶ surface_changes.jsonl 감사 로그
    """

    def __init__(
        self,
        instances: dict[str, HoneyDroneInstance],
        mtd_trigger_q: asyncio.Queue,
        drone_manager: object | None = None,
    ) -> None:
        self._instances     = instances
        self._mtd_trigger_q = mtd_trigger_q
        self._drone_manager = drone_manager
        self._task: Optional[asyncio.Task] = None
        self._client = None

        try:
            import docker
            self._client = docker.from_env()
        except Exception as e:
            logger.warning("mtd_monitor docker init failed", error=str(e))

    async def start(self) -> None:
        """
        [ROLE] 모니터링 루프 시작.

        [DATA FLOW]
            start() ──▶ _poll_loop() 태스크 생성
        """
        _SURFACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(
            self._poll_loop(), name="mtd_monitor_poll"
        )
        logger.info("mtd_monitor started", instances=len(self._instances))

    async def stop(self) -> None:
        """
        [ROLE] 모니터링 루프 종료.

        [DATA FLOW]
            stop() ──▶ task.cancel()
        """
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("mtd_monitor stopped")

    def log_surface_change(
        self,
        drone_id: str,
        action: str,
        old_surface: dict,
        new_surface: dict,
    ) -> None:
        """
        [ROLE] MTD surface 변경 이력을 JSONL 감사 로그에 기록.

        [DATA FLOW]
            변경 정보 ──▶ surface_changes.jsonl append
        """
        record = {
            "timestamp_ns": time.time_ns(),
            "drone_id": drone_id,
            "action": action,
            **{f"old_{k}": v for k, v in old_surface.items()},
            **{f"new_{k}": v for k, v in new_surface.items()},
        }
        try:
            with open(_SURFACE_LOG_PATH, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("surface_log_write_failed", error=str(e))

    async def _poll_loop(self) -> None:
        """
        [ROLE] 5초 주기 docker stats 폴링 + 이상 감지 루프.

        [DATA FLOW]
            asyncio.sleep(5) ──▶ _check_container(instance)
            ──▶ CPU > 80%: _trigger_mtd()
            ──▶ exited: _recover_container()
        """
        while True:
            try:
                await asyncio.sleep(_POLL_INTERVAL_SEC)
                if self._client is None:
                    continue

                for drone_id, instance in list(self._instances.items()):
                    await self._check_container(drone_id, instance)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("mtd_monitor_poll_error", error=str(e))

    async def _check_container(
        self, drone_id: str, instance: HoneyDroneInstance
    ) -> None:
        """
        [ROLE] 단일 컨테이너 상태 확인: CPU/MEM + 실행 상태.

        [DATA FLOW]
            container.stats(stream=False) ──▶ CPU% / MEM% 파싱
            ──▶ 임계값 초과 시 MTDTrigger 또는 복구
        """
        cc_name = f"cc_{drone_id}"
        loop = asyncio.get_event_loop()

        try:
            stats = await loop.run_in_executor(
                None, self._get_container_stats, cc_name
            )
        except Exception as e:
            # 컨테이너가 존재하지 않거나 종료됨
            logger.warning(
                "container_unreachable",
                drone_id=drone_id,
                cc_name=cc_name,
                error=str(e),
            )
            await self._recover_container(drone_id)
            return

        if stats is None:
            return

        cpu_pct = stats.get("cpu_pct", 0.0)
        mem_pct = stats.get("mem_pct", 0.0)

        logger.debug(
            "container_stats",
            drone_id=drone_id,
            cpu_pct=round(cpu_pct, 1),
            mem_pct=round(mem_pct, 1),
        )

        # CPU 과부하: MTDTrigger 자동 생성
        if cpu_pct > _CPU_THRESHOLD_PCT:
            logger.warning(
                "cpu_threshold_exceeded",
                drone_id=drone_id,
                cpu_pct=round(cpu_pct, 1),
            )
            trigger = MTDTrigger(
                source_drone_id=drone_id,
                attacker_level=AttackerLevel.L1_BASIC,
                urgency=0.3,
                recommended_actions=["port_rotate"],
            )
            await self._mtd_trigger_q.put(trigger)

    def _get_container_stats(self, container_name: str) -> dict | None:
        """
        [ROLE] docker stats --no-stream 동기 호출 + CPU/MEM 파싱.

        [DATA FLOW]
            container.stats(stream=False) ──▶ {cpu_pct, mem_pct}
        """
        if self._client is None:
            return None

        try:
            c = self._client.containers.get(container_name)
        except Exception:
            return None

        if c.status != "running":
            raise RuntimeError(f"container {container_name} status={c.status}")

        stats = c.stats(stream=False)

        # CPU% 계산 (Docker stats API 공식)
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        sys_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        n_cpus = stats["cpu_stats"].get("online_cpus", 1)
        cpu_pct = (cpu_delta / max(sys_delta, 1)) * n_cpus * 100.0

        # MEM% 계산
        mem_usage = stats["memory_stats"].get("usage", 0)
        mem_limit = stats["memory_stats"].get("limit", 1)
        mem_pct = (mem_usage / max(mem_limit, 1)) * 100.0

        return {"cpu_pct": cpu_pct, "mem_pct": mem_pct}

    async def _recover_container(self, drone_id: str) -> None:
        """
        [ROLE] 비정상 종료된 컨테이너 자동 복구 (rotate 호출).

        [DATA FLOW]
            drone_id ──▶ drone_manager.rotate(drone_id) ──▶ 새 컨테이너
        """
        if self._drone_manager is None or not hasattr(self._drone_manager, "rotate"):
            logger.warning(
                "cannot_recover_no_drone_manager",
                drone_id=drone_id,
            )
            return

        logger.info("auto_recovery_started", drone_id=drone_id)
        try:
            await self._drone_manager.rotate(drone_id)
            self.log_surface_change(
                drone_id=drone_id,
                action="auto_recovery",
                old_surface={"status": "exited"},
                new_surface={"status": "running"},
            )
            logger.info("auto_recovery_completed", drone_id=drone_id)
        except Exception as e:
            logger.error(
                "auto_recovery_failed",
                drone_id=drone_id,
                error=str(e),
            )
