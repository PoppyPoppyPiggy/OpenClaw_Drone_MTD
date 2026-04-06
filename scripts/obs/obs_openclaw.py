#!/usr/bin/env python3
"""OpenClaw Terminal — live agent decisions, phase changes, deception actions."""
import json, sys, time, os
from pathlib import Path

KEYWORDS = {"openclaw", "deception", "behavior", "false_flag", "ghost_port",
            "sysid_rotation", "breadcrumb", "attacker_level", "phase_changed",
            "proactive", "arm_crash", "service_mirror", "param_cycle",
            "mission_refresh", "agent_decision", "started", "stopped"}

COLORS = {
    "false_flag":  "\033[91m",  # red
    "ghost_port":  "\033[95m",  # magenta
    "sysid":       "\033[93m",  # yellow
    "breadcrumb":  "\033[92m",  # green
    "phase":       "\033[96m",  # cyan
    "proactive":   "\033[94m",  # blue
    "started":     "\033[97m",  # white bold
}
RST = "\033[0m"

def color_for(text: str) -> str:
    for k, c in COLORS.items():
        if k in text:
            return c
    return ""

def header():
    print("\033[44;97m  OPENCLAW AGENT  \033[0m")
    print("Watching: results/logs/engines.log")
    print("Keywords: phase, false_flag, ghost, sysid, breadcrumb, proactive")
    print("─" * 60)

def tail_log():
    log = Path("results/logs/engines.log")
    while not log.exists():
        print(f"\033[90mWaiting for OpenClaw engine to start...\033[0m", flush=True)
        time.sleep(2)

    with open(log) as f:
        # Seek to end
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.3)
                continue
            line = line.strip()
            try:
                r = json.loads(line)
                ev = r.get("event", "")
                # Filter: only show lines matching our keywords
                text_lower = line.lower()
                if not any(kw in text_lower for kw in KEYWORDS):
                    continue
                ts = r.get("timestamp", "")
                drone = r.get("drone_id", "")
                ip = r.get("attacker_ip", "")
                c = color_for(ev)
                # Format output
                parts = [f"{c}{ev}{RST}"]
                if drone:
                    parts.append(f"drone={drone}")
                if ip:
                    parts.append(f"ip={ip}")
                # Add extra context fields
                for k in ["behavior", "old_phase", "new_phase", "original_sysid",
                           "fake_sysid", "port", "ws_port_hint", "urgency",
                           "attacker_ip", "cmd_count", "sysid", "message"]:
                    if k in r and k not in ("event", "timestamp", "drone_id", "level", "logger"):
                        parts.append(f"{k}={r[k]}")
                print(" | ".join(parts), flush=True)
            except (json.JSONDecodeError, KeyError):
                pass

if __name__ == "__main__":
    os.chdir(Path(__file__).parent.parent.parent)
    header()
    try:
        tail_log()
    except KeyboardInterrupt:
        pass
