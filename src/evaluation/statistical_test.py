#!/usr/bin/env python3
"""
statistical_test.py — MIRAGE-UAS N=30 통계 검정 파이프라인 + LaTeX 테이블 생성

References:
  Wilcoxon  -- Wilcoxon, F. (1945), Biometrics Bull. 1(6):80-83
  Cohen's d -- Cohen, J. (1988), Statistical Power Analysis, 2nd ed.
  Holm-Bonf -- Holm, S. (1979), Scand. J. Stat. 6(2):65-70
  Bootstrap -- Efron & Tibshirani (1993), An Introduction to the Bootstrap
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import wilcoxon, shapiro
from scipy.stats import bootstrap as scipy_bootstrap
from statsmodels.stats.multitest import multipletests

from shared.constants import RESULTS_DIR
from shared.logger import get_logger

logger = get_logger(__name__)

_METRICS_DIR = Path(RESULTS_DIR) / "metrics"
_LATEX_DIR = Path(RESULTS_DIR) / "latex"


# ── Eq.22: Normality check ──────────────────────────────────────────────────

def check_normality(data: np.ndarray, alpha: float = 0.05) -> dict:
    """Shapiro-Wilk normality test (suitable for N < 50)."""
    if len(data) < 3:
        return {"normal": False, "p_value": None, "warning": "N < 3"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, p = shapiro(data)
    return {
        "statistic": round(float(stat), 4),
        "p_value": round(float(p), 6),
        "normal": float(p) > alpha,
        "test": "Shapiro-Wilk",
    }


# ── Eq.23: Wilcoxon signed-rank test ────────────────────────────────────────

def run_wilcoxon_test(
    baseline: list[float], treatment: list[float]
) -> dict[str, Any]:
    """Wilcoxon signed-rank test (non-parametric paired test)."""
    if len(baseline) != len(treatment):
        return {"error": "sample size mismatch", "p_value": 1.0, "significant": False}
    if len(baseline) < 5:
        return {"error": f"need >= 5 pairs (got {len(baseline)})", "p_value": 1.0, "significant": False}
    stat, p = wilcoxon(treatment, baseline, alternative="greater")
    return {
        "test": "Wilcoxon signed-rank",
        "statistic": round(float(stat), 4),
        "p_value": round(float(p), 6),
        "significant": float(p) < 0.05,
        "n_pairs": len(baseline),
        "alternative": "greater",
    }


# ── Eq.24: Cohen's d (paired) ───────────────────────────────────────────────

def compute_effect_size(a: list[float], b: list[float]) -> dict[str, Any]:
    """Cohen's d for paired samples: d_z = mean(D) / std(D)."""
    if not a or not b:
        return {"d": 0.0, "magnitude": "n/a"}
    diff = np.array(a) - np.array(b)
    std = float(np.std(diff, ddof=1))
    d = float(np.mean(diff)) / std if std > 1e-10 else 0.0
    abs_d = abs(d)
    mag = (
        "large" if abs_d >= 0.8 else
        "medium" if abs_d >= 0.5 else
        "small" if abs_d >= 0.2 else
        "negligible"
    )
    return {
        "cohens_d": round(d, 4),
        "d": round(d, 4),
        "magnitude": mag,
        "mean_diff": round(float(np.mean(diff)), 4),
        "std_diff": round(std, 4),
    }


# ── Eq.25: Holm-Bonferroni correction ───────────────────────────────────────

def holm_bonferroni(
    test_results: dict[str, dict], alpha: float = 0.05
) -> dict[str, dict]:
    """Holm-Bonferroni step-down correction for multiple comparisons."""
    names = list(test_results.keys())
    raw_p = [test_results[n]["p_value"] for n in names]
    rejected, p_adj, _, _ = multipletests(raw_p, alpha=alpha, method="holm")
    for i, name in enumerate(names):
        test_results[name]["p_adjusted"] = round(float(p_adj[i]), 6)
        test_results[name]["significant_corrected"] = bool(rejected[i])
    return test_results


# ── Eq.26: Bootstrap BCa confidence interval ────────────────────────────────

def bootstrap_ci(
    data: list[float],
    confidence: float = 0.95,
    n_resamples: int = 10_000,
    method: str = "BCa",
) -> dict:
    """Bootstrap BCa 95% CI — no normality assumption needed."""
    arr = np.array(data)
    result = scipy_bootstrap(
        (arr,),
        np.mean,
        n_resamples=n_resamples,
        confidence_level=confidence,
        method=method,
        random_state=42,
    )
    return {
        "mean": round(float(np.mean(arr)), 4),
        "std": round(float(np.std(arr, ddof=1)), 4),
        "ci_low": round(float(result.confidence_interval.low), 4),
        "ci_high": round(float(result.confidence_interval.high), 4),
        "confidence": confidence,
        "n_resamples": n_resamples,
        "method": method,
    }


# ── Full statistical evaluation pipeline ─────────────────────────────────────

def full_statistical_evaluation(
    proposed_scores: list[float],
    baselines: dict[str, list[float]],
    metric_name: str = "DeceptionScore",
) -> dict:
    """
    Complete N=30 statistical pipeline:
      1. Shapiro-Wilk normality
      2. Wilcoxon signed-rank (non-parametric)
      3. Cohen's d effect size
      4. Holm-Bonferroni correction
      5. Bootstrap BCa 95% CI
    """
    proposed = np.array(proposed_scores)

    # Summary with bootstrap CI
    summary = bootstrap_ci(proposed_scores)
    summary["metric"] = metric_name

    # Normality of differences (use first baseline)
    first_bl = list(baselines.values())[0] if baselines else proposed_scores
    summary["normality"] = check_normality(proposed - np.array(first_bl))

    # Pairwise comparisons
    comparisons: dict[str, dict] = {}
    for name, bl_scores in baselines.items():
        w = run_wilcoxon_test(bl_scores, proposed_scores)
        d = compute_effect_size(proposed_scores, bl_scores)
        comparisons[name] = {**w, **d}

    # Holm-Bonferroni
    if comparisons:
        comparisons = holm_bonferroni(comparisons)

    summary["comparisons"] = comparisons
    summary["all_significant"] = all(
        c.get("significant_corrected", False) for c in comparisons.values()
    )
    return summary


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
    """[ROLE] Table III LaTeX 생성 — includes SRRP + Entropy columns."""
    path = m_dir / "table_iii_mtd_latency.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())

    # Check if MTD effectiveness columns are present
    has_srrp = any("srrp_pct" in row for row in data)

    if has_srrp:
        lines = [
            r"\begin{table}[t]",
            r"\centering",
            r"\caption{MTD Response Latency and Effectiveness by Action Type}",
            r"\label{tab:mtd-latency}",
            r"\begin{tabular}{lrrrrr@{\hspace{4pt}}r}",
            r"\toprule",
            r"Action & Count & Avg (ms) & P95 (ms) & Success & SRRP (\%) & Entropy (bits) \\",
            r"\midrule",
        ]
        for row in data:
            lines.append(
                f"{row['action_type']} & {row['count']} & "
                f"{row['avg_ms']:.1f} & {row['p95_ms']:.1f} & "
                f"{row['success_rate']*100:.1f}\\% & "
                f"{row.get('srrp_pct', 0):.1f} & "
                f"{row.get('entropy_bits', 0):.1f} \\\\"
            )
    else:
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
    """Run all statistical tests + generate LaTeX tables."""
    m_dir = metrics_dir or _METRICS_DIR
    report: dict[str, Any] = {"tests": [], "latex_files": []}

    # N=30 statistics (if available)
    stats_path = m_dir / "statistics.json"
    if stats_path.exists():
        report["n30_statistics"] = json.loads(stats_path.read_text())

    # LaTeX tables
    latex_files = generate_latex_tables(m_dir)
    report["latex_files"] = [str(p) for p in latex_files]

    # Statistics LaTeX table (if N=30 data exists)
    _write_statistics_tex(m_dir, _LATEX_DIR, report.get("n30_statistics"))

    report_path = m_dir / "statistical_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    logger.info("statistical_report_saved", path=str(report_path))
    return report


def _write_statistics_tex(
    m_dir: Path, o_dir: Path, stats: dict | None
) -> None:
    """Generate LaTeX table for N=30 statistical results."""
    if not stats or "comparisons" not in stats:
        return
    o_dir.mkdir(parents=True, exist_ok=True)
    metric = stats.get("metric", "Score")
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        f"\\caption{{Statistical Comparison of {metric} (N=30)}}",
        r"\label{tab:statistics}",
        r"\begin{tabular}{lrrrrl}",
        r"\toprule",
        r"Comparison & Mean $\Delta$ & 95\% CI & $p_{\mathrm{adj}}$ & Cohen's $d$ & Effect \\",
        r"\midrule",
        f"\\textbf{{MIRAGE-UAS}} & {stats['mean']:.4f} & "
        f"[{stats['ci_low']:.4f}, {stats['ci_high']:.4f}] & --- & --- & --- \\\\",
        r"\midrule",
    ]
    for name, cmp in stats.get("comparisons", {}).items():
        p_adj = cmp.get("p_adjusted", cmp.get("p_value", 1.0))
        p_str = f"< 0.001" if p_adj < 0.001 else f"{p_adj:.4f}"
        d_val = cmp.get("cohens_d", cmp.get("d", 0))
        lines.append(
            f"vs {name} & {cmp.get('mean_diff', 0):.4f} & "
            f"--- & {p_str} & {d_val:.3f} & {cmp['magnitude']} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    out = o_dir / "table_statistics.tex"
    out.write_text("\n".join(lines))


if __name__ == "__main__":
    report = run_all_tests()
    print(f"Report: {json.dumps(report, indent=2)}")
