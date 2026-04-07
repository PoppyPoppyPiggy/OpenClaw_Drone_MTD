#!/usr/bin/env python3
"""
mavlink_response_gen.py — MAVLink 응답 생성기

Project  : MIRAGE-UAS
Module   : Honey Drone / MAVLink Response Generator
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - MavlinkCaptureEvent (허니드론에 들어온 MAVLink 명령)
    - HoneyDroneConfig    (드론 식별 정보)

[Outputs]
    - bytes: 공격자에게 보낼 MAVLink 응답 패킷

[Dependencies]
    - pymavlink >= 2.4.41

[설계 원칙]
    - 실제 ArduPilot Copter v4.3.x 응답 패턴을 모방
    - 공격자가 "진짜 드론"이라고 믿도록 충분히 현실적인 응답
    - 식별 가능한 fingerprint는 노이즈로 오염 (anti-fingerprint)
    - 모든 응답에 약간의 지연 jitter 추가 (실제 드론 지연 모방)
"""

from __future__ import annotations

import json
import math
import random
import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as apm

from shared.logger import get_logger
from shared.models import DroneProtocol, HoneyDroneConfig, MavlinkCaptureEvent

logger = get_logger(__name__)

# ── MAVLink 상수 ───────────────────────────────────────────────────────────────
# ArduPilot Copter v4.3 기반 허니드론 시스템 ID
HONEY_SYSID    : int = 1
HONEY_COMPID   : int = 1
# 가상 위치 (서울 근교, ArduPilot SITL 기본값 유사)
_BASE_LAT_DEG  : float = 37.5665   # 위도 (서울)
_BASE_LON_DEG  : float = 126.9780  # 경도 (서울)
_BASE_ALT_M    : float = 100.0     # 고도 (m)
# 지터 범위 (ms) — 실제 드론 통신 지연 모방
_JITTER_MIN_MS : float = 5.0
_JITTER_MAX_MS : float = 50.0


# ── FlightState ────────────────────────────────────────────────────────────────
@dataclass
class FlightState:
    """
    [ROLE] 허니드론의 가상 비행 상태 유지.
           응답 생성 시 일관된 텔레메트리 제공 (anti-fingerprint용 상태 추적).
    """
    lat_deg    : float = _BASE_LAT_DEG
    lon_deg    : float = _BASE_LON_DEG
    alt_m      : float = _BASE_ALT_M
    roll_rad   : float = 0.0
    pitch_rad  : float = 0.0
    yaw_rad    : float = 1.57   # 동쪽 방향
    armed      : bool  = False
    mode       : int   = 0      # STABILIZE
    boot_time  : int   = 0

    def __post_init__(self) -> None:
        self.boot_time = int(time.time() * 1000)

    def time_boot_ms(self) -> int:
        return int(time.time() * 1000) - self.boot_time

    def nudge(self) -> None:
        """[ROLE] 매 응답마다 위치/자세에 미세 변화 → 정적 honeypot 회피."""
        self.lat_deg  += random.gauss(0, 1e-6)
        self.lon_deg  += random.gauss(0, 1e-6)
        self.alt_m    += random.gauss(0, 0.01)
        self.roll_rad  = random.gauss(0, 0.005)
        self.pitch_rad = random.gauss(0, 0.005)
        self.yaw_rad  += random.gauss(0, 0.002)


# ── MavlinkResponseGenerator ──────────────────────────────────────────────────
class MavlinkResponseGenerator:
    """
    [ROLE] 공격자의 MAVLink 명령에 대해 실제 ArduPilot Copter처럼 응답 생성.
           허니드론이 진짜 드론으로 보이도록 충분한 현실성 확보.

    [DATA FLOW]
        MavlinkCaptureEvent (공격자 명령)
        ──▶ generate(event)
        ──▶ bytes (MAVLink 응답 패킷)
        ──▶ UDP sendto(attacker_ip, attacker_port)
    """

    def __init__(self, config: HoneyDroneConfig) -> None:
        self._config = config
        self._state  = FlightState()
        # 드론마다 고유한 sysid (공격자 추적 용이)
        self._sysid  = HONEY_SYSID + config.index
        self._mav    = mavutil.mavlink.MAVLink(
            file=None, srcSystem=self._sysid, srcComponent=HONEY_COMPID
        )
        self._mav.robust_parsing = True

    def _emit_packet_event(self, event: MavlinkCaptureEvent, response_bytes: bytes, deception_notes: list) -> None:
        """Fire-and-forget UDP emit of packet generation event."""
        try:
            data = {
                "event": "packet_generated",
                "drone_id": self._config.drone_id,
                "timestamp": time.time(),
                "request_type": event.msg_type,
                "response_bytes_hex": response_bytes[:20].hex() if response_bytes else "",
                "key_fields": {
                    "srcSystem": self._sysid,
                    "type": "COPTER",
                    "autopilot": "ARDUPILOTMEGA",
                    "system_status": "ACTIVE" if self._state.armed else "STANDBY",
                },
                "deception_applied": deception_notes,
                "vs_real_drone": {
                    "srcSystem_real": 1,
                    "srcSystem_sent": self._sysid,
                    "position_jitter_m": round(abs(self._state.lat_deg - 37.5665) * 111000, 2),
                },
            }
            raw = json.dumps(data).encode("utf-8")
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(raw, ("127.0.0.1", 19996))
            finally:
                sock.close()
        except Exception:
            pass

    def generate(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] 수신된 MAVLink 이벤트 유형에 따라 적절한 응답 패킷 생성.
               알 수 없는 메시지 유형은 None 반환 (응답 없음 = 정상 드론 동작).

        [DATA FLOW]
            MavlinkCaptureEvent.msg_type
            ──▶ 핸들러 디스패치
            ──▶ bytes (MAVLink 직렬화)
        """
        self._state.nudge()
        # 실제 드론 지연 모방 — jitter 적용
        jitter_ms = random.uniform(_JITTER_MIN_MS, _JITTER_MAX_MS)
        time.sleep(jitter_ms / 1000.0)

        handler = {
            "HEARTBEAT":             self._heartbeat,
            "COMMAND_LONG":          self._command_ack,
            "PARAM_REQUEST_LIST":    self._param_value,
            "PARAM_REQUEST_READ":    self._param_value,
            "MISSION_REQUEST_LIST":  self._mission_count,
            "REQUEST_DATA_STREAM":   self._data_stream_ack,
            "SET_MODE":              self._command_ack_mode,
        }.get(event.msg_type)

        if handler is None:
            logger.debug(
                "no response for msg_type",
                drone_id=self._config.drone_id,
                msg_type=event.msg_type,
            )
            return None

        try:
            result = handler(event)
        except Exception as e:
            logger.error(
                "response generation failed",
                drone_id=self._config.drone_id,
                msg_type=event.msg_type,
                error=str(e),
            )
            return None

        if result is not None:
            deception_notes_map = {
                "HEARTBEAT": ["sysid_offset"],
                "PARAM_REQUEST_LIST": ["fake_param_value"],
                "PARAM_REQUEST_READ": ["fake_param_value"],
                "COMMAND_LONG": ["ack_always_accepted"],
                "MISSION_REQUEST_LIST": ["fake_mission_count"],
            }
            notes = deception_notes_map.get(event.msg_type, ["position_jitter"])
            self._emit_packet_event(event, result, notes)

        return result

    # ── 응답 핸들러 ──────────────────────────────────────────────────────────────

    def _heartbeat(self, event: MavlinkCaptureEvent) -> bytes:
        """
        [ROLE] HEARTBEAT 요청에 ArduPilot Copter 응답 생성.
               armed 상태, 비행 모드, 기체 유형 포함.

        [DATA FLOW]
            FlightState.armed, mode ──▶ HEARTBEAT 패킷 ──▶ bytes
        """
        base_mode = (
            apm.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED |
            (apm.MAV_MODE_FLAG_SAFETY_ARMED if self._state.armed else 0)
        )
        msg = self._mav.heartbeat_encode(
            type=apm.MAV_TYPE_QUADROTOR,
            autopilot=apm.MAV_AUTOPILOT_ARDUPILOTMEGA,
            base_mode=base_mode,
            custom_mode=self._state.mode,
            system_status=apm.MAV_STATE_ACTIVE if self._state.armed
                          else apm.MAV_STATE_STANDBY,
            mavlink_version=3,
        )
        return msg.pack(self._mav)

    def _command_ack(self, event: MavlinkCaptureEvent) -> bytes:
        """
        [ROLE] COMMAND_LONG에 대한 ACK 응답.
               ARM/DISARM, TAKEOFF 명령에 현실적으로 응답.

        [DATA FLOW]
            event.payload_hex ──▶ command 파싱 ──▶ COMMAND_ACK ──▶ bytes
        """
        # payload에서 command 번호 파싱 (MAVLink COMMAND_LONG: bytes 0-1)
        cmd = 0
        try:
            raw = bytes.fromhex(event.payload_hex)
            if len(raw) >= 2:
                cmd = struct.unpack_from("<H", raw, 0)[0]
        except (ValueError, struct.error):
            pass

        # ARM 명령 처리
        if cmd == apm.MAV_CMD_COMPONENT_ARM_DISARM:
            try:
                param1 = struct.unpack_from("<f", bytes.fromhex(event.payload_hex), 4)[0]
                self._state.armed = (param1 > 0.5)
            except (ValueError, struct.error):
                pass

        result = apm.MAV_RESULT_ACCEPTED
        msg = self._mav.command_ack_encode(command=cmd, result=result)
        return msg.pack(self._mav)

    def _command_ack_mode(self, event: MavlinkCaptureEvent) -> bytes:
        """[ROLE] SET_MODE에 대한 ACK. 비행 모드 변경 수락."""
        try:
            raw = bytes.fromhex(event.payload_hex)
            if len(raw) >= 4:
                self._state.mode = struct.unpack_from("<I", raw, 0)[0]
        except (ValueError, struct.error):
            pass
        msg = self._mav.command_ack_encode(
            command=apm.MAV_CMD_DO_SET_MODE,
            result=apm.MAV_RESULT_ACCEPTED,
        )
        return msg.pack(self._mav)

    def _param_value(self, event: MavlinkCaptureEvent) -> bytes:
        """
        [ROLE] PARAM_REQUEST에 대해 가짜 파라미터 응답.
               실제 ArduPilot 파라미터명/값 사용으로 현실감 확보.

        [DATA FLOW]
            PARAM_REQUEST ──▶ 무작위 ArduPilot 파라미터 선택 ──▶ PARAM_VALUE
        """
        # 실제 ArduPilot Copter 파라미터 샘플
        _params = {
            "ARMING_CHECK":  1.0,
            "PILOT_SPEED_UP": 250.0,
            "WPNAV_SPEED":   500.0,
            "RTL_ALT":       1500.0,
            "BATT_CAPACITY": 5200.0,
            "FS_BATT_ENABLE": 2.0,
            "COMPASS_USE":   1.0,
            "GPS_TYPE":      1.0,
        }
        param_id, param_val = random.choice(list(_params.items()))
        msg = self._mav.param_value_encode(
            param_id=param_id.encode().ljust(16, b"\x00"),
            param_value=param_val,
            param_type=apm.MAV_PARAM_TYPE_REAL32,
            param_count=len(_params),
            param_index=list(_params.keys()).index(param_id),
        )
        return msg.pack(self._mav)

    def _mission_count(self, event: MavlinkCaptureEvent) -> bytes:
        """[ROLE] MISSION_REQUEST_LIST에 미션 개수 응답 (현실적 미션 보유 모방)."""
        count = random.randint(3, 12)
        msg = self._mav.mission_count_encode(
            target_system=event.sysid,
            target_component=event.compid,
            count=count,
        )
        return msg.pack(self._mav)

    def _data_stream_ack(self, event: MavlinkCaptureEvent) -> bytes:
        """[ROLE] REQUEST_DATA_STREAM에 HEARTBEAT로 응답 (스트림 시작 신호)."""
        return self._heartbeat(event)

    def get_telemetry_packet(self) -> bytes:
        """
        [ROLE] 주기적 텔레메트리 브로드캐스트 패킷 생성.
               허니드론이 정기적으로 alive 신호를 보내도록.

        [DATA FLOW]
            FlightState ──▶ GLOBAL_POSITION_INT + ATTITUDE 번갈아 전송
        """
        self._state.nudge()
        # 짝수/홀수 초에 따라 메시지 유형 교번 (실제 드론 패턴)
        if int(time.time()) % 2 == 0:
            msg = self._mav.global_position_int_encode(
                time_boot_ms=self._state.time_boot_ms(),
                lat=int(self._state.lat_deg * 1e7),
                lon=int(self._state.lon_deg * 1e7),
                alt=int(self._state.alt_m * 1000),
                relative_alt=int(self._state.alt_m * 1000),
                vx=random.randint(-5, 5),
                vy=random.randint(-5, 5),
                vz=random.randint(-2, 2),
                hdg=int(math.degrees(self._state.yaw_rad) * 100) % 36000,
            )
        else:
            msg = self._mav.attitude_encode(
                time_boot_ms=self._state.time_boot_ms(),
                roll=self._state.roll_rad,
                pitch=self._state.pitch_rad,
                yaw=self._state.yaw_rad,
                rollspeed=random.gauss(0, 0.01),
                pitchspeed=random.gauss(0, 0.01),
                yawspeed=random.gauss(0, 0.005),
            )
        pkt = msg.pack(self._mav)
        # Emit telemetry packet event with a synthetic capture event
        telem_event = MavlinkCaptureEvent(
            msg_type="TELEMETRY_BROADCAST",
            sysid=self._sysid,
            compid=HONEY_COMPID,
            payload_hex="",
        )
        self._emit_packet_event(telem_event, pkt, ["position_jitter", "telemetry_broadcast"])
        return pkt
