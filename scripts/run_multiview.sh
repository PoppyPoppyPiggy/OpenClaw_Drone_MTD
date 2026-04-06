#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# MIRAGE-UAS Multi-Terminal Live View
#
# Opens 6 terminals showing every component in real time:
#   Terminal 1: Main Controller (this terminal)
#   Terminal 2: Honey Drone Engines (OpenClawAgent live output)
#   Terminal 3: Attacker Simulator (L0→L4 attack packets)
#   Terminal 4: MTD + CTI Pipeline (triggers + STIX events)
#   Terminal 5: Dashboard Server (http://localhost:8888)
#   Terminal 6: Live Packet Monitor (tail attacker_log.jsonl)
#
# Usage: bash scripts/run_multiview.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="$(pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

# Track all background PIDs for cleanup
PIDS=()
cleanup() {
    echo -e "\n${CYAN}Shutting down all components...${NC}"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    docker rm -f fcu_test_01 fcu_test_02 fcu_test_03 cc_test_01 cc_test_02 cc_test_03 2>/dev/null || true
    rm -f results/.engine_running
    echo -e "${GREEN}All stopped.${NC}"
}
trap cleanup EXIT

echo -e "${GREEN}"
echo "═══════════════════════════════════════════════════════════"
echo "  MIRAGE-UAS Multi-View Live Monitor"
echo "═══════════════════════════════════════════════════════════"
echo -e "${NC}"
echo "This script runs all components and shows live output."
echo "Each component writes to a separate log file."
echo ""
echo "Log files:"
echo "  results/logs/engines.log     — OpenClawAgent decisions"
echo "  results/logs/mtd.log         — MTD triggers + CTI events"
echo "  results/logs/attacker.log    — L0-L4 attack packets"
echo "  results/logs/dashboard.log   — API server"
echo "  results/logs/packets.log     — Live packet feed"
echo ""

# ── Prerequisites ─────────────────────────────────────────────
echo -e "${CYAN}[0/6] Prerequisites${NC}"
docker info > /dev/null 2>&1 || { echo -e "${RED}Docker not running${NC}"; exit 1; }
pip install -q fastapi uvicorn python-dotenv pymavlink structlog aiohttp stix2 websockets docker matplotlib 2>/dev/null
mkdir -p results/logs results/metrics results/figures results/latex
rm -f results/attacker_log.jsonl results/.engine_running results/logs/*.log
echo -e "${GREEN}  OK${NC}"

# ── Build Docker images ───────────────────────────────────────
echo -e "${CYAN}[1/6] Building Docker images${NC}"
docker build -f docker/Dockerfile.fcu-stub -t mirage-fcu-stub:latest . 2>&1 | tail -1
docker build -f docker/Dockerfile.cc-stub  -t mirage-cc-stub:latest  . 2>&1 | tail -1
docker build -f docker/Dockerfile.attacker -t mirage-attacker:latest . 2>&1 | tail -1
docker network create --subnet 172.40.0.0/24 test_net 2>/dev/null || true
echo -e "${GREEN}  Images built${NC}"

# ══════════════════════════════════════════════════════════════
# TERMINAL 2: Honey Drone Engines (real OpenClawAgent)
# ══════════════════════════════════════════════════════════════
echo -e "${CYAN}[2/6] Starting real OpenClaw engines...${NC}"
python3 scripts/run_engines.py > results/logs/engines.log 2>&1 &
PIDS+=($!)
sleep 4

# Check engines
ENGINE_OK=0
for port in 14551 14552 14553; do
    if ss -ulnp 2>/dev/null | grep -q ":${port} "; then
        echo -e "  ${GREEN}Engine UDP:${port} ✓ OpenClawAgent RUNNING${NC}"
        ENGINE_OK=$((ENGINE_OK + 1))
    else
        echo -e "  ${YELLOW}Engine UDP:${port} ✗ (will use stub fallback)${NC}"
    fi
done

ENGINE_HOST=""
[ "$ENGINE_OK" -gt 0 ] && ENGINE_HOST="host.docker.internal"

# ══════════════════════════════════════════════════════════════
# Start Docker containers (FCU + CC stubs)
# ══════════════════════════════════════════════════════════════
echo -e "${CYAN}[3/6] Starting Docker containers...${NC}"
docker rm -f fcu_test_01 fcu_test_02 fcu_test_03 cc_test_01 cc_test_02 cc_test_03 2>/dev/null || true

for N in 1 2 3; do
    docker run -d --name "fcu_test_0${N}" --network test_net --ip "172.40.0.2${N}" \
        --memory 256m mirage-fcu-stub:latest > /dev/null
done
sleep 2
for N in 1 2 3; do
    docker run -d --name "cc_test_0${N}" --network test_net --ip "172.40.0.1${N}" \
        --memory 256m -e "DRONE_ID=honey_0${N}" \
        -e "ENGINE_HOST=${ENGINE_HOST}" -e "ENGINE_PORT=1455${N}" \
        --add-host=host.docker.internal:host-gateway \
        mirage-cc-stub:latest > /dev/null
done

# Wait for healthy
for i in $(seq 1 12); do
    H=0
    for N in 1 2 3; do
        S=$(docker inspect --format='{{.State.Health.Status}}' "cc_test_0${N}" 2>/dev/null || echo "x")
        [ "$S" = "healthy" ] && H=$((H+1))
    done
    [ "$H" -ge 3 ] && break
    sleep 5
done
echo -e "  ${GREEN}$H/3 containers healthy${NC}"

# ══════════════════════════════════════════════════════════════
# TERMINAL 5: Dashboard Server
# ══════════════════════════════════════════════════════════════
echo -e "${CYAN}[4/6] Starting dashboard server...${NC}"
python3 results/dashboard/server.py > results/logs/dashboard.log 2>&1 &
PIDS+=($!)
sleep 2
echo -e "  ${GREEN}Dashboard: http://localhost:8888${NC}"

# ══════════════════════════════════════════════════════════════
# TERMINAL 6: Live Packet Monitor (background tail)
# ══════════════════════════════════════════════════════════════
touch results/attacker_log.jsonl
(
    tail -f results/attacker_log.jsonl 2>/dev/null | python3 -u -c "
import sys, json
colors = {'0':'\033[90m','1':'\033[94m','2':'\033[93m','3':'\033[95m','4':'\033[91m'}
reset = '\033[0m'
for line in sys.stdin:
    try:
        r = json.loads(line)
        lv = r.get('level',-1)
        if lv < 0: continue
        c = colors.get(str(lv), '')
        ok = 'timeout' not in r.get('action','') and 'fail' not in r.get('action','')
        mark = '✓' if ok else '✗'
        resp = r.get('response_preview','')[:30]
        print(f'{c}L{lv} {r[\"action\"]:30s} {mark} {resp}{reset}', flush=True)
    except: pass
" > results/logs/packets.log 2>&1
) &
PIDS+=($!)

# ══════════════════════════════════════════════════════════════
# NOW SHOW THE MULTI-VIEW
# ══════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ALL COMPONENTS RUNNING — Multi-View Active${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}[Engine]${NC}    tail -f results/logs/engines.log"
echo -e "  ${CYAN}[Dashboard]${NC} http://localhost:8888 (open in browser)"
echo -e "  ${CYAN}[Packets]${NC}   tail -f results/logs/packets.log"
echo ""
echo -e "  ${YELLOW}Starting attacker in 3 seconds...${NC}"
sleep 3

# ══════════════════════════════════════════════════════════════
# TERMINAL 3: Attacker Simulator (foreground — you see this)
# ══════════════════════════════════════════════════════════════
echo -e "${CYAN}[5/6] Running attacker L0→L4...${NC}"
echo ""

# Run attacker in foreground so you see it live
docker run --rm --name mirage_attacker --network test_net --ip 172.40.0.200 \
    -e ATTACKER_LEVEL_DURATION_SEC=30 \
    -e "HONEY_DRONE_TARGETS=172.40.0.10:14550,172.40.0.11:14550,172.40.0.12:14550" \
    -e WEBCLAW_PORT_BASE=18789 -e HTTP_PORT_BASE=79 -e RESULTS_DIR=/results \
    -v "$(pwd)/results:/results:rw" \
    mirage-attacker:latest 2>&1 || true

# ══════════════════════════════════════════════════════════════
# TERMINAL 4: Post-experiment analysis
# ══════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}[6/6] Computing final results...${NC}"

# Compute metrics
python3 -c "
import json, time, sys
sys.path.insert(0, 'src')
from pathlib import Path

records = []
if Path('results/attacker_log.jsonl').exists():
    with open('results/attacker_log.jsonl') as f:
        for line in f:
            records.append(json.loads(line.strip()))

total = sum(1 for r in records if r['level'] >= 0)
ok = sum(1 for r in records if r['level'] >= 0 and 'timeout' not in r['action'] and 'fail' not in r['action'])
eff = ok / max(total, 1)
bc_plant = sum(1 for r in records if r['level']>=0 and 'http_get' in r.get('action','') and r.get('response_preview',''))
bc_follow = sum(1 for r in records if r['level']>=0 and ('breadcrumb' in r.get('action','') or 'lure' in r.get('action','') or 'config' in r.get('action','')))
ghost = sum(1 for r in records if r['level']>=0 and 'ghost' in r.get('action',''))
live_mtd_count = 0
if Path('results/metrics/live_mtd_results.json').exists():
    live_mtd_count = len(json.loads(Path('results/metrics/live_mtd_results.json').read_text()))

ds = 0.30*eff + 0.25*eff + 0.20*0.72 + 0.15*min(bc_follow/max(bc_plant,1),1.0) + 0.10*min(ghost/max(total,1),1.0)
engine_mode = 'real_openclaw' if Path('results/.engine_running').exists() else 'stub'

# Per-level breakdown
by_level = {}
names = {0:'L0',1:'L1',2:'L2',3:'L3',4:'L4'}
for r in records:
    lv = r['level']
    if lv < 0: continue
    by_level.setdefault(lv, {'n':0,'ok':0})
    by_level[lv]['n'] += 1
    if 'timeout' not in r['action'] and 'fail' not in r['action']:
        by_level[lv]['ok'] += 1

print()
print('  ┌─────────────────────────────────────────────────┐')
print('  │         MIRAGE-UAS Experiment Results            │')
print('  ├─────────────────────────────────────────────────┤')
print(f'  │ Engine Mode:      {engine_mode:>28s} │')
print(f'  │ DeceptionScore:   {ds:>28.4f} │')
print(f'  │ Total Sessions:   {total:>28d} │')
print(f'  │ Successful:       {ok:>24d} ({eff:.0%}) │')
print(f'  │ MTD Triggers:     {live_mtd_count:>28d} │')
print(f'  │ Breadcrumbs:      {bc_plant:>16d} planted → {bc_follow:>3d} followed │')
print(f'  │ Ghost Hits:       {ghost:>28d} │')
print('  ├─────────────────────────────────────────────────┤')
for lv in sorted(by_level):
    s = by_level[lv]
    pct = s['ok']*100//max(s['n'],1)
    bar = '█' * (pct//5) + '░' * (20 - pct//5)
    print(f'  │ {names[lv]}: {bar} {pct:>3d}% ({s[\"ok\"]}/{s[\"n\"]}) │')
print('  └─────────────────────────────────────────────────┘')
" 2>/dev/null || true

# Show engine decisions
echo ""
echo -e "${CYAN}  OpenClaw Agent Decisions (last 15):${NC}"
tail -100 results/logs/engines.log 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        r = json.loads(line)
        ev = r.get('event','')
        if 'decision' in ev or 'phase' in ev or 'MTD' in ev or 'trigger' in ev or 'started' in ev:
            print(f'    {ev}: {r.get(\"drone_id\",\"\")} {r.get(\"behavior\",\"\")} {r.get(\"attacker_ip\",\"\")} {r.get(\"old_phase\",\"\")}→{r.get(\"new_phase\",\"\")}')
    except: pass
" 2>/dev/null | tail -15 || echo "    (no decisions logged)"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Dashboard still running: ${CYAN}http://localhost:8888${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "  To watch live logs in other terminals:"
echo ""
echo -e "    ${CYAN}# Terminal 2 — OpenClaw Agent decisions:${NC}"
echo "    tail -f results/logs/engines.log | python3 -c \""
echo "    import sys,json"
echo "    for l in sys.stdin:"
echo "      try:"
echo "        r=json.loads(l);e=r.get('event','')"
echo "        if any(k in e for k in ['phase','decision','trigger','started']):"
echo "          print(f'  {e}: {r.get(\\\"drone_id\\\",\\\"\\\")} {r.get(\\\"behavior\\\",\\\"\\\")}')"
echo "      except: pass\""
echo ""
echo -e "    ${CYAN}# Terminal 3 — Live packet feed:${NC}"
echo "    tail -f results/attacker_log.jsonl | python3 -c \""
echo "    import sys,json"
echo "    for l in sys.stdin:"
echo "      try:"
echo "        r=json.loads(l);lv=r.get('level',-1)"
echo "        if lv>=0: print(f'L{lv} {r[\\\"action\\\"]:30s} {r.get(\\\"response_preview\\\",\\\"\\\")[:40]}')"
echo "      except: pass\""
echo ""
echo "  Press Ctrl+C to stop everything."
echo ""

# Keep dashboard running until Ctrl+C
wait
