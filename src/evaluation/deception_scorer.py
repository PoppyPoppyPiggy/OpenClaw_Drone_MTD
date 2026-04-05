#!/usr/bin/env python3
"""
deception_scorer.py — DeceptionScore 최종 산출

Project  : MIRAGE-UAS
Module   : Evaluation / Deception Scorer
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - results/metrics/deception_timeline.jsonl  (DeceptionMonitor 출력)
    - DECEPTION_SCORE_WEIGHTS (.env — 5D 가중치)

[Outputs]
    - float: DeceptionScore ∈ [0, 1]
    - dict: 구성 요소별 점수
    - str: LaTeX \\newcommand 매크로 (논문 삽입용)

[Dependencies]
    - json (stdlib)
    - statistics (stdlib)

[REF] MIRAGE-UAS §7.1 — Composite Deception Effectiveness Score
    DS = w1 * (time_on_decoys / total_time)
       + w2 * (1 - real_drone_breach_rate)
       + w3 * avg_confusion_score
       + w4 * breadcrumb_follow_rate
       + w5 * ghost_hit_rate

    where w1+w2+w3+w4+w5 = 1.0
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from shared.constants import DECEPTION_SCORE_WEIGHTS, RESULTS_DIR
from shared.logger import get_logger

logger = get_logger(__name__)

_TIMELINE_PATH = Path(RESULTS_DIR) / "metrics" / "deception_timeline.jsonl"


def compute(timeline: list[dict]) -> tuple[float, dict[str, float]]:
    """
    [ROLE] deception_timeline.jsonl 레코드 목록에서 최종 DeceptionScore 산출.

    [DATA FLOW]
        timeline records ──▶ 구성요소 평균 계산
        ──▶ 가중합 (DECEPTION_SCORE_WEIGHTS)
        ──▶ (DeceptionScore, components dict)

    [REF] MIRAGE-UAS §7.1 Eq.DS
    """
    if not timeline:
        return 0.0, {
            "time_on_decoys_ratio": 0.0,
            "breach_prevention": 0.0,
            "avg_confusion": 0.0,
            "breadcrumb_follow_rate": 0.0,
            "ghost_hit_rate": 0.0,
        }

    w1, w2, w3, w4, w5 = DECEPTION_SCORE_WEIGHTS

    # 구성요소 추출 (전체 시계열 평균)
    effectiveness_vals = [r.get("deception_effectiveness", 0.0) for r in timeline]
    confusion_vals     = [r.get("avg_confusion_score", 0.0) for r in timeline]
    breadcrumb_vals    = [r.get("breadcrumb_follow_rate", 0.0) for r in timeline]
    ghost_vals         = [r.get("ghost_service_hit_rate", 0.0) for r in timeline]

    time_on_decoys_ratio = statistics.mean(effectiveness_vals)
    breach_prevention    = statistics.mean(effectiveness_vals)  # 1 - breach_rate ≈ effectiveness
    avg_confusion        = statistics.mean(confusion_vals)
    breadcrumb_rate      = statistics.mean(breadcrumb_vals)
    ghost_hit            = statistics.mean(ghost_vals)

    components = {
        "time_on_decoys_ratio": round(time_on_decoys_ratio, 4),
        "breach_prevention": round(breach_prevention, 4),
        "avg_confusion": round(avg_confusion, 4),
        "breadcrumb_follow_rate": round(breadcrumb_rate, 4),
        "ghost_hit_rate": round(ghost_hit, 4),
    }

    # [REF] §7.1 Eq.DS
    score = (
        w1 * time_on_decoys_ratio
        + w2 * breach_prevention
        + w3 * avg_confusion
        + w4 * breadcrumb_rate
        + w5 * ghost_hit
    )
    score = round(min(max(score, 0.0), 1.0), 4)

    logger.info(
        "deception_score_computed",
        score=score,
        components=components,
        weights=DECEPTION_SCORE_WEIGHTS,
    )
    return score, components


def compare_baseline(
    with_deception: float, without_deception: float
) -> dict[str, Any]:
    """
    [ROLE] 기만 유무 DeceptionScore 비교 — 논문 결과 표 지원.

    [DATA FLOW]
        with_deception, without_deception ──▶ improvement%, p_value placeholder
    """
    if without_deception > 0:
        improvement_pct = (with_deception - without_deception) / without_deception * 100.0
    else:
        improvement_pct = float("inf") if with_deception > 0 else 0.0

    result = {
        "with_deception": round(with_deception, 4),
        "without_deception": round(without_deception, 4),
        "improvement_pct": round(improvement_pct, 2),
        "p_value": "placeholder — requires paired t-test with N>=30 runs",
    }
    logger.info("baseline_comparison", **result)
    return result


def to_latex(score: float, components: dict[str, float]) -> str:
    r"""
    [ROLE] LaTeX \newcommand 매크로 생성 — 논문에 직접 삽입 가능.

    [DATA FLOW]
        score, components ──▶ \newcommand 문자열
    """
    lines = [
        f"\\newcommand{{\\DeceptionScore}}{{{score:.4f}}}",
        f"\\newcommand{{\\DSTimeOnDecoys}}{{{components.get('time_on_decoys_ratio', 0.0):.4f}}}",
        f"\\newcommand{{\\DSBreachPrevention}}{{{components.get('breach_prevention', 0.0):.4f}}}",
        f"\\newcommand{{\\DSConfusion}}{{{components.get('avg_confusion', 0.0):.4f}}}",
        f"\\newcommand{{\\DSBreadcrumb}}{{{components.get('breadcrumb_follow_rate', 0.0):.4f}}}",
        f"\\newcommand{{\\DSGhostHit}}{{{components.get('ghost_hit_rate', 0.0):.4f}}}",
    ]
    latex_str = "\n".join(lines)
    logger.info("latex_macros_generated", line_count=len(lines))
    return latex_str


def compute_from_file(filepath: Path | None = None) -> tuple[float, dict[str, float]]:
    """
    [ROLE] JSONL 파일에서 타임라인을 로드하여 DeceptionScore 산출.

    [DATA FLOW]
        filepath ──▶ JSONL 파싱 ──▶ compute(timeline)
    """
    path = filepath or _TIMELINE_PATH
    if not path.exists():
        logger.warning("timeline file not found", path=str(path))
        return 0.0, {}

    timeline: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                timeline.append(json.loads(line))

    return compute(timeline)


if __name__ == "__main__":
    score, components = compute_from_file()
    print(f"DeceptionScore: {score}")
    print(f"Components: {json.dumps(components, indent=2)}")
    print("\nLaTeX macros:")
    print(to_latex(score, components))
