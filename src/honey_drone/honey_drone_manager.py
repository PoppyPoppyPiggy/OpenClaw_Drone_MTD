#!/usr/bin/env python3
"""
honey_drone_manager.py — DVD Docker 기반 허니드론 인스턴스 관리자

Project  : MIRAGE-UAS
Module   : Honey Drone / Manager
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - HoneyDroneConfig (per-instance 설정)
    - constants.py (HONEY_DRONE_COUNT, 포트 범위, 이미지명)

[Outputs]
    - HoneyDroneInstance (실행 중 컨테이너 상태)
    - MTD rotate() 시 새 인스턴스 반환

[Dependencies]
    - docker >= 7.0.0 (Python Docker SDK)

[DVD 컨테이너 스택 (per instance)]
    fcu-honey-0N  : nicholasaleks/dvd-flight-controller  (내부 TCP SITL)
    cc-honey-0N   : nicholasaleks/dvd-companion-computer  (외부 공격 노출)

[MTD 연동]
    rotate(drone_id):
      1. 기존 cc/fcu 컨테이너 stop
      2. 새 컨테이너 spawn (동일 설정, 새 컨테이너 ID)
      3. 포트 재매핑으로 공격자 추적 혼란 유발
"""

import asyncio
import time
from typing import Optional

import docker
from docker.errors import DockerException, NotFound
from docker.models.containers import Container

from shared.constants import (
    DOCKER_IMAGE_DVD_CC,
    DOCKER_IMAGE_DVD_FCU,
    DOCKER_NETWORK_NAME,
    HONEY_DRONE_COUNT,
    HTTP_PORT_BASE,
    MAVLINK_PORT_BASE,
    RTSP_PORT_BASE,
    SITL_PORT_BASE,
    WEBCLAW_PORT_BASE,
)
from shared.logger import get_logger
from shared.models import (
    DroneStatus,
    HoneyDroneConfig,
    HoneyDroneInstance,
)

logger = get_logger(__name__)

# FCU health check 재시도 설정 (인프라 고정값)
_FCU_HEALTH_RETRIES   : int   = 10
_FCU_HEALTH_INTERVAL  : float = 3.0   # 초


class HoneyDroneError(Exception):
    """허니드론 관리 작업 실패 시 발생."""


class HoneyDroneManager:
    """
    [ROLE] HONEY_DRONE_COUNT개 DVD 컨테이너 스택의 생성/종료/로테이션 총괄.
           AgenticDecoyEngine 외부에서 컨테이너 생애주기를 담당.

    [DATA FLOW]
        build_configs() ──▶ HoneyDroneConfig list
        spawn(config) ──▶ Docker API ──▶ HoneyDroneInstance
        rotate(drone_id) ──▶ teardown + spawn ──▶ HoneyDroneInstance (새 컨테이너)
    """

    def __init__(self) -> None:
        try:
            self._client = docker.from_env()
        except DockerException as e:
            raise HoneyDroneError(
                f"Docker 데몬 연결 실패. Docker Desktop이 실행 중인지 확인하세요: {e}"
            ) from e

        # drone_id → HoneyDroneInstance
        self._instances: dict[str, HoneyDroneInstance] = {}

    @staticmethod
    def build_configs() -> list[HoneyDroneConfig]:
        """
        [ROLE] .env 상수 기반으로 N개 허니드론 설정 리스트 생성.
               포트는 인스턴스 index로 자동 할당.

        [DATA FLOW]
            HONEY_DRONE_COUNT, 포트 상수
            ──▶ HoneyDroneConfig(index=1..N) list
        """
        configs = []
        for i in range(1, HONEY_DRONE_COUNT + 1):
            configs.append(HoneyDroneConfig(
                drone_id=f"honey_{i:02d}",
                index=i,
                sitl_port=SITL_PORT_BASE + i,
                mavlink_port=MAVLINK_PORT_BASE + i,
                webclaw_port=WEBCLAW_PORT_BASE + i,
                http_port=HTTP_PORT_BASE + i,
                rtsp_port=RTSP_PORT_BASE + i,
                docker_image=DOCKER_IMAGE_DVD_CC,
                fcu_image=DOCKER_IMAGE_DVD_FCU,
                network=DOCKER_NETWORK_NAME,
            ))
        return configs

    async def spawn(self, config: HoneyDroneConfig) -> HoneyDroneInstance:
        """
        [ROLE] FCU + CC 컨테이너 쌍을 생성하고 FCU health 확인 후 인스턴스 반환.

        [DATA FLOW]
            HoneyDroneConfig
            ──▶ _spawn_fcu()    ──▶ FCU Container
            ──▶ _wait_fcu_healthy()
            ──▶ _spawn_cc()     ──▶ CC Container
            ──▶ HoneyDroneInstance 등록 및 반환
        """
        if config.drone_id in self._instances:
            raise HoneyDroneError(
                f"Drone '{config.drone_id}' 이미 실행 중입니다. "
                f"먼저 teardown()을 호출하세요."
            )
        try:
            fcu_container = await asyncio.get_event_loop().run_in_executor(
                None, self._spawn_fcu, config
            )
            await self._wait_fcu_healthy(fcu_container, config.drone_id)

            cc_container = await asyncio.get_event_loop().run_in_executor(
                None, self._spawn_cc, config
            )

            instance = HoneyDroneInstance(
                config=config,
                fcu_container_id=fcu_container.id,
                cc_container_id=cc_container.id,
                status=DroneStatus.IDLE,
            )
            self._instances[config.drone_id] = instance

            logger.info(
                "honey_drone spawned",
                drone_id=config.drone_id,
                mavlink_port=config.mavlink_port,
                webclaw_port=config.webclaw_port,
                fcu_id=fcu_container.id[:12],
                cc_id=cc_container.id[:12],
            )
            return instance

        except Exception as e:
            raise HoneyDroneError(
                f"Drone '{config.drone_id}' spawn 실패: {e}"
            ) from e

    async def teardown(self, drone_id: str) -> None:
        """
        [ROLE] 컨테이너 쌍을 중지/제거하고 로그를 results/logs에 저장.

        [DATA FLOW]
            drone_id ──▶ 컨테이너 조회 ──▶ stop + remove + logs 저장
            ──▶ _instances dict에서 제거
        """
        instance = self._instances.get(drone_id)
        if not instance:
            logger.warning("teardown: drone not found", drone_id=drone_id)
            return

        instance.status = DroneStatus.TERMINATED

        await asyncio.get_event_loop().run_in_executor(
            None, self._stop_containers, instance
        )

        del self._instances[drone_id]
        logger.info("honey_drone torn down", drone_id=drone_id)

    async def rotate(self, drone_id: str) -> HoneyDroneInstance:
        """
        [ROLE] MTD: 기존 컨테이너를 중지하고 동일 설정으로 새 컨테이너 생성.
               rotation_count 증가. 공격자 추적 맥락 초기화.

        [DATA FLOW]
            drone_id ──▶ 기존 config 보존 ──▶ teardown ──▶ spawn
            ──▶ rotation_count + 1 ──▶ HoneyDroneInstance
        """
        existing = self._instances.get(drone_id)
        if not existing:
            raise HoneyDroneError(f"rotate 대상 drone '{drone_id}'을 찾을 수 없습니다.")

        config          = existing.config
        rotation_count  = existing.rotation_count + 1

        logger.info(
            "mtd_rotation_started",
            drone_id=drone_id,
            rotation_count=rotation_count,
        )
        await self.teardown(drone_id)
        new_instance = await self.spawn(config)
        new_instance.rotation_count = rotation_count

        logger.info(
            "mtd_rotation_completed",
            drone_id=drone_id,
            rotation_count=rotation_count,
            new_cc_id=new_instance.cc_container_id[:12],
        )
        return new_instance

    async def get_status(self, drone_id: str) -> DroneStatus:
        """
        [ROLE] 특정 드론의 현재 컨테이너 상태 조회.

        [DATA FLOW]
            drone_id ──▶ Docker inspect ──▶ DroneStatus
        """
        instance = self._instances.get(drone_id)
        if not instance:
            return DroneStatus.TERMINATED

        try:
            cc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.containers.get(instance.cc_container_id)
            )
            status_map = {
                "running": DroneStatus.IDLE,
                "exited":  DroneStatus.TERMINATED,
                "paused":  DroneStatus.TERMINATED,
            }
            return status_map.get(cc.status, DroneStatus.TERMINATED)
        except NotFound:
            return DroneStatus.TERMINATED
        except DockerException as e:
            logger.error(
                "get_status failed",
                drone_id=drone_id,
                error=str(e),
            )
            return DroneStatus.TERMINATED

    def list_active(self) -> list[HoneyDroneInstance]:
        """
        [ROLE] 활성 허니드론 인스턴스 목록 반환.

        [DATA FLOW]
            _instances dict ──▶ TERMINATED 제외 ──▶ list
        """
        return [
            inst for inst in self._instances.values()
            if inst.status != DroneStatus.TERMINATED
        ]

    # ── Docker 컨테이너 생성 (동기, executor에서 실행) ─────────────────────────

    def _spawn_fcu(self, config: HoneyDroneConfig) -> Container:
        """
        [ROLE] ArduPilot SITL FCU 컨테이너 생성 (내부 네트워크 전용).

        [DATA FLOW]
            HoneyDroneConfig ──▶ docker.containers.run() ──▶ Container
        """
        return self._client.containers.run(
            image=config.fcu_image,
            name=f"fcu_{config.drone_id}",
            hostname=f"fcu-{config.drone_id}",
            network=config.network,
            ports={
                f"5760/tcp": ("127.0.0.1", config.sitl_port),
            },
            environment={
                "DRONE_ID":                config.drone_id,
                "DRONE_ROLE":              "honeypot",
                "SITL_TCP_PORT":           "5760",
                "COMPANION_COMPUTER_HOST": f"cc-{config.drone_id}",
                "LOG_LEVEL":               "INFO",
            },
            labels={
                "dvd.component":    "flight-controller",
                "dvd.drone_id":     config.drone_id,
                "dvd.role":         "honeypot",
                "dvd.attacker_facing": "false",
            },
            cap_drop=["ALL"],
            cap_add=["SYS_PTRACE"],
            security_opt=["no-new-privileges:true"],
            mem_limit="1g",
            nano_cpus=int(1.5 * 1e9),
            detach=True,
            remove=False,
        )

    def _spawn_cc(self, config: HoneyDroneConfig) -> Container:
        """
        [ROLE] Companion Computer 컨테이너 생성 (공격자에게 의도적 노출).
               MAVLink :mavlink_port, HTTP :http_port, RTSP :rtsp_port,
               WebSocket :webclaw_port 모두 외부 노출.

        [DATA FLOW]
            HoneyDroneConfig ──▶ docker.containers.run() ──▶ Container
        """
        return self._client.containers.run(
            image=config.docker_image,
            name=f"cc_{config.drone_id}",
            hostname=f"cc-{config.drone_id}",
            network=config.network,
            ports={
                f"14550/udp": ("0.0.0.0", config.mavlink_port),
                f"80/tcp":    ("127.0.0.1", config.http_port),
                f"8554/tcp":  ("0.0.0.0", config.rtsp_port),
            },
            environment={
                "DRONE_ID":    config.drone_id,
                "DRONE_ROLE":  "honeypot",
                "FCU_HOST":    f"fcu-{config.drone_id}",
                "FCU_PORT":    "5760",
                "MAVLINK_PORT": "14550",
                "RTSP_PORT":   "8554",
                "WEB_PORT":    "80",
                "MAVLINK_ROUTER_ENDPOINTS":
                    f"udp:0.0.0.0:14550,udp:cti-interceptor:1955{config.index}",
                "LOG_LEVEL":   "INFO",
            },
            labels={
                "dvd.component":       "companion-computer",
                "dvd.drone_id":        config.drone_id,
                "dvd.role":            "honeypot",
                "dvd.attacker_facing": "true",
                "dvd.attack_surface":  "mavlink,http,rtsp",
            },
            cap_drop=["ALL"],
            cap_add=["NET_BIND_SERVICE", "NET_RAW"],
            security_opt=["no-new-privileges:true"],
            mem_limit="512m",
            nano_cpus=int(1.0 * 1e9),
            detach=True,
            remove=False,
        )

    def _stop_containers(self, instance: HoneyDroneInstance) -> None:
        """
        [ROLE] CC + FCU 컨테이너를 순서대로 중지/제거.
               CC 먼저 종료 후 FCU 종료.

        [DATA FLOW]
            HoneyDroneInstance.{cc,fcu}_container_id
            ──▶ container.stop() ──▶ container.remove()
        """
        for cid, label in [
            (instance.cc_container_id, "cc"),
            (instance.fcu_container_id, "fcu"),
        ]:
            try:
                c = self._client.containers.get(cid)
                c.stop(timeout=10)
                c.remove(force=True)
                logger.debug(
                    "container stopped",
                    drone_id=instance.config.drone_id,
                    type=label,
                    container_id=cid[:12],
                )
            except NotFound:
                pass
            except DockerException as e:
                logger.warning(
                    "container stop failed",
                    drone_id=instance.config.drone_id,
                    type=label,
                    error=str(e),
                )

    async def _wait_fcu_healthy(
        self, container: Container, drone_id: str
    ) -> None:
        """
        [ROLE] FCU SITL TCP 포트가 열릴 때까지 대기 (health check 대체).
               최대 _FCU_HEALTH_RETRIES * _FCU_HEALTH_INTERVAL 초 대기.

        [DATA FLOW]
            container.reload() ──▶ 상태 확인
            ──▶ "running" 확인 후 반환
            ──▶ 초과 시 HoneyDroneError
        """
        for attempt in range(_FCU_HEALTH_RETRIES):
            await asyncio.sleep(_FCU_HEALTH_INTERVAL)
            try:
                container.reload()
                if container.status == "running":
                    logger.debug(
                        "fcu healthy",
                        drone_id=drone_id,
                        attempt=attempt + 1,
                    )
                    return
            except DockerException as e:
                logger.debug(
                    "fcu health check error",
                    drone_id=drone_id,
                    attempt=attempt + 1,
                    error=str(e),
                )

        raise HoneyDroneError(
            f"FCU '{drone_id}' health check 실패 "
            f"({_FCU_HEALTH_RETRIES}회 시도). "
            f"dvd-flight-controller 이미지 존재 여부를 확인하세요."
        )
