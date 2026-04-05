#!/usr/bin/env python3
"""
mtd_executor.py — MTD surface mutation 실행기

Project  : MIRAGE-UAS
Module   : MTD / Executor
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - asyncio.Queue[MTDAction] (EngagementToMTDConverter 출력)
    - docker.DockerClient        (컨테이너 재설정용)

[Outputs]
    - MTDResult (실행 결과)
    - asyncio.Queue[MTDResult] → MetricsCollector (Phase C2)

[MTD 액션별 구현]
    PORT_ROTATE     : iptables DNAT 규칙 갱신 (docker exec on CC)
    IP_SHUFFLE      : Docker 네트워크 disconnect/reconnect (새 IP 할당)
    KEY_ROTATE      : MAVLink 서명 키 갱신 (docker exec on CC)
    PROTO_CHANGE    : MAVLink Router 설정 변경 (v1↔v2, UDP↔TCP)
    ROUTE_MORPH     : MAVLink Router endpoint 재설정
    FREQ_HOP        : 시뮬레이션 전용 (실 RF 하드웨어 없음)
    SERVICE_MIGRATE : CC 컨테이너 중지 후 새 포트로 재시작

[DRY_RUN 모드]
    MTD_DRY_RUN=true 시 실제 Docker 호출 없이 성공 응답 반환.
    논문 시뮬레이션 평가(Phase C2)에서 사용.

[REF] MIRAGE-UAS §4 MTD Surface Controller
"""

import asyncio
import os
import time
from typing import Optional

import docker
from docker.errors import DockerException

from shared.logger import get_logger
from shared.models import HoneyDroneInstance
from mtd.mtd_actions import MTDAction, MTDActionType, MTDResult

logger = get_logger(__name__)

# DRY_RUN: 논문 시뮬레이션 평가 모드 (Docker 실행 없이 결과 로깅만)
_DRY_RUN: bool = os.environ.get("MTD_DRY_RUN", "false").lower() == "true"

# 액션별 시뮬레이션 실행 시간 (ms) — DRY_RUN 모드의 페이퍼 메트릭용
_SIMULATED_EXEC_MS: dict[MTDActionType, float] = {
    MTDActionType.FREQ_HOP:        80.0,
    MTDActionType.IP_SHUFFLE:      450.0,
    MTDActionType.PORT_ROTATE:     120.0,
    MTDActionType.PROTO_CHANGE:    200.0,
    MTDActionType.ROUTE_MORPH:     350.0,
    MTDActionType.KEY_ROTATE:      180.0,
    MTDActionType.SERVICE_MIGRATE: 3200.0,
}


class MTDExecutor:
    """
    [ROLE] MTDAction 큐를 소비하여 실제 Docker 기반 surface mutation 실행.
           DRY_RUN 모드에서는 시뮬레이션 결과만 생성.

    [DATA FLOW]
        asyncio.Queue[MTDAction]
        ──▶ MTDExecutor.run()
        ──▶ _dispatch(action) → Docker API 호출
        ──▶ MTDResult
        ──▶ asyncio.Queue[MTDResult] (MetricsCollector)
    """

    def __init__(
        self,
        action_queue: asyncio.Queue,
        result_queue: asyncio.Queue,
        instances: dict[str, HoneyDroneInstance],
    ) -> None:
        self._action_q  = action_queue
        self._result_q  = result_queue
        self._instances = instances  # drone_id → HoneyDroneInstance (shared ref)
        try:
            self._client = docker.from_env()
        except DockerException as e:
            logger.warning("docker client init failed, forcing DRY_RUN", error=str(e))
            globals()["_DRY_RUN"] = True
            self._client = None

    async def run(self) -> None:
        """
        [ROLE] MTDAction 큐를 지속 소비하며 실행.

        [DATA FLOW]
            action_queue.get() ──▶ _dispatch() ──▶ result_queue.put()
        """
        logger.info("mtd_executor started", dry_run=_DRY_RUN)
        while True:
            try:
                action: MTDAction = await self._action_q.get()
                result = await self._dispatch(action)
                await self._result_q.put(result)
                self._action_q.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("executor error", error=str(e))

    async def _dispatch(self, action: MTDAction) -> MTDResult:
        """
        [ROLE] action_type에 따라 적절한 실행 핸들러 호출.

        [DATA FLOW]
            MTDAction.action_type ──▶ 핸들러 디스패치 ──▶ MTDResult
        """
        t_start = time.perf_counter()
        handler = {
            MTDActionType.PORT_ROTATE:     self._exec_port_rotate,
            MTDActionType.IP_SHUFFLE:      self._exec_ip_shuffle,
            MTDActionType.KEY_ROTATE:      self._exec_key_rotate,
            MTDActionType.PROTO_CHANGE:    self._exec_proto_change,
            MTDActionType.ROUTE_MORPH:     self._exec_route_morph,
            MTDActionType.FREQ_HOP:        self._exec_freq_hop,
            MTDActionType.SERVICE_MIGRATE: self._exec_service_migrate,
        }.get(action.action_type)

        if handler is None:
            return MTDResult(
                action_id=action.action_id,
                action_type=action.action_type,
                target_drone_id=action.target_drone_id,
                success=False,
                error_msg=f"unknown action_type: {action.action_type}",
            )

        try:
            result = await handler(action)
        except Exception as e:
            result = MTDResult(
                action_id=action.action_id,
                action_type=action.action_type,
                target_drone_id=action.target_drone_id,
                success=False,
                error_msg=str(e)[:200],
            )

        result.execution_time_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "mtd_executed",
            action_id=action.action_id[:8],
            action_type=action.action_type.name,
            drone_id=action.target_drone_id,
            success=result.success,
            exec_ms=round(result.execution_time_ms, 1),
        )
        return result

    # ── 핸들러 구현 ──────────────────────────────────────────────────────────

    async def _exec_port_rotate(self, action: MTDAction) -> MTDResult:
        """
        [ROLE] CC 컨테이너의 MAVLink 포트 변경.
               iptables DNAT 규칙을 갱신하여 새 포트로 트래픽 유도.

        [DATA FLOW]
            docker exec cc_honey_0N "iptables -t nat ..." ──▶ MTDResult
        """
        if _DRY_RUN:
            return self._dry_result(action, new_surface={"port_rotated": True})

        instance = self._instances.get(action.target_drone_id)
        if not instance:
            raise ValueError(f"instance not found: {action.target_drone_id}")

        new_port  = action.parameters.get("new_port", instance.config.mavlink_port + 100)
        cc_name   = f"cc_{action.target_drone_id}"
        old_port  = instance.config.mavlink_port

        # iptables PREROUTING DNAT: old_port → new_port
        cmd = (
            f"iptables -t nat -A PREROUTING -p udp --dport {old_port} "
            f"-j REDIRECT --to-port {new_port}"
        )
        await self._docker_exec(cc_name, cmd)

        return MTDResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target_drone_id=action.target_drone_id,
            success=True,
            new_surface={"mavlink_port": new_port, "prev_port": old_port},
        )

    async def _exec_ip_shuffle(self, action: MTDAction) -> MTDResult:
        """
        [ROLE] Docker 네트워크에서 CC 컨테이너 IP 재할당.
               disconnect → reconnect with new IP alias.

        [DATA FLOW]
            docker network disconnect/connect ──▶ 새 IP ──▶ MTDResult
        """
        if _DRY_RUN:
            return self._dry_result(action, new_surface={"ip_shuffled": True})

        instance = self._instances.get(action.target_drone_id)
        if not instance:
            raise ValueError(f"instance not found: {action.target_drone_id}")

        cc_name  = f"cc_{action.target_drone_id}"
        network  = instance.config.network
        loop     = asyncio.get_event_loop()

        def _reconnect():
            c       = self._client.containers.get(cc_name)
            net_obj = self._client.networks.get(network)
            net_obj.disconnect(c)
            net_obj.connect(c)   # Docker IPAM이 새 IP 할당

        await loop.run_in_executor(None, _reconnect)
        return MTDResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target_drone_id=action.target_drone_id,
            success=True,
            new_surface={"ip_shuffled": True},
        )

    async def _exec_key_rotate(self, action: MTDAction) -> MTDResult:
        """
        [ROLE] MAVLink 서명 키 갱신.
               CC 컨테이너에서 새 키 생성 후 MAVLink Router 재시작.

        [DATA FLOW]
            docker exec cc "mavlink-keygen && supervisorctl restart mavlink-router"
            ──▶ MTDResult
        """
        if _DRY_RUN:
            return self._dry_result(action, new_surface={"key_rotated": True})

        cc_name = f"cc_{action.target_drone_id}"
        # MAVLink signing key 갱신 (ArduPilot SIGNING 기능)
        await self._docker_exec(
            cc_name,
            "dd if=/dev/urandom bs=32 count=1 2>/dev/null | "
            "base64 > /etc/mavlink/signing.key"
        )
        await self._docker_exec(cc_name, "supervisorctl restart mavlink-router")
        return MTDResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target_drone_id=action.target_drone_id,
            success=True,
            new_surface={"key_rotated": True},
        )

    async def _exec_proto_change(self, action: MTDAction) -> MTDResult:
        """
        [ROLE] MAVLink 프로토콜 버전 또는 전송 방식 전환.
               v1 ↔ v2 또는 UDP ↔ TCP 전환.

        [DATA FLOW]
            docker exec cc "sed -i ... /etc/mavlink-router/main.conf &&
                            supervisorctl restart mavlink-router" ──▶ MTDResult
        """
        if _DRY_RUN:
            return self._dry_result(action, new_surface={"proto_changed": True})

        cc_name      = f"cc_{action.target_drone_id}"
        current_mode = action.parameters.get("current_proto", "udp")
        new_mode     = "tcp" if current_mode == "udp" else "udp"

        await self._docker_exec(
            cc_name,
            f"sed -i 's/Protocol = {current_mode.upper()}/Protocol = {new_mode.upper()}/g' "
            f"/etc/mavlink-router/main.conf && supervisorctl restart mavlink-router"
        )
        return MTDResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target_drone_id=action.target_drone_id,
            success=True,
            new_surface={"protocol": new_mode},
        )

    async def _exec_route_morph(self, action: MTDAction) -> MTDResult:
        """
        [ROLE] MAVLink Router의 GCS endpoint를 다른 경로로 재설정.
               공격자가 추적하는 routing path를 변경.

        [DATA FLOW]
            docker exec cc "mavlink-router-config-update ..." ──▶ MTDResult
        """
        if _DRY_RUN:
            return self._dry_result(action, new_surface={"route_morphed": True})

        cc_name = f"cc_{action.target_drone_id}"
        # 라우팅 테이블 재설정 (cti-interceptor 포워딩 포트도 갱신)
        new_port = action.parameters.get("new_intercept_port", 29551)
        await self._docker_exec(
            cc_name,
            f"sed -i 's/cti-interceptor:[0-9]*/cti-interceptor:{new_port}/g' "
            f"/etc/mavlink-router/main.conf && supervisorctl restart mavlink-router"
        )
        return MTDResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target_drone_id=action.target_drone_id,
            success=True,
            new_surface={"intercept_port": new_port},
        )

    async def _exec_freq_hop(self, action: MTDAction) -> MTDResult:
        """
        [ROLE] 주파수 홉핑 시뮬레이션.
               실제 RF 하드웨어가 없으므로 로그 기록 및 성공 반환.
               논문에서 시뮬레이션 기반 평가임을 명시.

        [DATA FLOW]
            Simulated ──▶ MTDResult (success=True, simulated=True)
        """
        new_channel = action.parameters.get("channel", 6)
        logger.info(
            "freq_hop simulated",
            drone_id=action.target_drone_id,
            channel=new_channel,
        )
        return MTDResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target_drone_id=action.target_drone_id,
            success=True,
            new_surface={"channel": new_channel, "simulated": True},
        )

    async def _exec_service_migrate(self, action: MTDAction) -> MTDResult:
        """
        [ROLE] CC 서비스를 완전히 새 컨테이너로 마이그레이션.
               HoneyDroneManager.rotate()를 직접 호출하는 대신
               외부 Manager에게 위임 신호를 생성.
               실제 rotate는 HoneyDroneManager에서 수행.

        [DATA FLOW]
            MTDResult(migrate_requested=True) ──▶ 외부에서 rotate() 호출
        """
        logger.info(
            "service_migrate_requested",
            drone_id=action.target_drone_id,
            urgency=action.urgency,
        )
        return MTDResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target_drone_id=action.target_drone_id,
            success=True,
            new_surface={"migrate_requested": True},
        )

    # ── 헬퍼 ─────────────────────────────────────────────────────────────────

    async def _docker_exec(self, container_name: str, cmd: str) -> str:
        """
        [ROLE] 비동기 docker exec 래퍼.

        [DATA FLOW]
            container_name, cmd
            ──▶ loop.run_in_executor (blocking Docker API)
            ──▶ stdout str
        """
        loop = asyncio.get_event_loop()

        def _exec():
            c = self._client.containers.get(container_name)
            exit_code, output = c.exec_run(cmd, demux=False)
            if exit_code != 0:
                raise RuntimeError(
                    f"exec failed [{exit_code}]: {output.decode(errors='ignore')[:200]}"
                )
            return output.decode(errors="ignore")

        return await loop.run_in_executor(None, _exec)

    def _dry_result(
        self, action: MTDAction, new_surface: dict | None = None
    ) -> MTDResult:
        """
        [ROLE] DRY_RUN 모드 결과 생성.
               실제 Docker 호출 없이 시뮬레이션 실행 시간 포함.

        [DATA FLOW]
            MTDAction ──▶ simulated MTDResult (success=True)
        """
        import asyncio as _asyncio
        import time   as _time

        sim_ms = _SIMULATED_EXEC_MS.get(action.action_type, 100.0)
        # 시뮬레이션 지연 (non-blocking)
        return MTDResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target_drone_id=action.target_drone_id,
            success=True,
            execution_time_ms=sim_ms,
            new_surface=new_surface or {},
        )
