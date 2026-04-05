#!/usr/bin/env python3
"""
mavlink_interceptor.py — MAVLink UDP 패킷 수동 인터셉터

Project  : MIRAGE-UAS
Module   : CTI Pipeline / MAVLink Interceptor
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - UDP 포트 19551/52/53
      (DVD CC의 MAVLink Router → cti-interceptor 포워딩)
    - constants.py (HONEY_DRONE_COUNT)

[Outputs]
    - asyncio.Queue[MavlinkCaptureEvent] → AttackEventParser

[설계 원칙]
    - Passive tap: 공격자 트래픽에 응답하지 않음 (캡처 전용)
    - pymavlink로 raw bytes → MAVLink 메시지 파싱
    - 파싱 실패 패킷은 raw hex 보존 후 is_anomalous=True 표시
    - N개 드론의 인터셉터 포트를 동시 리스닝 (asyncio.gather)

[DATA FLOW]
    DVD CC (MAVLink Router: MAVLINK_ROUTER_ENDPOINTS)
    ──▶ UDP 19551/52/53 (passive copy)
    ──▶ MavlinkInterceptor._listen_drone(N)
    ──▶ _parse_packet() ──▶ MavlinkCaptureEvent
    ──▶ asyncio.Queue[MavlinkCaptureEvent] (AttackEventParser 소비)
"""

import asyncio
import hashlib
import socket
import uuid
from typing import Optional

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as apm

from shared.constants import HONEY_DRONE_COUNT
from shared.logger import get_logger
from shared.models import DroneProtocol, MavlinkCaptureEvent

logger = get_logger(__name__)

# CTI 인터셉터 포트 기준값 (docker-compose와 일치)
_INTERCEPT_PORT_BASE: int = 19550
# UDP 수신 버퍼 크기
_UDP_RECV_BUF: int = 4096


class MavlinkInterceptor:
    """
    [ROLE] N개 허니드론의 MAVLink 트래픽 사본을 동시에 수신하는 비동기 인터셉터.
           파싱된 이벤트를 큐에 push하여 Track B CTI 파이프라인 공급.

    [DATA FLOW]
        start() ──▶ asyncio.gather(_listen_drone×N) [병렬]
        _listen_drone(i): UDP recv ──▶ _parse_packet() ──▶ event_queue.put()
    """

    def __init__(self, event_queue: asyncio.Queue) -> None:
        self._event_q = event_queue
        # 드론 인덱스 → (attacker_ip → session_id) 세션 캐시
        self._sessions: dict[int, dict[str, str]] = {
            i: {} for i in range(1, HONEY_DRONE_COUNT + 1)
        }
        self._sockets: list[socket.socket] = []
        self._tasks:   list[asyncio.Task]  = []

    async def start(self) -> None:
        """
        [ROLE] N개 드론 인터셉터 포트에 대한 UDP 수신 태스크 병렬 기동.

        [DATA FLOW]
            HONEY_DRONE_COUNT ──▶ _setup_socket(i) × N
            ──▶ asyncio.gather(_listen_drone(i) × N)
        """
        for i in range(1, HONEY_DRONE_COUNT + 1):
            sock = self._setup_socket(i)
            if sock:
                self._sockets.append(sock)
                task = asyncio.create_task(
                    self._listen_drone(i, sock),
                    name=f"intercept_honey_{i:02d}",
                )
                self._tasks.append(task)

        logger.info(
            "mavlink_interceptor started",
            drone_count=HONEY_DRONE_COUNT,
            ports=[_INTERCEPT_PORT_BASE + i for i in range(1, HONEY_DRONE_COUNT + 1)],
        )

    async def stop(self) -> None:
        """[ROLE] 모든 수신 태스크 및 소켓 정리."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for sock in self._sockets:
            sock.close()
        self._tasks.clear()
        self._sockets.clear()
        logger.info("mavlink_interceptor stopped")

    # ── 내부 구현 ─────────────────────────────────────────────────────────────

    def _setup_socket(self, drone_index: int) -> Optional[socket.socket]:
        """
        [ROLE] 드론 N번 인터셉터 포트에 UDP 소켓 바인딩.
               실패 시 None 반환 (다른 드론은 계속 수신).

        [DATA FLOW]
            drone_index ──▶ port = _INTERCEPT_PORT_BASE + drone_index
            ──▶ socket.bind() ──▶ socket 반환
        """
        port = _INTERCEPT_PORT_BASE + drone_index
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)
            sock.bind(("0.0.0.0", port))
            logger.info(
                "intercept socket bound",
                drone_index=drone_index,
                port=port,
            )
            return sock
        except OSError as e:
            logger.error(
                "intercept socket bind failed",
                drone_index=drone_index,
                port=port,
                error=str(e),
            )
            return None

    async def _listen_drone(self, drone_index: int, sock: socket.socket) -> None:
        """
        [ROLE] 단일 드론 인터셉터 포트 수신 루프.
               수신 패킷을 파싱하여 이벤트 큐에 push.

        [DATA FLOW]
            UDP recv ──▶ _parse_packet() ──▶ event_queue.put()
        """
        loop     = asyncio.get_event_loop()
        drone_id = f"honey_{drone_index:02d}"

        while True:
            try:
                data, addr = await loop.sock_recvfrom(sock, _UDP_RECV_BUF)  # type: ignore[arg-type]
                attacker_ip   = addr[0]
                attacker_port = addr[1]

                session_id = self._get_or_create_session(drone_index, attacker_ip)
                events     = self._parse_packet(
                    data, drone_id, attacker_ip, attacker_port, session_id
                )
                for event in events:
                    await self._event_q.put(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(
                    "listen_drone error",
                    drone_id=drone_id,
                    error=str(e),
                )

    def _parse_packet(
        self,
        data: bytes,
        drone_id: str,
        src_ip: str,
        src_port: int,
        session_id: str,
    ) -> list[MavlinkCaptureEvent]:
        """
        [ROLE] Raw UDP 바이트를 MAVLink 메시지로 파싱하여
               MavlinkCaptureEvent 리스트 반환 (1 패킷 = 1~N 메시지).

        [DATA FLOW]
            bytes ──▶ pymavlink.parse_buffer()
            ──▶ MAVLink message ──▶ MavlinkCaptureEvent
            파싱 실패 ──▶ MavlinkCaptureEvent(is_anomalous=True)
        """
        events: list[MavlinkCaptureEvent] = []

        try:
            mav = mavutil.mavlink.MAVLink(file=None)
            mav.robust_parsing = True
            msgs = mav.parse_buffer(data)

            if not msgs:
                # 파싱 실패 → 원시 패킷 보존 (anomalous)
                events.append(MavlinkCaptureEvent(
                    drone_id=drone_id,
                    src_ip=src_ip,
                    src_port=src_port,
                    protocol=DroneProtocol.MAVLINK,
                    msg_type="UNKNOWN",
                    msg_id=-1,
                    payload_hex=data.hex(),
                    is_anomalous=True,
                    session_id=session_id,
                ))
                return events

            for msg in msgs:
                msg_type = msg.get_type()
                events.append(MavlinkCaptureEvent(
                    drone_id=drone_id,
                    src_ip=src_ip,
                    src_port=src_port,
                    protocol=DroneProtocol.MAVLINK,
                    msg_type=msg_type,
                    msg_id=msg.get_msgId(),
                    sysid=msg.get_srcSystem(),
                    compid=msg.get_srcComponent(),
                    payload_hex=bytes(msg.get_payload()).hex(),
                    is_anomalous=self._is_anomalous_msg(msg),
                    session_id=session_id,
                ))

        except Exception as e:
            # 완전 파싱 실패 → raw hex 보존
            events.append(MavlinkCaptureEvent(
                drone_id=drone_id,
                src_ip=src_ip,
                src_port=src_port,
                protocol=DroneProtocol.MAVLINK,
                msg_type="PARSE_ERROR",
                payload_hex=data.hex(),
                is_anomalous=True,
                session_id=session_id,
            ))
            logger.debug("packet parse error", drone_id=drone_id, error=str(e))

        return events

    def _is_anomalous_msg(self, msg) -> bool:
        """
        [ROLE] MAVLink 메시지가 이상 패킷인지 휴리스틱 판단.
               COMMAND_LONG으로 ARM 시도, PARAM_SET, FILE_TRANSFER 등 탐지.

        [DATA FLOW]
            MAVLink message ──▶ 메시지 유형 + 필드 값 검사 ──▶ bool
        """
        msg_type = msg.get_type()

        # ARM/DISARM 명령
        if msg_type == "COMMAND_LONG":
            try:
                if int(msg.command) == apm.MAV_CMD_COMPONENT_ARM_DISARM:
                    return True
            except AttributeError:
                pass

        # 고위험 메시지 유형
        _HIGH_RISK: frozenset[str] = frozenset([
            "PARAM_SET",
            "FILE_TRANSFER_PROTOCOL",
            "LOG_REQUEST_DATA",
            "MISSION_ITEM",
            "MISSION_ITEM_INT",
            "SET_ACTUATOR_CONTROL_TARGET",
            "GPS_INJECT_DATA",
        ])
        return msg_type in _HIGH_RISK

    def _get_or_create_session(self, drone_index: int, attacker_ip: str) -> str:
        """
        [ROLE] (drone_index, attacker_ip) 조합 세션 ID 관리.
               동일 IP의 이벤트를 동일 session_id로 묶음.

        [DATA FLOW]
            attacker_ip ──▶ MD5 기반 결정론적 UUID ──▶ session_id
        """
        cache = self._sessions[drone_index]
        if attacker_ip not in cache:
            key = f"{attacker_ip}:honey_{drone_index:02d}"
            cache[attacker_ip] = str(uuid.UUID(
                hashlib.md5(key.encode()).hexdigest()
            ))
        return cache[attacker_ip]
