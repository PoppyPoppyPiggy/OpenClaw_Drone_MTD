"""
MIRAGE-UAS 기만 효과 다차원 측정 프레임워크

기만 "성공"을 단일 임계치(p_real < 0.2)로 판단하는 것은 부적절.
논문들에서 검증된 7개 독립 지표로 다차원 평가.

[기만 성공의 정의 — 다차원]
  기만은 단일 이벤트가 아니라 연속적 과정이며,
  여러 독립 차원에서 동시에 평가되어야 한다:

  1. 응답 품질 (Accuracy)     — 응답이 진짜 시스템처럼 보이는가?
  2. 유인 효과 (Temptation)   — 공격자가 더 깊이 들어오는가?
  3. 탐지 회피 (Cloaking)     — 허니팟으로 탐지되지 않았는가?
  4. 체류 시간 (Dwell)        — 공격자가 얼마나 오래 머물렀는가?
  5. 상호작용 깊이 (Depth)    — 공격자가 몇 단계까지 진행했는가?
  6. 명령 다양성 (Diversity)  — 공격자가 다양한 행동을 시도했는가?
  7. CTI 수율 (Intel Yield)   — 위협 정보를 얼마나 수집했는가?

References:
  Eq.01 Accuracy     -- Wang et al. (2024) HoneyGPT arXiv:2406.01882
  Eq.02 Temptation   -- Wang et al. (2024) HoneyGPT arXiv:2406.01882
  Eq.03 CRR          -- Wang et al. (2024) HoneyGPT arXiv:2406.01882
  Eq.04 MID          -- Wang et al. (2024) HoneyGPT arXiv:2406.01882
  Eq.05 Cloaking     -- Aradi et al. (2025) Acta Polytechnica 22(12):295
  Eq.06 Sojourn      -- Ferguson-Walter et al. (2020) arXiv:2005.12969
  Eq.07 CmdDiversity -- Ferguson-Walter et al. (2020) arXiv:2005.12969
  Eq.08 PhaseDepth   -- MIRAGE-UAS (this work)
  Eq.09 IntelYield   -- arXiv:2512.05321 (Practical Honeypot CTI)
  Eq.10 DES-v3       -- MIRAGE-UAS composite (this work)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class SessionDeceptionMetrics:
    """단일 공격자 세션의 다차원 기만 측정값"""
    # HoneyGPT classification (Eq.01-02)
    salc: int = 0       # Successful Attack, Logic Compliance
    salnlc: int = 0     # Successful Attack, No Logic Compliance
    falc: int = 0       # Failed Attack, Logic Compliance
    falnlc: int = 0     # Failed Attack, No Logic Compliance

    # Engagement (Eq.06-07)
    dwell_sec: float = 0.0          # 세션 체류 시간 (초)
    commands_total: int = 0         # 전체 명령 수
    commands_responded: int = 0     # 응답한 명령 수
    unique_command_types: int = 0   # 고유 명령 유형 수

    # Phase progression (Eq.08)
    max_phase_reached: int = 0      # 0=RECON, 1=EXPLOIT, 2=PERSIST, 3=EXFIL
    phase_transitions: int = 0      # 단계 전환 횟수

    # CTI yield (Eq.09)
    ttps_captured: int = 0          # 수집된 ATT&CK TTP 수
    stix_bundles: int = 0           # 생성된 STIX 번들 수

    # Detection status (Eq.05)
    honeypot_detected: bool = False
    evasion_signals: int = 0        # 허니팟 의심 행동 횟수


def deception_accuracy(sessions: list[SessionDeceptionMetrics]) -> float:
    """
    Eq.01 -- Deception Accuracy
    공격 명령에 대해 논리적으로 올바른 응답을 반환한 비율

    Accuracy = SALC / (SALC + SALNLC)

    Reference: Wang et al. (2024), "HoneyGPT: Breaking the Trilemma
    in Terminal Honeypots with Large Language Model," arXiv:2406.01882v2
    HoneyGPT(GPT-4) 기준 ~96% 달성
    """
    total_salc = sum(s.salc for s in sessions)
    total_salnlc = sum(s.salnlc for s in sessions)
    denom = total_salc + total_salnlc
    return total_salc / denom if denom > 0 else 0.5


def temptation_score(sessions: list[SessionDeceptionMetrics]) -> float:
    """
    Eq.02 -- Temptation Score
    공격자를 허니팟에 더 깊이 유인하는 능력 측정.
    breach_rate와 독립적: 허니팟 품질만 측정.

    Temptation = SALC / (SALC + FALC)

    Reference: Wang et al. (2024), HoneyGPT arXiv:2406.01882v2
    GPT-4 기반 ~83% 달성
    """
    total_salc = sum(s.salc for s in sessions)
    total_falc = sum(s.falc for s in sessions)
    denom = total_salc + total_falc
    return total_salc / denom if denom > 0 else 0.5


def cloaking_success_rate(sessions: list[SessionDeceptionMetrics]) -> float:
    """
    Eq.05 -- Cloaking Success Rate
    공격자가 허니드론을 실제 드론으로 오인하는 비율.
    Fingerprinting resistance와 동의어.

    CloakingRate = |{s : s.honeypot_detected == False}| / |sessions|

    Reference: Aradi et al. (2025), "Metrics-Driven Evaluation and
    Optimization of Honeypots," Acta Polytechnica Hungarica 22(12):295-313
    DOI: 10.12700/APH.22.12.2025.12.19
    """
    if not sessions:
        return 0.5
    undetected = sum(1 for s in sessions if not s.honeypot_detected)
    return undetected / len(sessions)


def command_response_rate(sessions: list[SessionDeceptionMetrics]) -> float:
    """
    Eq.03 -- Command Response Rate (CRR)
    수신한 명령 중 응답한 비율. 높을수록 상호작용 지속.

    CRR = N_responded / N_total

    Reference: Wang et al. (2024), HoneyGPT arXiv:2406.01882v2
    GPT-4 기준: 99.93%
    """
    total = sum(s.commands_total for s in sessions)
    responded = sum(s.commands_responded for s in sessions)
    return responded / total if total > 0 else 0.0


def mean_interaction_degree(sessions: list[SessionDeceptionMetrics]) -> float:
    """
    Eq.04 -- Mean Interaction Degree (MID)
    세션별 응답 성공률의 평균. 세션 간 일관성 측정.

    MID = (1/N) * Σ_i (responded_i / total_i)

    Reference: Wang et al. (2024), HoneyGPT arXiv:2406.01882v2
    GPT-4 기준: 99.91%
    """
    if not sessions:
        return 0.0
    degrees = []
    for s in sessions:
        if s.commands_total > 0:
            degrees.append(s.commands_responded / s.commands_total)
        else:
            degrees.append(0.0)
    return sum(degrees) / len(degrees)


def sojourn_time_ratio(
    sessions: list[SessionDeceptionMetrics],
    t_max: float = 600.0,
) -> float:
    """
    Eq.06 -- Sojourn Time Ratio
    공격자 체류 시간의 정규화 비율.
    높을수록 공격자가 오래 머물렀음 = 기만 지속 성공.

    STR = (1/N) * Σ_i min(dwell_i / T_max, 1.0)

    Reference: Ferguson-Walter et al. (2020), "A Taxonomy for
    Dynamic Honeypot Measures of Effectiveness," arXiv:2005.12969
    """
    if not sessions:
        return 0.0
    ratios = [min(s.dwell_sec / t_max, 1.0) for s in sessions]
    return sum(ratios) / len(ratios)


def command_diversity_index(sessions: list[SessionDeceptionMetrics]) -> float:
    """
    Eq.07 -- Command Diversity Index
    공격자가 사용한 고유 명령 유형의 다양성.
    다양할수록 공격자가 더 깊이 탐색 = 기만 성공적.

    CDI = (1/N) * Σ_i (unique_types_i / max_possible_types)

    max_possible_types는 MAVLink 기준 23 (attck_mapper.py 매핑 수)

    Reference: Ferguson-Walter et al. (2020), arXiv:2005.12969
    """
    max_types = 23.0  # MIRAGE-UAS에서 관측 가능한 최대 명령 유형
    if not sessions:
        return 0.0
    indices = [min(s.unique_command_types / max_types, 1.0) for s in sessions]
    return sum(indices) / len(indices)


def phase_depth_score(sessions: list[SessionDeceptionMetrics]) -> float:
    """
    Eq.08 -- Phase Depth Score
    공격자가 도달한 최대 공격 단계의 정규화 값.
    EXFIL(3)까지 진행 = 완전히 속았음.

    PDS = (1/N) * Σ_i (max_phase_i / 3)

    MIRAGE-UAS 자체 지표 (기존 연구에 없는 UAS 특화 지표).
    근거: 공격 단계가 깊어질수록 공격자가 실제 시스템으로 확신.
      RECON(0): 탐색만 — 아직 판단 안 함
      EXPLOIT(1): 공격 시도 — 진짜로 판단하고 있음
      PERSIST(2): 지속 접근 — 확실히 속음
      EXFIL(3): 데이터 탈취 — 완전히 속음
    """
    if not sessions:
        return 0.0
    depths = [s.max_phase_reached / 3.0 for s in sessions]
    return sum(depths) / len(depths)


def intel_yield_rate(
    sessions: list[SessionDeceptionMetrics],
    t_observation_hours: float = 1.0,
) -> float:
    """
    Eq.09 -- Intelligence Yield Rate
    시간당 수집된 고유 위협 정보(TTP + STIX) 수.
    높을수록 기만이 효과적으로 CTI를 생산.

    IYR = (Σ_i ttps_i + stix_i) / T_hours

    Reference: arXiv:2512.05321, "A Practical Honeypot-Based
    Threat Intelligence Framework," Section 4.2
    """
    total_intel = sum(s.ttps_captured + s.stix_bundles for s in sessions)
    return total_intel / max(t_observation_hours, 0.01)


def compute_deception_effectiveness(
    sessions: list[SessionDeceptionMetrics],
    t_max: float = 600.0,
) -> dict:
    """
    Eq.10 -- DES v3: Multi-Dimensional Deception Effectiveness Score

    단일 임계치(p_real < 0.2) 대신, 7개 독립 차원의 가중 합산.
    각 차원이 기만의 다른 측면을 측정하므로,
    하나가 낮아도 다른 차원에서 보완 가능.

    DES = w1*Accuracy + w2*Temptation + w3*Cloaking
        + w4*SojournRatio + w5*PhaseDepth + w6*CRR + w7*CDI

    가중치 설계 근거:
      - Accuracy (0.20): 응답 품질은 기만의 기초 (HoneyGPT)
      - Temptation (0.15): 유인은 능동 기만의 핵심 (HoneyGPT)
      - Cloaking (0.15): 탐지 회피는 생존 조건 (Aradi 2025)
      - Sojourn (0.20): 체류 시간은 기만 효과의 직접 증거 (Ferguson-Walter)
      - PhaseDepth (0.15): 공격 진행도는 확신 수준 (MIRAGE-UAS)
      - CRR (0.10): 응답 완성도 (HoneyGPT)
      - CDI (0.05): 행동 다양성은 부가 지표 (Ferguson-Walter)
    """
    acc = deception_accuracy(sessions)
    temp = temptation_score(sessions)
    clk = cloaking_success_rate(sessions)
    sojourn = sojourn_time_ratio(sessions, t_max)
    depth = phase_depth_score(sessions)
    crr = command_response_rate(sessions)
    cdi = command_diversity_index(sessions)
    mid = mean_interaction_degree(sessions)

    # Weighted composite
    des = (0.20 * acc + 0.15 * temp + 0.15 * clk
           + 0.20 * sojourn + 0.15 * depth + 0.10 * crr + 0.05 * cdi)

    return {
        "des_v3": round(des, 4),
        "accuracy": round(acc, 4),
        "temptation": round(temp, 4),
        "cloaking_rate": round(clk, 4),
        "sojourn_ratio": round(sojourn, 4),
        "phase_depth": round(depth, 4),
        "command_response_rate": round(crr, 4),
        "command_diversity": round(cdi, 4),
        "mean_interaction_degree": round(mid, 4),
        "n_sessions": len(sessions),
        "source": "multi_dimensional_real",
    }


# ── Legacy compatibility ─────────────────────────────────────────────────────

def compute_confusion_score_realtime(
    sessions: list[SessionDeceptionMetrics],
    w1: float = 0.333,
    w2: float = 0.333,
    w3: float = 0.334,
) -> dict:
    """
    Legacy 3-component score (backward compatible).
    New code should use compute_deception_effectiveness() instead.
    """
    acc = deception_accuracy(sessions)
    temp = temptation_score(sessions)
    clk = cloaking_success_rate(sessions)

    score = w1 * acc + w2 * temp + w3 * clk

    return {
        "confusion_score": round(score, 4),
        "accuracy": round(acc, 4),
        "temptation": round(temp, 4),
        "cloaking_rate": round(clk, 4),
        "weights": {"w1": w1, "w2": w2, "w3": w3},
        "source": "real_measurement",
        "n_sessions": len(sessions),
    }
