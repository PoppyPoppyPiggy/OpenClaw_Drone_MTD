#!/usr/bin/env python3
"""
plot_results.py — MIRAGE-UAS 논문 Figure 생성기

Project  : MIRAGE-UAS
Module   : Evaluation / Plot Results
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - results/metrics/table_ii_engagement.json
    - results/metrics/table_iii_mtd_latency.json
    - results/metrics/table_iv_dataset.json
    - results/metrics/table_v_deception.json
    - results/metrics/table_vi_agent_decisions.json
    - results/metrics/deception_timeline.jsonl

[Outputs]
    - results/figures/table_ii.pdf / .png   (Engagement by Level)
    - results/figures/table_iii.pdf / .png  (MTD Latency by Action)
    - results/figures/table_iv.pdf / .png   (Dataset Distribution)
    - results/figures/table_v.pdf / .png    (Deception Success)
    - results/figures/table_vi.pdf / .png   (Agent Decisions)
    - results/figures/timeline.pdf / .png   (Deception Timeline)

[Dependencies]
    - matplotlib >= 3.8

[설계 원칙]
    - IEEE 논문 스타일: Times New Roman, 3.5" 단일 컬럼 폭
    - top/right 스파인 제거, 회색 y-그리드만 표시
    - 모든 figure를 PDF + PNG(300DPI) 동시 저장

[DATA FLOW]
    results/metrics/*.json ──▶ load_all_metrics()
    ──▶ plot_table_*() ──▶ results/figures/*.{pdf,png}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # 헤드리스 서버 호환
import matplotlib.pyplot as plt

from shared.constants import RESULTS_DIR
from shared.logger import get_logger

logger = get_logger(__name__)

_METRICS_DIR = Path(RESULTS_DIR) / "metrics"
_FIGURES_DIR = Path(RESULTS_DIR) / "figures"

# ── IEEE 논문 스타일 설정 ──────────────────────────────────────────────────────
_STYLE = {
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 9,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.axis": "y",
    "grid.alpha": 0.3,
    "grid.color": "#cccccc",
}

# ── 공통 색상 ──────────────────────────────────────────────────────────────────
_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
           "#8c564b", "#e377c2"]


def _save_fig(fig: plt.Figure, name: str) -> None:
    """
    [ROLE] Figure를 PDF + PNG 동시 저장.

    [DATA FLOW]
        fig ──▶ results/figures/{name}.pdf + .png
    """
    _FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        path = _FIGURES_DIR / f"{name}.{ext}"
        fig.savefig(path)
        logger.debug("figure_saved", path=str(path))
    plt.close(fig)


def load_all_metrics() -> dict[str, Any]:
    """
    [ROLE] results/metrics/ 디렉토리의 모든 JSON 파일 로드.

    [DATA FLOW]
        results/metrics/*.json ──▶ {filename_stem: data} dict
    """
    data: dict[str, Any] = {}
    if not _METRICS_DIR.exists():
        logger.warning("metrics_dir_not_found", path=str(_METRICS_DIR))
        return data
    for path in sorted(_METRICS_DIR.glob("*.json")):
        try:
            data[path.stem] = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("metric_load_failed", path=str(path), error=str(e))
    return data


def plot_table_ii(data: list[dict]) -> None:
    """
    [ROLE] Table II — 공격자 레벨별 평균 체류 시간 grouped bar chart.

    [DATA FLOW]
        list[TableII dict] ──▶ grouped bar ──▶ results/figures/table_ii.{pdf,png}
    """
    if not data:
        return
    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(3.5, 2.5))
        levels = [d["level"] for d in data]
        dwell  = [d["avg_dwell_sec"] for d in data]
        cmds   = [d["avg_commands"] for d in data]

        x = range(len(levels))
        w = 0.35
        ax.bar([i - w / 2 for i in x], dwell, w, label="Avg Dwell (s)", color=_COLORS[0])
        ax.bar([i + w / 2 for i in x], cmds, w, label="Avg Commands", color=_COLORS[1])
        ax.set_xticks(list(x))
        ax.set_xticklabels(levels, rotation=45, ha="right")
        ax.set_ylabel("Value")
        ax.set_title("Engagement Metrics by Attacker Level")
        ax.legend(loc="upper left")
        _save_fig(fig, "table_ii")
    logger.info("plot_table_ii_done", levels=len(data))


def plot_table_iii(data: list[dict]) -> None:
    """
    [ROLE] Table III — MTD 액션 유형별 실행 지연 horizontal bar chart.

    [DATA FLOW]
        list[TableIII dict] ──▶ horizontal bar ──▶ results/figures/table_iii.{pdf,png}
    """
    if not data:
        return
    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(3.5, 2.5))
        actions = [d["action_type"] for d in data]
        avg_ms  = [d["avg_ms"] for d in data]
        p95_ms  = [d["p95_ms"] for d in data]

        y = range(len(actions))
        ax.barh(list(y), avg_ms, height=0.4, label="Avg (ms)", color=_COLORS[0])
        ax.barh([i + 0.4 for i in y], p95_ms, height=0.4, label="P95 (ms)", color=_COLORS[3])
        ax.set_yticks([i + 0.2 for i in y])
        ax.set_yticklabels(actions)
        ax.set_xlabel("Latency (ms)")
        ax.set_title("MTD Response Latency by Action Type")
        ax.legend(loc="lower right")
        _save_fig(fig, "table_iii")
    logger.info("plot_table_iii_done", actions=len(data))


def plot_table_iv(data: dict) -> None:
    """
    [ROLE] Table IV — 프로토콜별 양성/음성 stacked bar.

    [DATA FLOW]
        table_iv dict ──▶ stacked bar ──▶ results/figures/table_iv.{pdf,png}
    """
    if not data or "by_protocol" not in data:
        return
    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(3.5, 2.5))
        protos = list(data["by_protocol"].keys())
        counts = [data["by_protocol"][p] for p in protos]
        pos    = data.get("positive_count", 0)
        neg    = data.get("negative_count", 0)
        total  = max(pos + neg, 1)

        # 각 프로토콜 내 양성/음성 비율 근사 (전체 비율 적용)
        pos_ratio = pos / total
        pos_vals  = [int(c * pos_ratio) for c in counts]
        neg_vals  = [c - p for c, p in zip(counts, pos_vals)]

        ax.bar(protos, pos_vals, label="Positive (attack)", color=_COLORS[3])
        ax.bar(protos, neg_vals, bottom=pos_vals, label="Negative (benign)", color=_COLORS[2])
        ax.set_ylabel("Samples")
        ax.set_title("Dataset Distribution by Protocol")
        ax.legend()
        _save_fig(fig, "table_iv")
    logger.info("plot_table_iv_done")


def plot_table_v(data: dict) -> None:
    """
    [ROLE] Table V — Deception 성공률 pie chart.

    [DATA FLOW]
        table_v dict ──▶ pie chart ──▶ results/figures/table_v.{pdf,png}
    """
    if not data:
        return
    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(3.5, 2.5))
        protected = data.get("protected_sessions", 0)
        breached  = data.get("breached_sessions", 0)

        if protected + breached == 0:
            return

        ax.pie(
            [protected, breached],
            labels=["Protected", "Breached"],
            colors=[_COLORS[2], _COLORS[3]],
            autopct="%1.1f%%",
            startangle=90,
        )
        ax.set_title(f"Deception Success Rate ({data.get('success_rate', 0):.1%})")
        _save_fig(fig, "table_v")
    logger.info("plot_table_v_done")


def plot_table_vi(data: list[dict]) -> None:
    """
    [ROLE] Table VI — 에이전트 행동 트리거 카운트 bar chart.

    [DATA FLOW]
        list[TableVI dict] ──▶ bar chart ──▶ results/figures/table_vi.{pdf,png}
    """
    if not data:
        return
    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(3.5, 2.5))
        behaviors = [d["behavior_triggered"] for d in data]
        counts    = [d["count"] for d in data]

        ax.bar(range(len(behaviors)), counts, color=_COLORS[:len(behaviors)])
        ax.set_xticks(range(len(behaviors)))
        ax.set_xticklabels(behaviors, rotation=45, ha="right")
        ax.set_ylabel("Count")
        ax.set_title("Agent Autonomous Decision Triggers")
        _save_fig(fig, "table_vi")
    logger.info("plot_table_vi_done", behaviors=len(data))


def plot_deception_timeline(jsonl_path: Path | None = None) -> None:
    """
    [ROLE] Deception Timeline — 기만 효과 시계열 (effectiveness + confusion).

    [DATA FLOW]
        deception_timeline.jsonl ──▶ line plot ──▶ results/figures/timeline.{pdf,png}
    """
    path = jsonl_path or (_METRICS_DIR / "deception_timeline.jsonl")
    if not path.exists():
        return

    records: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        return

    with plt.rc_context(_STYLE):
        fig, ax1 = plt.subplots(figsize=(3.5, 2.5))

        ts    = [r.get("timestamp", 0) for r in records]
        t0    = ts[0] if ts else 0
        t_rel = [(t - t0) for t in ts]
        eff   = [r.get("deception_effectiveness", 0) for r in records]
        conf  = [r.get("avg_confusion_score", 0) for r in records]

        ax1.plot(t_rel, eff, color=_COLORS[0], linewidth=1.2, label="Effectiveness")
        ax1.set_xlabel("Time (s)")
        ax1.set_ylabel("Effectiveness", color=_COLORS[0])
        ax1.tick_params(axis="y", labelcolor=_COLORS[0])

        ax2 = ax1.twinx()
        ax2.plot(t_rel, conf, color=_COLORS[1], linewidth=1.2, linestyle="--", label="Confusion")
        ax2.set_ylabel("Confusion Score", color=_COLORS[1])
        ax2.tick_params(axis="y", labelcolor=_COLORS[1])
        ax2.spines["top"].set_visible(False)

        ax1.set_title("Deception Effectiveness Over Time")
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="lower right")

        _save_fig(fig, "timeline")
    logger.info("plot_deception_timeline_done", records=len(records))


def main() -> None:
    """
    [ROLE] 모든 가용 메트릭 파일에 대해 플롯 생성.
           파일이 없는 경우 해당 플롯을 건너뜀.

    [DATA FLOW]
        load_all_metrics() ──▶ 조건부 plot_table_*() ──▶ results/figures/
    """
    metrics = load_all_metrics()

    if "table_ii_engagement" in metrics:
        plot_table_ii(metrics["table_ii_engagement"])
    if "table_iii_mtd_latency" in metrics:
        plot_table_iii(metrics["table_iii_mtd_latency"])
    if "table_iv_dataset" in metrics:
        plot_table_iv(metrics["table_iv_dataset"])
    if "table_v_deception" in metrics:
        plot_table_v(metrics["table_v_deception"])
    if "table_vi_agent_decisions" in metrics:
        plot_table_vi(metrics["table_vi_agent_decisions"])

    plot_deception_timeline()

    print(f"Figures saved to: {_FIGURES_DIR}")


if __name__ == "__main__":
    main()
