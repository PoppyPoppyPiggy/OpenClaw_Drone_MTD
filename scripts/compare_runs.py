#!/usr/bin/env python3
"""
compare_runs.py — Side-by-side comparison of multiple experiment runs.

Consumes the results.<policy>/ directories produced by
scripts/run_all_policies.sh and prints a Markdown table of
key metrics (MTD count, avg μ_A / confusion, CTI yield, signaling
mixing summary). Also emits results/policy_sweep.json.

Usage:
    python3 scripts/compare_runs.py results.dqn results.signaling_eq results.hybrid
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def summarize_run(run_dir: Path) -> dict:
    metrics_dir = run_dir / "metrics"
    out = {
        "run": run_dir.name,
        "policy": run_dir.name.replace("results.", ""),
        "mtd_triggers": 0,
        "avg_confusion": None,
        "cti_unique_ttps": 0,
        "signaling_mixing": None,
        "decisions": 0,
    }
    if not metrics_dir.exists():
        out["status"] = "missing"
        return out

    # MTD
    mtd = _load_json(metrics_dir / "live_mtd_results.json", [])
    out["mtd_triggers"] = len(mtd) if isinstance(mtd, list) else 0

    # Confusion (avg over per-drone files)
    confusions = []
    for p in metrics_dir.glob("confusion_honey_*.json"):
        data = _load_json(p, {})
        v = data.get("avg_confusion_score")
        if isinstance(v, (int, float)):
            confusions.append(v)
    out["avg_confusion"] = round(mean(confusions), 4) if confusions else None

    # CTI
    all_ttps = set()
    for p in metrics_dir.glob("cti_honey_*.json"):
        data = _load_json(p, {})
        for t in data.get("unique_ttps", []):
            all_ttps.add(t)
    out["cti_unique_ttps"] = len(all_ttps)

    # Decisions
    dec_total = 0
    for p in metrics_dir.glob("decisions_honey_*.json"):
        data = _load_json(p, [])
        if isinstance(data, list):
            dec_total += len(data)
    out["decisions"] = dec_total

    # Signaling Game mixing (aggregate across drones)
    mixings = []
    for p in metrics_dir.glob("signaling_game_honey_*.json"):
        data = _load_json(p, {})
        mx = data.get("mixing")
        if isinstance(mx, list):
            mixings.append(mx)
    if mixings:
        # Element-wise average of mixing distributions
        n_skills = len(mixings[0])
        avg_mix = [round(mean(m[i] for m in mixings), 4) for i in range(n_skills)]
        out["signaling_mixing"] = avg_mix

    out["status"] = "ok"
    return out


def print_table(rows: list[dict]) -> None:
    print()
    print("| Policy | MTD triggers | Avg μ_A (confusion) | Unique TTPs | Decisions | Signaling mixing |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        mix = r["signaling_mixing"]
        mix_str = ",".join(f"{v:.2f}" for v in mix) if mix else "—"
        conf = f"{r['avg_confusion']:.4f}" if r["avg_confusion"] is not None else "—"
        print(f"| {r['policy']} | {r['mtd_triggers']} | {conf} | "
              f"{r['cti_unique_ttps']} | {r['decisions']} | {mix_str} |")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="results.<policy> directories")
    ap.add_argument("--output", default="results/policy_sweep.json")
    args = ap.parse_args()

    rows = [summarize_run(Path(d)) for d in args.runs]
    print_table(rows)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2))
    print(f"JSON → {out_path}")


if __name__ == "__main__":
    main()
