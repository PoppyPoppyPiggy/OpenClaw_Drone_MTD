#!/usr/bin/env python3
"""
statistical_test.py — MIRAGE-UAS 통계 검정 및 LaTeX 테이블 생성

Project  : MIRAGE-UAS
Module   : Evaluation / Statistical Test
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - results/metrics/table_ii_engagement.json
    - results/metrics/table_iii_mtd_latency.json
    - results/metrics/table_iv_dataset.json
    - results/metrics/table_v_deception.json
    - results/metrics/table_vi_agent_decisions.json

[Outputs]
    - results/metrics/statistical_report.json
    - results/latex/table_ii.tex ... table_vi.tex

[Dependencies]
    - scipy >= 1.11 (Wilcoxon signed-rank test)
    - numpy (Cohen's d 계산)

[설계 원칙]
    - Bonferroni correction: p_corrected = p_raw × N_comparisons
    - 효과 크기: Cohen's d (|d|<0.2 small, 0.2-0.8 medium, >0.8 large)
    - LaTeX 출력: \\begin{table}...\\end{table} 직접 논문 삽입 가능

[DATA FLOW]
    results/metrics/*.json ──▶ run_all_tests()
    ──▶ statistical_report.json
    ──▶ generate_latex_tables() ──▶ results/latex/*.tex
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from shared.constants import RESULTS_DIR
from shared.logger import get_logger

logger = get_logger(__name__)

_METRICS_DIR = Path(RESULTS_DIR) / "metrics"
_LATEX_DIR   = Path(RESULTS_DIR) / "latex"

# Bonferroni 보정 비교 수 (baseline vs treatment × 3 metrics)
_N_COMPARISONS: int = 3


def run_wilcoxon_test(
    baseline: list[float], treatment: list[float]
) -> dict[str, Any]:
    """
    [ROLE] Wilcoxon signed-rank test + Bonferroni correction.
           paired sample 비모수 검정 — 정규성 가정 불필요.

    [DATA FLOW]
        baseline, treatment ──▶ scipy.stats.wilcoxon()
        ──▶ {statistic, p_raw, p_corrected, significant}
    """
    try:
        from scipy import stats
        stat, p_raw = stats.wilcoxon(baseline, treatment)
    except ImportError:
        logger.warning("scipy not installed — using placeholder p-value")
        stat = 0.0
        p_raw = 1.0
    except ValueError as e:
        logger.warning("wilcoxon_failed", error=str(e))
        return {"statistic": 0.0, "p_raw": 1.0, "p_corrected": 1.0,
                "significant": False, "error": str(e)}

    p_corrected = min(p_raw * _N_COMPARISONS, 1.0)
    return {
        "statistic": round(float(stat), 4),
        "p_raw": round(float(p_raw), 6),
        "p_corrected": round(float(p_corrected), 6),
        "significant": p_corrected < 0.05,
    }


def compute_effect_size(a: list[float], b: list[float]) -> dict[str, Any]:
    """
    [ROLE] Cohen's d 효과 크기 계산.
           |d| < 0.2 = small, 0.2-0.8 = medium, > 0.8 = large.

    [DATA FLOW]
        a, b ──▶ d = (mean_a - mean_b) / pooled_std ──▶ {d, magnitude}
    """
    if not a or not b:
        return {"d": 0.0, "magnitude": "n/a"}

    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    var_a  = sum((x - mean_a) ** 2 for x in a) / max(len(a) - 1, 1)
    var_b  = sum((x - mean_b) ** 2 for x in b) / max(len(b) - 1, 1)
    pooled = math.sqrt((var_a + var_b) / 2.0)

    if pooled < 1e-10:
        d = 0.0
    else:
        d = (mean_a - mean_b) / pooled

    abs_d = abs(d)
    if abs_d < 0.2:
        mag = "small"
    elif abs_d < 0.8:
        mag = "medium"
    else:
        mag = "large"

    return {"d": round(d, 4), "magnitude": mag}


def generate_latex_tables(
    metrics_dir: Path | None = None, output_dir: Path | None = None
) -> list[Path]:
    """
    [ROLE] 메트릭 JSON에서 LaTeX tabular 소스 생성.
           논문에 직접 \\input{} 가능한 형식.

    [DATA FLOW]
        results/metrics/table_*.json ──▶ LaTeX \\begin{table}...
        ──▶ results/latex/table_*.tex
    """
    m_dir = metrics_dir or _METRICS_DIR
    o_dir = output_dir or _LATEX_DIR
    o_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # Table II: Engagement
    _write_table_ii_tex(m_dir, o_dir, written)
    # Table III: MTD Latency
    _write_table_iii_tex(m_dir, o_dir, written)
    # Table IV: Dataset
    _write_table_iv_tex(m_dir, o_dir, written)
    # Table V: Deception
    _write_table_v_tex(m_dir, o_dir, written)
    # Table VI: Agent Decisions
    _write_table_vi_tex(m_dir, o_dir, written)

    logger.info("latex_tables_generated", count=len(written))
    return written


def _write_table_ii_tex(m_dir: Path, o_dir: Path, written: list[Path]) -> None:
    """[ROLE] Table II LaTeX 생성."""
    path = m_dir / "table_ii_engagement.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Engagement Metrics by Attacker Level}",
        r"\label{tab:engagement}",
        r"\begin{tabular}{lrrrrr}",
        r"\toprule",
        r"Level & Sessions & Avg Dwell (s) & Max Dwell (s) & Avg Cmds & WS Rate \\",
        r"\midrule",
    ]
    for row in data:
        lines.append(
            f"{row['level']} & {row['session_count']} & "
            f"{row['avg_dwell_sec']:.1f} & {row['max_dwell_sec']:.1f} & "
            f"{row['avg_commands']:.1f} & {row['ws_session_rate']:.2f} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    out = o_dir / "table_ii.tex"
    out.write_text("\n".join(lines))
    written.append(out)


def _write_table_iii_tex(m_dir: Path, o_dir: Path, written: list[Path]) -> None:
    """[ROLE] Table III LaTeX 생성."""
    path = m_dir / "table_iii_mtd_latency.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{MTD Response Latency by Action Type}",
        r"\label{tab:mtd-latency}",
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Action & Count & Avg (ms) & P95 (ms) & Success \\",
        r"\midrule",
    ]
    for row in data:
        lines.append(
            f"{row['action_type']} & {row['count']} & "
            f"{row['avg_ms']:.1f} & {row['p95_ms']:.1f} & "
            f"{row['success_rate']*100:.1f}\\% \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    out = o_dir / "table_iii.tex"
    out.write_text("\n".join(lines))
    written.append(out)


def _write_table_iv_tex(m_dir: Path, o_dir: Path, written: list[Path]) -> None:
    """[ROLE] Table IV LaTeX 생성."""
    path = m_dir / "table_iv_dataset.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{DVD-CTI-Dataset-v1 Statistics}",
        r"\label{tab:dataset}",
        r"\begin{tabular}{lr}",
        r"\toprule",
        r"Metric & Value \\",
        r"\midrule",
        f"Total samples & {data.get('total_samples', 0)} \\\\",
        f"Positive (attack) & {data.get('positive_count', 0)} \\\\",
        f"Negative (benign) & {data.get('negative_count', 0)} \\\\",
        f"Class ratio (neg/pos) & {data.get('class_ratio', 0):.2f} \\\\",
        f"Unique TTPs & {data.get('unique_ttp_count', 0)} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    out = o_dir / "table_iv.tex"
    out.write_text("\n".join(lines))
    written.append(out)


def _write_table_v_tex(m_dir: Path, o_dir: Path, written: list[Path]) -> None:
    """[ROLE] Table V LaTeX 생성."""
    path = m_dir / "table_v_deception.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Deception Success Rate}",
        r"\label{tab:deception}",
        r"\begin{tabular}{lr}",
        r"\toprule",
        r"Metric & Value \\",
        r"\midrule",
        f"Total sessions & {data.get('total_sessions', 0)} \\\\",
        f"Protected & {data.get('protected_sessions', 0)} \\\\",
        f"Breached & {data.get('breached_sessions', 0)} \\\\",
        f"Success rate & {data.get('success_rate', 0)*100:.1f}\\% \\\\",
        f"Avg dwell (s) & {data.get('avg_dwell_sec', 0):.1f} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    out = o_dir / "table_v.tex"
    out.write_text("\n".join(lines))
    written.append(out)


def _write_table_vi_tex(m_dir: Path, o_dir: Path, written: list[Path]) -> None:
    """[ROLE] Table VI LaTeX 생성."""
    path = m_dir / "table_vi_agent_decisions.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Agent Autonomous Decision Triggers}",
        r"\label{tab:agent-decisions}",
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"Behavior & Count & Avg Dwell After (s) & Confusion $\Delta$ \\",
        r"\midrule",
    ]
    for row in data:
        lines.append(
            f"{row['behavior_triggered']} & {row['count']} & "
            f"{row['avg_attacker_dwell_after_sec']:.1f} & "
            f"{row['confusion_score_delta']:+.4f} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    out = o_dir / "table_vi.tex"
    out.write_text("\n".join(lines))
    written.append(out)


def run_all_tests(metrics_dir: Path | None = None) -> dict[str, Any]:
    """
    [ROLE] 모든 통계 검정 실행 + 결과 저장.

    [DATA FLOW]
        results/metrics/*.json ──▶ 검정 실행
        ──▶ results/metrics/statistical_report.json
    """
    m_dir = metrics_dir or _METRICS_DIR
    report: dict[str, Any] = {"tests": [], "latex_files": []}

    # Table II에서 L0 vs L3 dwell time 비교 (유의미한 차이 검증)
    t2_path = m_dir / "table_ii_engagement.json"
    if t2_path.exists():
        data = json.loads(t2_path.read_text())
        by_level = {d["level"]: d for d in data}
        if "L0_SCRIPT_KIDDIE" in by_level and "L3_ADVANCED" in by_level:
            l0_dwell = by_level["L0_SCRIPT_KIDDIE"]["avg_dwell_sec"]
            l3_dwell = by_level["L3_ADVANCED"]["avg_dwell_sec"]
            # 단일 값이므로 효과 크기만 보고
            report["tests"].append({
                "name": "L0_vs_L3_dwell",
                "l0_dwell": l0_dwell,
                "l3_dwell": l3_dwell,
                "note": "requires N>=6 paired samples for Wilcoxon",
            })

    # LaTeX 테이블 생성
    latex_files = generate_latex_tables(m_dir)
    report["latex_files"] = [str(p) for p in latex_files]

    # 보고서 저장
    report_path = m_dir / "statistical_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    logger.info("statistical_report_saved", path=str(report_path))

    return report


if __name__ == "__main__":
    report = run_all_tests()
    print(f"Report: {json.dumps(report, indent=2)}")
