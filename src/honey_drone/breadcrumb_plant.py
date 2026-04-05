#!/usr/bin/env python3
"""
breadcrumb_plant.py — Fake Credential / Path 심기 (Breadcrumb Planting)

Project  : MIRAGE-UAS
Module   : Honey Drone / Breadcrumb Plant
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - AttackerLevel      (공격자 수준 → breadcrumb 복잡도 결정)
    - HoneyDroneConfig   (드론 식별 및 포트 정보)
    - GhostService list  (활성 ghost 서비스 목록 → 경로 유인 대상)

[Outputs]
    - Breadcrumb         (심어진 breadcrumb 레코드)
    - dict[str, Any]     (프로토콜별 응답에 삽입할 breadcrumb 데이터)

[Dependencies]
    - secrets  (stdlib, 랜덤 크레덴셜 생성)
    - hashlib  (stdlib, 결정론적 토큰 생성)

[설계 원칙]
    ① Breadcrumb은 공격자가 "발견"하도록 설계된 가짜 정보
    ② 각 breadcrumb은 고유 ID로 추적 → 공격자가 사용하면 즉시 탐지
    ③ 레벨별 복잡도 조절: L0에는 단순 비밀번호, L4에는 SSH 키 + API 토큰 + 내부 경로
    ④ breadcrumb 사용 = 공격자 의도 확인 = MTD urgency 즉시 상승
    ⑤ 모든 breadcrumb은 일정 주기로 갱신 (stale detection 방지)

[DATA FLOW]
    DeceptionOrchestrator
    ──▶ BreadcrumbPlanter.generate_breadcrumbs(level)
    ──▶ Breadcrumb list
    ──▶ FakeServiceFactory 응답에 삽입
    ──▶ 공격자 접촉 시 DeceptionStateManager.record_breadcrumb_used()
"""

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from shared.logger import get_logger
from shared.models import AttackerLevel, HoneyDroneConfig

logger = get_logger(__name__)


# ── Breadcrumb 유형 ───────────────────────────────────────────────────────────
class BreadcrumbType(str, Enum):
    CREDENTIAL     = "credential"       # 사용자명/비밀번호 쌍
    API_TOKEN      = "api_token"        # API 인증 토큰
    SSH_KEY        = "ssh_key"          # 가짜 SSH 개인키 경로
    INTERNAL_PATH  = "internal_path"    # 내부 서비스 URL/경로
    CONFIG_FILE    = "config_file"      # 설정 파일 경로/내용
    MAVLINK_KEY    = "mavlink_key"      # MAVLink 서명 키
    DATABASE_DSN   = "database_dsn"     # 가짜 DB 연결 문자열


# ── Breadcrumb 레코드 ────────────────────────────────────────────────────────
@dataclass
class Breadcrumb:
    """
    [ROLE] 심어진 breadcrumb 1개의 불변 레코드.
           공격자의 접촉 여부를 추적하기 위한 식별 정보 포함.
    """
    breadcrumb_id   : str            = field(default_factory=lambda: str(uuid.uuid4()))
    drone_id        : str            = ""
    crumb_type      : BreadcrumbType = BreadcrumbType.CREDENTIAL
    # 공격자에게 노출되는 데이터
    key             : str            = ""    # 식별 키 (username, path 등)
    value           : str            = ""    # 식별 값 (password, token 등)
    context         : str            = ""    # 삽입 문맥 (HTTP 응답 경로, 설정 파일명 등)
    # 추적 메타데이터
    planted_at_ns   : int            = field(default_factory=lambda: time.time_ns())
    expires_at_ns   : Optional[int]  = None  # None = 만료 없음
    was_accessed    : bool           = False  # 공격자가 접촉했는지
    accessed_at_ns  : Optional[int]  = None
    accessed_by_ip  : str            = ""
    # 대상 레벨
    target_level    : AttackerLevel  = AttackerLevel.L0_SCRIPT_KIDDIE

    def __repr__(self) -> str:
        status = "USED" if self.was_accessed else "planted"
        return (
            f"Breadcrumb(id={self.breadcrumb_id[:8]}, "
            f"type={self.crumb_type.value}, key={self.key}, "
            f"status={status}, target=L{self.target_level})"
        )


# ── BreadcrumbPlanter ─────────────────────────────────────────────────────────
class BreadcrumbPlanter:
    """
    [ROLE] 공격자 레벨에 맞춤화된 breadcrumb을 생성하고 관리.
           FakeServiceFactory 응답에 삽입할 데이터를 제공.

    [DATA FLOW]
        DeceptionOrchestrator
        ──▶ generate_breadcrumbs(level) ──▶ list[Breadcrumb]
        ──▶ get_http_breadcrumbs()      ──▶ dict (HTTP 응답 삽입용)
        ──▶ get_ssh_breadcrumbs()       ──▶ dict (SSH 배너 삽입용)
        ──▶ mark_used(breadcrumb_id)    ──▶ Breadcrumb (추적 갱신)
    """

    def __init__(self, config: HoneyDroneConfig) -> None:
        self._config = config
        self._breadcrumbs: dict[str, Breadcrumb] = {}   # breadcrumb_id → Breadcrumb
        # 값 역인덱스: 특정 token/password가 사용되면 어떤 breadcrumb인지 빠르게 조회
        self._value_index: dict[str, str] = {}           # value → breadcrumb_id
        self._key_index: dict[str, str] = {}             # key → breadcrumb_id

    @property
    def all_breadcrumbs(self) -> list[Breadcrumb]:
        return list(self._breadcrumbs.values())

    @property
    def used_breadcrumbs(self) -> list[Breadcrumb]:
        return [b for b in self._breadcrumbs.values() if b.was_accessed]

    def generate_breadcrumbs(
        self,
        attacker_level: AttackerLevel,
        ghost_ports: Optional[list[int]] = None,
    ) -> list[Breadcrumb]:
        """
        [ROLE] 공격자 레벨에 맞는 breadcrumb 세트 생성.
               레벨이 높을수록 더 정교하고 유혹적인 breadcrumb 배치.

        [DATA FLOW]
            attacker_level ──▶ 레벨별 생성기 호출 ──▶ list[Breadcrumb]
            ──▶ _breadcrumbs dict에 등록 ──▶ 인덱스 갱신
        """
        crumbs: list[Breadcrumb] = []

        # L0+: 기본 크레덴셜
        crumbs.extend(self._generate_credentials(attacker_level))

        # L1+: API 토큰
        if attacker_level >= AttackerLevel.L1_BASIC:
            crumbs.extend(self._generate_api_tokens(attacker_level))

        # L2+: 내부 경로 + 설정 파일
        if attacker_level >= AttackerLevel.L2_INTERMEDIATE:
            crumbs.extend(self._generate_internal_paths(attacker_level, ghost_ports))
            crumbs.extend(self._generate_config_files(attacker_level))

        # L3+: SSH 키 + MAVLink 서명 키
        if attacker_level >= AttackerLevel.L3_ADVANCED:
            crumbs.extend(self._generate_ssh_keys(attacker_level))
            crumbs.extend(self._generate_mavlink_keys(attacker_level))

        # L4: DB DSN
        if attacker_level >= AttackerLevel.L4_APT:
            crumbs.extend(self._generate_database_dsn(attacker_level))

        # 등록
        for crumb in crumbs:
            self._breadcrumbs[crumb.breadcrumb_id] = crumb
            if crumb.value:
                self._value_index[crumb.value] = crumb.breadcrumb_id
            if crumb.key:
                self._key_index[crumb.key] = crumb.breadcrumb_id

        logger.info(
            "breadcrumbs generated",
            drone_id=self._config.drone_id,
            level=attacker_level.name,
            count=len(crumbs),
            types=[c.crumb_type.value for c in crumbs],
        )
        return crumbs

    def check_value_used(self, value: str) -> Optional[Breadcrumb]:
        """
        [ROLE] 공격자가 사용한 값이 breadcrumb인지 확인.
               API 토큰, 비밀번호 등이 ghost 서비스에서 사용되면 호출.

        [DATA FLOW]
            value ──▶ 역인덱스 조회 ──▶ Breadcrumb | None
        """
        bid = self._value_index.get(value)
        return self._breadcrumbs.get(bid) if bid else None

    def check_key_used(self, key: str) -> Optional[Breadcrumb]:
        """
        [ROLE] 공격자가 사용한 키(username, path)가 breadcrumb인지 확인.

        [DATA FLOW]
            key ──▶ 역인덱스 조회 ──▶ Breadcrumb | None
        """
        bid = self._key_index.get(key)
        return self._breadcrumbs.get(bid) if bid else None

    def mark_used(
        self, breadcrumb_id: str, attacker_ip: str,
    ) -> Optional[Breadcrumb]:
        """
        [ROLE] breadcrumb이 공격자에 의해 사용됨을 기록.

        [DATA FLOW]
            breadcrumb_id, attacker_ip
            ──▶ Breadcrumb.was_accessed = True
            ──▶ 로그 출력 (보안 이벤트)
        """
        crumb = self._breadcrumbs.get(breadcrumb_id)
        if not crumb:
            return None

        crumb.was_accessed   = True
        crumb.accessed_at_ns = time.time_ns()
        crumb.accessed_by_ip = attacker_ip

        logger.warning(
            "breadcrumb used by attacker",
            breadcrumb_id=breadcrumb_id[:8],
            crumb_type=crumb.crumb_type.value,
            key=crumb.key,
            attacker_ip=attacker_ip,
            drone_id=crumb.drone_id,
        )
        return crumb

    def get_http_injection_data(self) -> dict[str, Any]:
        """
        [ROLE] HTTP 응답에 삽입할 breadcrumb 데이터 반환.
               FakeServiceFactory._generate_http_body()에서 호출.

        [DATA FLOW]
            활성 breadcrumbs ──▶ HTTP 삽입용 dict
        """
        data: dict[str, Any] = {}

        for crumb in self._breadcrumbs.values():
            if crumb.was_accessed:
                continue    # 이미 사용된 것은 제거

            if crumb.crumb_type == BreadcrumbType.CREDENTIAL:
                data.setdefault("_debug_users", []).append({
                    "user": crumb.key,
                    "pass": crumb.value,
                    "role": "operator",
                })
            elif crumb.crumb_type == BreadcrumbType.API_TOKEN:
                data["_api_key"] = crumb.value
            elif crumb.crumb_type == BreadcrumbType.INTERNAL_PATH:
                data.setdefault("_internal_endpoints", []).append(crumb.value)
            elif crumb.crumb_type == BreadcrumbType.CONFIG_FILE:
                data["_config_path"] = crumb.value

        return data

    def get_ssh_injection_data(self) -> dict[str, Any]:
        """
        [ROLE] SSH 배너/MOTD에 삽입할 breadcrumb 데이터 반환.

        [DATA FLOW]
            SSH 관련 breadcrumbs ──▶ SSH 삽입용 dict
        """
        data: dict[str, Any] = {}
        for crumb in self._breadcrumbs.values():
            if crumb.was_accessed:
                continue
            if crumb.crumb_type == BreadcrumbType.SSH_KEY:
                data["key_path"] = crumb.value
            elif crumb.crumb_type == BreadcrumbType.CREDENTIAL:
                data.setdefault("accounts", []).append({
                    "user": crumb.key,
                    "hint": crumb.context,
                })
        return data

    def clear_expired(self) -> int:
        """
        [ROLE] 만료된 breadcrumb 제거.

        [DATA FLOW]
            현재 시각 ──▶ 만료 검사 ──▶ 삭제 + 인덱스 갱신
        """
        now_ns = time.time_ns()
        expired_ids = [
            bid for bid, crumb in self._breadcrumbs.items()
            if crumb.expires_at_ns is not None and crumb.expires_at_ns < now_ns
        ]
        for bid in expired_ids:
            crumb = self._breadcrumbs.pop(bid)
            self._value_index.pop(crumb.value, None)
            self._key_index.pop(crumb.key, None)
        return len(expired_ids)

    def clear_all(self) -> None:
        """[ROLE] 모든 breadcrumb 제거 (MTD 로테이션 시 호출)."""
        self._breadcrumbs.clear()
        self._value_index.clear()
        self._key_index.clear()

    # ── 레벨별 생성기 ─────────────────────────────────────────────────────────

    def _generate_credentials(self, level: AttackerLevel) -> list[Breadcrumb]:
        """[ROLE] 가짜 사용자 크레덴셜 생성."""
        drone_id = self._config.drone_id
        pairs = [
            ("admin",    self._make_token(drone_id, "admin",    8)),
            ("operator", self._make_token(drone_id, "operator", 12)),
        ]
        if level >= AttackerLevel.L2_INTERMEDIATE:
            pairs.append(
                ("root", self._make_token(drone_id, "root", 16))
            )

        return [
            Breadcrumb(
                drone_id=drone_id,
                crumb_type=BreadcrumbType.CREDENTIAL,
                key=user,
                value=passwd,
                context=f"HTTP debug endpoint /{drone_id}/debug",
                target_level=level,
            )
            for user, passwd in pairs
        ]

    def _generate_api_tokens(self, level: AttackerLevel) -> list[Breadcrumb]:
        """[ROLE] 가짜 API 토큰 생성."""
        token = f"mav_{self._make_token(self._config.drone_id, 'api', 32)}"
        return [
            Breadcrumb(
                drone_id=self._config.drone_id,
                crumb_type=BreadcrumbType.API_TOKEN,
                key="X-MAV-Token",
                value=token,
                context="HTTP response header",
                target_level=level,
            ),
        ]

    def _generate_internal_paths(
        self, level: AttackerLevel, ghost_ports: Optional[list[int]] = None,
    ) -> list[Breadcrumb]:
        """[ROLE] 내부 서비스 경로 breadcrumb 생성 (ghost 서비스로 유도)."""
        paths: list[Breadcrumb] = []
        drone_id = self._config.drone_id

        # 기본 내부 경로
        paths.append(Breadcrumb(
            drone_id=drone_id,
            crumb_type=BreadcrumbType.INTERNAL_PATH,
            key="gcs_endpoint",
            value=f"http://10.0.0.{self._config.index + 10}:8080/api/v2/gcs",
            context="config response",
            target_level=level,
        ))

        # ghost 서비스 포트로 유도
        if ghost_ports:
            for i, port in enumerate(ghost_ports[:3]):
                paths.append(Breadcrumb(
                    drone_id=drone_id,
                    crumb_type=BreadcrumbType.INTERNAL_PATH,
                    key=f"service_{i}",
                    value=f"http://10.0.0.{self._config.index + 10}:{port}/",
                    context="internal service discovery",
                    target_level=level,
                ))

        return paths

    def _generate_config_files(self, level: AttackerLevel) -> list[Breadcrumb]:
        """[ROLE] 가짜 설정 파일 경로 생성."""
        return [
            Breadcrumb(
                drone_id=self._config.drone_id,
                crumb_type=BreadcrumbType.CONFIG_FILE,
                key="ardupilot_config",
                value="/opt/ardupilot/config/default.parm",
                context="system info response",
                target_level=level,
            ),
            Breadcrumb(
                drone_id=self._config.drone_id,
                crumb_type=BreadcrumbType.CONFIG_FILE,
                key="mavlink_router_conf",
                value="/etc/mavlink-router/main.conf",
                context="router debug output",
                target_level=level,
            ),
        ]

    def _generate_ssh_keys(self, level: AttackerLevel) -> list[Breadcrumb]:
        """[ROLE] 가짜 SSH 키 경로 생성."""
        return [
            Breadcrumb(
                drone_id=self._config.drone_id,
                crumb_type=BreadcrumbType.SSH_KEY,
                key="deploy_key",
                value="/home/drone/.ssh/id_ed25519_deploy",
                context="SSH MOTD banner",
                target_level=level,
            ),
        ]

    def _generate_mavlink_keys(self, level: AttackerLevel) -> list[Breadcrumb]:
        """[ROLE] 가짜 MAVLink 서명 키 생성."""
        fake_key = self._make_token(self._config.drone_id, "mavkey", 32)
        return [
            Breadcrumb(
                drone_id=self._config.drone_id,
                crumb_type=BreadcrumbType.MAVLINK_KEY,
                key="mavlink_signing_key",
                value=fake_key,
                context="MAVLink param response SIGNING_KEY",
                target_level=level,
            ),
        ]

    def _generate_database_dsn(self, level: AttackerLevel) -> list[Breadcrumb]:
        """[ROLE] 가짜 DB 연결 문자열 생성 (L4 APT 유인용)."""
        fake_pass = self._make_token(self._config.drone_id, "dbpass", 16)
        return [
            Breadcrumb(
                drone_id=self._config.drone_id,
                crumb_type=BreadcrumbType.DATABASE_DSN,
                key="telemetry_db",
                value=f"postgresql://drone_svc:{fake_pass}@10.0.0.5:5432/telemetry",
                context="env variable leak in error response",
                target_level=level,
            ),
        ]

    # ── 유틸리티 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _make_token(drone_id: str, salt: str, length: int) -> str:
        """
        [ROLE] 결정론적이면서 현실적으로 보이는 가짜 토큰 생성.
               동일 (drone_id, salt) 조합에서 항상 같은 값 생성 (재현성).

        [DATA FLOW]
            drone_id + salt ──▶ SHA-256 ──▶ hex[:length]
        """
        h = hashlib.sha256(f"{drone_id}:{salt}:{secrets.token_hex(4)}".encode())
        return h.hexdigest()[:length]
