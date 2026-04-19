#!/usr/bin/env python3
"""
compare_v1_v2.py — Compare V1 vs V2 prompt runs head-to-head

Produces a markdown diff table showing:
  - skill-distribution shift (per-skill Δ%)
  - entropy improvement
  - phase-preference uniqueness
  - avg_R delta (expected flat per the env-heuristic finding)

V1 dir default:  results/llm_multi_seed_v1
V2 dir default:  results/llm_v2

[OUTPUT]
    docs/prompt_ablation.md
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def load_by_model(dir_path: Path) -> dict[str, list[dict]]:
    by_model: dict[str, list[dict]] = {}
    if not dir_path.exists():
        return by_model
    for f in sorted(dir_path.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        m = d.get("model")
        if m:
            by_model.setdefault(m, []).append(d)
    return by_model


def entropy_bits(dist_pct: dict[str, float]) -> float:
    total = sum(dist_pct.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for v in dist_pct.values():
        p = v / total
        if p > 0:
            h -= p * math.log2(p)
    return h


def summarise(runs: list[dict]) -> dict:
    if not runs:
        return {}
    def mean(key: str) -> float:
        return float(sum(r.get(key, 0) or 0 for r in runs) / len(runs))
    dist = {}
    for r in runs:
        for k, v in (r.get("action_distribution") or {}).items():
            dist[k] = dist.get(k, 0.0) + float(v)
    # average the distribution
    dist = {k: round(v / len(runs), 2) for k, v in dist.items()}
    phase_uniq = [len(set((r.get("phase_preference") or {}).values())) for r in runs]
    return {
        "n_runs": len(runs),
        "avg_reward": round(mean("avg_reward"), 3),
        "avg_p_real": round(mean("avg_p_real"), 4),
        "survival_rate": round(mean("survival_rate"), 4),
        "skill_distribution_pct": dist,
        "entropy_bits": round(entropy_bits(dist), 3),
        "phase_uniq_mean": round(sum(phase_uniq) / len(phase_uniq), 2),
    }


def render(v1: dict[str, list[dict]], v2: dict[str, list[dict]]) -> str:
    models = sorted(set(list(v1.keys()) + list(v2.keys())))
    lines = [
        "# Prompt V1 → V2 — Ablation Report",
        "",
        "Measurement of the mode-collapse mitigation in V2 (A-E keying + "
        "HARD RULES + last_action feedback + repeat_penalty + temp 0.9).",
        "",
        "## Headline",
        "",
        "| Model | metric | V1 | V2 | Δ |",
        "|---|---|---|---|---|",
    ]
    for m in models:
        s1 = summarise(v1.get(m, []))
        s2 = summarise(v2.get(m, []))
        if not s1 or not s2:
            continue
        def row(metric: str, key: str, fmt: str = "{:.3f}", better: str = "higher") -> None:
            v1_v = s1.get(key)
            v2_v = s2.get(key)
            if v1_v is None or v2_v is None:
                return
            delta = v2_v - v1_v
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
            lines.append(
                f"| `{m}` | {metric} | {fmt.format(v1_v)} | "
                f"{fmt.format(v2_v)} | {arrow} {delta:+.3f} |"
            )
        row("H(skill) bits", "entropy_bits")
        row("phase-uniq /4", "phase_uniq_mean", "{:.2f}")
        row("avg_R", "avg_reward", "{:.2f}")
        row("avg_p_real", "avg_p_real", "{:.3f}")
        row("survival", "survival_rate", "{:.2f}")

    lines += [
        "",
        "## Skill distribution diff (V2 − V1, absolute percentage points)",
        "",
    ]
    for m in models:
        s1 = summarise(v1.get(m, []))
        s2 = summarise(v2.get(m, []))
        if not s1 or not s2:
            continue
        lines += [f"### `{m}`", "", "| skill | V1 % | V2 % | Δ pp |", "|---|---|---|---|"]
        all_skills = sorted(set(list(s1["skill_distribution_pct"].keys()) +
                                 list(s2["skill_distribution_pct"].keys())))
        for k in all_skills:
            v1v = s1["skill_distribution_pct"].get(k, 0.0)
            v2v = s2["skill_distribution_pct"].get(k, 0.0)
            d = v2v - v1v
            arrow = "↑" if d > 0 else ("↓" if d < 0 else "=")
            lines.append(f"| `{k}` | {v1v:.1f} | {v2v:.1f} | {arrow} {d:+.1f} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-dir", default="results/llm_multi_seed_v1")
    parser.add_argument("--v2-dir", default="results/llm_v2")
    parser.add_argument("--output", default="docs/prompt_ablation.md")
    args = parser.parse_args()

    v1 = load_by_model(Path(args.v1_dir))
    v2 = load_by_model(Path(args.v2_dir))
    if not v1 and not v2:
        print("(no data)")
        return 0
    md = render(v1, v2)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"→ {out}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
