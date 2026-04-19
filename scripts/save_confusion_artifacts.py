#!/usr/bin/env python3
"""
save_confusion_artifacts.py — Save confusion matrices as .npz + PNG heatmap

Saves one file per (model, seed) combination in results/llm_v2/.
Also produces a 3x3 grid heatmap showing all runs at once.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def save_one(run_path: Path, out_dir: Path) -> dict | None:
    data = json.loads(run_path.read_text())
    cm = data.get("policy_metrics", {}).get("phase_skill_confusion_matrix")
    labels = data.get("policy_metrics", {}).get("phase_skill_confusion_labels", {})
    if cm is None:
        return None

    model = data.get("model", "unknown")
    seed = data.get("seed", 0)
    phases = labels.get("phases", ["RECON", "EXPLOIT", "PERSIST", "EXFIL"])
    skills = [s.replace("proactive_", "") for s in labels.get("skills", [])]
    cm_arr = np.asarray(cm, dtype=np.int64)

    tag = safe_name(f"{model}_seed{seed}")
    npz_path = out_dir / f"phase_skill_cm_{tag}.npz"
    np.savez(
        npz_path,
        confusion=cm_arr,
        phases=np.array(phases),
        skills=np.array(skills),
        model=np.array(model),
        seed=np.array(seed),
    )

    # Normalised view (row-stochastic)
    row_tot = cm_arr.sum(axis=1, keepdims=True)
    row_tot_safe = np.where(row_tot == 0, 1, row_tot)
    pct = cm_arr / row_tot_safe

    png_path = out_dir / f"phase_skill_cm_{tag}.png"
    fig, ax = plt.subplots(figsize=(7, 4), dpi=120)
    im = ax.imshow(pct, cmap="Blues", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(skills)))
    ax.set_xticklabels(skills, rotation=30, ha="right")
    ax.set_yticks(range(len(phases)))
    ax.set_yticklabels(phases)
    ax.set_xlabel("Skill")
    ax.set_ylabel("Attacker phase")
    ax.set_title(f"{model}  seed={seed}  (row-normalised %)")
    for i in range(pct.shape[0]):
        for j in range(pct.shape[1]):
            color = "white" if pct[i, j] > 0.5 else "black"
            ax.text(j, i, f"{pct[i, j]*100:.0f}", ha="center", va="center",
                    color=color, fontsize=9)
    fig.colorbar(im, ax=ax, label="fraction of phase's calls")
    fig.tight_layout()
    fig.savefig(png_path)
    plt.close(fig)

    return {
        "tag": tag,
        "model": model,
        "seed": seed,
        "npz": str(npz_path),
        "png": str(png_path),
        "matrix_shape": list(cm_arr.shape),
    }


def save_grid(out_dir: Path, records: list[dict]) -> str | None:
    """All 9 runs on one 3x3 grid heatmap."""
    if not records:
        return None
    # Group by model, order seeds
    by_model: dict[str, list[dict]] = {}
    for r in records:
        by_model.setdefault(r["model"], []).append(r)
    for m in by_model:
        by_model[m].sort(key=lambda x: x["seed"])

    models = sorted(by_model.keys())
    max_seeds = max(len(v) for v in by_model.values())
    fig, axes = plt.subplots(
        len(models), max_seeds, figsize=(5 * max_seeds, 3.2 * len(models)),
        dpi=120, sharex=True, sharey=True,
    )
    if len(models) == 1:
        axes = np.array([axes])
    if max_seeds == 1:
        axes = axes.reshape(-1, 1)

    for i, model in enumerate(models):
        for j, rec in enumerate(by_model[model]):
            npz = np.load(rec["npz"])
            cm = npz["confusion"]
            row_tot = cm.sum(axis=1, keepdims=True)
            pct = cm / np.where(row_tot == 0, 1, row_tot)
            phases = list(npz["phases"])
            skills = list(npz["skills"])
            ax = axes[i, j]
            im = ax.imshow(pct, cmap="Blues", aspect="auto", vmin=0, vmax=1)
            ax.set_title(f"{model} seed={rec['seed']}", fontsize=9)
            ax.set_xticks(range(len(skills)))
            ax.set_xticklabels(skills, rotation=30, ha="right", fontsize=7)
            ax.set_yticks(range(len(phases)))
            ax.set_yticklabels(phases, fontsize=8)
            for a in range(pct.shape[0]):
                for b in range(pct.shape[1]):
                    color = "white" if pct[a, b] > 0.5 else "black"
                    ax.text(b, a, f"{pct[a, b]*100:.0f}", ha="center", va="center",
                            color=color, fontsize=7)
    fig.suptitle("Phase × Skill confusion (V2, row-normalised %) — all 9 runs",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    grid_path = out_dir / "phase_skill_cm_grid.png"
    fig.savefig(grid_path)
    plt.close(fig)
    return str(grid_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-dir", default="results/llm_v2")
    args = parser.parse_args()
    runs_dir = Path(args.llm_dir)

    records = []
    for f in sorted(runs_dir.glob("*_seed*.json")):
        if f.name == "summary.json":
            continue
        rec = save_one(f, runs_dir)
        if rec:
            records.append(rec)
            print(f"saved {rec['tag']}: {rec['npz']} + {rec['png']}")
    grid = save_grid(runs_dir, records)
    if grid:
        print(f"grid heatmap: {grid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
