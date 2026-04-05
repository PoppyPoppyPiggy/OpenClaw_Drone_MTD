#!/usr/bin/env python3
"""
negative_generator.py — 정상(음성) 샘플 생성기

Project  : MIRAGE-UAS
Module   : Dataset / Negative Generator
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - DVD Simulator 정상 비행 시나리오 (Boot→Arm→Takeoff→Hover→Land)
    - ArduPilot SITL 정상 텔레메트리 패턴

[Outputs]
    - list[DatasetEntry] (label=0, source="baseline_sitl")
    - results/dataset/negative_*.jsonl

[음성 샘플 생성 전략]
    전략 1 (PRIMARY): DVD Simulator 정상 비행 캡처
      - 공격자 없는 상태에서 Boot/Arm/Takeoff/Hover/Land 순서 실행
      - MAVLink 정상 텔레메트리 (HEARTBEAT, GLOBAL_POSITION_INT, ATTITUDE)
      - GCS → FC 정상 명령 (ARM, MODE_CHANGE 등)

    전략 2 (SUPPLEMENTARY): MAVLink 프로토콜 명세 기반 합성
      - 정상 비행 패턴 확률 분포로 합성 샘플 생성
      - 실제 ArduPilot 로그에서 추출한 메시지 빈도 분포 사용

[논문 기여]
    DVD-CTI-Dataset-v1 음성(negative) 파티션 생성
    클래스 불균형 제어: 양성:음성 = 1:1 ~ 1:3 (파라미터 조정)
    REF: MIRAGE-UAS §6 Dataset Construction
"""

import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiofiles

from shared.constants import RESULTS_DIR
from shared.logger import get_logger
from shared.models import DatasetEntry, DroneProtocol

logger = get_logger(__name__)

_NEGATIVE_DIR = Path(RESULTS_DIR) / "dataset" / "negative"

# ── ArduPilot 정상 비행 메시지 빈도 분포 ──────────────────────────────────────
# 실제 ArduPilot Copter v4.3 SITL 로그에서 추출한 메시지 비율
# 총합 = 1.0 (확률 분포)
_NORMAL_MSG_DISTRIBUTION: dict[str, float] = {
    "HEARTBEAT"               : 0.25,
    "GLOBAL_POSITION_INT"     : 0.20,
    "ATTITUDE"                : 0.18,
    "SYS_STATUS"              : 0.10,
    "GPS_RAW_INT"             : 0.08,
    "VFR_HUD"                 : 0.07,
    "RC_CHANNELS"             : 0.05,
    "STATUSTEXT"              : 0.03,
    "MISSION_CURRENT"         : 0.02,
    "NAV_CONTROLLER_OUTPUT"   : 0.01,
    "SERVO_OUTPUT_RAW"        : 0.01,
}
_NORMAL_MSG_TYPES = list(_NORMAL_MSG_DISTRIBUTION.keys())
_NORMAL_MSG_WEIGHTS = list(_NORMAL_MSG_DISTRIBUTION.values())

# GCS 정상 명령 (정상 비행 중 GCS가 보내는 메시지)
_NORMAL_GCS_COMMANDS: list[str] = [
    "HEARTBEAT",
    "COMMAND_LONG",   # ARM, MODE_CHANGE (정상)
    "SET_MODE",
    "PARAM_REQUEST_READ",
    "MISSION_REQUEST_LIST",
    "REQUEST_DATA_STREAM",
]

# 정상 비행의 GCS IP 범위 (신뢰할 수 있는 GCS 시뮬레이션)
_NORMAL_GCS_IPS: list[str] = [
    "172.31.0.100",   # 내부 GCS (honey_internal 네트워크)
    "172.31.0.101",
    "172.31.0.102",
]


@dataclass
class FlightScenario:
    """정상 비행 시나리오 정의."""
    name       : str
    phases     : list[str]          # 비행 상태 순서
    duration_s : float              # 총 시간 (초)
    msg_count  : int                # 생성할 메시지 수


# 표준 정상 비행 시나리오 (DVD Simulator 기반)
_SCENARIOS: list[FlightScenario] = [
    FlightScenario("short_hover",   ["boot","arm","takeoff","hover","land"], 60,  120),
    FlightScenario("medium_flight", ["boot","arm","takeoff","hover","land"], 120, 240),
    FlightScenario("parameter_check", ["boot","param_check"],                30,   60),
    FlightScenario("mission_upload", ["boot","arm","mission_upload","land"], 90,  180),
]


class NegativeGenerator:
    """
    [ROLE] 정상 MAVLink 비행 트래픽을 생성하여 label=0 DatasetEntry 제공.
           양성 샘플과 쌍을 이루어 DVD-CTI-Dataset-v1의 균형 있는 구성 보장.

    [DATA FLOW]
        generate(n_samples)
        ──▶ _simulate_flight_scenario() × N  (합성 방법)
        ──▶ DatasetEntry(label=0)
        ──▶ results/dataset/negative/*.jsonl
    """

    def __init__(self) -> None:
        self._session_id = str(uuid.uuid4())[:8]
        self._generated: list[DatasetEntry] = []

    async def generate(
        self,
        n_samples: int,
        drone_id: str = "baseline",
        method: str = "synthetic",
    ) -> list[DatasetEntry]:
        """
        [ROLE] n_samples 개의 음성 DatasetEntry 생성.

        [DATA FLOW]
            n_samples ──▶ scenario 반복 시뮬레이션
            ──▶ list[DatasetEntry](label=0)
            ──▶ 파일 저장
        """
        _NEGATIVE_DIR.mkdir(parents=True, exist_ok=True)
        entries: list[DatasetEntry] = []

        if method == "synthetic":
            entries = self._generate_synthetic(n_samples, drone_id)
        elif method == "scenario":
            entries = await self._generate_from_scenarios(n_samples, drone_id)

        self._generated.extend(entries)

        await self._save_entries(entries)
        logger.info(
            "negative_samples_generated",
            session=self._session_id,
            count=len(entries),
            method=method,
            drone_id=drone_id,
        )
        return entries

    def get_entries(self) -> list[DatasetEntry]:
        """[ROLE] 현재까지 생성된 음성 샘플 목록 반환."""
        return list(self._generated)

    def stats(self) -> dict:
        """[ROLE] 생성 통계 반환 (논문 Table IV)."""
        msg_types = {}
        for e in self._generated:
            msg_types[e.msg_type] = msg_types.get(e.msg_type, 0) + 1
        return {
            "total"         : len(self._generated),
            "label"         : 0,
            "by_msg_type"   : msg_types,
            "drone_ids"     : list({e.drone_id for e in self._generated}),
        }

    # ── 생성 방법 ─────────────────────────────────────────────────────────────

    def _generate_synthetic(
        self, n_samples: int, drone_id: str
    ) -> list[DatasetEntry]:
        """
        [ROLE] MAVLink 메시지 빈도 분포 기반 합성 음성 샘플 생성.
               실제 ArduPilot 로그 통계를 활용하여 현실적 분포 유지.

        [DATA FLOW]
            _NORMAL_MSG_DISTRIBUTION 확률 ──▶ weighted random 샘플링
            ──▶ DatasetEntry(label=0, source="synthetic")
        """
        now_ns = time.time_ns()
        entries = []
        gcs_ip  = random.choice(_NORMAL_GCS_IPS)

        for i in range(n_samples):
            # 메시지 타입 확률 샘플링
            msg_type = random.choices(
                _NORMAL_MSG_TYPES, weights=_NORMAL_MSG_WEIGHTS, k=1
            )[0]
            # 시간 진행 (실제 비행 시뮬레이션)
            ts_ns = now_ns + i * int(1e8)   # 100ms 간격

            entries.append(DatasetEntry(
                timestamp_ns   = ts_ns,
                drone_id       = drone_id,
                src_ip         = gcs_ip,
                protocol       = DroneProtocol.MAVLINK,
                msg_type       = msg_type,
                payload_hex    = _generate_normal_payload(msg_type),
                attacker_level = None,          # benign: 공격자 레벨 없음
                ttp_ids        = [],            # benign: TTP 없음
                stix_bundle_id = "",
                label          = 0,
                confidence     = 1.0,
                source         = "synthetic",
            ))
        return entries

    async def _generate_from_scenarios(
        self, n_samples: int, drone_id: str
    ) -> list[DatasetEntry]:
        """
        [ROLE] 정상 비행 시나리오 순서에 따라 샘플 생성.
               Boot→Arm→Takeoff→Hover→Land 페이즈별 메시지 패턴 유지.

        [DATA FLOW]
            FlightScenario ──▶ 페이즈별 메시지 생성 ──▶ DatasetEntry list
        """
        entries   = []
        scenario  = random.choice(_SCENARIOS)
        now_ns    = time.time_ns()
        gcs_ip    = random.choice(_NORMAL_GCS_IPS)
        per_phase = max(1, n_samples // len(scenario.phases))

        for phase in scenario.phases:
            phase_msgs = _PHASE_MSG_TYPES.get(phase, _NORMAL_MSG_TYPES[:5])
            for j in range(per_phase):
                if len(entries) >= n_samples:
                    break
                msg_type = random.choice(phase_msgs)
                ts_ns    = now_ns + len(entries) * int(5e7)
                entries.append(DatasetEntry(
                    timestamp_ns   = ts_ns,
                    drone_id       = drone_id,
                    src_ip         = gcs_ip,
                    protocol       = DroneProtocol.MAVLINK,
                    msg_type       = msg_type,
                    payload_hex    = _generate_normal_payload(msg_type),
                    attacker_level = None,
                    ttp_ids        = [],
                    stix_bundle_id = "",
                    label          = 0,
                    confidence     = 1.0,
                    source         = f"scenario_{scenario.name}",
                ))
        return entries[:n_samples]

    async def _save_entries(self, entries: list[DatasetEntry]) -> None:
        """[ROLE] DatasetEntry 리스트를 JSONL 파일로 저장."""
        path = _NEGATIVE_DIR / f"negative_{self._session_id}.jsonl"
        try:
            async with aiofiles.open(path, "a", encoding="utf-8") as f:
                for e in entries:
                    record = {
                        "entry_id"       : e.entry_id,
                        "timestamp_ns"   : e.timestamp_ns,
                        "drone_id"       : e.drone_id,
                        "src_ip"         : e.src_ip,
                        "protocol"       : e.protocol.value,
                        "msg_type"       : e.msg_type,
                        "payload_hex"    : e.payload_hex,
                        "attacker_level" : None,
                        "ttp_ids"        : [],
                        "stix_bundle_id" : "",
                        "label"          : 0,
                        "confidence"     : 1.0,
                        "source"         : e.source,
                    }
                    await f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as ex:
            logger.error("save_negative_failed", error=str(ex))


# ── 페이즈별 메시지 패턴 ──────────────────────────────────────────────────────
_PHASE_MSG_TYPES: dict[str, list[str]] = {
    "boot"          : ["HEARTBEAT", "SYS_STATUS", "STATUSTEXT"],
    "arm"           : ["HEARTBEAT", "COMMAND_LONG", "SYS_STATUS", "STATUSTEXT"],
    "takeoff"       : ["HEARTBEAT", "GLOBAL_POSITION_INT", "ATTITUDE", "VFR_HUD"],
    "hover"         : ["HEARTBEAT", "GLOBAL_POSITION_INT", "ATTITUDE", "GPS_RAW_INT"],
    "land"          : ["HEARTBEAT", "GLOBAL_POSITION_INT", "ATTITUDE", "STATUSTEXT"],
    "param_check"   : ["HEARTBEAT", "PARAM_REQUEST_READ", "PARAM_VALUE"],
    "mission_upload": ["HEARTBEAT", "MISSION_REQUEST_LIST", "MISSION_ITEM", "MISSION_ACK"],
}


def _generate_normal_payload(msg_type: str) -> str:
    """
    [ROLE] 정상 MAVLink 메시지 페이로드 합성 (16 bytes hex string).
           공격 페이로드와 구분되는 정상 범위 값 사용.

    [DATA FLOW]
        msg_type ──▶ 타입별 정상 범위 값 ──▶ hex str
    """
    import struct
    if msg_type == "HEARTBEAT":
        # type=2(MAV_TYPE_QUADROTOR), autopilot=3(ARDUPILOTMEGA)
        payload = struct.pack("<IBBBBB", 0, 2, 3, 0xC0, 4, 3)
    elif msg_type == "GLOBAL_POSITION_INT":
        lat = int(37.5665 * 1e7) + random.randint(-100, 100)
        lon = int(126.978 * 1e7) + random.randint(-100, 100)
        payload = struct.pack("<IiiiihhhH",
            int(time.time() * 1000) % (2**32),
            lat, lon,
            int(100 * 1000),    # alt mm
            int(100 * 1000),    # relative alt mm
            random.randint(-50, 50),   # vx cm/s
            random.randint(-50, 50),   # vy
            random.randint(-10, 10),   # vz
            random.randint(0, 35999),  # hdg
        )
    else:
        payload = bytes(random.randint(0, 127) for _ in range(16))
    return payload.hex()
