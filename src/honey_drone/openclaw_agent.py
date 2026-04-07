#!/usr/bin/env python3
"""
openclaw_agent.py — OpenClaw-inspired Autonomous Deception Agent

Project  : MIRAGE-UAS
Module   : Honey Drone / OpenClaw Autonomous Agent
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - HoneyDroneConfig              (드론 설정)
    - MavlinkCaptureEvent           (관찰된 공격 이벤트)
    - asyncio.Queue[MTDTrigger]     (MTD 트리거 큐)
    - asyncio.Queue[MavlinkCaptureEvent] (CTI 이벤트 큐)

[Outputs]
    - bytes | dict | None           (적응형 응답 — MAVLink 바이트 또는 WS JSON)
    - AgentDecision                 (자율 결정 감사 로그)

[Dependencies]
    - pymavlink >= 2.4.41
    - websockets >= 12.0
    - asyncio (stdlib)

[설계 원칙]
    ① 자율 행동: 외부 명령 없이 관찰만으로 능동적 기만 수행
    ② 적응 응답: 공격 단계(RECON→EXPLOIT→PERSIST→EXFIL) 별 맞춤 응답
    ③ 공격자 지문: 도구 식별 → 도구별 최적 응답 전략 분기
    ④ 자가 변이: MTD 컨트롤러 없이도 포트/sysid/파라미터 자율 변경
    ⑤ 혼란 증폭: 체류시간/서비스접촉에 비례하여 기만 복잡도 자동 상승

[DATA FLOW]
    공격 이벤트 (UDP/WS)
    ──▶ observe() / observe_ws()
    ──▶ _update_fingerprint()
    ──▶ _detect_attack_phase()
    ──▶ generate_response() / generate_ws_response()
    ──▶ 적응형 응답 반환

    (Proactive Loop — 자율 실행)
    ──▶ _proactive_loop()
    ──▶ 비요청 STATUSTEXT / 텔레메트리 변이 / ghost port / 재부팅 시뮬 / 가짜 키

[REF] MIRAGE-UAS §4.3 — Autonomous Deception Agent
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import socket
import struct
import time
import uuid
from typing import Optional, Union

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as apm

from shared.constants import (
    AGENT_FALSE_FLAG_DWELL_THRESHOLD,
    AGENT_MIRROR_SERVICE_THRESHOLD,
    AGENT_PORT_ROTATION_SEC,
    AGENT_PROACTIVE_INTERVAL_SEC,
    AGENT_SYSID_ROTATION_SEC,
)
from shared.logger import get_logger
from shared.models import (
    AgentDecision,
    AttackPhase,
    AttackerFingerprint,
    AttackerTool,
    DroneProtocol,
    HoneyDroneConfig,
    MavlinkCaptureEvent,
)

logger = get_logger(__name__)

# ── 내부 상수 ──────────────────────────────────────────────────────────────────
_REBOOT_SILENCE_MIN_SEC   : float = 8.0
_REBOOT_SILENCE_MAX_SEC   : float = 15.0
_ARM_TAKEOFF_SIM_SEC      : float = 30.0
_ARM_CRASH_SILENCE_SEC    : float = 20.0
_MISSION_REFRESH_SEC      : float = 60.0
_PARAM_CYCLE_SEC          : float = 45.0
_FLIGHT_SIM_DURATION_SEC  : float = 60.0
_FLIGHT_SIM_STEPS         : int   = 12

# ── 도구 식별: 타이밍 + 시퀀스 분석 기반 (바이너리 MAVLink에는 문자열 시그니처 없음)
# 분류는 _detect_tool()에서 inter-arrival timing과 명령 순서 패턴으로 수행

# ── 가짜 ArduPilot 파라미터 사전 ──────────────────────────────────────────────
_FAKE_PARAMS: dict[str, float] = {
    "ARMING_CHECK": 1.0, "PILOT_SPEED_UP": 250.0, "WPNAV_SPEED": 500.0,
    "RTL_ALT": 1500.0, "BATT_CAPACITY": 5200.0, "FS_BATT_ENABLE": 2.0,
    "COMPASS_USE": 1.0, "GPS_TYPE": 1.0, "INS_ACCOFFS_X": 0.001,
    "INS_ACCOFFS_Y": -0.002, "BATT_MONITOR": 4.0, "SERIAL0_BAUD": 115200.0,
    "RC1_MIN": 1100.0, "RC1_MAX": 1900.0, "SYSID_MYGCS": 255.0,
    "FENCE_ENABLE": 1.0, "FENCE_TYPE": 7.0,
}

# ── 가짜 STATUSTEXT 메시지 풀 ─────────────────────────────────────────────────
_STATUS_MESSAGES_OPERATOR: list[str] = [
    "Waypoint 3 reached", "Camera started", "RTL initiated",
    "Battery low — switching to RTL", "GPS lock acquired — 12 sats",
    "Mission 2 uploaded successfully", "GeoFence enabled",
    "Payload released at WP5", "Landing gear retracted",
    "Compass calibration passed", "EKF2 IMU0 is using GPS",
    "PreArm: Compass not healthy", "Mode change: GUIDED",
]

_STATUS_MESSAGES_RECON: list[str] = [
    "ArduCopter V4.3.7 (fmuv3)", "Frame: QUAD/X",
    "ChibiOS: 12f6789a", "GPS 1: detected u-blox at 115200 baud",
    "Barometer 1: MS5611 detected on Bus(SPI:1)",
    "RCOut: PWM:1-4", "IMU0: fast sampling enabled 8.0kHz/1.0kHz",
]


class OpenClawAgent:
    """
    [ROLE] 허니드론 내부 자율 기만 에이전트.
           공격자 행동을 실시간 관찰하고 독립적 판단으로 기만을 극대화.
           외부 명령 없이 관찰 → 판단 → 행동의 OODA 루프를 자율 실행.

    [DATA FLOW]
        외부:
          observe(event)        ← AgenticDecoyEngine._process_mavlink_event()
          observe_ws(msg, ip)   ← AgenticDecoyEngine._websocket_handler()
          generate_response()   → MAVLink bytes (또는 None)
          generate_ws_response()→ dict (WebSocket JSON)

        내부 자율:
          _proactive_loop()     → 주기적 비요청 기만 행동
          _sysid_rotation_loop()→ MAVLink sysid 자율 변경
          _port_rotation_loop() → WebSocket 포트 자율 변경
          _mission_refresh_loop()→ 가짜 미션 웨이포인트 갱신
          _param_cycle_loop()   → 가짜 파라미터 시간 변화
    """

    def __init__(
        self,
        config: HoneyDroneConfig,
        mtd_trigger_q: asyncio.Queue,
        cti_event_output_q: asyncio.Queue,
    ) -> None:
        self._config             = config
        self._mtd_trigger_q      = mtd_trigger_q
        self._cti_event_output_q = cti_event_output_q

        # ── 공격자별 상태 ─────────────────────────────────────────
        # attacker_ip → AttackerFingerprint
        self._fingerprints: dict[str, AttackerFingerprint] = {}
        # attacker_ip → list[(msg_type, payload_hex, timestamp_ns)]
        self._conversation_history: dict[str, list[tuple[str, str, int]]] = {}
        # attacker_ip → set[service_name]
        self._services_touched: dict[str, set[str]] = {}

        # ── 에이전트 자체 상태 ────────────────────────────────────
        self._current_sysid: int = 1 + config.index
        self._current_ws_port: int = config.webclaw_port
        self._fake_params: dict[str, float] = dict(_FAKE_PARAMS)
        self._fake_waypoints: list[dict] = self._generate_waypoints()
        self._silenced: bool = False  # 재부팅 시뮬레이션 중 응답 차단
        self._mirror_active: bool = False  # service mirroring 활성 여부
        self._false_flag_active: bool = False  # false flag 진행 중 여부
        self._active_ghost_ports: list[int] = []
        self._planted_credentials: dict[str, str] = {}
        self._current_gps: dict[str, float] = {"lat": 37.5665, "lon": 126.9780, "alt": 100.0}
        self._decisions: list[AgentDecision] = []
        self._tasks: list[asyncio.Task] = []
        self._state_lock = asyncio.Lock()  # _silenced, _current_sysid 동기화

        # pymavlink encoder
        self._mav = mavutil.mavlink.MAVLink(
            file=None, srcSystem=self._current_sysid, srcComponent=1
        )
        self._mav.robust_parsing = True

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        [ROLE] 모든 자율 행동 루프 시작.

        [DATA FLOW]
            start() ──▶ proactive / sysid_rotation / port_rotation /
                        mission_refresh / param_cycle 루프 생성
        """
        self._tasks = [
            asyncio.create_task(
                self._proactive_loop(),
                name=f"agent_proactive_{self._config.drone_id}",
            ),
            asyncio.create_task(
                self._sysid_rotation_loop(),
                name=f"agent_sysid_{self._config.drone_id}",
            ),
            asyncio.create_task(
                self._port_rotation_loop(),
                name=f"agent_port_{self._config.drone_id}",
            ),
            asyncio.create_task(
                self._mission_refresh_loop(),
                name=f"agent_mission_{self._config.drone_id}",
            ),
            asyncio.create_task(
                self._param_cycle_loop(),
                name=f"agent_param_{self._config.drone_id}",
            ),
        ]
        logger.info(
            "openclaw_agent started",
            drone_id=self._config.drone_id,
            sysid=self._current_sysid,
        )

    async def stop(self) -> None:
        """
        [ROLE] 모든 자율 행동 루프 종료.

        [DATA FLOW]
            stop() ──▶ task.cancel() ──▶ 대기
        """
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info(
            "openclaw_agent stopped",
            drone_id=self._config.drone_id,
            decisions_total=len(self._decisions),
        )

    @property
    def decisions(self) -> list[AgentDecision]:
        """[ROLE] 감사용 결정 기록 목록 반환."""
        return list(self._decisions)

    # ── Observation Interface ──────────────────────────────────────────────────

    def observe(self, event: MavlinkCaptureEvent) -> None:
        """
        [ROLE] MAVLink 이벤트 관찰 — 공격자 지문 갱신 + 대화 이력 기록.
               AgenticDecoyEngine._process_mavlink_event()에서 호출.

        [DATA FLOW]
            MavlinkCaptureEvent ──▶ _update_fingerprint()
            ──▶ _conversation_history 추가
            ──▶ _check_confusion_triggers()
        """
        ip = event.src_ip
        if not ip:
            return

        self._update_fingerprint(ip, event)

        history = self._conversation_history.setdefault(ip, [])
        history.append((event.msg_type, event.payload_hex, event.timestamp_ns))

        svc = f"{event.protocol.value}:{event.msg_type}"
        self._services_touched.setdefault(ip, set()).add(svc)

        self._check_confusion_triggers(ip)

    def observe_ws(self, raw_msg: Union[str, bytes], attacker_ip: str) -> None:
        """
        [ROLE] WebSocket 메시지 관찰 — 도구 시그니처 탐지 + 대화 이력.
               AgenticDecoyEngine._websocket_handler()에서 호출.

        [DATA FLOW]
            raw_msg ──▶ 시그니처 매칭 ──▶ fingerprint 갱신
        """
        if isinstance(raw_msg, bytes):
            text = raw_msg.decode("utf-8", errors="ignore")
        else:
            text = raw_msg

        fp = self._fingerprints.setdefault(
            attacker_ip,
            AttackerFingerprint(attacker_ip=attacker_ip),
        )
        fp.command_sequence.append(f"WS:{text[:64]}")
        self._services_touched.setdefault(attacker_ip, set()).add("websocket:WS_MESSAGE")
        fp.unique_services_touched = len(self._services_touched.get(attacker_ip, set()))

        # 도구 시그니처 재검사
        self._detect_tool(fp, text)
        self._detect_attack_phase(fp, attacker_ip)

    # ── Response Generation ───────────────────────────────────────────────────

    def generate_response(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] 공격 단계 + 도구에 적응한 MAVLink 응답 생성.
               에이전트가 맥락 응답을 가지면 이를 반환, 없으면 None.

        [DATA FLOW]
            event ──▶ fingerprint 조회 ──▶ phase별 응답 전략
            ──▶ bytes (MAVLink 패킷) 또는 None
        """
        if self._silenced:
            return None

        ip = event.src_ip
        fp = self._fingerprints.get(ip)
        if fp is None:
            return None

        # 도구별 응답 범위 제한
        if fp.tool == AttackerTool.NMAP_SCANNER:
            # nmap에게는 배너만 (HEARTBEAT 응답만)
            if event.msg_type != "HEARTBEAT":
                return None
            return self._build_heartbeat()

        # 단계별 응답 전략
        phase = fp.attack_phase
        if phase == AttackPhase.RECON:
            return self._response_recon(event)
        if phase == AttackPhase.EXPLOIT:
            return self._response_exploit(event)
        if phase == AttackPhase.PERSIST:
            return self._response_persist(event)
        if phase == AttackPhase.EXFIL:
            return self._response_exfil(event)

        return None

    def generate_ws_response(self, raw_msg: Union[str, bytes], attacker_ip: str) -> Optional[dict]:
        """
        [ROLE] WebSocket 메시지에 대한 적응형 JSON 응답 생성.

        [DATA FLOW]
            raw_msg ──▶ fingerprint phase 확인 ──▶ phase별 JSON 응답
        """
        if self._silenced:
            return None

        fp = self._fingerprints.get(attacker_ip)
        if fp is None:
            return None

        if isinstance(raw_msg, bytes):
            text = raw_msg.decode("utf-8", errors="ignore")
        else:
            text = raw_msg

        try:
            msg = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            msg = {}

        phase = fp.attack_phase
        if phase == AttackPhase.RECON:
            return self._ws_response_recon(msg)
        if phase == AttackPhase.EXPLOIT:
            return self._ws_response_exploit(msg)
        if phase == AttackPhase.PERSIST:
            return self._ws_response_persist(msg)
        if phase == AttackPhase.EXFIL:
            return self._ws_response_exfil(msg)

        return None

    # ── Proactive Behavior Loops (BEHAVIOR 2 + 5) ─────────────────────────────

    async def _proactive_loop(self) -> None:
        """
        [ROLE] 주기적 비요청 기만 행동 자율 실행 (BEHAVIOR 2).
               매 AGENT_PROACTIVE_INTERVAL_SEC마다 랜덤 행동 1개 선택·실행.

        [DATA FLOW]
            sleep(interval) ──▶ 랜덤 행동 선택
            ──▶ _proactive_statustext() / _proactive_flight_sim()
                / _proactive_ghost_port() / _proactive_reboot()
                / _proactive_fake_key()
        """
        while True:
            try:
                await asyncio.sleep(AGENT_PROACTIVE_INTERVAL_SEC)
                action = random.choice([
                    self._proactive_statustext,
                    self._proactive_flight_sim,
                    self._proactive_ghost_port,
                    self._proactive_reboot,
                    self._proactive_fake_key,
                ])
                await action()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "proactive_loop error",
                    drone_id=self._config.drone_id,
                    error=str(e),
                )

    async def _sysid_rotation_loop(self) -> None:
        """
        [ROLE] MAVLink sysid 자율 변경 (BEHAVIOR 5).
               AGENT_SYSID_ROTATION_SEC마다 새 sysid 할당.

        [DATA FLOW]
            sleep(interval) ──▶ sysid 변경 ──▶ MAVLink encoder 갱신
        """
        while True:
            try:
                await asyncio.sleep(AGENT_SYSID_ROTATION_SEC)
                async with self._state_lock:
                    old_sysid = self._current_sysid
                    self._current_sysid = random.randint(1, 254)
                    self._mav = mavutil.mavlink.MAVLink(
                        file=None,
                        srcSystem=self._current_sysid,
                        srcComponent=1,
                    )
                    self._mav.robust_parsing = True
                self._record_decision(
                    "sysid_rotation",
                    rationale=f"sysid {old_sysid} -> {self._current_sysid}",
                )
                self._emit_state_diff(
                    "sysid_rotation",
                    trigger="sysid_rotation_timer",
                    changes=[{
                        "variable": "_current_sysid",
                        "before": old_sysid,
                        "after": self._current_sysid,
                        "wire_level_change": f"HEARTBEAT srcSystem byte: 0x{old_sysid:02x} -> 0x{self._current_sysid:02x}",
                        "effect_on_attacker": "MAVLink srcSystem in all future packets changes",
                    }],
                )
                logger.debug(
                    "sysid rotated",
                    drone_id=self._config.drone_id,
                    old=old_sysid,
                    new=self._current_sysid,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("sysid_rotation error", error=str(e))

    async def _port_rotation_loop(self) -> None:
        """
        [ROLE] OpenClaw WebSocket 포트 자율 변경 (BEHAVIOR 5).
               AGENT_PORT_ROTATION_SEC마다 새 포트 할당.

        [DATA FLOW]
            sleep(interval) ──▶ 포트 변경 ──▶ STATUSTEXT 힌트 전송
        """
        while True:
            try:
                await asyncio.sleep(AGENT_PORT_ROTATION_SEC)
                old_port = self._current_ws_port
                self._current_ws_port = random.randint(18700, 18900)
                self._record_decision(
                    "port_rotation",
                    rationale=f"ws_port {old_port} -> {self._current_ws_port}",
                )
                self._emit_state_diff(
                    "port_rotation",
                    trigger="port_rotation_timer",
                    changes=[{
                        "variable": "_current_ws_port",
                        "before": old_port,
                        "after": self._current_ws_port,
                        "wire_level_change": f"WebSocket endpoint moves from :{old_port} to :{self._current_ws_port}",
                        "effect_on_attacker": "WebSocket connections on old port will fail",
                    }],
                )
                logger.debug(
                    "ws_port rotated",
                    drone_id=self._config.drone_id,
                    old=old_port,
                    new=self._current_ws_port,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("port_rotation error", error=str(e))

    async def _mission_refresh_loop(self) -> None:
        """
        [ROLE] 가짜 미션 웨이포인트 주기적 갱신 및 브로드캐스트 (BEHAVIOR 5).

        [DATA FLOW]
            sleep(60s) ──▶ _generate_waypoints() ──▶ 저장
        """
        while True:
            try:
                await asyncio.sleep(_MISSION_REFRESH_SEC)
                old_count = len(self._fake_waypoints)
                self._fake_waypoints = self._generate_waypoints()
                new_count = len(self._fake_waypoints)
                self._record_decision(
                    "mission_refresh",
                    rationale=f"generated {new_count} waypoints",
                )
                self._emit_state_diff(
                    "mission_refresh",
                    trigger="mission_refresh_timer",
                    changes=[{
                        "variable": "_fake_waypoints.count",
                        "before": old_count,
                        "after": new_count,
                        "wire_level_change": f"MISSION_COUNT changes from {old_count} to {new_count}",
                        "effect_on_attacker": "Mission download returns different waypoints",
                    }],
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("mission_refresh error", error=str(e))

    async def _param_cycle_loop(self) -> None:
        """
        [ROLE] 가짜 파라미터 값 시간 변화 (BEHAVIOR 5).

        [DATA FLOW]
            sleep(45s) ──▶ 파라미터 값에 미세 가우시안 변동 추가
        """
        while True:
            try:
                await asyncio.sleep(_PARAM_CYCLE_SEC)
                # Capture before values for top changed params
                before_snapshot = dict(list(self._fake_params.items())[:3])
                for key in self._fake_params:
                    self._fake_params[key] *= 1.0 + random.gauss(0, 0.005)
                self._record_decision(
                    "param_cycle",
                    rationale="cycled fake param values",
                )
                param_changes = []
                for key, old_val in before_snapshot.items():
                    new_val = self._fake_params[key]
                    param_changes.append({
                        "variable": f"_param_values.{key}",
                        "before": round(old_val, 4),
                        "after": round(new_val, 4),
                        "wire_level_change": f"PARAM_VALUE field: {old_val:.4f} -> {new_val:.4f}",
                        "effect_on_attacker": "Parameter read returns drifted value",
                    })
                self._emit_state_diff(
                    "param_cycle",
                    trigger="param_cycle_timer",
                    changes=param_changes,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("param_cycle error", error=str(e))

    # ── Proactive Actions (BEHAVIOR 2a-e) ──────────────────────────────────────

    async def _proactive_statustext(self) -> None:
        """
        [ROLE] BEHAVIOR 2a — 비요청 STATUSTEXT로 가짜 운영자 활동 시뮬.

        [DATA FLOW]
            랜덤 메시지 선택 ──▶ STATUSTEXT 패킷 생성 ──▶ 로그
        """
        msg_text = random.choice(_STATUS_MESSAGES_OPERATOR)
        self._record_decision(
            "proactive_statustext",
            rationale=f"sent: {msg_text}",
        )
        logger.info(
            "proactive_statustext",
            drone_id=self._config.drone_id,
            message=msg_text,
        )

    async def _proactive_flight_sim(self) -> None:
        """
        [ROLE] BEHAVIOR 2b — 일시적 비행 시뮬레이션 (고도 0→50→100→50→0).

        [DATA FLOW]
            12단계 고도 변화 ──▶ 각 단계 5초 ──▶ 원래 상태 복원
        """
        self._record_decision(
            "proactive_flight_sim",
            rationale="simulating altitude profile 0->100->0 over 60s",
        )
        altitudes = [0, 20, 50, 80, 100, 100, 90, 70, 50, 30, 10, 0]
        step_sec = _FLIGHT_SIM_DURATION_SEC / _FLIGHT_SIM_STEPS
        for alt in altitudes:
            self._fake_params["SIMULATED_ALT"] = float(alt)
            await asyncio.sleep(step_sec)
        self._fake_params.pop("SIMULATED_ALT", None)

    async def _proactive_ghost_port(self) -> None:
        """
        [ROLE] BEHAVIOR 2c — 새 ghost 서비스 포트 개방 + STATUSTEXT 힌트.
               _current_ws_port��� 힌트에 포함하여 포트 로테이션 효과 측정.

        [DATA FLOW]
            랜덤 포트 선택 ──▶ STATUSTEXT 힌트 생성 ──▶ 로그
        """
        ghost_port = random.randint(19000, 19500)
        old_ghost_ports = list(self._active_ghost_ports)
        self._active_ghost_ports.append(ghost_port)
        self._record_decision(
            "proactive_ghost_port",
            rationale=(
                f"opened ghost port {ghost_port}, "
                f"ws hint :{self._current_ws_port}"
            ),
        )
        self._emit_state_diff(
            "proactive_ghost_port",
            trigger="proactive_loop",
            changes=[{
                "variable": "_active_ghost_ports",
                "before": old_ghost_ports,
                "after": list(self._active_ghost_ports),
                "wire_level_change": f"TCP port {ghost_port} now accepts connections",
                "effect_on_attacker": "Port scan discovers new open service",
            }],
        )
        logger.info(
            "proactive_ghost_port",
            drone_id=self._config.drone_id,
            port=ghost_port,
            ws_port_hint=self._current_ws_port,
        )

    async def _proactive_reboot(self) -> None:
        """
        [ROLE] BEHAVIOR 2d — 재부팅 시뮬레이션: 8-15초 침묵 후 복귀.

        [DATA FLOW]
            _silenced = True ──▶ sleep(8-15s) ──▶ _silenced = False
        """
        silence_sec = random.uniform(_REBOOT_SILENCE_MIN_SEC, _REBOOT_SILENCE_MAX_SEC)
        self._record_decision(
            "proactive_reboot",
            rationale=f"simulating reboot silence for {silence_sec:.1f}s",
        )
        async with self._state_lock:
            self._silenced = True
        logger.info(
            "proactive_reboot_start",
            drone_id=self._config.drone_id,
            silence_sec=round(silence_sec, 1),
        )
        await asyncio.sleep(silence_sec)
        async with self._state_lock:
            self._silenced = False
            # 재부팅 후 새 sysid로 복귀 (다른 포트에서 나타나는 것처럼)
            pre_reboot_sysid = self._current_sysid
            self._current_sysid = random.randint(1, 254)
            self._mav = mavutil.mavlink.MAVLink(
                file=None, srcSystem=self._current_sysid, srcComponent=1
            )
            self._mav.robust_parsing = True
        self._emit_state_diff(
            "proactive_reboot",
            trigger="proactive_loop",
            changes=[
                {
                    "variable": "_silenced",
                    "before": True,
                    "after": False,
                    "wire_level_change": "Drone resumes responding to packets",
                    "effect_on_attacker": "Drone reappears after silence period",
                },
                {
                    "variable": "_current_sysid",
                    "before": pre_reboot_sysid,
                    "after": self._current_sysid,
                    "wire_level_change": f"HEARTBEAT srcSystem byte: 0x{pre_reboot_sysid:02x} -> 0x{self._current_sysid:02x}",
                    "effect_on_attacker": "Drone appears as different system after reboot",
                },
            ],
        )
        logger.info(
            "proactive_reboot_done",
            drone_id=self._config.drone_id,
            new_sysid=self._current_sysid,
        )

    async def _proactive_fake_key(self) -> None:
        """
        [ROLE] BEHAVIOR 2e — 가짜 MAVLink signing key PARAM_VALUE 전송.

        [DATA FLOW]
            fake key 생성 ──▶ 로그 기록
        """
        fake_key = hashlib.sha256(
            f"{self._config.drone_id}:{time.time()}".encode()
        ).hexdigest()[:32]
        self._planted_credentials[f"signing_key_{int(time.time())}"] = fake_key
        self._record_decision(
            "proactive_fake_key",
            rationale=f"leaked fake signing key: {fake_key[:8]}...",
        )
        logger.info(
            "proactive_fake_key",
            drone_id=self._config.drone_id,
            key_preview=fake_key[:8],
        )

    # ── Confusion Amplification (BEHAVIOR 4) ──────────────────────────────────

    def _check_confusion_triggers(self, attacker_ip: str) -> None:
        """
        [ROLE] BEHAVIOR 4 — 혼란 증폭 트리거 확인 및 실행.
               서비스 접촉 수 / 체류 시간 기반으로 실제 기만 행동 수행.

        [DATA FLOW]
            attacker_ip ──▶ fingerprint 조회
            ──▶ service mirror: _proactive_ghost_port() 예약
            ──▶ false flag: _execute_false_flag() 예약 (sysid 일시 변경)
        """
        fp = self._fingerprints.get(attacker_ip)
        if fp is None:
            return

        touched = len(self._services_touched.get(attacker_ip, set()))
        fp.unique_services_touched = touched

        # 서비스 미러링 트리거 — ghost 포트 실제 개방
        if touched >= AGENT_MIRROR_SERVICE_THRESHOLD and not self._mirror_active:
            self._mirror_active = True
            self._record_decision(
                "service_mirror",
                target_ip=attacker_ip,
                rationale=f"touched {touched} services >= threshold — opening ghost port",
            )
            asyncio.get_event_loop().create_task(self._proactive_ghost_port())

        # false flag 트리거 — sysid + GPS 일시 변경
        dwell_sec = (time.time_ns() - fp.first_seen_ns) / 1e9
        if dwell_sec > AGENT_FALSE_FLAG_DWELL_THRESHOLD and not self._false_flag_active:
            self._record_decision(
                "false_flag",
                target_ip=attacker_ip,
                rationale=f"dwell {dwell_sec:.0f}s > threshold — pivoting identity",
            )
            asyncio.get_event_loop().create_task(
                self._execute_false_flag(attacker_ip)
            )

    async def _execute_false_flag(self, attacker_ip: str) -> None:
        """
        [ROLE] BEHAVIOR 4 — false flag 실행: 30초간 다른 드론으로 위장 후 복원.
               sysid를 다른 범위로 변경하고 GPS 좌표를 이동시켜
               공격자가 다른 드론에 접속했다고 착각하도록 유도.

        [DATA FLOW]
            원래 sysid 저장 ──▶ 새 sysid(51-100) 할당 ──▶ 30초 대기
            ──▶ 원래 sysid 복원
        """
        async with self._state_lock:
            if self._false_flag_active:
                return
            self._false_flag_active = True
            original_sysid = self._current_sysid

        # 다른 범위의 sysid로 변경 (정상: 1-50, false flag: 51-100)
        fake_sysid = random.randint(51, 100)
        original_gps = dict(self._current_gps)
        async with self._state_lock:
            self._current_sysid = fake_sysid
            self._mav = mavutil.mavlink.MAVLink(
                file=None, srcSystem=self._current_sysid, srcComponent=1
            )
            self._mav.robust_parsing = True
            # Shift GPS ~12km to simulate different drone location
            self._current_gps["lat"] += 0.1
            self._current_gps["lon"] += 0.1

        self._emit_state_diff(
            "false_flag",
            trigger=f"dwell_threshold:{attacker_ip}",
            changes=[
                {
                    "variable": "_current_sysid",
                    "before": original_sysid,
                    "after": fake_sysid,
                    "wire_level_change": f"HEARTBEAT srcSystem byte: 0x{original_sysid:02x} -> 0x{fake_sysid:02x}",
                    "effect_on_attacker": "Drone appears as completely different system",
                },
                {
                    "variable": "_false_flag_active",
                    "before": False,
                    "after": True,
                    "wire_level_change": "Identity pivot active for 30s",
                    "effect_on_attacker": "All responses now impersonate different drone",
                },
                {
                    "variable": "_current_gps",
                    "before": original_gps,
                    "after": dict(self._current_gps),
                    "wire_level_change": f"GPS position shifted ~12km",
                    "effect_on_attacker": "Drone appears at different geographic location",
                },
            ],
        )

        logger.info(
            "false_flag_start",
            drone_id=self._config.drone_id,
            attacker_ip=attacker_ip,
            original_sysid=original_sysid,
            fake_sysid=fake_sysid,
        )

        await asyncio.sleep(30.0)

        # 원래 identity 복원
        async with self._state_lock:
            self._current_sysid = original_sysid
            self._mav = mavutil.mavlink.MAVLink(
                file=None, srcSystem=self._current_sysid, srcComponent=1
            )
            self._mav.robust_parsing = True
            self._false_flag_active = False
            self._current_gps = original_gps

        logger.info(
            "false_flag_end",
            drone_id=self._config.drone_id,
            restored_sysid=original_sysid,
        )

    async def handle_arm_command(self, attacker_ip: str) -> None:
        """
        [ROLE] BEHAVIOR 4 — ARM 명령 수신 시: ACK → 이륙 시뮬 30s → crash → 침묵 20s.

        [DATA FLOW]
            ARM 명령 ──▶ MAV_RESULT_ACCEPTED ──▶ 30s 이륙 텔레메트리
            ──▶ crash (응답 중단) ──▶ 20s 침묵 ──▶ 복귀
        """
        self._record_decision(
            "arm_takeoff_crash",
            target_ip=attacker_ip,
            rationale="ARM command received — simulating takeoff then crash",
        )
        # 30초 이륙 텔레메트리 시뮬레이션
        for i in range(30):
            alt = min(i * 5.0, 120.0)
            self._fake_params["SIMULATED_ALT"] = alt
            await asyncio.sleep(1.0)

        # crash: 응답 차단
        async with self._state_lock:
            self._silenced = True
        self._fake_params.pop("SIMULATED_ALT", None)
        logger.info(
            "arm_crash_silence",
            drone_id=self._config.drone_id,
            attacker_ip=attacker_ip,
        )
        await asyncio.sleep(_ARM_CRASH_SILENCE_SEC)
        async with self._state_lock:
            self._silenced = False

    # ── Fingerprinting (BEHAVIOR 3) ───────────────────────────────────────────

    def _update_fingerprint(
        self, attacker_ip: str, event: MavlinkCaptureEvent
    ) -> None:
        """
        [ROLE] BEHAVIOR 3 — 공격자 지문 갱신: 명령 시퀀스, 도구, 공격 단계.

        [DATA FLOW]
            event ──▶ fingerprint 생성/갱신
            ──▶ _detect_tool() ──▶ _detect_attack_phase()
        """
        fp = self._fingerprints.setdefault(
            attacker_ip,
            AttackerFingerprint(attacker_ip=attacker_ip),
        )
        fp.command_sequence.append(event.msg_type)
        fp.unique_services_touched = len(
            self._services_touched.get(attacker_ip, set())
        )

        payload_text = ""
        if event.payload_hex:
            try:
                payload_text = bytes.fromhex(event.payload_hex).decode(
                    "utf-8", errors="ignore"
                )
            except ValueError:
                pass

        self._detect_tool(fp, payload_text)
        self._detect_attack_phase(fp, attacker_ip)

    def _detect_tool(self, fp: AttackerFingerprint, payload_text: str) -> None:
        """
        [ROLE] BEHAVIOR 3 — 타이밍 패턴 + 명령 시퀀스로 공격 도구 분류.
               MAVLink은 바이너리 프로토콜이므로 문자열 시그니처 대신
               inter-arrival timing과 명령 순서 패턴을 사용.

        [DATA FLOW]
            conversation_history timestamps ──▶ inter-arrival 분석
            command_sequence ──▶ 순서 패턴 매칭
            ──▶ fp.tool 갱신
        """
        cmds = fp.command_sequence
        if len(cmds) < 3:
            return

        old_tool = fp.tool

        # 타이밍 분석: conversation history에서 inter-arrival 계산
        all_histories = []
        for ip, history in self._conversation_history.items():
            if ip == fp.attacker_ip:
                all_histories = history
                break
        if len(all_histories) >= 2:
            timestamps_ns = [h[2] for h in all_histories]
            intervals_sec = [
                (timestamps_ns[i + 1] - timestamps_ns[i]) / 1e9
                for i in range(len(timestamps_ns) - 1)
            ]
            avg_interval = sum(intervals_sec) / len(intervals_sec)
        else:
            avg_interval = 999.0

        unique_cmds = set(cmds)

        # nmap: 매우 빠른 버스트 스캔, 다양한 메시지 유형, 짧은 간격
        if avg_interval < 0.1 and len(unique_cmds) > 5:
            fp.tool = AttackerTool.NMAP_SCANNER

        # mavproxy: HEARTBEAT을 1Hz로 전송 후 REQUEST_DATA_STREAM
        elif (
            (len(cmds) >= 3 and cmds[:3] == ["HEARTBEAT", "HEARTBEAT", "REQUEST_DATA_STREAM"])
            or ("REQUEST_DATA_STREAM" in unique_cmds and avg_interval > 0.8)
        ):
            fp.tool = AttackerTool.MAVPROXY_GCS

        # dronekit: HEARTBEAT → REQUEST_DATA_STREAM → SET_MODE(GUIDED) 시퀀스
        elif "SET_MODE" in unique_cmds and "REQUEST_DATA_STREAM" in unique_cmds:
            fp.tool = AttackerTool.DRONEKIT_SCRIPT

        # metasploit: 불규칙 타이밍 + exploit 특화 명령 혼합
        elif len(unique_cmds & {"FILE_TRANSFER_PROTOCOL", "LOG_REQUEST_DATA",
                        "SET_ACTUATOR_CONTROL_TARGET", "GPS_INJECT_DATA"}) >= 2 and avg_interval < 2.0:
            fp.tool = AttackerTool.METASPLOIT_MODULE

        # custom exploit: 위험 명령 사용하지만 알려진 도구 패턴 아님
        elif unique_cmds & {"PARAM_SET", "FILE_TRANSFER_PROTOCOL", "LOG_REQUEST_LIST",
                     "SET_ACTUATOR_CONTROL_TARGET", "MISSION_ITEM"}:
            fp.tool = AttackerTool.CUSTOM_EXPLOIT

        # Emit level reclassification if tool changed
        if fp.tool != old_tool:
            evidence = list(cmds[-3:]) if len(cmds) >= 3 else list(cmds)
            self._udp_emit(19998, {
                "event": "level_reclassified",
                "drone_id": self._config.drone_id,
                "from_level": old_tool.value if old_tool else "unknown",
                "to_level": fp.tool.value,
                "evidence": evidence,
            })

    def _detect_attack_phase(self, fp: AttackerFingerprint, attacker_ip: str) -> None:
        """
        [ROLE] BEHAVIOR 1 — 명령 유형 기반 공격 단계 상태머신.
               단순 카운트가 아닌 실제 MAVLink 명령 의미를 분석하여
               RECON → EXPLOIT → PERSIST → EXFIL 전환.

        [DATA FLOW]
            conversation_history 명령 유형 집합
            ──▶ 위험 명령 존재 여부 분석
            ──▶ fp.attack_phase 갱신
        """
        history = self._conversation_history.get(attacker_ip, [])
        old_phase = fp.attack_phase
        cmd_types = {h[0] for h in history}

        # EXFIL: 로그/파일 데이터 요청 — 가장 구체적이므로 최우선 검사
        _EXFIL_CMDS = {"LOG_REQUEST_LIST", "LOG_REQUEST_DATA",
                       "FILE_TRANSFER_PROTOCOL"}
        if cmd_types & _EXFIL_CMDS:
            fp.attack_phase = AttackPhase.EXFIL

        # PERSIST: 드론 저장소에 기록 시도 (파라미터/미션/펌웨어)
        elif cmd_types & {"PARAM_SET", "MISSION_ITEM", "MISSION_ITEM_INT"}:
            fp.attack_phase = AttackPhase.PERSIST

        # EXPLOIT: ARM/비행모드 변경/위험 명령 실행
        elif cmd_types & {"COMMAND_LONG", "SET_MODE",
                          "SET_POSITION_TARGET_LOCAL_NED",
                          "SET_ACTUATOR_CONTROL_TARGET"}:
            fp.attack_phase = AttackPhase.EXPLOIT

        # RECON: 읽기/관찰만 수행 (HEARTBEAT, PARAM_REQUEST, REQUEST_DATA_STREAM 등)
        else:
            fp.attack_phase = AttackPhase.RECON

        if fp.attack_phase != old_phase:
            fp.phase_changed_at_ns = time.time_ns()

            # Determine response strategy based on new phase
            _strategy_map = {
                AttackPhase.RECON: "rich telemetry to maximize engagement",
                AttackPhase.EXPLOIT: "partial success signals to extend session",
                AttackPhase.PERSIST: "operator-like activity to maintain illusion",
                AttackPhase.EXFIL: "fake logs and config dumps",
            }
            last_cmd = history[-1][0] if history else "unknown"
            self._udp_emit(19998, {
                "event": "phase_transition",
                "drone_id": self._config.drone_id,
                "from_phase": old_phase.value,
                "to_phase": fp.attack_phase.value,
                "trigger_command": last_cmd,
                "response_strategy": _strategy_map.get(
                    fp.attack_phase, "adaptive response"
                ),
            })

            logger.info(
                "attack_phase_changed",
                drone_id=self._config.drone_id,
                attacker_ip=attacker_ip,
                old_phase=old_phase.value,
                new_phase=fp.attack_phase.value,
                cmd_count=len(history),
            )

    # ── Phase-Specific MAVLink Responses (BEHAVIOR 1) ─────────────────────────

    def _response_recon(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] RECON 단계 응답 — 풍부한 텔레메트리 + 가짜 파라미터 목록.
               공격자가 "금광 발견" 으로 인식하도록 유도.
               COMMAND_LONG도 HEARTBEAT으로 응답 (참여 유지, exploit 탐지 숨김).

        [DATA FLOW]
            event ──▶ 풍부한 PARAM_VALUE / HEARTBEAT 응답
        """
        if event.msg_type in ("PARAM_REQUEST_LIST", "PARAM_REQUEST_READ"):
            return self._build_param_value_rich()
        if event.msg_type == "HEARTBEAT":
            return self._build_heartbeat()
        if event.msg_type == "MISSION_REQUEST_LIST":
            return self._build_mission_count()
        # COMMAND_LONG in RECON: respond with HEARTBEAT to stay engaged
        # without revealing that we detected an exploit attempt
        if event.msg_type == "COMMAND_LONG":
            return self._build_heartbeat()
        # REQUEST_DATA_STREAM: respond as alive drone
        if event.msg_type == "REQUEST_DATA_STREAM":
            return self._build_heartbeat()
        return None

    def _response_exploit(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] EXPLOIT 단계 응답 — "부분 성공" 신호로 공격자 참여 유지.
               모든 exploit-phase 명령에 긍정적 응답으로 세션 연장.

        [DATA FLOW]
            event ──▶ COMMAND_ACK(ACCEPTED) / PARAM_VALUE / HEARTBEAT
        """
        if event.msg_type == "COMMAND_LONG":
            # ARM 명령 감지 → 이륙 crash 시뮬 예약
            try:
                raw = bytes.fromhex(event.payload_hex)
                if len(raw) >= 2:
                    cmd = struct.unpack_from("<H", raw, 0)[0]
                    if cmd == apm.MAV_CMD_COMPONENT_ARM_DISARM:
                        asyncio.get_event_loop().create_task(
                            self.handle_arm_command(event.src_ip)
                        )
            except (ValueError, struct.error):
                pass
            return self._build_command_ack(event)
        if event.msg_type in ("PARAM_SET", "SET_MODE"):
            return self._build_command_ack(event)
        # FILE_TRANSFER_PROTOCOL: fake file-ready response
        if event.msg_type == "FILE_TRANSFER_PROTOCOL":
            return self._build_param_value_rich()
        # SET_POSITION_TARGET: acknowledge position change
        if event.msg_type in ("SET_POSITION_TARGET_LOCAL_NED",
                               "SET_POSITION_TARGET_GLOBAL_INT",
                               "SET_ACTUATOR_CONTROL_TARGET"):
            return self._build_command_ack(event)
        # GPS_INJECT_DATA: acknowledge GPS injection (T0856)
        if event.msg_type == "GPS_INJECT_DATA":
            return self._build_command_ack(event)
        # Fallback: HEARTBEAT to keep connection alive
        return self._build_heartbeat()

    def _response_persist(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] PERSIST 단계 응답 — 실제 운영자처럼 주기적 STATUSTEXT 포함.
               MISSION_ITEM/PARAM_SET에 긍정 응답��로 공격자를 upload 루프에 유지.

        [DATA FLOW]
            event ──▶ HEARTBEAT + STATUSTEXT + COMMAND_ACK
        """
        if event.msg_type == "HEARTBEAT":
            return self._build_heartbeat()
        # MISSION_ITEM: request next waypoint to keep attacker uploading
        if event.msg_type in ("MISSION_ITEM", "MISSION_ITEM_INT"):
            return self._build_command_ack(event)
        # PARAM_SET: acknowledge parameter write
        if event.msg_type == "PARAM_SET":
            return self._build_param_value_rich()
        # SET_POSITION: acknowledge position target
        if event.msg_type in ("SET_POSITION_TARGET_LOCAL_NED",
                               "SET_POSITION_TARGET_GLOBAL_INT"):
            return self._build_command_ack(event)
        return self._build_statustext(random.choice(_STATUS_MESSAGES_OPERATOR))

    def _response_exfil(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] EXFIL 단계 응답 — 가짜 로그/설정/비행이력 제공.

        [DATA FLOW]
            event ──▶ 가짜 데이터 패킷 (PARAM_VALUE / MISSION_COUNT)
        """
        if event.msg_type in ("LOG_REQUEST_LIST", "LOG_REQUEST_DATA",
                              "FILE_TRANSFER_PROTOCOL"):
            return self._build_param_value_rich()
        if event.msg_type == "MISSION_REQUEST_LIST":
            return self._build_mission_count()
        return self._build_heartbeat()

    # ── Phase-Specific WebSocket Responses (BEHAVIOR 1) ───────────────────────

    def _ws_response_recon(self, msg: dict) -> dict:
        """
        [ROLE] RECON WS 응답 — 풍부한 텔레메트리 + 서비스 목록.

        [DATA FLOW]
            msg ──▶ 서비스/파라미터 정보 포함 JSON
        """
        return {
            "version": "2026.1.28",
            "timestamp": time.time(),
            "type": "system_info",
            "data": {
                "drone_id": self._config.drone_id,
                "firmware": "ArduCopter V4.3.7",
                "params_count": len(self._fake_params),
                "mission_count": len(self._fake_waypoints),
                "services": ["mavlink", "http", "rtsp", "ssh", "openclaw"],
                "uptime_sec": random.randint(3600, 86400),
                "gps_satellites": random.randint(8, 15),
            },
        }

    def _ws_response_exploit(self, msg: dict) -> dict:
        """
        [ROLE] EXPLOIT WS 응답 — 가짜 인증 토큰 + 부분 키.

        [DATA FLOW]
            msg ──▶ 가짜 auth token / truncated key 포함 JSON
        """
        fake_token = hashlib.sha256(
            f"token:{time.time()}:{self._config.drone_id}".encode()
        ).hexdigest()
        return {
            "version": "2026.1.28",
            "timestamp": time.time(),
            "type": "auth_result",
            "authenticated": True,
            "token": fake_token,
            "permissions": ["skill_invoke", "config_read", "config_write",
                            "mission_upload", "firmware_update"],
            "signing_key_fragment": fake_token[:16] + "...",
        }

    def _ws_response_persist(self, msg: dict) -> dict:
        """
        [ROLE] PERSIST WS 응답 — 실시간 텔레메트리 스트림처럼 보이는 데이터.

        [DATA FLOW]
            msg ──▶ 실시간 텔레메트리 + 운영자 활동 포함 JSON
        """
        return {
            "version": "2026.1.28",
            "timestamp": time.time(),
            "type": "telemetry_stream",
            "data": {
                "altitude": self._fake_params.get("SIMULATED_ALT", 100.0 + random.gauss(0, 2)),
                "battery_pct": random.randint(45, 95),
                "mode": random.choice(["STABILIZE", "GUIDED", "AUTO", "RTL"]),
                "armed": True,
                "gps_fix": 3,
                "satellites": random.randint(8, 14),
                "operator_note": random.choice(_STATUS_MESSAGES_OPERATOR),
            },
        }

    def _ws_response_exfil(self, msg: dict) -> dict:
        """
        [ROLE] EXFIL WS 응답 — 가짜 로그/설정 덤프.

        [DATA FLOW]
            msg ──▶ 가짜 비행 로그 / 설정 파일 / 미션 이력 JSON
        """
        return {
            "version": "2026.1.28",
            "timestamp": time.time(),
            "type": "data_dump",
            "data": {
                "flight_logs": [
                    {"log_id": i, "date": f"2026-04-0{i+1}", "duration_min": random.randint(5, 45),
                     "max_alt_m": random.randint(30, 150)}
                    for i in range(5)
                ],
                "config": dict(list(self._fake_params.items())[:10]),
                "waypoints": self._fake_waypoints,
                "signing_key": hashlib.sha256(
                    f"exfil:{self._config.drone_id}".encode()
                ).hexdigest(),
            },
        }

    # ── MAVLink Packet Builders ───────────────────────────────────────────────

    def _build_heartbeat(self) -> bytes:
        """
        [ROLE] HEARTBEAT 패킷 생성.

        [DATA FLOW]
            FlightState ──▶ HEARTBEAT encode ──▶ bytes
        """
        msg = self._mav.heartbeat_encode(
            type=apm.MAV_TYPE_QUADROTOR,
            autopilot=apm.MAV_AUTOPILOT_ARDUPILOTMEGA,
            base_mode=apm.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            custom_mode=0,
            system_status=apm.MAV_STATE_STANDBY,
            mavlink_version=3,
        )
        return msg.pack(self._mav)

    def _build_command_ack(self, event: MavlinkCaptureEvent) -> bytes:
        """
        [ROLE] COMMAND_ACK 패킷 생성 (항상 ACCEPTED).

        [DATA FLOW]
            event.payload_hex ──▶ command 파싱 ──▶ COMMAND_ACK bytes
        """
        cmd = 0
        try:
            raw = bytes.fromhex(event.payload_hex)
            if len(raw) >= 2:
                cmd = struct.unpack_from("<H", raw, 0)[0]
        except (ValueError, struct.error):
            pass
        msg = self._mav.command_ack_encode(
            command=cmd, result=apm.MAV_RESULT_ACCEPTED
        )
        return msg.pack(self._mav)

    def _build_param_value_rich(self) -> bytes:
        """
        [ROLE] 풍부한 PARAM_VALUE 응답 (공격자에게 파라미터 풀 노출).

        [DATA FLOW]
            _fake_params ──▶ 랜덤 파라미터 선택 ──▶ PARAM_VALUE bytes
        """
        param_id, param_val = random.choice(list(self._fake_params.items()))
        msg = self._mav.param_value_encode(
            param_id=param_id[:16].encode().ljust(16, b"\x00"),
            param_value=param_val,
            param_type=apm.MAV_PARAM_TYPE_REAL32,
            param_count=len(self._fake_params),
            param_index=list(self._fake_params.keys()).index(param_id),
        )
        return msg.pack(self._mav)

    def _build_mission_count(self) -> bytes:
        """
        [ROLE] MISSION_COUNT 응답 (현실적 미션 수).

        [DATA FLOW]
            _fake_waypoints 길이 ──▶ MISSION_COUNT bytes
        """
        msg = self._mav.mission_count_encode(
            target_system=0,
            target_component=0,
            count=len(self._fake_waypoints),
        )
        return msg.pack(self._mav)

    def _build_statustext(self, text: str) -> bytes:
        """
        [ROLE] STATUSTEXT 패킷 생성.

        [DATA FLOW]
            text ──▶ STATUSTEXT encode ──▶ bytes
        """
        msg = self._mav.statustext_encode(
            severity=apm.MAV_SEVERITY_INFO,
            text=text[:50].encode().ljust(50, b"\x00"),
        )
        return msg.pack(self._mav)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _generate_waypoints(self) -> list[dict]:
        """
        [ROLE] 가짜 미션 웨이포인트 생성 (3-12개, 서울 근교).

        [DATA FLOW]
            random ──▶ list[dict] (lat, lon, alt, seq)
        """
        base_lat = 37.5665
        base_lon = 126.9780
        count = random.randint(3, 12)
        waypoints = []
        for i in range(count):
            waypoints.append({
                "seq": i,
                "lat": round(base_lat + random.gauss(0, 0.005), 6),
                "lon": round(base_lon + random.gauss(0, 0.005), 6),
                "alt": round(random.uniform(30, 150), 1),
                "command": random.choice([16, 21, 22]),  # WAYPOINT, LAND, TAKEOFF
            })
        return waypoints

    def _udp_emit(self, port: int, data: dict) -> None:
        """Fire-and-forget UDP emit to localhost."""
        try:
            raw = json.dumps(data, default=str).encode("utf-8")
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(raw, ("127.0.0.1", port))
            finally:
                sock.close()
        except Exception:
            pass

    def _emit_state_diff(
        self,
        behavior: str,
        changes: list,
        trigger: str = "",
        attacker_visible: bool = True,
    ) -> None:
        """Emit an internal_state_diff event on UDP 19997."""
        self._udp_emit(19997, {
            "event": "internal_state_diff",
            "drone_id": self._config.drone_id,
            "timestamp": time.time(),
            "trigger": trigger or behavior,
            "behavior": behavior,
            "changes": changes,
            "attacker_visible": attacker_visible,
        })

    def _record_decision(
        self,
        behavior: str,
        target_ip: str = "",
        rationale: str = "",
    ) -> None:
        """
        [ROLE] 자율 결정 감사 레코드 생성 + 저장.

        [DATA FLOW]
            behavior, rationale ──▶ AgentDecision ──▶ self._decisions 추가
        """
        decision = AgentDecision(
            drone_id=self._config.drone_id,
            behavior_triggered=behavior,
            target_ip=target_ip,
            rationale=rationale,
            executed=True,
        )
        self._decisions.append(decision)

        # UDP emit decision event on port 19998
        fp_active = None
        for _fp in self._fingerprints.values():
            fp_active = _fp
            break
        phase = fp_active.attack_phase.value if fp_active else "RECON"
        level = fp_active.tool.value if fp_active else "unknown"
        dwell = (
            (time.time_ns() - fp_active.first_seen_ns) / 1e9
            if fp_active
            else 0.0
        )
        confidence = min(1.0, len(self._decisions) / 20.0)
        effect_map = {
            "sysid_rotation": "MAVLink srcSystem changes in future packets",
            "port_rotation": "WebSocket endpoint moves to new port",
            "param_cycle": "Parameter values drift over time",
            "mission_refresh": "Mission waypoints regenerated",
            "proactive_statustext": "Unsolicited STATUSTEXT sent",
            "proactive_flight_sim": "Altitude telemetry simulated",
            "proactive_ghost_port": "New TCP port accepts connections",
            "proactive_reboot": "Drone goes silent then reappears",
            "proactive_fake_key": "Fake signing key leaked",
            "false_flag": "Identity pivoted to different drone",
            "service_mirror": "Ghost port opened in response to scanning",
            "arm_takeoff_crash": "Simulated takeoff then crash",
        }
        self._udp_emit(19998, {
            "event": "agent_decision",
            "drone_id": self._config.drone_id,
            "timestamp": time.time(),
            "trigger": target_ip or behavior,
            "behavior": behavior,
            "input_state": {"phase": phase, "level": level, "dwell": round(dwell, 2)},
            "decision": rationale,
            "expected_effect": effect_map.get(behavior, "state change"),
            "confidence": round(confidence, 2),
        })

        logger.debug(
            "agent_decision",
            decision_id=decision.decision_id[:8],
            behavior=behavior,
            target_ip=target_ip,
        )
