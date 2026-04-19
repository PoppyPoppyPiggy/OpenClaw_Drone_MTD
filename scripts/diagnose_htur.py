#!/usr/bin/env python3
"""
diagnose_htur.py — HTUR (Honey-Token Uptake Ratio) offline validation

[ROLE]
    Drive OpenClawService with a synthetic scripted attacker that
      1) harvests honey-tokens from auth / config_dump endpoints,
      2) re-submits the harvested tokens on subsequent auth attempts.
    Measures HTUR, CPR, FSR per-attacker and aggregate.

    This is the simulated ground-truth for HTUR computation; once the
    Docker stack is wired up, the same metrics come out of the live
    attacker_sim.py traffic.

[WHY]
    Addresses the primary limitation of the simulated DeceptionEnv
    evaluation — it adds an observed-behaviour metric (fake-data uptake)
    that is independent of the _action_effect heuristic.

[USAGE]
    python scripts/diagnose_htur.py
    python scripts/diagnose_htur.py --output results/diagnostics/htur.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from honey_drone.openclaw_service import OpenClawService  # noqa: E402


def run_attacker_session(svc: OpenClawService, attacker_ip: str) -> dict:
    """
    Scripted 6-step attacker session:
      1. auth (no token)                -> harvests api_token
      2. config                         -> harvests signing_key, ssh_password
      3. auth with token (reuse!)       -> HTUR hit #1
      4. auth with ssh_password         -> HTUR hit #2
      5. auth with signing_key          -> HTUR hit #3
      6. auth with FAKE token           -> should NOT count (control)
    """
    harvested: dict[str, str] = {}

    # Step 1: plain auth — empty credentials, harvest api_token
    resp1 = svc.handle(json.dumps({"type": "auth"}).encode("utf-8"), attacker_ip)
    if resp1 and resp1.get("token"):
        harvested["api_token"] = resp1["token"]

    # Step 2: config dump — harvest all the credentials it drops
    resp2 = svc.handle(json.dumps({"type": "config"}).encode("utf-8"), attacker_ip)
    if resp2 and resp2.get("config"):
        cfg = resp2["config"]
        harvested["signing_key"] = cfg.get("signing_key", "")
        harvested["ssh_password"] = cfg.get("ssh_password", "")

    # Steps 3-5: reuse each harvested credential via auth (triggers _check_reuse)
    reuse_attempts = 0
    for field, value in [
        ("token", harvested.get("api_token", "")),
        ("password", harvested.get("ssh_password", "")),
        ("api_key", harvested.get("signing_key", "")),
    ]:
        if not value:
            continue
        svc.handle(
            json.dumps({"type": "auth", field: value}).encode("utf-8"),
            attacker_ip,
        )
        reuse_attempts += 1

    # Step 6: control — auth with an obvious fake token that was never issued
    svc.handle(
        json.dumps({"type": "auth", "token": "fake-sk-NEVER-ISSUED-000"}).encode("utf-8"),
        attacker_ip,
    )

    return {
        "attacker_ip": attacker_ip,
        "harvested_tokens": list(harvested.keys()),
        "reuse_attempts": reuse_attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", default="results/diagnostics/htur.json",
    )
    parser.add_argument(
        "--attackers", default="10.0.0.42,10.0.0.99,10.0.0.66",
    )
    parser.add_argument("--drone-id", default="honey_htur_diag")
    args = parser.parse_args()

    svc = OpenClawService(drone_id=args.drone_id, index=1)
    ips = [x.strip() for x in args.attackers.split(",") if x.strip()]

    sessions = [run_attacker_session(svc, ip) for ip in ips]
    stats = svc.get_htur_stats()

    # Per-attacker HTUR breakdown
    per_ip: dict[str, dict] = {}
    for rec in svc._issued_tokens:
        ip = rec["attacker_ip"]
        per_ip.setdefault(ip, {"issued": set(), "reused": set()})
        per_ip[ip]["issued"].add(rec["id"])
    for rec in svc._reuse_events:
        ip = rec["attacker_ip"]
        per_ip.setdefault(ip, {"issued": set(), "reused": set()})
        per_ip[ip]["reused"].add(rec["issued_id"])
    per_ip_report = {
        ip: {
            "issued_unique": len(v["issued"]),
            "reused_unique": len(v["reused"]),
            "htur": (len(v["reused"]) / len(v["issued"])) if v["issued"] else 0.0,
        }
        for ip, v in per_ip.items()
    }

    doc = {
        "drone_id": args.drone_id,
        "attackers": sessions,
        "aggregate_stats": stats,
        "per_attacker_htur": per_ip_report,
        "issued_tokens_sample": svc._issued_tokens[:5],
        "reuse_events_sample": svc._reuse_events[:10],
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, default=str))

    print("=== HTUR diagnostic ===")
    print(f"Drone    : {args.drone_id}")
    print(f"Attackers: {len(ips)}")
    print()
    print(f"Aggregate")
    print(f"  issued_unique        : {stats['issued_unique']}")
    print(f"  reused_unique        : {stats['reused_unique']}")
    print(f"  HTUR                 : {stats['htur']:.3f}")
    print(f"  CPR (auth reuses)    : {stats['auth_reuses']} reuses")
    print(f"  FSR                  : {stats['fsr']:.3f}")
    print(f"  total_reuse_events   : {stats['total_reuse_events']}")
    print()
    print("Per-attacker:")
    for ip, rep in per_ip_report.items():
        print(f"  {ip:<16} issued={rep['issued_unique']:>2}  "
              f"reused={rep['reused_unique']:>2}  HTUR={rep['htur']:.2f}")
    print()
    print(f"Saved → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
