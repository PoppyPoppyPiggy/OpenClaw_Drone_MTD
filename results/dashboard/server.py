#!/usr/bin/env python3
"""MIRAGE-UAS Dashboard API — serves metrics + agent state + packet flow."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import json, csv, struct
from pathlib import Path

app = FastAPI(title="MIRAGE-UAS Dashboard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

R = Path("results")

def _load(p):
    try: return json.loads(p.read_text()) if p.exists() else {}
    except: return {}
def _load_list(p):
    try: return json.loads(p.read_text()) if p.exists() else []
    except: return []
def _load_jsonl(p, limit=200):
    if not p.exists(): return []
    lines = p.read_text().splitlines()
    return [json.loads(l) for l in lines[-limit:] if l.strip()]

@app.get("/api/summary")
def summary(): return _load(R/"metrics/summary.json")
@app.get("/api/table2")
def table2(): return _load_list(R/"metrics/table_ii_engagement.json")
@app.get("/api/table3")
def table3(): return _load_list(R/"metrics/table_iii_mtd_latency.json")
@app.get("/api/table4")
def table4(): return _load(R/"metrics/table_iv_dataset.json")
@app.get("/api/table5")
def table5(): return _load(R/"metrics/table_v_deception.json")
@app.get("/api/table6")
def table6(): return _load_list(R/"metrics/table_vi_agent_decisions.json")
@app.get("/api/timeline")
def timeline(): return _load_jsonl(R/"metrics/deception_timeline.jsonl", 50)
@app.get("/api/attacker_log")
def attacker_log(): return _load_jsonl(R/"attacker_log.jsonl", 200)

@app.get("/api/agent_state")
def agent_state():
    """Reconstruct OpenClaw agent internal state from attacker log."""
    log = _load_jsonl(R/"attacker_log.jsonl", 500)
    if not log: return {}
    # Build per-attacker fingerprint + phase progression
    attackers = {}
    phase_transitions = []
    behaviors = {"RECON":0,"EXPLOIT":0,"PERSIST":0,"EXFIL":0}
    tool_map = {"UNKNOWN":0}
    cmd_sequence = []
    for r in log:
        if r.get("level",-1) < 0: continue
        lv = r["level"]
        action = r["action"]
        ok = "timeout" not in action and "fail" not in action
        # Infer phase from level+action
        if lv <= 1 and "heartbeat" in action: phase = "RECON"
        elif lv <= 1 and "arm" in action: phase = "EXPLOIT"
        elif lv == 2: phase = "EXPLOIT"
        elif lv == 3 and "ws" in action: phase = "EXPLOIT"
        elif lv == 3 and "rtsp" in action: phase = "PERSIST"
        elif lv == 4 and "log" in action: phase = "EXFIL"
        elif lv == 4 and "breadcrumb" in action: phase = "EXFIL"
        elif lv == 4 and "ghost" in action: phase = "EXFIL"
        elif lv == 4 and "gps" in action: phase = "EXPLOIT"
        else: phase = "RECON"
        behaviors[phase] = behaviors.get(phase, 0) + 1
        # Infer tool
        if lv == 0: tool = "NMAP_SCANNER"
        elif lv <= 1: tool = "MAVPROXY_GCS"
        elif lv == 2: tool = "DRONEKIT_SCRIPT"
        elif lv == 3: tool = "CUSTOM_EXPLOIT"
        elif lv == 4: tool = "CUSTOM_EXPLOIT"
        else: tool = "UNKNOWN"
        tool_map[tool] = tool_map.get(tool, 0) + 1
        cmd_sequence.append({"t": r["timestamp"], "action": action[:30], "level": lv,
                             "phase": phase, "tool": tool, "ok": ok,
                             "resp_size": len(r.get("response_preview",""))})
        # Track phase transitions
        if len(cmd_sequence) >= 2:
            prev = cmd_sequence[-2]["phase"]
            curr = phase
            if prev != curr:
                phase_transitions.append({"from": prev, "to": curr, "at_cmd": len(cmd_sequence),
                                          "trigger": action[:25]})
    # Agent autonomous behaviors (from table6)
    t6 = _load_list(R/"metrics/table_vi_agent_decisions.json")
    # Response coverage matrix
    coverage = {}
    for r in log:
        if r.get("level",-1) < 0: continue
        action = r["action"]
        ok = "timeout" not in action and "fail" not in action
        resp_len = len(r.get("response_preview",""))
        # Map action to msg_type
        msg = action.replace("_timeout","").replace("_fail","")
        phase_key = cmd_sequence[min(len(cmd_sequence)-1, log.index(r))]["phase"] if cmd_sequence else "RECON"
        key = f"{msg}|{phase_key}"
        if key not in coverage:
            coverage[key] = {"msg": msg[:20], "phase": phase_key, "resp_bytes": resp_len if ok else 0, "count": 0, "ok": 0}
        coverage[key]["count"] += 1
        if ok: coverage[key]["ok"] += 1

    return {
        "phase_distribution": behaviors,
        "phase_transitions": phase_transitions[:20],
        "tool_classification": tool_map,
        "cmd_sequence": cmd_sequence[-100:],
        "autonomous_behaviors": t6,
        "response_coverage": list(coverage.values())[:40],
        "total_decisions": sum(b.get("count",0) for b in t6),
        "silenced_periods": 0,
        "false_flag_count": 0,
        "sysid_rotations": sum(1 for b in t6 if b.get("behavior_triggered","")=="sysid_rotation"),
    }

@app.get("/api/packet_flow")
def packet_flow():
    """OMNeT++ packet flow — per-packet detail with binary decode."""
    log = _load_jsonl(R/"attacker_log.jsonl", 300)
    packets = []
    for i, r in enumerate(log):
        if r.get("level",-1) < 0: continue
        lv = r["level"]
        action = r["action"]
        ok = "timeout" not in action and "fail" not in action
        target = r.get("target","")
        resp = r.get("response_preview","")
        dur = r.get("duration_ms",0)
        # Determine protocol
        if "http" in action or "login" in action: proto = "HTTP"
        elif "ws_" in action: proto = "WebSocket"
        elif "rtsp" in action: proto = "RTSP"
        elif "ssh" in action: proto = "SSH"
        elif "ghost" in action: proto = "TCP/Ghost"
        elif "gps" in action: proto = "MAVLink/GPS"
        else: proto = "MAVLink/UDP"
        # Decode response hex if MAVLink
        decoded = ""
        if proto.startswith("MAVLink") and resp and len(resp) >= 8:
            try:
                raw = bytes.fromhex(resp[:18])
                if len(raw) >= 9:
                    cm, ty, ap, bm, ss, mv = struct.unpack_from("<IBBBBB", raw, 0)
                    types = {2:"QUADROTOR",6:"GCS"}
                    decoded = f"HEARTBEAT type={types.get(ty,ty)} autopilot={ap} mode=0x{bm:02x} status={ss}"
                elif len(raw) >= 3:
                    cmd, res = struct.unpack_from("<HB", raw, 0)
                    results = {0:"ACCEPTED",1:"DENIED",4:"FAILED"}
                    decoded = f"ACK cmd={cmd} result={results.get(res,res)}"
            except: pass
        elif proto == "HTTP" and resp:
            decoded = resp[:80]
        elif proto == "WebSocket" and resp:
            decoded = resp[:80]
        # Direction
        src_ip = "172.40.0.200"  # attacker
        dst_ip = target.split(":")[0] if ":" in target else target
        dst_port = target.split(":")[-1] if ":" in target else "?"
        packets.append({
            "seq": i, "timestamp": r["timestamp"], "level": lv,
            "protocol": proto, "action": action[:30],
            "src": src_ip, "dst": dst_ip, "dst_port": dst_port,
            "size_bytes": len(resp)//2 if resp and all(c in "0123456789abcdef" for c in resp[:8].lower()) else len(resp),
            "duration_ms": round(dur, 1), "success": ok,
            "decoded": decoded[:120],
            "response_hex": resp[:32] if resp else "",
        })
    # OMNeT++ trace summary
    omnet_trace = Path("omnetpp_trace")
    omnet_files = {}
    for f in ["attack_scenario.xml","traffic_trace.csv","mtd_events.csv","replay.ini"]:
        p = omnet_trace / f
        omnet_files[f] = {"exists": p.exists(), "size": p.stat().st_size if p.exists() else 0}
    return {
        "packets": packets[-150:],
        "total_packets": len(packets),
        "by_protocol": {},
        "omnet_files": omnet_files,
    }

@app.get("/api/all")
def all_data():
    return {
        "summary": summary(), "table2": table2(), "table3": table3(),
        "table4": table4(), "table5": table5(), "table6": table6(),
        "timeline": timeline()[-50:], "attacker_log": attacker_log()[-100:],
        "agent_state": agent_state(), "packet_flow": packet_flow(),
    }

app.mount("/", StaticFiles(directory="results/dashboard", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
