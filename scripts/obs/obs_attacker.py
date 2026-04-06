#!/usr/bin/env python3
"""Attacker Terminal — live feed from attacker_log.jsonl with per-drone status."""
import json, time, os
from pathlib import Path
from typing import Dict

LEVEL_COLORS = {
    0: "\033[90m",  # gray
    1: "\033[94m",  # blue
    2: "\033[93m",  # yellow
    3: "\033[95m",  # magenta
    4: "\033[91m",  # red
}
RST = "\033[0m"
OK = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"

LEVEL_NAMES = {
    0: "L0 Script Kiddie",
    1: "L1 Basic MAVLink",
    2: "L2 HTTP Enum",
    3: "L3 WebSocket CVE",
    4: "L4 APT Chain",
}

def header():
    print("\033[43;30m  ATTACKER SIMULATOR  \033[0m")
    print("Watching: results/attacker_log.jsonl")
    print("─" * 60)

def main():
    os.chdir(Path(__file__).parent.parent.parent)
    header()

    log = Path("results/attacker_log.jsonl")
    while not log.exists():
        print(f"\033[90mWaiting for attacker to start...\033[0m", flush=True)
        time.sleep(2)

    # Per-drone engagement tracker
    drones: Dict[str, dict] = {}
    current_level = -1
    total = 0
    success = 0

    with open(log) as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.3)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                lv = r.get("level", -1)
                if lv < 0:
                    # Final score
                    resp = r.get("response_preview", "")
                    if "deception_score" in resp:
                        print(f"\n\033[97;42m  FINAL: {resp[:80]}  \033[0m\n", flush=True)
                    continue

                action = r.get("action", "")
                target = r.get("target", "")
                resp = r.get("response_preview", "")[:40]
                dur = r.get("duration_ms", 0)
                ok = "timeout" not in action and "fail" not in action

                total += 1
                if ok:
                    success += 1

                # Level change?
                if lv != current_level:
                    current_level = lv
                    c = LEVEL_COLORS.get(lv, "")
                    name = LEVEL_NAMES.get(lv, f"L{lv}")
                    print(f"\n{c}{'═'*60}")
                    print(f"  LEVEL: {name}")
                    print(f"{'═'*60}{RST}\n", flush=True)

                # Track per-drone
                dst = target.split(":")[0] if ":" in target else "?"
                if dst not in drones:
                    drones[dst] = {"ok": 0, "fail": 0, "last_action": ""}
                if ok:
                    drones[dst]["ok"] += 1
                else:
                    drones[dst]["fail"] += 1
                drones[dst]["last_action"] = action[:25]

                # Print packet line
                c = LEVEL_COLORS.get(lv, "")
                mark = OK if ok else FAIL
                resp_short = resp.replace('"', "")[:35] if resp else ""
                print(
                    f"  {c}L{lv}{RST} {mark} {action:30s} "
                    f"→ {target:22s} "
                    f"{dur:7.1f}ms "
                    f"\033[90m{resp_short}\033[0m",
                    flush=True,
                )

                # Periodic drone summary (every 20 packets)
                if total % 20 == 0:
                    pct = success * 100 // max(total, 1)
                    print(f"\n  \033[97m[{total} pkts | {pct}% engaged]\033[0m", end="")
                    for ip, st in sorted(drones.items()):
                        tot_d = st["ok"] + st["fail"]
                        pct_d = st["ok"] * 100 // max(tot_d, 1)
                        print(f"  {ip}: {pct_d}%({tot_d})", end="")
                    print("\n", flush=True)

            except (json.JSONDecodeError, KeyError):
                pass

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
