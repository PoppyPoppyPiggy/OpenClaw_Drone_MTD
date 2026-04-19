#!/usr/bin/env python3
"""
analyze_deception_lifetime.py — Summarise attacker-belief trajectories

Reads results/diagnostics/attacker_belief_*.json (produced by attacker_sim
with the LLM belief tracker enabled) and produces:

  results/diagnostics/deception_lifetime_summary.json
  paper/tables/table_ix_attacker_belief.md
  paper/figures/fig_attacker_belief_trajectory.png (per-run line plot)

[USAGE]
    python scripts/analyze_deception_lifetime.py
    python scripts/analyze_deception_lifetime.py --diag-dir results/diagnostics
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_belief_files(dir_path: Path) -> list[dict]:
    runs: list[dict] = []
    for f in sorted(dir_path.glob("attacker_belief_*.json")):
        try:
            data = json.loads(f.read_text())
            runs.append({"path": str(f), **data})
        except Exception:
            continue
    return runs


def render_trajectory_plot(runs: list[dict], out_path: Path) -> None:
    if not runs:
        return
    fig, ax = plt.subplots(figsize=(9, 5), dpi=130)
    colors = plt.cm.tab10.colors
    for i, r in enumerate(runs):
        hist = r.get("history", [])
        if not hist:
            continue
        ts = [h["ts"] for h in hist]
        mu = [h["mu_real"] for h in hist]
        label = (Path(r["path"]).stem
                 .replace("attacker_belief_", "")) + \
                f"  [model={r.get('summary', {}).get('model', '?')}]"
        ax.plot(ts, mu, marker="o", markersize=3, linewidth=1.2,
                color=colors[i % len(colors)], label=label[:40])
    ax.axhline(0.5, color="gray", linestyle="--", alpha=0.6,
               label="disbelief threshold (μ_real = 0.5)")
    ax.set_xlabel("seconds since campaign start")
    ax.set_ylabel("attacker μ_real (P(target is real drone))")
    ax.set_title("Attacker-belief trajectory over the L0-L4 campaign",
                 fontsize=11)
    ax.set_ylim(-0.05, 1.05)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="lower left", fontsize=7, framealpha=0.85)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def render_markdown(runs: list[dict]) -> str:
    lines = [
        "# Table IX — Attacker-Belief & Deception Lifetime (LLM Attacker)",
        "",
        "Per-run summary of the LLM-based attacker-belief tracker appended",
        "to the scripted L0-L4 campaign. The tracker re-estimates P(real",
        "drone | observations) after every packet response, producing an",
        "auditable belief trajectory. 'Packets to disbelief' is the first",
        "packet index where μ_real drops below 0.5. 'Belief AUC' is the",
        "time-normalised area under the belief curve (1.0 = perfect",
        "deception maintained throughout the run; 0.0 = attacker detected",
        "honey immediately).",
        "",
        "| Run | Model | Packets | Packets→disbelief | Final μ_real | Belief AUC | Top suspicion signal |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        s = r.get("summary", {})
        model = s.get("model", "?")
        n_pkt = s.get("packets_observed", 0)
        disbel = s.get("packets_to_disbelief", None)
        disbel_s = str(disbel) if disbel is not None else "never"
        mu_final = s.get("final_mu_real", 0.0)
        auc = s.get("belief_auc_normalised", 0.0)
        top = s.get("top_suspicion_signals", [])
        top_sig = top[0]["signal"][:50] + f" (×{top[0]['count']})" if top else "—"
        lines.append(
            f"| `{Path(r['path']).stem}` | `{model}` | {n_pkt} | "
            f"{disbel_s} | {mu_final:.3f} | {auc:.3f} | {top_sig} |"
        )

    lines += [
        "",
        "## Interpretation",
        "",
        "- **Packets→disbelief** answers \"how many attacker packets can the",
        "  defender sustain before the attacker suspects a honeydrone?\".",
        "  `never` ⇒ defender maintained deception through the entire run.",
        "- **Belief AUC** is the integral, so even slow decay still gives",
        "  partial credit — useful when the run is cut short before the",
        "  belief fully collapses.",
        "- **Top suspicion signal** shows the most-frequent qualitative cue",
        "  the LLM attacker identified; this is a richer signal than raw",
        "  scalar metrics and is worth citing qualitatively in the paper.",
        "",
        "## Scope notes",
        "",
        "- This is the **Option B proof-of-concept**: the tracker observes",
        "  the scripted L0-L4 attacker's packet stream; it does NOT drive",
        "  the attacker's action selection. Full symmetric LLM-vs-LLM is",
        "  future work (see `AttackerPolicy::LLMAttackerPolicy`).",
        "- Belief updates run as fire-and-forget async tasks alongside the",
        "  scripted loop; a small number of observations may not finish if",
        "  the campaign terminates early (timeout logged in summary).",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diag-dir", default="results/diagnostics")
    parser.add_argument("--output-md",
                        default="paper/tables/table_ix_attacker_belief.md")
    parser.add_argument("--output-fig",
                        default="paper/figures/fig_attacker_belief_trajectory.png")
    parser.add_argument("--output-json",
                        default="results/diagnostics/deception_lifetime_summary.json")
    args = parser.parse_args()

    runs = load_belief_files(Path(args.diag_dir))
    if not runs:
        print(f"(no attacker_belief_*.json files in {args.diag_dir})")
        return 0

    # Markdown table
    md = render_markdown(runs)
    out_md = Path(args.output_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md)

    # PNG trajectory
    out_fig = Path(args.output_fig)
    out_fig.parent.mkdir(parents=True, exist_ok=True)
    render_trajectory_plot(runs, out_fig)

    # Consolidated summary JSON
    doc = {"runs": [{"path": r["path"], "summary": r.get("summary", {})}
                    for r in runs]}
    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(doc, indent=2))

    print(f"→ {out_md}")
    print(f"→ {out_fig}")
    print(f"→ {out_json}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
