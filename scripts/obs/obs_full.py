#!/usr/bin/env python3
"""Full Experiment Terminal — poll summary.json + show overall progress."""
import json, time, os, sys
from pathlib import Path
from typing import Optional

def header():
    print("\033[42;97m  EXPERIMENT STATUS  \033[0m")
    print("Polling: results/metrics/summary.json every 3s")
    print("─" * 60)

def read_summary() -> Optional[dict]:
    p = Path("results/metrics/summary.json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None

def read_attacker_log_stats() -> dict:
    p = Path("results/attacker_log.jsonl")
    if not p.exists():
        return {"total": 0, "ok": 0, "current_level": -1}
    total = ok = 0
    last_level = -1
    with open(p) as f:
        for line in f:
            try:
                r = json.loads(line)
                lv = r.get("level", -1)
                if lv < 0:
                    continue
                total += 1
                last_level = lv
                if "timeout" not in r.get("action", "") and "fail" not in r.get("action", ""):
                    ok += 1
            except Exception:
                pass
    return {"total": total, "ok": ok, "current_level": last_level}

def check_engines() -> str:
    if Path("results/.engine_running").exists():
        return "\033[92mreal_openclaw\033[0m"
    return "\033[93mstub\033[0m"

def level_name(lv: int) -> str:
    names = {0: "L0 Script Kiddie", 1: "L1 Basic", 2: "L2 Intermediate",
             3: "L3 Advanced", 4: "L4 APT"}
    return names.get(lv, f"L{lv}")

def main():
    os.chdir(Path(__file__).parent.parent.parent)
    header()
    start = time.time()

    while True:
        elapsed = time.time() - start
        summary = read_summary()
        log_stats = read_attacker_log_stats()
        engine = check_engines()

        print(f"\033[90m{'─'*60}\033[0m")
        print(f"\033[97mElapsed: {elapsed:.0f}s\033[0m  |  Engine: {engine}")
        print()

        if summary:
            ds = summary.get("deception_score", summary.get("deception_success", 0))
            ds_color = "\033[92m" if ds >= 0.7 else "\033[93m" if ds >= 0.5 else "\033[91m"
            print(f"  DeceptionScore:  {ds_color}{ds:.4f}\033[0m")
            print(f"  Sessions:        {summary.get('total_sessions', 0)}")
            print(f"  Engagement:      {summary.get('engagement_rate', summary.get('deception_success', 0)):.1%}")
            print(f"  MTD Actions:     {summary.get('total_mtd_actions', 0)}")
            print(f"  Dataset Size:    {summary.get('dataset_size', 0)}")
            print(f"  Unique TTPs:     {summary.get('unique_ttps', 0)}")
            print(f"  Engine Mode:     {summary.get('engine_mode', '?')}")
        else:
            print("  \033[90msummary.json not written yet...\033[0m")

        print()
        if log_stats["total"] > 0:
            pct = log_stats["ok"] * 100 // max(log_stats["total"], 1)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            lv = log_stats["current_level"]
            lv_colors = {0: "\033[90m", 1: "\033[94m", 2: "\033[93m", 3: "\033[95m", 4: "\033[91m"}
            lv_c = lv_colors.get(lv, "")
            print(f"  Attacker Level:  {lv_c}{level_name(lv)}\033[0m")
            print(f"  Packets:         {log_stats['total']} total, {log_stats['ok']} success")
            print(f"  Engagement:      {bar} {pct}%")
        else:
            print("  \033[90mNo attacker packets yet...\033[0m")

        # Check for figures/latex
        fig_count = len(list(Path("results/figures").glob("*.pdf"))) if Path("results/figures").exists() else 0
        tex_count = len(list(Path("results/latex").glob("*.tex"))) if Path("results/latex").exists() else 0
        if fig_count or tex_count:
            print(f"\n  Outputs:         {fig_count} figures, {tex_count} LaTeX tables")

        print(flush=True)
        time.sleep(3)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
