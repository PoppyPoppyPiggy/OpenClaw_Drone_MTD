#!/usr/bin/env python3
"""MTD Terminal — tail engine logs filtered for MTD events only."""
import json, time, os
from pathlib import Path

MTD_KEYWORDS = {"mtd_trigger", "mtd_action", "port_rotate", "ip_shuffle",
                "key_rotate", "service_migrate", "route_morph", "proto_change",
                "freq_hop", "urgency", "recommended_actions", "mtd",
                "surface", "rotate", "shuffle"}

COLORS = {
    "port_rotate":     "\033[94m",  # blue
    "ip_shuffle":      "\033[93m",  # yellow
    "key_rotate":      "\033[92m",  # green
    "service_migrate": "\033[91m",  # red
    "trigger":         "\033[96m",  # cyan
    "urgency":         "\033[95m",  # magenta
}
RST = "\033[0m"

def header():
    print("\033[41;97m  MTD CONTROLLER  \033[0m")
    print("Watching: results/logs/engines.log + results/metrics/live_mtd_results.json")
    print("Keywords: mtd_trigger, port_rotate, ip_shuffle, key_rotate, service_migrate")
    print("─" * 60)

def color_for(text: str) -> str:
    for k, c in COLORS.items():
        if k in text:
            return c
    return ""

def main():
    os.chdir(Path(__file__).parent.parent.parent)
    header()

    log = Path("results/logs/engines.log")
    mtd_file = Path("results/metrics/live_mtd_results.json")
    total_triggers = 0
    last_mtd_size = 0

    # Wait for log file
    while not log.exists():
        print(f"\033[90mWaiting for engine log...\033[0m", flush=True)
        time.sleep(2)

    with open(log) as f:
        f.seek(0, 2)  # seek to end
        while True:
            line = f.readline()
            if line:
                line = line.strip()
                try:
                    r = json.loads(line)
                    text_lower = line.lower()
                    if not any(kw in text_lower for kw in MTD_KEYWORDS):
                        continue
                    ev = r.get("event", "")
                    c = color_for(ev)
                    drone = r.get("drone_id", r.get("source_drone_id", ""))
                    parts = [f"{c}{ev}{RST}"]
                    if drone:
                        parts.append(f"drone={drone}")
                    for k in ["urgency", "attacker_level", "level", "actions",
                              "recommended_actions", "action_type", "new_port",
                              "old_port", "new_ip", "old_ip"]:
                        if k in r:
                            parts.append(f"{k}={r[k]}")
                    if "trigger" in ev.lower() or "mtd" in ev.lower():
                        total_triggers += 1
                    print(f"[{total_triggers:3d}] " + " | ".join(parts), flush=True)
                except (json.JSONDecodeError, KeyError):
                    pass
            else:
                # Check live_mtd_results.json for updates
                if mtd_file.exists():
                    sz = mtd_file.stat().st_size
                    if sz != last_mtd_size:
                        last_mtd_size = sz
                        try:
                            data = json.loads(mtd_file.read_text())
                            if isinstance(data, list) and len(data) > total_triggers:
                                for entry in data[total_triggers:]:
                                    total_triggers += 1
                                    print(f"\033[96m[{total_triggers:3d}] MTD_RESULT: "
                                          f"{entry}\033[0m", flush=True)
                        except Exception:
                            pass
                time.sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
