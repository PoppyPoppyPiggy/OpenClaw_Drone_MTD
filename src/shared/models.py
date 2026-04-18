#!/usr/bin/env python3
"""
models.py — MIRAGE-UAS 공유 데이터 모델

Project  : MIRAGE-UAS (Moving-target Intelligent Responsive Agentic
           deception enGinE for UAS)
Module   : Shared / Data Models
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.2.0

[Inputs]
    - 없음 (순수 데이터 정의 모듈)

[Outputs]
    - 모든 레이어(honey_drone / mtd / cti_pipeline / dataset)에서 import

[Dependencies]
    - dataclasses (stdlib)
    - enum       (stdlib)
    - uuid       (stdlib)

[설계 원칙]
    - 모든 dataclass는 __repr__ 구현 (논문 로깅 재현성)
    - 불변 필드는 field(default_factory=...) 로 안전 처리
    - 논문 수식 참조 주석 포함
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Optional


# ── AttackerLevel ──────────────────────────────────────────────────────────────
# L0–L4 공격자 분류 모델
# REF: MIRAGE-UAS §3 Threat Model / TIFS T-IFS-25285-2026 §III-B
class AttackerLevel(IntEnum):
    L0_SCRIPT_KIDDIE = 0
    # 자동화 스캐너, 무작위 페이로드, 프로토콜 인식 없음
    L1_BASIC         = 1
    # MAVLink 프로토콜 인식, 단순 COMMAND_LONG 시도
    L2_INTERMEDIATE  = 2
    # 취약점 타겟팅, 재연결 시도, 파라미터 덤프
    L3_ADVANCED      = 3
    # 다단계 공격, 측면 이동 시도, WebSocket API 익스플로잇
    L4_APT           = 4
    # 지속 접근, 스텔스 유지, CVE 체인 익스플로잇


# ── DroneProtocol ──────────────────────────────────────────────────────────────
class DroneProtocol(str, Enum):
    MAVLINK   = "mavlink"
    HTTP      = "http"
    RTSP      = "rtsp"
    WEBSOCKET = "websocket"   # OpenClaw-style API port (18789+N)


# ── DroneStatus ────────────────────────────────────────────────────────────────
class DroneStatus(str, Enum):
    IDLE         = "idle"
    ENGAGED      = "engaged"       # 공격자가 이 드론에 접속 중
    UNDER_ATTACK = "under_attack"  # 활성 exploit 감지
    ROTATING     = "rotating"      # MTD 로테이션 진행 중
    TERMINATED   = "terminated"    # 컨테이너 종료됨


# ── KillChainPhase ─────────────────────────────────────────────────────────────
class KillChainPhase(str, Enum):
    RECONNAISSANCE  = "reconnaissance"
    WEAPONIZATION   = "weaponization"
    DELIVERY        = "delivery"
    EXPLOITATION    = "exploitation"
    INSTALLATION    = "installation"
    C2              = "command_and_control"
    ACTION          = "actions_on_objectives"


# ── AttackPhase (OpenClaw Agent 공격 단계 분류) ────────────────────────────────
# REF: MIRAGE-UAS §4.3 — Autonomous Deception Agent attack phase model
class AttackPhase(str, Enum):
    RECON   = "recon"       # 정찰: 포트 스캔, 서비스 탐색
    EXPLOIT = "exploit"     # 익스플로잇: 취약점 시도, 인증 우회
    PERSIST = "persist"     # 지속: 세션 유지, 명령 반복
    EXFIL   = "exfil"       # 유출: 로그/설정/비행이력 탈취 시도


# ── AttackerTool (공격 도구 fingerprint 분류) ──────────────────────────────────
# REF: MIRAGE-UAS §4.3 — Attacker fingerprinting heuristic
class AttackerTool(str, Enum):
    NMAP_SCANNER      = "nmap_scanner"
    MAVPROXY_GCS      = "mavproxy_gcs"
    DRONEKIT_SCRIPT   = "dronekit_script"
    CUSTOM_EXPLOIT    = "custom_exploit"
    METASPLOIT_MODULE = "metasploit_module"
    UNKNOWN           = "unknown"


# ── MavlinkCaptureEvent ────────────────────────────────────────────────────────
@dataclass
class MavlinkCaptureEvent:
    """
    [ROLE] 허니드론으로 유입된 원시 패킷을 캡처한 불변 이벤트 레코드.
           Track A(AgenticDecoyEngine)와 Track B(CTI Pipeline) 모두의 입력 단위.

    [DATA FLOW]
        Network (UDP/TCP/WS) ──▶ MavlinkInterceptor ──▶ MavlinkCaptureEvent
        MavlinkCaptureEvent ──▶ AgenticDecoyEngine (Track A)
        MavlinkCaptureEvent ──▶ AttackEventParser  (Track B)
    """
    # 식별
    event_id     : str          = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ns : int          = field(default_factory=lambda: time.time_ns())
    # 출처
    drone_id     : str          = ""
    src_ip       : str          = ""
    src_port     : int          = 0
    protocol     : DroneProtocol = DroneProtocol.MAVLINK
    # MAVLink 필드 (protocol == MAVLINK 인 경우)
    msg_type     : str          = ""    # e.g. "COMMAND_LONG", "HEARTBEAT"
    msg_id       : int          = -1
    sysid        : int          = 0
    compid       : int          = 0
    payload_hex  : str          = ""    # 원시 페이로드 (hex-encoded, 재현성 보장)
    # HTTP/RTSP 필드 (protocol == HTTP/RTSP 인 경우)
    http_method  : str          = ""    # GET/POST/PUT
    http_path    : str          = ""    # /api/v1/params etc.
    # 분류 보조
    is_anomalous : bool         = False # 파서가 이상 패킷으로 표시
    session_id   : str          = ""    # 동일 공격자 세션 묶음 식별자

    def __repr__(self) -> str:
        return (
            f"MavlinkCaptureEvent(id={self.event_id[:8]}, "
            f"drone={self.drone_id}, src={self.src_ip}:{self.src_port}, "
            f"proto={self.protocol.value}, msg={self.msg_type}, "
            f"ts={self.timestamp_ns})"
        )


# ── EngagementMetrics ──────────────────────────────────────────────────────────
@dataclass
class EngagementMetrics:
    """
    [ROLE] 공격자 1세션의 기만 참여(engagement) 지표 집계.
           Track A → MTD Controller 신호의 핵심 데이터.

    [DATA FLOW]
        EngagementTracker ──▶ EngagementMetrics
        EngagementMetrics ──▶ MTDTrigger (urgency 계산)
        EngagementMetrics ──▶ AgenticDecoyEngine (응답 전략 조정)

    [REF] MIRAGE-UAS Eq.NEW-1 (r_dec deception reward 수식 입력값)
          r_dec = w_dwell·min(dwell/T_max,1) + w_cmd·log(1+N_cmd) + w_prot·I(safe)
    """
    session_id          : str
    drone_id            : str
    attacker_ip         : str
    attacker_level      : AttackerLevel
    session_start_ns    : int
    last_activity_ns    : int
    # r_dec 입력 변수들 (MIRAGE-UAS Eq.NEW-1)
    dwell_time_sec      : float              = 0.0   # t in Eq.NEW-1
    commands_issued     : int                = 0     # N_cmd in Eq.NEW-1
    protocols_used      : list[DroneProtocol] = field(default_factory=list)
    exploit_attempts    : int                = 0     # CVE-2026-25253 시도 수
    websocket_sessions  : int                = 0     # OpenClaw WS 연결 수
    real_drone_breached : bool               = False # I(safe) = not breached

    def __repr__(self) -> str:
        return (
            f"EngagementMetrics(session={self.session_id[:8]}, "
            f"drone={self.drone_id}, attacker={self.attacker_ip}, "
            f"level={self.attacker_level.name}, "
            f"dwell={self.dwell_time_sec:.1f}s, cmds={self.commands_issued}, "
            f"exploits={self.exploit_attempts})"
        )


# ── MTDTrigger ─────────────────────────────────────────────────────────────────
@dataclass
class MTDTrigger:
    """
    [ROLE] AgenticDecoyEngine → MTD Controller 방향 신호.
           engagement 임계값 초과 시 MTD 실행을 요청하는 이벤트.

    [DATA FLOW]
        AgenticDecoyEngine ──▶ asyncio.Queue[MTDTrigger]
        ──▶ MTDController.execute()
        ──▶ MTDExecutor (포트/IP/프로토콜 변경)
    """
    trigger_id           : str   = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ns         : int   = field(default_factory=lambda: time.time_ns())
    source_drone_id      : str   = ""
    attacker_level       : AttackerLevel = AttackerLevel.L0_SCRIPT_KIDDIE
    engagement           : Optional[EngagementMetrics] = None
    # urgency: [0.0, 1.0]. L4 exploit attempt = 1.0, idle scan = 0.1
    urgency              : float = 0.0
    # MTD 컨트롤러가 우선 검토할 액션 힌트
    recommended_actions  : list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"MTDTrigger(id={self.trigger_id[:8]}, "
            f"drone={self.source_drone_id}, "
            f"level={self.attacker_level.name}, urgency={self.urgency:.2f}, "
            f"actions={self.recommended_actions})"
        )


# ── ParsedAttackEvent ──────────────────────────────────────────────────────────
@dataclass
class ParsedAttackEvent:
    """
    [ROLE] MavlinkCaptureEvent를 ATT&CK TTP + L0-L4 분류 후 구조화한 레코드.
           STIX 2.1 변환의 직접 입력 단위.

    [DATA FLOW]
        MavlinkCaptureEvent ──▶ AttackEventParser ──▶ ParsedAttackEvent
        ParsedAttackEvent ──▶ STIXConverter ──▶ stix2.Bundle
        ParsedAttackEvent ──▶ DatasetBuilder  (Track B)
    """
    raw_event        : MavlinkCaptureEvent
    attacker_level   : AttackerLevel
    ttp_ids          : list[str]   # e.g. ["T0800", "T0816", "T0830"]
    kill_chain_phase : KillChainPhase
    confidence       : float        # [0.0, 1.0] 분류 신뢰도
    dwell_time_sec   : float = 0.0
    stix_bundle_id   : str   = ""   # 변환 후 채워짐

    def __repr__(self) -> str:
        return (
            f"ParsedAttackEvent(event={self.raw_event.event_id[:8]}, "
            f"level={self.attacker_level.name}, "
            f"ttps={self.ttp_ids}, conf={self.confidence:.2f})"
        )


# ── HoneyDroneConfig ───────────────────────────────────────────────────────────
@dataclass
class HoneyDroneConfig:
    """
    [ROLE] 허니드론 인스턴스 1개의 설정 명세.
           HoneyDroneManager.spawn()의 입력.

    [DATA FLOW]
        .env ──▶ constants.py ──▶ HoneyDroneConfig ──▶ HoneyDroneManager.spawn()
    """
    drone_id       : str    # "honey_01", "honey_02", ...
    index          : int    # 1, 2, 3 (포트 오프셋 계산용)
    sitl_port      : int    # ArduPilot SITL TCP (5761+)
    mavlink_port   : int    # MAVLink UDP GCS 포트 (14551+)
    webclaw_port   : int    # OpenClaw WebSocket 포트 (18790+)
    http_port      : int    # CC Web UI HTTP (8081+)
    rtsp_port      : int    # RTSP 카메라 스트림 (8554+)
    fcu_host       : str    = "fcu-honey-01"   # ArduPilot SITL host (compose service name)
    docker_image   : str    = "nicholasaleks/dvd-companion-computer:latest"
    fcu_image      : str    = "nicholasaleks/dvd-flight-controller:latest"
    network        : str    = "honey_isolated"
    exposed        : bool   = True    # 공격자에게 의도적 노출
    max_dwell_sec  : float  = 300.0   # 최대 허용 체류 시간 (sec)
    cve_exposed    : bool   = True    # CVE-2026-25253 패치 미적용 (L3-L4 lure)

    def __repr__(self) -> str:
        return (
            f"HoneyDroneConfig(id={self.drone_id}, "
            f"mavlink_port={self.mavlink_port}, "
            f"webclaw_port={self.webclaw_port}, exposed={self.exposed})"
        )


# ── HoneyDroneInstance ─────────────────────────────────────────────────────────
@dataclass
class HoneyDroneInstance:
    """
    [ROLE] 실행 중인 허니드론 컨테이너 스택의 런타임 상태.

    [DATA FLOW]
        HoneyDroneManager.spawn() ──▶ HoneyDroneInstance
        HoneyDroneInstance ──▶ AgenticDecoyEngine (config 참조)
        HoneyDroneInstance ──▶ MTDController (rotate 대상)
    """
    config           : HoneyDroneConfig
    fcu_container_id : str
    cc_container_id  : str
    status           : DroneStatus = DroneStatus.IDLE
    started_at_ns    : int         = field(default_factory=lambda: time.time_ns())
    active_sessions  : int         = 0
    total_events     : int         = 0
    rotation_count   : int         = 0   # MTD로 교체된 횟수

    def __repr__(self) -> str:
        return (
            f"HoneyDroneInstance(id={self.config.drone_id}, "
            f"status={self.status.value}, "
            f"sessions={self.active_sessions}, "
            f"events={self.total_events}, rotations={self.rotation_count})"
        )


# ── DatasetEntry ───────────────────────────────────────────────────────────────
@dataclass
class DatasetEntry:
    """
    [ROLE] DVD-CTI-Dataset-v1 레코드 단위.
           양성(attack=1) / 음성(attack=0) 레이블 쌍 구성.

    [DATA FLOW]
        ParsedAttackEvent ──▶ DatasetBuilder ──▶ DatasetEntry (label=1)
        NegativeSampleGenerator   ──▶ DatasetEntry (label=0)
        DatasetEntry list ──▶ DatasetPackager ──▶ CSV + STIX bundles
    """
    entry_id        : str   = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ns    : int   = field(default_factory=lambda: time.time_ns())
    drone_id        : str   = ""
    src_ip          : str   = ""
    protocol        : DroneProtocol = DroneProtocol.MAVLINK
    msg_type        : str   = ""
    payload_hex     : str   = ""
    attacker_level  : Optional[AttackerLevel] = None  # benign = None
    ttp_ids         : list[str] = field(default_factory=list)
    stix_bundle_id  : str   = ""
    label           : int   = 0      # 0=benign, 1=attack
    confidence      : float = 1.0    # 레이블 신뢰도
    source          : str   = ""     # "honeydrone" | "baseline_sitl" | "synthetic"

    def __repr__(self) -> str:
        label_str = "attack" if self.label == 1 else "benign"
        return (
            f"DatasetEntry(id={self.entry_id[:8]}, "
            f"proto={self.protocol.value}, msg={self.msg_type}, "
            f"label={label_str}, conf={self.confidence:.2f})"
        )


# ── AttackerFingerprint (OpenClaw Agent 공격자 지문) ───────────────────────────
@dataclass
class AttackerFingerprint:
    """
    [ROLE] 공격자 행동 패턴 기반 도구 식별 지문.
           OpenClawAgent가 공격 도구별 적응 응답을 생성하는 근거.

    [DATA FLOW]
        OpenClawAgent._fingerprint_attacker()
        ──▶ AttackerFingerprint (per-attacker)
        ──▶ generate_response() 응답 전략 분기
    """
    attacker_ip            : str
    tool                   : AttackerTool        = AttackerTool.UNKNOWN
    attack_phase           : AttackPhase         = AttackPhase.RECON
    command_sequence       : list[str]           = field(default_factory=list)
    first_seen_ns          : int                 = field(default_factory=lambda: time.time_ns())
    phase_changed_at_ns    : int                 = field(default_factory=lambda: time.time_ns())
    unique_services_touched: int                 = 0

    def __repr__(self) -> str:
        return (
            f"AttackerFingerprint(ip={self.attacker_ip}, "
            f"tool={self.tool.value}, phase={self.attack_phase.value}, "
            f"cmds={len(self.command_sequence)}, "
            f"services={self.unique_services_touched})"
        )


# ── AgentDecision (OpenClaw Agent 자율 결정 기록) ──────────────────────────────
@dataclass
class AgentDecision:
    """
    [ROLE] OpenClawAgent의 자율 행동 결정 감사(audit) 레코드.
           논문 Table VI 및 DeceptionScore 산출의 입력.

    [DATA FLOW]
        OpenClawAgent._execute_behavior()
        ──▶ AgentDecision (로그 + metrics)
        ──▶ MetricsCollector.collect_agent_decisions()
        ──▶ Table VI JSON
    """
    decision_id        : str  = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ns       : int  = field(default_factory=lambda: time.time_ns())
    drone_id           : str  = ""
    behavior_triggered : str  = ""     # e.g. "proactive_statustext", "false_flag"
    target_ip          : str  = ""     # 관련 공격자 IP (없으면 빈 문자열)
    rationale          : str  = ""     # 결정 근거 (로깅/재현성)
    executed           : bool = False  # 실제 실행 여부

    def __repr__(self) -> str:
        return (
            f"AgentDecision(id={self.decision_id[:8]}, "
            f"drone={self.drone_id}, behavior={self.behavior_triggered}, "
            f"target={self.target_ip}, executed={self.executed})"
        )
