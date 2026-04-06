#!/usr/bin/env python3
"""MIRAGE-UAS Dashboard API Server — serves metrics JSON for live dashboard."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import json, csv
from pathlib import Path

app = FastAPI(title="MIRAGE-UAS Dashboard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

R = Path("results")

def _load(p):
    try: return json.loads(p.read_text()) if p.exists() else {}
    except Exception: return {}

def _load_list(p):
    try: return json.loads(p.read_text()) if p.exists() else []
    except Exception: return []

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
def timeline():
    p = R/"metrics/deception_timeline.jsonl"
    if not p.exists(): return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

@app.get("/api/attacker_log")
def attacker_log():
    p = R/"attacker_log.jsonl"
    if not p.exists(): return []
    lines = p.read_text().splitlines()
    return [json.loads(l) for l in lines[-200:] if l.strip()]

@app.get("/api/mtd_events")
def mtd_events():
    p = Path("omnetpp_trace/mtd_events.csv")
    if not p.exists(): return []
    return list(csv.DictReader(open(p)))

@app.get("/api/all")
def all_data():
    return {
        "summary": summary(), "table2": table2(), "table3": table3(),
        "table4": table4(), "table5": table5(), "table6": table6(),
        "timeline": timeline()[-50:], "attacker_log": attacker_log()[-100:],
    }

app.mount("/", StaticFiles(directory="results/dashboard", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
