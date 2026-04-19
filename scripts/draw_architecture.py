#!/usr/bin/env python3
"""
draw_architecture.py — MIRAGE-UAS 3-tier architecture diagram (Docker edition).
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch


OUT = Path("paper/figures/fig_architecture.png")
OUT.parent.mkdir(parents=True, exist_ok=True)


def box(ax, x, y, w, h, text, color="#dfe7f5", edge="#1f4e79",
        fontsize=9, weight="normal"):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.06",
        linewidth=1.4, facecolor=color, edgecolor=edge,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h / 2, text,
            ha="center", va="center", fontsize=fontsize, weight=weight)


def arrow(ax, xy1, xy2, label="", color="#555", style="->,head_length=6,head_width=4",
          fontsize=7, curve=0.0):
    a = FancyArrowPatch(xy1, xy2,
                        arrowstyle=style, color=color, linewidth=1.2,
                        connectionstyle=f"arc3,rad={curve}")
    ax.add_patch(a)
    if label:
        mx = (xy1[0] + xy2[0]) / 2
        my = (xy1[1] + xy2[1]) / 2
        ax.text(mx, my, label, fontsize=fontsize, ha="center",
                va="center", color=color,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none",
                          alpha=0.85))


def main() -> None:
    fig, ax = plt.subplots(figsize=(12.5, 8.5), dpi=150)
    ax.set_xlim(0, 12.5)
    ax.set_ylim(0, 10)
    ax.axis("off")

    ax.text(6.25, 9.6, "MIRAGE-UAS 3-Tier Architecture  (Docker deployment)",
            ha="center", va="center", fontsize=14, weight="bold")

    # ── Tier 1 GCS Docker container ──────────────────────
    ax.text(0.6, 8.7, "Tier 1 — GCS Strategic Agent  (Docker: mirage-gcs)",
            fontsize=11, weight="bold", color="#1f4e79")
    # Operator — outside Docker
    box(ax, 0.5, 7.3, 2.3, 1.1,
        "Human operator\n(PowerShell / SSH)", color="#e8f1fb")
    # GCS container
    box(ax, 3.5, 7.3, 3.8, 1.1,
        "strategic_agent.py\nOpenClaw-inspired,\nqwen2.5:14b via Ollama",
        color="#cfe1f5", edge="#1f4e79")
    # Ollama
    box(ax, 9.5, 7.3, 2.5, 1.1,
        "Ollama  (Windows host)\nllama3.1 / qwen2.5 / gemma2",
        color="#fff3c8", edge="#b88a00")

    arrow(ax, (2.8, 7.85), (3.5, 7.85), label="chat / CLI")
    arrow(ax, (7.3, 7.85), (9.5, 7.85),
          label="prompt (HTTP)\nvia 172.23.240.1:11434", fontsize=6.5)
    arrow(ax, (9.5, 7.60), (7.3, 7.60), label="action + skill_bias",
          curve=-0.1, color="#1f77b4")

    # Dashed Docker boundary around GCS
    from matplotlib.patches import Rectangle
    gcs_frame = Rectangle((3.4, 7.2), 4.0, 1.3, linewidth=1.2,
                          edgecolor="#444", facecolor="none", linestyle="--")
    ax.add_patch(gcs_frame)
    ax.text(3.42, 8.48, "Docker: mirage-gcs", fontsize=7, color="#444")

    # Strategic directive arrow (Tier 1 → Tier 2)
    arrow(ax, (5.4, 7.3), (5.4, 6.4),
          label="strategic_directive  UDP 19995\n(via Docker DNS cc_honey_0N)",
          color="#c44e52", fontsize=7)

    # ── Tier 2 honey containers ──────────────────────────
    ax.text(0.6, 6.35, "Tier 2 — Tactical LLM Defender  (3 × cc_honey_0N Docker)",
            fontsize=11, weight="bold", color="#1f4e79")

    y_t2 = 4.7
    for i, x0 in enumerate([0.4, 4.4, 8.4]):
        # Container dashed outline
        cont_frame = Rectangle((x0, y_t2 - 0.15), 3.7, 2.0,
                               linewidth=1.2, edgecolor="#444",
                               facecolor="none", linestyle="--")
        ax.add_patch(cont_frame)
        ax.text(x0 + 0.05, y_t2 + 1.68,
                f"Docker: cc_honey_0{i+1}  (mirage-honeydrone)",
                fontsize=7, color="#444")
        box(ax, x0 + 0.1, y_t2 + 0.9, 3.5, 0.55,
            "OpenClawAgent  (per-packet rule-based hot path)",
            color="#e7f7e0")
        box(ax, x0 + 0.1, y_t2 + 0.25, 3.5, 0.55,
            "LLMTacticalAgent  (v2 prompt, 8s proactive loop)\n"
            "+ OpenClawService (Tier 3 lure, HTUR tracker)",
            color="#cfe1f5")

    # Ollama HTTP arrows from each honey
    for x0 in (2.2, 6.2, 10.2):
        arrow(ax, (x0 + 0.05, 5.2), (9.9, 6.7),
              color="#888", curve=-0.1)

    # ── External threat surface ──────────────────────────
    ax.text(0.6, 4.3, "External threat surface (honey_net 172.30.0.0/24)",
            fontsize=10, weight="bold", color="#1f4e79")
    box(ax, 0.4, 2.9, 3.4, 1.1,
        "attacker_sim.py\nL0-L4 campaign\n(nmap / MAVLink / HTTP /\n WS / SSH, credential reuse)",
        color="#ffd9c0", edge="#c44e52")
    box(ax, 4.4, 2.9, 3.7, 1.1,
        "honey_net\nMAVLink 14551-53 / HTTP 8081-83\nWS 18789-91 / RTSP 8554-56",
        color="#f7f0e0")
    box(ax, 8.7, 2.9, 3.4, 1.1,
        "fcu_honey_0N\nArduPilot SITL-compatible\nTCP 5760 (internal)",
        color="#e6e6fa", edge="#555")

    arrow(ax, (3.8, 3.45), (4.4, 3.45), label="probe/exploit",
          color="#c44e52")
    arrow(ax, (8.1, 3.45), (8.7, 3.45), label="telemetry", color="#888")
    for x0 in (2.2, 6.2, 10.2):
        arrow(ax, (x0 + 0.1, 4.0), (x0 - 0.3, 4.6),
              color="#888", curve=0.1)

    # ── UDP event bus ────────────────────────────────────
    box(ax, 0.4, 1.35, 11.8, 1.2,
        "UDP Event Bus (JSON, fire-and-forget):\n"
        "19995 strategic_directive (Tier 1→2, cross-container)  |  "
        "19996 packet_events  |  19997 state_diff\n"
        "19998 agent_decisions (every LLM skill pick)  |  19999 state_broadcast",
        color="#e0e0e0", edge="#333", fontsize=9)

    # ── Summary row ──────────────────────────────────────
    box(ax, 0.4, 0.2, 11.8, 0.95,
        "Deployment:  docker compose -f config/docker-compose.honey.yml  "
        "-f config/docker-compose.honey.llm.yml up -d      "
        "|      Ollama: Windows host @ 172.23.240.1:11434      "
        "|      Approved models: llama3.1:8b / qwen2.5:14b / gemma2:9b",
        color="#fefbea", edge="#b88a00", fontsize=8)

    fig.tight_layout()
    fig.savefig(OUT, bbox_inches="tight")
    plt.close(fig)
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
