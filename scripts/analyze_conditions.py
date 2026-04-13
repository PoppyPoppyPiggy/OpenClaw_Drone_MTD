#!/usr/bin/env python3
"""
analyze_conditions.py — 4가지 방어 조건 결과 통합 분석
Hou 2025 하이브리드 방어 비교 테이블 생성

Reference: Hou et al. (2025), Computers 14(12):513
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from evaluation.hybrid_defense import build_hybrid_defense_table


def load_condition(cond_dir: str) -> list[dict]:
    """Load trial results from a condition directory."""
    sessions: list[dict] = []
    for f in sorted(glob.glob(f"{cond_dir}/trial_*.json")):
        try:
            with open(f) as fp:
                data = json.load(fp)
            sessions.append({
                "breached": data.get("breach_rate", 0) > 0.5,
                "service_disrupted": data.get("service_disrupted", False),
                "mtd_cost": data.get("avg_mtd_cost", 0.0),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return sessions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze 4 defense conditions for Hou 2025 table"
    )
    parser.add_argument(
        "--conditions-dir",
        default="results/conditions",
        help="Directory containing condition subdirectories",
    )
    parser.add_argument(
        "--output",
        default="results/metrics/hybrid_defense_table.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    table = build_hybrid_defense_table(
        sessions_no_defense=load_condition(f"{args.conditions_dir}/no_defense"),
        sessions_mtd_only=load_condition(f"{args.conditions_dir}/mtd_only"),
        sessions_deception_only=load_condition(f"{args.conditions_dir}/deception_only"),
        sessions_mirage=load_condition(f"{args.conditions_dir}/mirage_full"),
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(table, f, indent=2)

    print("\n=== Hybrid Defense Comparison Table (Hou 2025) ===")
    print(f"{'Condition':<30s} {'P_attack':>9s} {'Avail':>9s} {'Cost':>9s} {'Payoff':>9s}  N")
    print("-" * 78)
    for row in table["table"]:
        print(
            f"  {row['condition']:<28s} "
            f"{row['p_attack']:>8.4f} "
            f"{row['availability']:>8.4f} "
            f"{row['defense_cost']:>8.4f} "
            f"{row['payoff']:>8.4f}  "
            f"{row['n_sessions']}"
        )
    print(f"\nCitation: {table['citation']}")


if __name__ == "__main__":
    main()
