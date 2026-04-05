#!/usr/bin/env python3
"""
metrics_collector.py — MIRAGE-UAS 논문 메트릭 수집기

Project  : MIRAGE-UAS
Module   : Evaluation / Metrics Collector
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.2.0

[Inputs]
    - list[EngagementMetrics]   (EngagementTracker에서 수집)
    - list[MTDResult]           (MTDExecutor에서 수집)
    - ValidationReport          (DatasetValidator에서 수집)
    - list[DatasetEntry]        (DatasetPackager에서 수집)
    - list[AgentDecision]       (OpenClawAgent에서 수집)

[Outputs]
    - results/metrics/table_ii_engagement.json   (논문 Table II)
    - results/metrics/table_iii_mtd_latency.json (논문 Table III)
    - results/metrics/table_iv_dataset.json      (논문 Table IV)
    - results/metrics/table_v_deception.json     (논문 Table V)
    - results/metrics/table_vi_agent_decisions.json (논문 Table VI)
    - results/metrics/summary.json               (전체 요약)

[논문 테이블 매핑]
    Table II  : Engagement Metrics by Attacker Level
                (avg_dwell_sec, max_dwell_sec, avg_cmds, exploit_rate)
    Table III : MTD Response Latency by Action Type
                (avg_ms, min_ms, max_ms, p95_ms, success_rate)
    Table IV  : Dataset Statistics
                (total, by_level, by_protocol, ttp_coverage)
    Table V   : Deception Success Rate
                (sessions_total, breached, not_breached, success_rate%)
    Table VI  : Agent Autonomous Decisions
                (behavior_triggered, count, avg_attacker_dwell_after_sec, confusion_score_delta)
"""

import json
import statistics
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.constants import RESULTS_DIR
from shared.logger import get_logger
from shared.models import AgentDecision, AttackerLevel, DatasetEntry, EngagementMetrics

logger = get_logger(__name__)

_METRICS_DIR = Path(RESULTS_DIR) / "metrics"


# ── 메트릭 컨테이너 ───────────────────────────────────────────────────────────

@dataclass
class TableII_Engagement:
    """논문 Table II — 공격자 레벨별 Engagement 지표."""
    level            : str
    session_count    : int
    avg_dwell_sec    : float
    max_dwell_sec    : float
    median_dwell_sec : float
    avg_commands     : float
    avg_exploits     : float
    ws_session_rate  : float   # WebSocket 연결 비율 (L3-L4 식별 신호)

    def __repr__(self) -> str:
        return (
            f"TableII({self.level}: n={self.session_count}, "
            f"dwell={self.avg_dwell_sec:.1f}s, cmds={self.avg_commands:.1f})"
        )


@dataclass
class TableIII_MTDLatency:
    """논문 Table III — MTD 액션 유형별 실행 지연."""
    action_type   : str
    count         : int
    avg_ms        : float
    min_ms        : float
    max_ms        : float
    p95_ms        : float    # 95th percentile
    success_rate  : float    # 성공률 [0.0, 1.0]

    def __repr__(self) -> str:
        return (
            f"TableIII({self.action_type}: n={self.count}, "
            f"avg={self.avg_ms:.1f}ms, p95={self.p95_ms:.1f}ms, "
            f"ok={self.success_rate:.1%})"
        )


@dataclass
class TableV_Deception:
    """논문 Table V — Deception 성공률."""
    total_sessions      : int
    breached_sessions   : int
    protected_sessions  : int
    success_rate        : float   # 실 드론 보호 성공률
    avg_dwell_sec       : float   # 전체 평균 체류 시간
    l3_l4_session_rate  : float   # L3+L4 공격자 비율

    def __repr__(self) -> str:
        return (
            f"TableV: sessions={self.total_sessions}, "
            f"success={self.success_rate:.1%}, "
            f"avg_dwell={self.avg_dwell_sec:.1f}s"
        )


@dataclass
class TableVI_AgentDecision:
    """
    [ROLE] 논문 Table VI — OpenClaw Agent 자율 결정 집계.

    [DATA FLOW]
        list[AgentDecision] ──▶ collect_agent_decisions()
        ──▶ behavior별 그룹핑 ──▶ count, avg_dwell_after, confusion_delta
        ──▶ TableVI_AgentDecision ──▶ JSON 저장
    """
    behavior_triggered          : str
    count                       : int
    avg_attacker_dwell_after_sec: float
    confusion_score_delta       : float

    def __repr__(self) -> str:
        return (
            f"TableVI({self.behavior_triggered}: n={self.count}, "
            f"dwell_after={self.avg_attacker_dwell_after_sec:.1f}s, "
            f"confusion_delta={self.confusion_score_delta:+.3f})"
        )


@dataclass
class ExperimentSummary:
    """전체 실험 요약 (논문 §7 서두)."""
    experiment_id       : str
    start_time          : float
    end_time            : float
    duration_sec        : float
    honey_drone_count   : int
    total_sessions      : int
    total_mtd_actions   : int
    deception_success   : float
    dataset_size        : int
    unique_ttps         : int


# ── MetricsCollector ──────────────────────────────────────────────────────────

class MetricsCollector:
    """
    [ROLE] 실험 전반에서 수집된 데이터를 논문 Table/Figure 형식으로 집계.
           Phase C2 평가의 핵심 산출물 생성.

    [DATA FLOW]
        실험 종료 후:
        collect_engagement(metrics_list) ──▶ Table II JSON
        collect_mtd_results(results_list) ──▶ Table III JSON
        collect_dataset_stats(entries)    ──▶ Table IV JSON
        collect_deception(metrics_list)   ──▶ Table V JSON
        collect_agent_decisions(decisions)──▶ Table VI JSON
        generate_summary(...)             ──▶ summary JSON
    """

    def __init__(self, experiment_id: str) -> None:
        self._exp_id     = experiment_id
        self._start_time = time.time()
        _METRICS_DIR.mkdir(parents=True, exist_ok=True)

    def collect_engagement(
        self, all_metrics: list[EngagementMetrics]
    ) -> list[TableII_Engagement]:
        """
        [ROLE] 전체 세션 EngagementMetrics를 L0-L4 레벨별로 집계.
               논문 Table II 데이터 생성.

        [DATA FLOW]
            list[EngagementMetrics]
            ──▶ level별 그룹핑
            ──▶ avg/max/median dwell, avg cmds, ws_rate 계산
            ──▶ list[TableII_Engagement] ──▶ JSON 저장
        """
        by_level: dict[str, list[EngagementMetrics]] = defaultdict(list)
        for m in all_metrics:
            by_level[m.attacker_level.name].append(m)

        rows: list[TableII_Engagement] = []
        for level_name in [lv.name for lv in AttackerLevel]:
            group = by_level.get(level_name, [])
            if not group:
                continue
            dwells   = [m.dwell_time_sec  for m in group]
            cmds     = [m.commands_issued for m in group]
            exploits = [m.exploit_attempts for m in group]
            ws_cnt   = sum(1 for m in group if m.websocket_sessions > 0)

            rows.append(TableII_Engagement(
                level=level_name,
                session_count=len(group),
                avg_dwell_sec=round(statistics.mean(dwells), 2),
                max_dwell_sec=round(max(dwells), 2),
                median_dwell_sec=round(statistics.median(dwells), 2),
                avg_commands=round(statistics.mean(cmds), 2),
                avg_exploits=round(statistics.mean(exploits), 4),
                ws_session_rate=round(ws_cnt / len(group), 4),
            ))

        self._save("table_ii_engagement.json", [_as_dict(r) for r in rows])
        logger.info(
            "table_ii_collected",
            levels=len(rows),
            total_sessions=len(all_metrics),
        )
        return rows

    def collect_mtd_results(self, results: list[Any]) -> list[TableIII_MTDLatency]:
        """
        [ROLE] MTDResult 목록을 액션 유형별로 집계하여 지연 통계 산출.
               논문 Table III 데이터 생성.

        [DATA FLOW]
            list[MTDResult]
            ──▶ action_type별 그룹핑
            ──▶ avg/min/max/p95 latency + success_rate
            ──▶ list[TableIII_MTDLatency] ──▶ JSON 저장
        """
        by_type: dict[str, list[Any]] = defaultdict(list)
        for r in results:
            by_type[r.action_type.name].append(r)

        rows: list[TableIII_MTDLatency] = []
        for action_name, group in by_type.items():
            latencies = sorted([r.execution_time_ms for r in group])
            successes = [r.success for r in group]
            p95_idx   = max(0, int(len(latencies) * 0.95) - 1)

            rows.append(TableIII_MTDLatency(
                action_type=action_name,
                count=len(group),
                avg_ms=round(statistics.mean(latencies), 2),
                min_ms=round(latencies[0], 2),
                max_ms=round(latencies[-1], 2),
                p95_ms=round(latencies[p95_idx], 2),
                success_rate=round(sum(successes) / len(successes), 4),
            ))

        rows.sort(key=lambda r: r.avg_ms)
        self._save("table_iii_mtd_latency.json", [_as_dict(r) for r in rows])
        logger.info("table_iii_collected", action_types=len(rows))
        return rows

    def collect_dataset_stats(
        self, entries: list[DatasetEntry]
    ) -> dict:
        """
        [ROLE] DatasetEntry 목록에서 논문 Table IV 통계 집계.

        [DATA FLOW]
            list[DatasetEntry]
            ──▶ total, by_label, by_level, by_protocol, ttp_coverage
            ──▶ dict ──▶ table_iv_dataset.json
        """
        positive = [e for e in entries if e.label == 1]
        negative = [e for e in entries if e.label == 0]

        by_level: dict[str, int] = defaultdict(int)
        by_proto: dict[str, int] = defaultdict(int)
        all_ttps: set[str]       = set()

        for e in positive:
            lv = e.attacker_level.name if e.attacker_level else "unknown"
            by_level[lv] += 1
            all_ttps.update(e.ttp_ids)

        for e in entries:
            by_proto[e.protocol.value] += 1

        stats = {
            "total_samples"  : len(entries),
            "positive_count" : len(positive),
            "negative_count" : len(negative),
            "class_ratio"    : round(len(negative) / max(len(positive), 1), 2),
            "by_attacker_level": dict(by_level),
            "by_protocol"    : dict(by_proto),
            "unique_ttp_count": len(all_ttps),
            "unique_ttps"    : sorted(all_ttps),
        }
        self._save("table_iv_dataset.json", stats)
        logger.info(
            "table_iv_collected",
            total=len(entries),
            ttps=len(all_ttps),
        )
        return stats

    def collect_deception(
        self, all_metrics: list[EngagementMetrics]
    ) -> TableV_Deception:
        """
        [ROLE] Deception 성공률 집계 — 논문 Table V.
               real_drone_breached=False 가 기만 성공.

        [DATA FLOW]
            list[EngagementMetrics]
            ──▶ breached / protected 카운트
            ──▶ success_rate = protected / total
            ──▶ TableV_Deception ──▶ JSON 저장
        """
        total     = len(all_metrics)
        breached  = sum(1 for m in all_metrics if m.real_drone_breached)
        protected = total - breached

        dwells = [m.dwell_time_sec for m in all_metrics] if all_metrics else [0.0]
        l3_l4  = sum(
            1 for m in all_metrics
            if m.attacker_level in (AttackerLevel.L3_ADVANCED, AttackerLevel.L4_APT)
        )

        result = TableV_Deception(
            total_sessions=total,
            breached_sessions=breached,
            protected_sessions=protected,
            success_rate=round(protected / max(total, 1), 4),
            avg_dwell_sec=round(statistics.mean(dwells), 2),
            l3_l4_session_rate=round(l3_l4 / max(total, 1), 4),
        )
        self._save("table_v_deception.json", _as_dict(result))
        logger.info(
            "table_v_collected",
            success_rate=f"{result.success_rate:.1%}",
            total=total,
        )
        return result

    def collect_agent_decisions(
        self, decisions: list[AgentDecision]
    ) -> list[TableVI_AgentDecision]:
        """
        [ROLE] OpenClawAgent 자율 결정 집계 — 논문 Table VI.
               behavior_triggered별 카운트, 평균 공격자 체류 시간 변화,
               confusion score delta.

        [DATA FLOW]
            list[AgentDecision]
            ──▶ behavior별 그룹핑
            ──▶ count, avg_dwell_after_sec, confusion_delta
            ──▶ list[TableVI_AgentDecision] ──▶ JSON 저장
        """
        by_behavior: dict[str, list[AgentDecision]] = defaultdict(list)
        for d in decisions:
            by_behavior[d.behavior_triggered].append(d)

        rows: list[TableVI_AgentDecision] = []
        for behavior, group in sorted(by_behavior.items()):
            # 평균 체류 시간: 결정 시점부터의 경과 시간 근사
            # (실제 측정은 EngagementTracker와 연동 필요 — 여기서는 결정 간격 근사)
            timestamps = sorted(d.timestamp_ns for d in group)
            if len(timestamps) >= 2:
                intervals = [
                    (timestamps[i + 1] - timestamps[i]) / 1e9
                    for i in range(len(timestamps) - 1)
                ]
                avg_dwell_after = round(statistics.mean(intervals), 2)
            else:
                avg_dwell_after = 0.0

            # confusion score delta: 행동 실행 비율 × 가중치 (heuristic)
            executed_count = sum(1 for d in group if d.executed)
            confusion_delta = round(executed_count / max(len(group), 1) * 0.1, 4)

            rows.append(TableVI_AgentDecision(
                behavior_triggered=behavior,
                count=len(group),
                avg_attacker_dwell_after_sec=avg_dwell_after,
                confusion_score_delta=confusion_delta,
            ))

        self._save(
            "table_vi_agent_decisions.json",
            [_as_dict(r) for r in rows],
        )
        logger.info(
            "table_vi_collected",
            behaviors=len(rows),
            total_decisions=len(decisions),
        )
        return rows

    def generate_summary(
        self,
        honey_drone_count: int,
        total_sessions: int,
        total_mtd_actions: int,
        deception_success: float,
        dataset_size: int,
        unique_ttps: int,
    ) -> ExperimentSummary:
        """
        [ROLE] 실험 전체 요약 생성 (논문 §7 서두 수치 직접 사용).

        [DATA FLOW]
            각종 카운터 ──▶ ExperimentSummary ──▶ summary.json
        """
        end_time = time.time()
        summary  = ExperimentSummary(
            experiment_id=self._exp_id,
            start_time=self._start_time,
            end_time=end_time,
            duration_sec=round(end_time - self._start_time, 1),
            honey_drone_count=honey_drone_count,
            total_sessions=total_sessions,
            total_mtd_actions=total_mtd_actions,
            deception_success=round(deception_success, 4),
            dataset_size=dataset_size,
            unique_ttps=unique_ttps,
        )
        self._save("summary.json", _as_dict(summary))
        logger.info(
            "experiment_summary_generated",
            exp_id=self._exp_id,
            duration_sec=summary.duration_sec,
            sessions=total_sessions,
            mtd_actions=total_mtd_actions,
            deception_success=f"{deception_success:.1%}",
        )
        return summary

    def _save(self, filename: str, data: Any) -> None:
        """[ROLE] 메트릭 데이터를 JSON으로 저장."""
        path = _METRICS_DIR / filename
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        logger.debug("metrics_saved", path=str(path))


def _as_dict(obj) -> dict:
    """dataclass → dict 변환 (JSON 직렬화용)."""
    try:
        return asdict(obj)
    except TypeError:
        return vars(obj)
