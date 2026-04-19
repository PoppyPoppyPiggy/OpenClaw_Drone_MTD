#!/usr/bin/env python3
"""
make_paper_figures.py — Generate 3 paper-figure mockups

Saves to results/llm_v2/figures/:
  fig_a_skill_dist_v1_vs_v2.png
  fig_b_phase_skill_heatmap_v2.png
  fig_c_reward_vs_belief_scatter.png

All figures use a consistent palette and include captions.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SKILL_PRETTY = [
    ("proactive_statustext", "statustext"),
    ("proactive_flight_sim", "flight_sim"),
    ("proactive_ghost_port", "ghost_port"),
    ("proactive_reboot", "reboot_sim"),
    ("proactive_fake_key", "credential_leak"),
]

# Colour palette (colour-blind safe-ish)
PALETTE = {
    "llama3.1:8b": "#1f77b4",
    "qwen2.5:14b": "#ff7f0e",
    "gemma2:9b": "#2ca02c",
}


def load_runs(dir_path: Path) -> list[dict]:
    if not dir_path.exists():
        return []
    runs = []
    for f in sorted(dir_path.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        try:
            runs.append(json.loads(f.read_text()))
        except Exception:
            pass
    return runs


def per_model_mean_dist(runs: list[dict]) -> dict[str, dict[str, float]]:
    """Average the action_distribution across seeds per model."""
    by_model: dict[str, list[dict]] = {}
    for r in runs:
        by_model.setdefault(r["model"], []).append(r)
    out: dict[str, dict[str, float]] = {}
    for m, rs in by_model.items():
        avg: dict[str, float] = {}
        for s_raw, s_nice in SKILL_PRETTY:
            vals = [float(r.get("action_distribution", {}).get(s_raw, 0.0)) for r in rs]
            avg[s_nice] = float(sum(vals) / len(vals)) if vals else 0.0
        out[m] = avg
    return out


def fig_a_skill_dist(v1_runs, v2_runs, out_path: Path) -> None:
    v1 = per_model_mean_dist(v1_runs)
    v2 = per_model_mean_dist(v2_runs)
    skills = [s_nice for _, s_nice in SKILL_PRETTY]
    models = [m for m in ["llama3.1:8b", "qwen2.5:14b", "gemma2:9b"]
              if m in v2 or m in v1]

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), dpi=120, sharey=True)
    width = 0.22
    x = np.arange(len(skills))
    for idx, (title, data) in enumerate([("V1 prompt (mode collapse)", v1),
                                           ("V2 prompt (mitigated)", v2)]):
        ax = axes[idx]
        for i, m in enumerate(models):
            vals = [data.get(m, {}).get(s, 0.0) for s in skills]
            ax.bar(x + (i - 1) * width, vals, width,
                   label=m, color=PALETTE.get(m, None), edgecolor="black",
                   linewidth=0.4)
        ax.set_xticks(x)
        ax.set_xticklabels(skills, rotation=20, ha="right", fontsize=9)
        ax.set_ylabel("skill-use share (%)" if idx == 0 else "")
        ax.set_title(title, fontsize=11)
        ax.set_ylim(0, 105)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        if idx == 0:
            ax.legend(loc="upper right", fontsize=8)
    fig.suptitle("Figure A — Skill execution distribution: V1 vs V2 "
                 "(3-model mean)", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_path)
    plt.close(fig)


def fig_b_heatmap(v2_runs, out_path: Path) -> None:
    # One row per model — average the 3 confusion matrices within each model
    by_model: dict[str, list[np.ndarray]] = {}
    phases: list[str] = []
    skills: list[str] = []
    for r in v2_runs:
        cm = r.get("policy_metrics", {}).get("phase_skill_confusion_matrix")
        lab = r.get("policy_metrics", {}).get("phase_skill_confusion_labels", {})
        if cm is None:
            continue
        by_model.setdefault(r["model"], []).append(np.asarray(cm, dtype=np.float64))
        if not phases:
            phases = lab.get("phases", [])
        if not skills:
            skills = [s.replace("proactive_", "") for s in lab.get("skills", [])]

    models = ["llama3.1:8b", "qwen2.5:14b", "gemma2:9b"]
    models = [m for m in models if m in by_model]

    fig, axes = plt.subplots(1, len(models), figsize=(4 * len(models), 3.5),
                             dpi=120, sharey=True)
    if len(models) == 1:
        axes = [axes]
    for i, m in enumerate(models):
        stacked = np.stack(by_model[m], axis=0)
        mean_cm = stacked.mean(axis=0)
        row = mean_cm.sum(axis=1, keepdims=True)
        pct = mean_cm / np.where(row == 0, 1, row)
        ax = axes[i]
        im = ax.imshow(pct, cmap="Blues", aspect="auto", vmin=0, vmax=1)
        ax.set_xticks(range(len(skills)))
        ax.set_xticklabels(skills, rotation=30, ha="right", fontsize=8)
        if i == 0:
            ax.set_yticks(range(len(phases)))
            ax.set_yticklabels(phases, fontsize=9)
        else:
            ax.set_yticks(range(len(phases)))
            ax.set_yticklabels([""] * len(phases))
        ax.set_title(f"{m}\nmean of 3 seeds", fontsize=10)
        for a in range(pct.shape[0]):
            for b in range(pct.shape[1]):
                color = "white" if pct[a, b] > 0.5 else "black"
                ax.text(b, a, f"{pct[a, b]*100:.0f}",
                        ha="center", va="center", color=color, fontsize=8)
    fig.suptitle("Figure B — Phase × Skill confusion (V2, row-normalised %)",
                 fontsize=12)
    fig.colorbar(im, ax=axes, fraction=0.03, pad=0.02,
                 label="fraction within phase")
    fig.tight_layout(rect=[0, 0, 0.95, 0.94])
    fig.savefig(out_path)
    plt.close(fig)


def fig_c_scatter(v1_runs, v2_runs, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 5), dpi=120)
    for runs, marker, label_suffix, alpha in [
        (v1_runs, "o", "V1", 0.9),
        (v2_runs, "^", "V2", 0.9),
    ]:
        for r in runs:
            m = r.get("model", "?")
            x = r.get("avg_reward", 0.0)
            y = r.get("avg_p_real", 0.0)
            ax.scatter(x, y, s=110, marker=marker,
                       color=PALETTE.get(m, "black"), edgecolor="black",
                       alpha=alpha,
                       label=f"{m} [{label_suffix}]")
    # Build deduped legend
    handles, labels = ax.get_legend_handles_labels()
    seen = {}
    for h, l in zip(handles, labels):
        seen[l] = h
    ax.legend(list(seen.values()), list(seen.keys()),
              loc="lower left", fontsize=8, frameon=True)
    ax.set_xlabel("avg episode reward (DeceptionEnv, heuristic)")
    ax.set_ylabel("avg P(real | obs)  (belief maintained)")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.set_title("Figure C — Reward vs Belief: V1 (circle) vs V2 (triangle)",
                 fontsize=11)
    ax.axhline(0.5, color="gray", linestyle="--", alpha=0.4,
               label="belief threshold (0.5)")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def main() -> int:
    out_dir = Path("results/llm_v2/figures")
    out_dir.mkdir(parents=True, exist_ok=True)
    v1_runs = load_runs(Path("results/llm_multi_seed_v1"))
    v2_runs = load_runs(Path("results/llm_v2"))

    fig_a_skill_dist(v1_runs, v2_runs, out_dir / "fig_a_skill_dist_v1_vs_v2.png")
    fig_b_heatmap(v2_runs, out_dir / "fig_b_phase_skill_heatmap_v2.png")
    fig_c_scatter(v1_runs, v2_runs, out_dir / "fig_c_reward_vs_belief_scatter.png")

    print("=== Figures written ===")
    for p in sorted(out_dir.glob("*.png")):
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
