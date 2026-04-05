#!/usr/bin/env python3
"""
constants.py — MIRAGE-UAS 환경변수 로더

Project  : MIRAGE-UAS
Module   : Shared / Constants
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.2.0

[Inputs]
    - config/.env  (python-dotenv 로드)

[Outputs]
    - 타입 변환된 상수 (float, int, list[float])
    - 모든 레이어에서 import

[설계 원칙]
    ① 연구 파라미터(수식 변수): 기본값 절대 없음 → ConfigError 발생
       RL 옵티마이저가 .env를 직접 수정하여 최적값 탐색
    ② 인프라 파라미터(포트, 이미지명): 운영 기본값 허용
    ③ 가중치 합 검증 (sum=1.0 필수인 파라미터)
    ④ 값 계산 없음 — 이 파일은 로드/검증만 담당

[REF] MIRAGE-UAS §4 / .env.example 파라미터 명세
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ── .env 로드 ──────────────────────────────────────────────────────────────────
# 프로젝트 루트의 config/.env 로드 (없으면 시스템 환경변수 사용)
_ENV_PATH = Path(__file__).parent.parent.parent / "config" / ".env"
load_dotenv(_ENV_PATH)


# ── 예외 정의 ──────────────────────────────────────────────────────────────────
class ConfigError(Exception):
    """환경변수 로드/검증 실패 시 발생."""


# ── 내부 로더 헬퍼 ─────────────────────────────────────────────────────────────

def _require_float(key: str) -> float:
    """
    [ROLE] 연구 파라미터용 필수 float 로더.
           값이 없거나 비어 있으면 ConfigError 발생 (기본값 절대 없음).

    [DATA FLOW]
        os.environ[key] ──▶ float 변환 ──▶ 반환
        없음 또는 빈 값 ──▶ ConfigError
    """
    raw = os.environ.get(key, "").strip()
    if not raw:
        raise ConfigError(
            f"[MIRAGE-UAS] 필수 연구 파라미터 '{key}'가 .env에 없습니다. "
            f"RL 옵티마이저 또는 수동으로 값을 입력하세요."
        )
    try:
        return float(raw)
    except ValueError as e:
        raise ConfigError(f"'{key}' float 변환 실패: '{raw}'") from e


def _require_float_list(key: str, expected_len: int | None = None) -> list[float]:
    """
    [ROLE] 콤마 구분 float 리스트 필수 로더 (가중치 배열 등).

    [DATA FLOW]
        os.environ[key] ──▶ split(',') ──▶ float 변환 ──▶ 길이 검증 ──▶ 반환
    """
    raw = os.environ.get(key, "").strip()
    if not raw:
        raise ConfigError(
            f"[MIRAGE-UAS] 필수 연구 파라미터 '{key}'가 .env에 없습니다."
        )
    try:
        values = [float(v.strip()) for v in raw.split(",") if v.strip()]
    except ValueError as e:
        raise ConfigError(f"'{key}' float list 변환 실패: '{raw}'") from e
    if expected_len is not None and len(values) != expected_len:
        raise ConfigError(
            f"'{key}' 길이 불일치: expected={expected_len}, got={len(values)}"
        )
    return values


def _require_weights(key: str, expected_len: int | None = None) -> list[float]:
    """
    [ROLE] 가중치 리스트 로더 + 합=1.0 검증.

    [DATA FLOW]
        _require_float_list(key) ──▶ sum 검증(≈1.0) ──▶ 반환
    """
    weights = _require_float_list(key, expected_len)
    total = sum(weights)
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(
            f"'{key}' 가중치 합이 1.0이 아닙니다: sum={total:.6f}, values={weights}"
        )
    return weights


def _get_int(key: str, default: int) -> int:
    """[ROLE] 인프라 파라미터용 int 로더 (기본값 허용)."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"'{key}' int 변환 실패: '{raw}'") from e


def _get_str(key: str, default: str) -> str:
    """[ROLE] 인프라 파라미터용 str 로더 (기본값 허용)."""
    return os.environ.get(key, default).strip() or default


def _get_bool(key: str, default: bool) -> bool:
    """[ROLE] 인프라 파라미터용 bool 로더."""
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# ════════════════════════════════════════════════════════════════════════════════
# [INFRA] 인프라 고정값 — 운영 기본값 허용, RL 탐색 제외
# ════════════════════════════════════════════════════════════════════════════════

MAVLINK_PORT_BASE   : int = _get_int("MAVLINK_PORT_BASE",  14550)
SITL_PORT_BASE      : int = _get_int("SITL_PORT_BASE",     5760)
WEBCLAW_PORT_BASE   : int = _get_int("WEBCLAW_PORT_BASE",  18789)
HTTP_PORT_BASE      : int = _get_int("HTTP_PORT_BASE",     8080)
RTSP_PORT_BASE      : int = _get_int("RTSP_PORT_BASE",     8553)
HONEY_DRONE_COUNT   : int = _get_int("HONEY_DRONE_COUNT",  3)
CTI_API_PORT        : int = _get_int("CTI_API_PORT",       8765)
CTI_API_HOST        : str = _get_str("CTI_API_HOST",       "127.0.0.1")
DOCKER_NETWORK_NAME : str = _get_str("DOCKER_NETWORK_NAME","honey_isolated")
DOCKER_IMAGE_DVD_CC : str = _get_str(
    "DOCKER_IMAGE_DVD_CC",
    "nicholasaleks/dvd-companion-computer:latest"
)
DOCKER_IMAGE_DVD_FCU: str = _get_str(
    "DOCKER_IMAGE_DVD_FCU",
    "nicholasaleks/dvd-flight-controller:latest"
)
LOG_LEVEL           : str = _get_str("LOG_LEVEL",  "INFO")
LOG_FORMAT          : str = _get_str("LOG_FORMAT", "json")
RESULTS_DIR         : str = _get_str("RESULTS_DIR", "results")

# ── 세션 관리 인프라 ───────────────────────────────────────────────────────────
SESSION_CLEANUP_INTERVAL_SEC : int   = _get_int("SESSION_CLEANUP_INTERVAL_SEC", 30)
SESSION_TIMEOUT_SEC          : float = float(_get_int("SESSION_TIMEOUT_SEC",    60))


# ════════════════════════════════════════════════════════════════════════════════
# [Eq.17] MTD Cost Function: C_mtd(a) = κ_ℓ · Σᵢ αᵢ · cost_i(a)
# ════════════════════════════════════════════════════════════════════════════════

# κ_ℓ : 비용 민감도 계수 (grid search 대상)
MTD_COST_SENSITIVITY_KAPPA: float = _require_float("MTD_COST_SENSITIVITY_KAPPA")

# αᵢ : 7D 액션 가중치
# 순서: [freq_hop, ip_shuffle, port_rotate, proto_change,
#        route_morph, key_rotate, service_migrate]
MTD_ALPHA_WEIGHTS: list[float] = _require_weights("MTD_ALPHA_WEIGHTS", expected_len=7)

# β : 침해방지 보상 가중치
MTD_BREACH_PREVENTION_BETA: float = _require_float("MTD_BREACH_PREVENTION_BETA")


# ════════════════════════════════════════════════════════════════════════════════
# [Eq.18] Compromise Probability: P_comp = p_base · Π_i(1-e_i)^(1/n)
# ════════════════════════════════════════════════════════════════════════════════

# p_base : 기저 침해 확률 ∈ (0, 1)
COMPROMISE_P_BASE: float = _require_float("COMPROMISE_P_BASE")

# p_base 범위 검증
if not (0.0 < COMPROMISE_P_BASE < 1.0):
    raise ConfigError(
        f"COMPROMISE_P_BASE={COMPROMISE_P_BASE}는 (0,1) 범위를 벗어납니다."
    )


# ════════════════════════════════════════════════════════════════════════════════
# [Eq.19] DES: Σⱼ wⱼ · δⱼ(s)
# ════════════════════════════════════════════════════════════════════════════════

# wⱼ : 4개 지표 가중치
# 순서: [breach_rate, mttc_ratio, cost_ratio, deception_engagement]
DES_WEIGHT_LIST: list[float] = _require_weights("DES_WEIGHT_LIST", expected_len=4)


# ════════════════════════════════════════════════════════════════════════════════
# [Eq.20] Redundancy: r_high·I(layers≥θ) + r_low·I(layers<θ)
# ════════════════════════════════════════════════════════════════════════════════

REDUNDANCY_REWARD_HIGH : float = _require_float("REDUNDANCY_REWARD_HIGH")
REDUNDANCY_REWARD_LOW  : float = _require_float("REDUNDANCY_REWARD_LOW")
REDUNDANCY_THRESHOLD   : float = _require_float("REDUNDANCY_THRESHOLD")


# ════════════════════════════════════════════════════════════════════════════════
# [NEW] Deception Reward: r_dec = w_dwell·min(t/T_max,1) + w_cmd·log(1+N) + w_prot·I(safe)
# r_total = r_mtd + λ_d · r_dec
# ════════════════════════════════════════════════════════════════════════════════

# λ_d : deception 보상 전체 스케일
DECEPTION_LAMBDA   : float = _require_float("DECEPTION_LAMBDA")

# w_dwell, w_cmd, w_prot: 합=1.0 검증
_dec_weights = _require_weights("DECEPTION_WEIGHTS", expected_len=3)
DECEPTION_W_DWELL   : float = _dec_weights[0]   # 체류 시간 가중치
DECEPTION_W_CMD     : float = _dec_weights[1]   # 명령 수 가중치
DECEPTION_W_PROTECT : float = _dec_weights[2]   # 실드론 보호 가중치

# T_max : dwell time 정규화 상한 (초)
DECEPTION_DWELL_MAX_SEC: float = _require_float("DECEPTION_DWELL_MAX_SEC")

# ── MTDTrigger urgency 임계값 (인프라 — 기본값 허용) ──────────────────────────
ENGAGEMENT_URGENCY_L1_THRESHOLD : float = float(_get_int("ENGAGEMENT_URGENCY_L1_THRESHOLD", 10))
ENGAGEMENT_URGENCY_L2_THRESHOLD : float = float(_get_int("ENGAGEMENT_URGENCY_L2_THRESHOLD", 30))
ENGAGEMENT_URGENCY_L3_THRESHOLD : float = float(_get_int("ENGAGEMENT_URGENCY_L3_THRESHOLD", 60))
ENGAGEMENT_EXPLOIT_THRESHOLD    : int   = _get_int("ENGAGEMENT_EXPLOIT_THRESHOLD", 1)


# ════════════════════════════════════════════════════════════════════════════════
# [NEW] Attacker Level Prior: P(level) (L0–L4, 합=1.0)
# ════════════════════════════════════════════════════════════════════════════════

_attacker_priors = _require_weights("ATTACKER_PRIORS", expected_len=5)
ATTACKER_PRIOR_L0 : float = _attacker_priors[0]
ATTACKER_PRIOR_L1 : float = _attacker_priors[1]
ATTACKER_PRIOR_L2 : float = _attacker_priors[2]
ATTACKER_PRIOR_L3 : float = _attacker_priors[3]
ATTACKER_PRIOR_L4 : float = _attacker_priors[4]


# ════════════════════════════════════════════════════════════════════════════════
# [RL] PPO Hyperparameters (Phase 미포함 — TIFS 코드베이스 전용)
# ════════════════════════════════════════════════════════════════════════════════

PPO_LEARNING_RATE : float = _require_float("PPO_LEARNING_RATE")
PPO_GAMMA         : float = _require_float("PPO_GAMMA")
PPO_CLIP_EPS      : float = _require_float("PPO_CLIP_EPS")
PPO_ENTROPY_COEF  : float = _require_float("PPO_ENTROPY_COEF")
PPO_N_STEPS       : int   = _get_int("PPO_N_STEPS",    2048)
PPO_BATCH_SIZE    : int   = _get_int("PPO_BATCH_SIZE", 64)
CURRICULUM_PHASE  : int   = _get_int("CURRICULUM_PHASE", 1)


# ════════════════════════════════════════════════════════════════════════════════
# [NEW] OpenClaw Agent — Autonomous Deception Behavior Parameters
# REF: MIRAGE-UAS §4.3 — OpenClaw-inspired agentic deception
# ════════════════════════════════════════════════════════════════════════════════

# 자율 proactive 행동 실행 주기 (초)
AGENT_PROACTIVE_INTERVAL_SEC  : float = _require_float("AGENT_PROACTIVE_INTERVAL_SEC")

# MAVLink sysid 자율 변경 주기 (초)
AGENT_SYSID_ROTATION_SEC      : float = _require_float("AGENT_SYSID_ROTATION_SEC")

# OpenClaw WebSocket 포트 자율 변경 주기 (초)
AGENT_PORT_ROTATION_SEC       : float = _require_float("AGENT_PORT_ROTATION_SEC")

# false flag 트리거 체류 시간 임계값 (초)
AGENT_FALSE_FLAG_DWELL_THRESHOLD : float = _require_float("AGENT_FALSE_FLAG_DWELL_THRESHOLD")

# service mirror 트리거 서비스 접촉 수 임계값
AGENT_MIRROR_SERVICE_THRESHOLD : int = int(_require_float("AGENT_MIRROR_SERVICE_THRESHOLD"))


# ════════════════════════════════════════════════════════════════════════════════
# [NEW] DeceptionScore Weights
# REF: MIRAGE-UAS §7.1 — Composite Deception Effectiveness Score
# ════════════════════════════════════════════════════════════════════════════════

DECEPTION_SCORE_WEIGHTS: list[float] = _require_weights(
    "DECEPTION_SCORE_WEIGHTS", expected_len=5
)
