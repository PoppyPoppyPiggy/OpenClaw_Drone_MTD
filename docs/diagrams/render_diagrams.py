#!/usr/bin/env python3
"""Render MIRAGE-UAS architecture diagrams as PNG using matplotlib."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

OUT = Path("docs/diagrams")
OUT.mkdir(parents=True, exist_ok=True)

# ═══ DIAGRAM 1: Component Flow ═══

def render_component_flow():
    fig, ax = plt.subplots(figsize=(18, 13))
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 13)
    ax.set_facecolor("#0d1117")
    fig.patch.set_facecolor("#0d1117")
    ax.axis("off")

    # Colors
    C = {"atk": "#552222", "dec": "#664400", "mtd": "#224466", "cti": "#226622", "eval": "#442266",
         "atk_t": "#ff5252", "dec_t": "#ffab00", "mtd_t": "#58a6ff", "cti_t": "#3fb950", "eval_t": "#bc8cff",
         "bg": "#161b22", "border": "#30363d", "txt": "#c9d1d9"}

    def box(x, y, w, h, label, color, tx_color="#c9d1d9", fs=8):
        rect = mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.1",
                                        facecolor=color, edgecolor=C["border"], linewidth=1)
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2, label, ha="center", va="center", fontsize=fs,
                color=tx_color, fontweight="bold", wrap=True)

    def arrow(x1, y1, x2, y2, label="", color="#8b949e"):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                     arrowprops=dict(arrowstyle="->", color=color, lw=1.5))
        if label:
            mx, my = (x1+x2)/2, (y1+y2)/2
            ax.text(mx, my+0.15, label, fontsize=6, color=color, ha="center", style="italic")

    # Title
    ax.text(9, 12.7, "MIRAGE-UAS Framework — Component Interaction", ha="center",
            fontsize=14, color="#c9d1d9", fontweight="bold")
    ax.text(9, 12.35, "DS Lab, Kyonggi University | ACM CCS 2026 | DeceptionScore = 0.714",
            ha="center", fontsize=9, color="#8b949e")

    # ── Layer 1: Attacker ──
    box(0.2, 10.5, 3.5, 1.5, "ATTACKER (L0-L4)\n\nL0: Random UDP\nL1: MAVLink HEARTBEAT/ARM\nL2: HTTP /api/v1/*\nL3: WebSocket CVE-2026-25253\nL4: Breadcrumb + Ghost chain", C["atk"], C["atk_t"], 7)

    # ── Layer 2: Honey Drone ──
    # CC Stub
    box(5, 10.5, 2.5, 1.5, "CC Stub (Docker)\nUDP:14550  HTTP:80\nWS:18789  RTSP:8554\nGhost:19000+", C["dec"], C["dec_t"], 7)
    # Engine
    box(8.5, 10.5, 4, 1.5, "AgenticDecoyEngine (Host)\n\nOpenClawAgent (5 tasks)\nMavlinkResponseGen\nEngagementTracker", C["dec"], C["dec_t"], 7)
    # Deception
    box(13.5, 10.5, 4, 1.5, "Deception Layer\n\nFakeServiceFactory\nBreadcrumbPlanter\nDeceptionStateManager\nDeceptionOrchestrator", C["dec"], C["dec_t"], 7)

    # ── Layer 3: MTD ──
    box(0.2, 7.5, 4, 2, "MTD Controller\n\nEngagementToMTD\nMTDExecutor (Docker SDK)\nDecoyRotationPolicy\nMTDMonitor\n\nPORT_ROTATE: 120ms\nIP_SHUFFLE: 450ms\nKEY_ROTATE: 180ms\nSERVICE_MIGRATE: 3200ms", C["mtd"], C["mtd_t"], 6.5)

    # ── Layer 4: CTI Pipeline ──
    box(5, 7.5, 5.5, 2, "CTI Pipeline (Track B)\n\nMavlinkInterceptor (:19551)\n→ AttackEventParser (L0-L4)\n→ ATTCKMapper (12 ICS TTPs)\n→ STIXConverter (STIX 2.1)\n→ CTI Ingest API (:8765)", C["cti"], C["cti_t"], 7)

    box(11, 7.5, 3.5, 2, "Dataset Builder\n\nPositiveCollector (label=1)\nNegativeGenerator (label=0)\nDatasetPackager\nDatasetValidator (V1-V6)\n\nDVD-CTI-Dataset-v1\n1007 rows, 12 TTPs", C["cti"], C["cti_t"], 6.5)

    # ── Layer 5: Evaluation ──
    box(0.2, 5, 6, 1.8, "Evaluation\n\nMetricsCollector (Table II-VI)\nDeceptionScorer: DS = Σ(wi·ci) = 0.714\nPlotResults (6 PDF figures)\nStatisticalTest (Wilcoxon + LaTeX)", C["eval"], C["eval_t"], 7)

    box(7, 5, 4, 1.8, "Dashboard (:8888)\n\n17 live charts (3 tabs)\nSwagger API (/docs)\nExcel download (.xlsx)\n20+ REST endpoints", C["eval"], C["eval_t"], 7)

    box(12, 5, 5.5, 1.8, "Outputs\n\n6 PDF figures (IEEE 300 DPI)\n5 LaTeX tables\n8-sheet Excel workbook\nOMNeT++ traces (4 files)\nReproducibility ZIP + SHA-256", C["eval"], C["eval_t"], 7)

    # ── Arrows ──
    arrow(3.7, 11.25, 5, 11.25, "attack\npackets", C["atk_t"])
    arrow(7.5, 11.25, 8.5, 11.25, "forward UDP\nENGINE_HOST", C["dec_t"])
    arrow(12.5, 11.25, 13.5, 11.25, "deception\nactions", C["dec_t"])
    arrow(6.25, 10.5, 6.25, 9.5, "MAVLink copy\n:19551", C["cti_t"])
    arrow(10, 10.5, 2.2, 9.5, "MTDTrigger\nurgency≥0.3", C["mtd_t"])
    arrow(10.5, 9.5, 10.5, 7.5, "STIX bundle", C["cti_t"])
    arrow(10, 7.5, 7, 6.8, "metrics\nJSON", C["eval_t"])
    arrow(4.2, 7.5, 3.2, 6.8, "MTDResult\nTable III", C["eval_t"])
    arrow(14, 10.5, 14, 9.5, "belief\nupdate", C["dec_t"])

    # Legend
    for i, (label, color) in enumerate([("Attacker", C["atk_t"]), ("Deception", C["dec_t"]),
                                          ("MTD", C["mtd_t"]), ("CTI", C["cti_t"]), ("Evaluation", C["eval_t"])]):
        ax.add_patch(mpatches.Rectangle((0.5 + i*3.5, 4.2), 0.4, 0.4, facecolor=color, alpha=0.3))
        ax.text(1.1 + i*3.5, 4.4, label, fontsize=7, color=color, va="center")

    fig.savefig(OUT / "mirage_component_flow.png", dpi=200, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    print(f"  Saved: {OUT / 'mirage_component_flow.png'}")


# ═══ DIAGRAM 2: Sequence Diagram ═══

def render_sequence():
    fig, ax = plt.subplots(figsize=(16, 20))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 20)
    ax.set_facecolor("#0d1117")
    fig.patch.set_facecolor("#0d1117")
    ax.axis("off")

    C = {"atk": "#ff5252", "dec": "#ffab00", "mtd": "#58a6ff", "cti": "#3fb950",
         "eval": "#bc8cff", "txt": "#c9d1d9", "bg": "#161b22", "note": "#1c2333"}

    # Title
    ax.text(8, 19.7, "L2 Attacker Session — Full Sequence", ha="center",
            fontsize=14, color=C["txt"], fontweight="bold")

    # Participants
    parts = [
        (1, "Attacker\n(L2)", C["atk"]),
        (3.5, "CC Stub\n(:80)", C["dec"]),
        (6, "OpenClaw\nAgent", C["dec"]),
        (8.5, "Engagement\nTracker", C["dec"]),
        (10.5, "MTD\nExecutor", C["mtd"]),
        (12.5, "Attack\nParser", C["cti"]),
        (14.5, "STIX\nConverter", C["cti"]),
    ]
    for x, label, color in parts:
        ax.add_patch(mpatches.FancyBboxPatch((x-0.6, 19), 1.2, 0.6, boxstyle="round,pad=0.05",
                                              facecolor="#161b22", edgecolor=color, linewidth=1.5))
        ax.text(x, 19.3, label, ha="center", va="center", fontsize=7, color=color, fontweight="bold")
        ax.plot([x, x], [0.5, 19], color="#30363d", linewidth=0.5, linestyle="--")

    def msg(x1, x2, y, label, color, dashed=False):
        style = "->" if not dashed else "->"
        ls = "--" if dashed else "-"
        ax.annotate("", xy=(x2, y), xytext=(x1, y),
                     arrowprops=dict(arrowstyle="->", color=color, lw=1.2, linestyle=ls))
        mx = (x1 + x2) / 2
        ax.text(mx, y + 0.15, label, fontsize=6, color=color, ha="center")

    def note(x, y, text, color=C["note"]):
        ax.add_patch(mpatches.FancyBboxPatch((x, y-0.3), 3.5, 0.6, boxstyle="round,pad=0.05",
                                              facecolor=color, edgecolor="#30363d", linewidth=0.5))
        ax.text(x + 0.1, y, text, fontsize=5.5, color=C["txt"], va="center")

    def phase_bar(y, label, color):
        ax.add_patch(mpatches.Rectangle((0.2, y-0.05), 15.6, 0.1, facecolor=color, alpha=0.15))
        ax.text(0.3, y, label, fontsize=7, color=color, fontweight="bold", va="center")

    # ── Phase 1: HTTP /api/v1/params ──
    phase_bar(18.2, "Phase 1: HTTP Request (RECON)", C["cti"])
    msg(1, 3.5, 17.8, "GET /api/v1/params", C["atk"])
    msg(3.5, 1, 17.4, "200 OK + breadcrumbs", C["dec"])
    note(3.8, 17.1, "ssh_password, signing_key, api_token")
    msg(3.5, 12.5, 16.8, "MavlinkCaptureEvent (HTTP)", C["cti"])
    msg(12.5, 14.5, 16.4, "ParsedAttackEvent L1", C["cti"])
    msg(14.5, 14.5, 16.0, "stix2.Bundle (7 objects)", C["cti"])

    # ── Phase 2: POST /login ──
    phase_bar(15.4, "Phase 2: Login Attempt (EXPLOIT)", C["dec"])
    msg(1, 3.5, 15.0, "POST /login {admin:admin}", C["atk"])
    msg(3.5, 1, 14.6, "{authenticated:true, token:sk-...}", C["dec"])
    msg(3.5, 12.5, 14.2, "CaptureEvent (POST)", C["cti"])
    note(12.8, 13.9, "T0836 Modify Parameter → EXPLOITATION")

    # ── Phase 3: MAVLink HEARTBEAT ──
    phase_bar(13.2, "Phase 3: MAVLink via Engine (RECON)", C["dec"])
    msg(1, 3.5, 12.8, "MAVLink HEARTBEAT", C["atk"])
    msg(3.5, 6, 12.4, "forward UDP to engine", C["dec"])
    msg(6, 8.5, 12.0, "observe() + update_session()", C["dec"])
    note(6.3, 11.7, "phase=RECON (only HEARTBEAT seen)")
    msg(6, 3.5, 11.4, "HEARTBEAT response (17B)", C["dec"])
    msg(3.5, 1, 11.0, "HEARTBEAT (sysid=2)", C["dec"])

    # ── Phase 4: ARM Command ──
    phase_bar(10.4, "Phase 4: ARM Command → EXPLOIT Phase", C["atk"])
    msg(1, 3.5, 10.0, "COMMAND_LONG (cmd=400 ARM)", C["atk"])
    msg(3.5, 6, 9.6, "forward to engine", C["dec"])
    msg(6, 8.5, 9.2, "observe() → phase: RECON→EXPLOIT", C["dec"])
    note(6.3, 8.9, "COMMAND_LONG detected → EXPLOIT phase")
    msg(6, 3.5, 8.6, "HEARTBEAT (stay engaged)", C["dec"])
    msg(3.5, 1, 8.2, "HEARTBEAT (17B)", C["dec"])

    # ── Phase 5: MTD Trigger ──
    phase_bar(7.6, "Phase 5: Urgency Threshold → MTD Trigger", C["mtd"])
    msg(8.5, 6, 7.2, "urgency=0.5 (≥0.3)", C["mtd"])
    msg(6, 10.5, 6.8, "MTDTrigger (PORT_ROTATE)", C["mtd"])
    note(10.8, 6.5, "iptables DNAT + mavlink-router HUP")
    msg(10.5, 10.5, 6.2, "MTDResult: 120ms success", C["mtd"])

    # ── Phase 6: STIX + Metrics ──
    phase_bar(5.6, "Phase 6: STIX Bundle + Metrics", C["eval"])
    msg(12.5, 14.5, 5.2, "ParsedAttackEvent → Bundle", C["cti"])
    note(12.8, 4.9, "Indicator + ObservedData + AttackPattern")
    msg(8.5, 8.5, 4.5, "Table II: L2 engagement", C["eval"], dashed=True)
    msg(10.5, 10.5, 4.1, "Table III: PORT_ROTATE 120ms", C["eval"], dashed=True)

    # DS result
    ax.add_patch(mpatches.FancyBboxPatch((5, 1), 6, 1.5, boxstyle="round,pad=0.1",
                                          facecolor="#112211", edgecolor=C["eval"], linewidth=2))
    ax.text(8, 2.1, "DeceptionScore = 0.714", ha="center", fontsize=11, color=C["eval"], fontweight="bold")
    ax.text(8, 1.6, "0.30×0.75 + 0.25×1.0 + 0.20×0.72 + 0.15×1.0 + 0.10×0.03",
            ha="center", fontsize=7, color=C["txt"])
    ax.text(8, 1.2, "engine_mode: real_openclaw | 0 breaches | 46 MTD triggers",
            ha="center", fontsize=6, color="#8b949e")

    fig.savefig(OUT / "mirage_sequence.png", dpi=200, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    print(f"  Saved: {OUT / 'mirage_sequence.png'}")


if __name__ == "__main__":
    print("Rendering MIRAGE-UAS diagrams...")
    render_component_flow()
    render_sequence()
    print("Done.")
