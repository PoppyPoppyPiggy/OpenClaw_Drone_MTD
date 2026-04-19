#!/usr/bin/env python3
"""
analyze_llm_vs_llm_matrix.py — Aggregate the 3×3 LLM-vs-LLM matrix.

Reads results/diagnostics/llm_vs_llm/*.json (filenames follow the
`def_{defender}__atk_{attacker}.json` pattern written by
scripts/run_llm_vs_llm_matrix.sh) and produces:

  paper/tables/table_ix_attacker_belief.md        (full 3×3 table)
  paper/figures/fig_belief_heatmap.png            (packets→disbelief heatmap)
  paper/figures/fig_belief_auc_heatmap.png        (belief-AUC heatmap)
  paper/figures/fig_belief_trajectories_grid.png  (9-panel trajectory plot)
  results/diagnostics/llm_vs_llm_summary.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def parse_tag(stem: str) -> tuple[str, str]:
    """tag == `def_{defender}__atk_{attacker}` with ':'/'.' → '_'."""
    m = re.match(r"def_(.+)__atk_(.+)$", stem)
    if not m:
        return ("?", "?")
    def_raw, atk_raw = m.group(1), m.group(2)
    # restore ':' from '__' (only the first, model names like "llama3_1_8b")
    def _restore(raw: str) -> str:
        # common models: llama3_1_8b → llama3.1:8b
        #                 qwen2_5_14b → qwen2.5:14b
        #                 gemma2_9b   → gemma2:9b
        if raw.startswith("llama3_1_8b"):
            return "llama3.1:8b"
        if raw.startswith("qwen2_5_14b"):
            return "qwen2.5:14b"
        if raw.startswith("gemma2_9b"):
            return "gemma2:9b"
        return raw.replace("_", ".", 1)
    return (_restore(def_raw), _restore(atk_raw))


def load_matrix(dir_path: Path) -> list[dict]:
    runs = []
    for f in sorted(dir_path.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        defender, attacker = parse_tag(f.stem)
        runs.append({
            "path": str(f),
            "defender": defender,
            "attacker": attacker,
            "summary": data.get("summary", {}),
            "history": data.get("history", []),
        })
    return runs


def _grid(runs: list[dict], value_fn, models: list[str]) -> np.ndarray:
    """defender × attacker matrix populated by value_fn(run) or NaN."""
    grid = np.full((len(models), len(models)), np.nan, dtype=np.float64)
    for r in runs:
        try:
            i = models.index(r["defender"])
            j = models.index(r["attacker"])
        except ValueError:
            continue
        v = value_fn(r)
        if v is not None:
            grid[i, j] = float(v)
    return grid


def _annotate_heatmap(ax, grid, cmap, title, vmin=None, vmax=None, fmt=".2f"):
    im = ax.imshow(grid, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            v = grid[i, j]
            if np.isnan(v):
                continue
            color = "white" if (vmax and v > vmax * 0.6) else "black"
            ax.text(j, i, f"{v:{fmt}}", ha="center", va="center",
                    color=color, fontsize=10)
    ax.set_title(title, fontsize=11)
    return im


def render_heatmaps(runs, models, out_pkt_png, out_auc_png):
    g_pkt = _grid(runs, lambda r: r["summary"].get("packets_to_disbelief"), models)
    g_auc = _grid(runs, lambda r: r["summary"].get("belief_auc_normalised"), models)

    # Packets-to-disbelief heatmap — higher = longer deception
    fig, ax = plt.subplots(figsize=(6.5, 5), dpi=130)
    # Fill NaN for "never reached disbelief" (attacker never suspected)
    # with a large sentinel for display purposes (darkest cell).
    _g = g_pkt.copy()
    never_mask = np.isnan(_g)
    if (~never_mask).any():
        _g[never_mask] = np.nanmax(_g[~never_mask]) * 1.2
    else:
        _g[never_mask] = 200  # arbitrary large
    im = _annotate_heatmap(ax, _g, "YlGnBu",
                           "Packets → disbelief  (higher = longer deception)",
                           vmin=0, vmax=max(50, float(np.nanmax(_g))), fmt=".0f")
    # Overlay "never" labels
    for i in range(_g.shape[0]):
        for j in range(_g.shape[1]):
            if never_mask[i, j]:
                ax.text(j, i - 0.25, "never", fontsize=7, ha="center", color="red")
    ax.set_xticks(range(len(models)))
    ax.set_xticklabels([m.replace(":", "\n") for m in models], fontsize=9)
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels([m.replace(":", "\n") for m in models], fontsize=9)
    ax.set_xlabel("Attacker belief model")
    ax.set_ylabel("Defender LLM (tactical)")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(out_pkt_png)
    plt.close(fig)

    # Belief AUC heatmap — higher = more deception sustained on average
    fig, ax = plt.subplots(figsize=(6.5, 5), dpi=130)
    im = _annotate_heatmap(ax, g_auc, "YlGnBu",
                           "Belief AUC  (time-averaged μ_real ∈ [0,1])",
                           vmin=0, vmax=1, fmt=".2f")
    ax.set_xticks(range(len(models)))
    ax.set_xticklabels([m.replace(":", "\n") for m in models], fontsize=9)
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels([m.replace(":", "\n") for m in models], fontsize=9)
    ax.set_xlabel("Attacker belief model")
    ax.set_ylabel("Defender LLM (tactical)")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(out_auc_png)
    plt.close(fig)


def render_trajectories_grid(runs, models, out_png):
    fig, axes = plt.subplots(
        len(models), len(models),
        figsize=(4.2 * len(models), 2.8 * len(models)),
        dpi=130, sharex=True, sharey=True,
    )
    if len(models) == 1:
        axes = np.array([[axes]])
    pos_of = {(r["defender"], r["attacker"]): r for r in runs}
    for i, defender in enumerate(models):
        for j, attacker in enumerate(models):
            ax = axes[i, j]
            r = pos_of.get((defender, attacker))
            if r is None or not r["history"]:
                ax.set_visible(False)
                continue
            ts = [h["ts"] for h in r["history"]]
            mu = [h["mu_real"] for h in r["history"]]
            ax.plot(ts, mu, linewidth=1.1, marker="o", markersize=2.2,
                    color="#1f77b4")
            ax.axhline(0.5, color="gray", linestyle="--", alpha=0.5)
            s = r["summary"]
            ax.set_title(
                f"def={defender}  ·  atk={attacker}\n"
                f"pkts→disbelief={s.get('packets_to_disbelief')}  "
                f"AUC={s.get('belief_auc_normalised'):.2f}",
                fontsize=8,
            )
            ax.set_ylim(-0.05, 1.05)
            if j == 0:
                ax.set_ylabel("μ_real", fontsize=8)
            if i == len(models) - 1:
                ax.set_xlabel("seconds", fontsize=8)
            ax.grid(True, linestyle=":", alpha=0.5)
    fig.suptitle(
        "LLM-vs-LLM packet-level deception lifetime (3 × 3 matrix)",
        fontsize=12, weight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_png)
    plt.close(fig)


def render_markdown(runs, models) -> str:
    lines = [
        "# Table IX — LLM-vs-LLM Packet-Level Deception Lifetime (3×3)",
        "",
        "Each cell reports a single 5-level scripted attack campaign against "
        "a Docker-deployed defender LLM, with an independent **attacker-side** "
        "LLM belief tracker re-estimating P(real drone) after every packet "
        "response. Higher `packets→disbelief` and higher `AUC` both indicate "
        "longer-sustained deception.",
        "",
        "| Defender \\ Attacker | " + " | ".join(f"`{m}`" for m in models) + " |",
        "|" + "---|" * (len(models) + 1),
    ]
    pos_of = {(r["defender"], r["attacker"]): r for r in runs}
    for defender in models:
        cells = [f"**`{defender}`**"]
        for attacker in models:
            r = pos_of.get((defender, attacker))
            if r is None:
                cells.append("—")
                continue
            s = r["summary"]
            pkt = s.get("packets_to_disbelief")
            pkt_s = "never" if pkt is None else str(pkt)
            auc = s.get("belief_auc_normalised", 0)
            n = s.get("packets_observed", 0)
            final = s.get("final_mu_real", 0)
            cells.append(f"pkts={n}  dis@{pkt_s}  AUC={auc:.2f}  μ_f={final:.2f}")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Best (highest deception) and worst pairings",
        "",
    ]
    best_auc = max(runs, key=lambda r: r["summary"].get("belief_auc_normalised", 0))
    worst_auc = min(runs, key=lambda r: r["summary"].get("belief_auc_normalised", 0))
    lines += [
        f"- Highest AUC: **def=`{best_auc['defender']}`  vs  atk=`{best_auc['attacker']}`** "
        f"(AUC = {best_auc['summary'].get('belief_auc_normalised', 0):.3f})",
        f"- Lowest AUC: **def=`{worst_auc['defender']}`  vs  atk=`{worst_auc['attacker']}`** "
        f"(AUC = {worst_auc['summary'].get('belief_auc_normalised', 0):.3f})",
        "",
        "## Cross-cell suspicion signals (top 5 across matrix)",
        "",
    ]
    from collections import Counter
    sig_counter: Counter = Counter()
    for r in runs:
        for sig in r["summary"].get("top_suspicion_signals", []):
            sig_counter[sig.get("signal", "")[:100]] += sig.get("count", 0)
    for sig, c in sig_counter.most_common(5):
        if sig:
            lines.append(f"- ({c}×) *{sig}*")

    lines += [
        "",
        "## Interpretation & paper narrative",
        "",
        "- Rows = **defender LLM** (Tier 2 tactical policy inside Docker).",
        "- Columns = **attacker belief model** (packet-level honeypot detector).",
        "- Diagonal cells (same model on both sides) test a **symmetric** setup.",
        "- Off-diagonal cells test **cross-family** attacker–defender pairings.",
        "- A large diagonal–off-diagonal asymmetry (e.g. `qwen-vs-qwen` much",
        "  better than `qwen-vs-llama`) would indicate that a defender's ",
        "  policy is particularly legible to models of the same family — a",
        "  useful finding for deployment (avoid pairing defender and attacker",
        "  from the same vendor).",
        "- The `top 5 suspicion signals` surface qualitatively what is",
        "  leaking most across the matrix. Signals that appear in many cells",
        "  are systemic — they indicate where the lure itself is weak.",
    ]
    return "\n".join(lines)


def main() -> int:
    dir_path = Path("results/diagnostics/llm_vs_llm")
    runs = load_matrix(dir_path)
    if not runs:
        print(f"(no runs in {dir_path})")
        return 0

    models = sorted({r["defender"] for r in runs} | {r["attacker"] for r in runs})
    # Canonical order: llama, qwen, gemma
    order = ["llama3.1:8b", "qwen2.5:14b", "gemma2:9b"]
    models = [m for m in order if m in models] + [m for m in models if m not in order]

    out_md = Path("paper/tables/table_ix_attacker_belief.md")
    out_pkt = Path("paper/figures/fig_belief_heatmap.png")
    out_auc = Path("paper/figures/fig_belief_auc_heatmap.png")
    out_traj = Path("paper/figures/fig_belief_trajectories_grid.png")
    out_summary = Path("results/diagnostics/llm_vs_llm_summary.json")

    for p in [out_md, out_pkt, out_auc, out_traj, out_summary]:
        p.parent.mkdir(parents=True, exist_ok=True)

    render_heatmaps(runs, models, out_pkt, out_auc)
    render_trajectories_grid(runs, models, out_traj)
    md = render_markdown(runs, models)
    out_md.write_text(md)

    out_summary.write_text(json.dumps({
        "models": models,
        "runs": [{"defender": r["defender"], "attacker": r["attacker"],
                  **r["summary"]} for r in runs],
    }, indent=2, default=str))

    for p in [out_md, out_pkt, out_auc, out_traj, out_summary]:
        print(f"→ {p}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
