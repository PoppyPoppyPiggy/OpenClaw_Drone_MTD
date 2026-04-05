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
import struct
import time
import uuid
from typing import Optional

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

# ── 도구 식별 시그니처 ────────────────────────────────────────────────────────
_NMAP_SIGNATURES: frozenset[str] = frozenset([
    "Nmap", "nmap", "masscan", "NSOCK",
])
_MAVPROXY_SIGNATURES: frozenset[str] = frozenset([
    "MAVProxy", "mavproxy", "GCS_CLIENT",
])
_DRONEKIT_SIGNATURES: frozenset[str] = frozenset([
    "dronekit", "DroneKit", "GUIDED", "vehicle.connect",
])
_METASPLOIT_SIGNATURES: frozenset[str] = frozenset([
    "msf", "meterpreter", "metasploit", "auxiliary/",
])

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
        self._decisions: list[AgentDecision] = []
        self._tasks: list[asyncio.Task] = []

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

    def observe_ws(self, raw_msg: str | bytes, attacker_ip: str) -> None:
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

    def generate_ws_response(self, raw_msg: str | bytes, attacker_ip: str) -> Optional[dict]:
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
                self._fake_waypoints = self._generate_waypoints()
                self._record_decision(
                    "mission_refresh",
                    rationale=f"generated {len(self._fake_waypoints)} waypoints",
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
                for key in self._fake_params:
                    self._fake_params[key] *= 1.0 + random.gauss(0, 0.005)
                self._record_decision(
                    "param_cycle",
                    rationale="cycled fake param values",
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

        [DATA FLOW]
            랜덤 포트 선택 ──▶ STATUSTEXT 힌트 생성 ──▶ 로그
        """
        ghost_port = random.randint(19000, 19500)
        self._record_decision(
            "proactive_ghost_port",
            rationale=f"opened ghost port {ghost_port}",
        )
        logger.info(
            "proactive_ghost_port",
            drone_id=self._config.drone_id,
            port=ghost_port,
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
        self._silenced = True
        logger.info(
            "proactive_reboot_start",
            drone_id=self._config.drone_id,
            silence_sec=round(silence_sec, 1),
        )
        await asyncio.sleep(silence_sec)
        self._silenced = False
        # 재부팅 후 새 sysid로 복귀 (다른 포트에서 나타나는 것처럼)
        self._current_sysid = random.randint(1, 254)
        self._mav = mavutil.mavlink.MAVLink(
            file=None, srcSystem=self._current_sysid, srcComponent=1
        )
        self._mav.robust_parsing = True
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
        [ROLE] BEHAVIOR 4 — 혼란 증폭 트리거 확인.
               서비스 접촉 수 / 체류 시간 기반으로 추가 기만 행동 예약.

        [DATA FLOW]
            attacker_ip ──▶ fingerprint 조회
            ──▶ service mirror / false flag / ARM crash 트리거
        """
        fp = self._fingerprints.get(attacker_ip)
        if fp is None:
            return

        touched = len(self._services_touched.get(attacker_ip, set()))
        fp.unique_services_touched = touched

        # 서비스 미러링 트리거
        if touched >= AGENT_MIRROR_SERVICE_THRESHOLD:
            self._record_decision(
                "service_mirror",
                target_ip=attacker_ip,
                rationale=f"touched {touched} services >= threshold",
            )

        # false flag 트리거 (체류 시간 기반)
        dwell_sec = (time.time_ns() - fp.first_seen_ns) / 1e9
        if dwell_sec > AGENT_FALSE_FLAG_DWELL_THRESHOLD:
            self._record_decision(
                "false_flag",
                target_ip=attacker_ip,
                rationale=f"dwell {dwell_sec:.0f}s > threshold",
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
        self._silenced = True
        self._fake_params.pop("SIMULATED_ALT", None)
        logger.info(
            "arm_crash_silence",
            drone_id=self._config.drone_id,
            attacker_ip=attacker_ip,
        )
        await asyncio.sleep(_ARM_CRASH_SILENCE_SEC)
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
        [ROLE] BEHAVIOR 3 — 페이로드 시그니처로 공격 도구 분류.

        [DATA FLOW]
            payload_text ──▶ 시그니처 매칭 ──▶ fp.tool 갱신
        """
        if any(sig in payload_text for sig in _NMAP_SIGNATURES):
            fp.tool = AttackerTool.NMAP_SCANNER
        elif any(sig in payload_text for sig in _MAVPROXY_SIGNATURES):
            fp.tool = AttackerTool.MAVPROXY_GCS
        elif any(sig in payload_text for sig in _DRONEKIT_SIGNATURES):
            fp.tool = AttackerTool.DRONEKIT_SCRIPT
        elif any(sig in payload_text for sig in _METASPLOIT_SIGNATURES):
            fp.tool = AttackerTool.METASPLOIT_MODULE
        elif len(fp.command_sequence) > 20 and fp.tool == AttackerTool.UNKNOWN:
            fp.tool = AttackerTool.CUSTOM_EXPLOIT

    def _detect_attack_phase(self, fp: AttackerFingerprint, attacker_ip: str) -> None:
        """
        [ROLE] BEHAVIOR 1 — 공격 단계 탐지 (RECON → EXPLOIT → PERSIST → EXFIL).
               대화 이력 길이 + 명령 패턴으로 단계 전환 결정.

        [DATA FLOW]
            conversation_history ──▶ 패턴 분석 ──▶ fp.attack_phase 갱신
        """
        history = self._conversation_history.get(attacker_ip, [])
        cmd_count = len(history)
        old_phase = fp.attack_phase

        # 명령 유형 집합
        cmd_types = {h[0] for h in history}

        # EXFIL: 로그/미션/파일 요청
        exfil_cmds = {"LOG_REQUEST_LIST", "LOG_REQUEST_DATA",
                      "FILE_TRANSFER_PROTOCOL", "MISSION_REQUEST_LIST"}
        if cmd_count > 30 and len(cmd_types & exfil_cmds) >= 2:
            fp.attack_phase = AttackPhase.EXFIL
        # PERSIST: 장시간 체류 + 반복 명령
        elif cmd_count > 20:
            fp.attack_phase = AttackPhase.PERSIST
        # EXPLOIT: ARM/SET_MODE/PARAM_SET 시도
        elif cmd_count > 8 and cmd_types & {"COMMAND_LONG", "PARAM_SET", "SET_MODE"}:
            fp.attack_phase = AttackPhase.EXPLOIT
        # RECON: 초기
        else:
            fp.attack_phase = AttackPhase.RECON

        if fp.attack_phase != old_phase:
            fp.phase_changed_at_ns = time.time_ns()
            logger.info(
                "attack_phase_changed",
                drone_id=self._config.drone_id,
                attacker_ip=attacker_ip,
                old_phase=old_phase.value,
                new_phase=fp.attack_phase.value,
                cmd_count=cmd_count,
            )

    # ── Phase-Specific MAVLink Responses (BEHAVIOR 1) ─────────────────────────

    def _response_recon(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] RECON 단계 응답 — 풍부한 텔레메트리 + 가짜 파라미터 목록.
               공격자가 "금광 발견" 으로 인식하도록 유도.

        [DATA FLOW]
            event ──▶ 풍부한 PARAM_VALUE / HEARTBEAT 응답
        """
        if event.msg_type in ("PARAM_REQUEST_LIST", "PARAM_REQUEST_READ"):
            return self._build_param_value_rich()
        if event.msg_type == "HEARTBEAT":
            return self._build_heartbeat()
        if event.msg_type == "MISSION_REQUEST_LIST":
            return self._build_mission_count()
        return None

    def _response_exploit(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] EXPLOIT 단계 응답 — "부분 성공" 신호로 공격자 참여 유지.

        [DATA FLOW]
            event ──▶ COMMAND_ACK(ACCEPTED) + 가짜 토큰 힌트
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
        return None

    def _response_persist(self, event: MavlinkCaptureEvent) -> Optional[bytes]:
        """
        [ROLE] PERSIST 단계 응답 — 실제 운영자처럼 주기적 STATUSTEXT 포함.

        [DATA FLOW]
            event ──▶ HEARTBEAT + STATUSTEXT 메시지
        """
        if event.msg_type == "HEARTBEAT":
            return self._build_heartbeat()
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
        logger.debug(
            "agent_decision",
            decision_id=decision.decision_id[:8],
            behavior=behavior,
            target_ip=target_ip,
        )
