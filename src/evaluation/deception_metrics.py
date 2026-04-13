"""
MIRAGE-UAS 기만 효과 실측 지표
confusion_score=0.72 하드코딩을 대체하는 3개 독립 수식

References:
  Eq.01 Accuracy  -- Wang et al. (2024) HoneyGPT arXiv:2406.01882
  Eq.02 Temptation -- Wang et al. (2024) HoneyGPT arXiv:2406.01882
  Eq.05 Cloaking  -- Aradi et al. (2025) Acta Polytechnica 22(12):295
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionDeceptionMetrics:
    """단일 공격자 세션의 기만 측정값"""
    # HoneyGPT 기준 필드 (attacker_log.jsonl에서 추출)
    salc: int = 0    # 공격 성공 + 논리적으로 올바른 응답
    salnlc: int = 0  # 공격 성공 + 논리적으로 부정확한 응답
    falc: int = 0    # 공격 실패 + 논리적으로 올바른 응답 (유인 성공)
    honeypot_detected: bool = False  # 공격자가 허니팟임을 탐지했는가


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


def compute_confusion_score_realtime(
    sessions: list[SessionDeceptionMetrics],
    w1: float = 0.333,
    w2: float = 0.333,
    w3: float = 0.334,
) -> dict:
    """
    confusion_score=0.72 하드코딩 완전 대체.
    3개 독립 수식의 가중 합산.

    DS_confusion = w1*Accuracy + w2*Temptation + w3*CloakingRate

    각 컴포넌트가 독립적인 기만 차원을 측정:
      Accuracy    -> 응답 품질 (논리 정합성)
      Temptation  -> 유인 효과 (공격자를 더 깊이 끌어들이는 능력)
      CloakingRate -> 탐지 회피 (허니팟 식별 방지)
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
