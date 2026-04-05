#!/usr/bin/env python3
"""
attck_mapper.py — MITRE ATT&CK for ICS v14 MAVLink/드론 TTP 매핑

Project  : MIRAGE-UAS
Module   : CTI Pipeline / ATT&CK Mapper
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - MavlinkCaptureEvent.msg_type (MAVLink 메시지 유형)
    - MavlinkCaptureEvent.protocol (MAVLink / HTTP / RTSP / WebSocket)

[Outputs]
    - list[str]: ATT&CK for ICS v14 TTP ID 목록 (e.g. ["T0855", "T0858"])
    - KillChainPhase: 공격 Kill Chain 단계

[설계 원칙]
    - ATT&CK for ICS v14 기반 (2024 업데이트)
    - MAVLink 2.0 메시지 유형별 1:N TTP 매핑
    - HTTP/RTSP/WebSocket 별도 매핑
    - 논문 §6 CTI Pipeline + Dataset에서 직접 인용
    - confidence 산출 기준 포함

[REF]
    MITRE ATT&CK for ICS v14 https://attack.mitre.org/techniques/ics/
    논문 §6 Table V: MAVLink TTP Coverage
"""

from __future__ import annotations

from shared.models import DroneProtocol, KillChainPhase

# ════════════════════════════════════════════════════════════════════════════════
# ATT&CK for ICS v14 — MAVLink 메시지 유형 → TTP 매핑
# 형식: {msg_type: (ttp_ids, kill_chain_phase, confidence)}
# confidence: 매핑 확신도 [0.0, 1.0]
# ════════════════════════════════════════════════════════════════════════════════

_MAVLINK_TTP_MAP: dict[str, tuple[list[str], KillChainPhase, float]] = {

    # ── Reconnaissance / 정찰 ──────────────────────────────────────────────────
    "HEARTBEAT": (
        ["T0842"],          # Network Sniffing
        KillChainPhase.RECONNAISSANCE,
        0.70,               # 정상 heartbeat도 있으므로 중간 확신도
    ),
    "PARAM_REQUEST_LIST": (
        ["T0842", "T0840"], # Network Sniffing + Network Connection Enumeration
        KillChainPhase.RECONNAISSANCE,
        0.90,
    ),
    "PARAM_REQUEST_READ": (
        ["T0842", "T0840"],
        KillChainPhase.RECONNAISSANCE,
        0.85,
    ),
    "MISSION_REQUEST_LIST": (
        ["T0840", "T0882"], # Network Enumeration + Theft of Operational Information
        KillChainPhase.RECONNAISSANCE,
        0.85,
    ),
    "MISSION_REQUEST": (
        ["T0840", "T0882"],
        KillChainPhase.RECONNAISSANCE,
        0.85,
    ),
    "LOG_REQUEST_LIST": (
        ["T0840", "T0882"],
        KillChainPhase.RECONNAISSANCE,
        0.90,
    ),
    "LOG_REQUEST_DATA": (
        ["T0840", "T0882"],
        KillChainPhase.RECONNAISSANCE,
        0.90,
    ),
    "AUTOPILOT_VERSION_REQUEST": (
        ["T0840", "T0888"], # Network Enumeration + Remote System Info Discovery
        KillChainPhase.RECONNAISSANCE,
        0.95,
    ),
    "DATA_STREAM_REQUEST_LIST": (
        ["T0840"],
        KillChainPhase.RECONNAISSANCE,
        0.75,
    ),

    # ── Initial Access / 초기 접근 ─────────────────────────────────────────────
    "COMMAND_LONG": (
        ["T0855"],          # Unauthorized Command Message
        KillChainPhase.EXPLOITATION,
        0.80,               # 정상 명령도 COMMAND_LONG 사용
    ),

    # ── Execution / 실행 ───────────────────────────────────────────────────────
    "SET_MODE": (
        ["T0858"],          # Change Operating Mode
        KillChainPhase.EXPLOITATION,
        0.90,
    ),
    "SET_POSITION_TARGET_LOCAL_NED": (
        ["T0855", "T0831"], # Unauthorized Command + Manipulation of Control
        KillChainPhase.ACTION,
        0.90,
    ),
    "SET_POSITION_TARGET_GLOBAL_INT": (
        ["T0855", "T0831"],
        KillChainPhase.ACTION,
        0.90,
    ),
    "SET_ACTUATOR_CONTROL_TARGET": (
        ["T0855", "T0831"],
        KillChainPhase.ACTION,
        0.95,
    ),
    "MANUAL_CONTROL": (
        ["T0855", "T0831"],
        KillChainPhase.ACTION,
        0.85,
    ),

    # ── Persistence / 지속 ────────────────────────────────────────────────────
    "FILE_TRANSFER_PROTOCOL": (
        ["T0843", "T0839"], # Program Upload + Module Firmware
        KillChainPhase.INSTALLATION,
        0.95,
    ),
    "PARAM_SET": (
        ["T0836"],          # Modify Parameter
        KillChainPhase.EXPLOITATION,
        0.90,
    ),

    # ── Impact / 영향 ─────────────────────────────────────────────────────────
    "MISSION_ITEM": (
        ["T0821", "T0858"], # Modify Controller Tasking + Change Operating Mode
        KillChainPhase.ACTION,
        0.90,
    ),
    "MISSION_ITEM_INT": (
        ["T0821", "T0858"],
        KillChainPhase.ACTION,
        0.90,
    ),
    "MISSION_SET_CURRENT": (
        ["T0821"],
        KillChainPhase.ACTION,
        0.85,
    ),
    "MISSION_START": (
        ["T0821", "T0831"],
        KillChainPhase.ACTION,
        0.90,
    ),

    # ── C2 / 명령제어 ──────────────────────────────────────────────────────────
    "SET_GPS_GLOBAL_ORIGIN": (
        ["T0856", "T0830"], # Spoof Reporting + Adversary-in-the-Middle
        KillChainPhase.C2,
        0.85,
    ),
    "GPS_INJECT_DATA": (
        ["T0856", "T0830"],
        KillChainPhase.C2,
        0.95,
    ),
}


# ── HTTP 매핑 ──────────────────────────────────────────────────────────────────
_HTTP_TTP_MAP: dict[str, tuple[list[str], KillChainPhase, float]] = {
    "GET:/api":  (["T0842", "T0888"], KillChainPhase.RECONNAISSANCE, 0.75),
    "GET:/":     (["T0840"],          KillChainPhase.RECONNAISSANCE, 0.70),
    "POST:/api": (["T0836"],          KillChainPhase.EXPLOITATION,   0.85),
    "PUT:/api":  (["T0836"],          KillChainPhase.EXPLOITATION,   0.85),
    "DELETE:/":  (["T0809"],          KillChainPhase.ACTION,         0.90),  # Data Destruction
}

# ── RTSP 매핑 ─────────────────────────────────────────────────────────────────
_RTSP_TTP_MAP: dict[str, tuple[list[str], KillChainPhase, float]] = {
    "DESCRIBE": (["T0842"],          KillChainPhase.RECONNAISSANCE, 0.80),
    "SETUP":    (["T0842"],          KillChainPhase.RECONNAISSANCE, 0.80),
    "PLAY":     (["T0842", "T0849"], KillChainPhase.ACTION,         0.85),  # Network Sniffing + Screen Capture
    "PAUSE":    (["T0815"],          KillChainPhase.ACTION,         0.70),  # Denial of View
    "TEARDOWN": (["T0813"],          KillChainPhase.ACTION,         0.60),  # Denial of Control
}

# ── WebSocket (OpenClaw) 매핑 ──────────────────────────────────────────────────
_WS_TTP_MAP: dict[str, tuple[list[str], KillChainPhase, float]] = {
    "WS_CONNECT": (["T0886"],          KillChainPhase.DELIVERY,     0.85),  # Remote Services
    "WS_MESSAGE": (["T0820", "T0886"], KillChainPhase.EXPLOITATION, 0.85),  # Exploitation of Remote Services
    "WS_AUTH_BYPASS": (
        ["T0820", "T0812", "T0830"],   # Exploitation + Default Credentials + AitM
        KillChainPhase.EXPLOITATION,
        0.98,
    ),
    "WS_SKILL_INVOKE": (["T0855", "T0807"], KillChainPhase.ACTION, 0.90),
}

# ── 기본값 (알 수 없는 메시지) ────────────────────────────────────────────────
_DEFAULT_TTP = (["T0842"], KillChainPhase.RECONNAISSANCE, 0.40)


# ════════════════════════════════════════════════════════════════════════════════
# ATTCKMapper 클래스
# ════════════════════════════════════════════════════════════════════════════════

class ATTCKMapper:
    """
    [ROLE] MAVLink/HTTP/RTSP/WebSocket 이벤트를 ATT&CK for ICS v14 TTP로 매핑.
           ParsedAttackEvent 생성에 필요한 ttp_ids, kill_chain_phase, confidence 제공.

    [DATA FLOW]
        DroneProtocol + msg_type
        ──▶ map_event()
        ──▶ (ttp_ids, kill_chain_phase, confidence)
        ──▶ AttackEventParser ──▶ ParsedAttackEvent
    """

    def map_event(
        self,
        protocol: DroneProtocol,
        msg_type: str,
        http_method: str = "",
        http_path: str = "",
    ) -> tuple[list[str], KillChainPhase, float]:
        """
        [ROLE] 프로토콜과 메시지 유형 기반 TTP 매핑.

        [DATA FLOW]
            (protocol, msg_type) ──▶ 프로토콜별 매핑 테이블 조회
            ──▶ (ttp_ids, kill_chain_phase, confidence)
        """
        if protocol == DroneProtocol.MAVLINK:
            return _MAVLINK_TTP_MAP.get(msg_type, _DEFAULT_TTP)

        if protocol == DroneProtocol.HTTP:
            key = f"{http_method.upper()}:{http_path[:5].lower()}"
            return _HTTP_TTP_MAP.get(key, _DEFAULT_TTP)

        if protocol == DroneProtocol.RTSP:
            return _RTSP_TTP_MAP.get(msg_type.upper(), _DEFAULT_TTP)

        if protocol == DroneProtocol.WEBSOCKET:
            return _WS_TTP_MAP.get(msg_type, _WS_TTP_MAP.get("WS_MESSAGE", _DEFAULT_TTP))

        return _DEFAULT_TTP

    def get_all_ttp_ids(self) -> set[str]:
        """
        [ROLE] 매핑 테이블에 등록된 전체 TTP ID 집합 반환.
               논문 Table V (ATT&CK Coverage) 계산에 사용.

        [DATA FLOW]
            _*_TTP_MAP 전체 ──▶ ttp_ids 합집합 ──▶ set[str]
        """
        all_ids: set[str] = set()
        for mapping in [_MAVLINK_TTP_MAP, _HTTP_TTP_MAP, _RTSP_TTP_MAP, _WS_TTP_MAP]:
            for ttp_ids, _, _ in mapping.values():
                all_ids.update(ttp_ids)
        return all_ids

    def coverage_report(self) -> dict:
        """
        [ROLE] 논문 §6 Table V 데이터 생성.
               프로토콜별 매핑된 TTP 수, 커버리지 요약.

        [DATA FLOW]
            _*_TTP_MAP ──▶ 통계 집계 ──▶ dict (JSON 직렬화 가능)
        """
        def _count(mapping):
            ttps = set()
            for ttp_ids, _, _ in mapping.values():
                ttps.update(ttp_ids)
            return {"message_types": len(mapping), "unique_ttps": len(ttps), "ttp_ids": sorted(ttps)}

        return {
            "mavlink":   _count(_MAVLINK_TTP_MAP),
            "http":      _count(_HTTP_TTP_MAP),
            "rtsp":      _count(_RTSP_TTP_MAP),
            "websocket": _count(_WS_TTP_MAP),
            "total_unique_ttps": len(self.get_all_ttp_ids()),
        }


# 모듈 레벨 싱글톤 (import 비용 최소화)
_mapper_singleton: ATTCKMapper | None = None


def get_mapper() -> ATTCKMapper:
    """
    [ROLE] ATTCKMapper 싱글톤 반환.
           모든 모듈에서 동일 인스턴스 공유.

    [DATA FLOW]
        None ──▶ ATTCKMapper() 생성 (최초 1회) ──▶ 반환
    """
    global _mapper_singleton
    if _mapper_singleton is None:
        _mapper_singleton = ATTCKMapper()
    return _mapper_singleton
