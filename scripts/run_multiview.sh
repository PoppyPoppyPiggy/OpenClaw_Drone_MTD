#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# MIRAGE-UAS Multi-Terminal Live View
#
# tmux 6-pane layout showing every component in real time:
#   Pane 0 (top-left):     Honeydrone 01 logs (OpenClaw OODA decisions)
#   Pane 1 (top-center):   Honeydrone 02 logs
#   Pane 2 (top-right):    Honeydrone 03 logs
#   Pane 3 (bottom-left):  Attacker L0→L4 (live attack feed)
#   Pane 4 (bottom-center): Live Packet Monitor (colored by level)
#   Pane 5 (bottom-right):  Metrics + MTD + BeliefState live
#
# Usage: bash scripts/run_multiview.sh
#        ATTACKER_LEVEL_DURATION_SEC=120 bash scripts/run_multiview.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="$(pwd)"

LEVEL_DURATION=${ATTACKER_LEVEL_DURATION_SEC:-120}
TOTAL_MIN=$(( LEVEL_DURATION * 5 / 60 ))

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

SESSION="mirage"

# ── Check tmux ────────────────────────────────────────────────
if ! command -v tmux &>/dev/null; then
    echo -e "${RED}tmux not installed. Install: sudo apt install tmux${NC}"
    exit 1
fi

# ── Cleanup function ─────────────────────────────────────────
cleanup() {
    echo -e "\n${CYAN}Shutting down...${NC}"
    docker rm -f honeydrone_01 honeydrone_02 honeydrone_03 mirage_attacker 2>/dev/null || true
    rm -f results/.engine_running
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    echo -e "${GREEN}All stopped.${NC}"
}
trap cleanup EXIT

# ── Prerequisites ────────────────────────────────────────────
echo -e "${CYAN}[1/5] Prerequisites...${NC}"
docker info > /dev/null 2>&1 || { echo -e "${RED}Docker not running${NC}"; exit 1; }
pip install -q python-dotenv pymavlink structlog aiohttp stix2 websockets matplotlib 2>/dev/null
mkdir -p results/logs results/metrics results/figures results/latex results/dataset
rm -f results/attacker_log.jsonl results/.engine_running results/logs/*.log
rm -f results/metrics/confusion_honey_*.json results/metrics/decisions_honey_*.json results/metrics/cti_honey_*.json
echo -e "${GREEN}  OK${NC}"

# ── Build images ─────────────────────────────────────────────
echo -e "${CYAN}[2/5] Building Docker images...${NC}"
docker build -q -f docker/Dockerfile.honeydrone -t mirage-honeydrone:latest . > /dev/null
docker build -q -f docker/Dockerfile.attacker -t mirage-attacker:latest . > /dev/null
docker network create --subnet 172.40.0.0/24 test_net 2>/dev/null || true
echo -e "${GREEN}  Images ready${NC}"

# ── Start honeydrone containers ──────────────────────────────
echo -e "${CYAN}[3/5] Starting honeydrone fleet...${NC}"
docker rm -f honeydrone_01 honeydrone_02 honeydrone_03 mirage_attacker 2>/dev/null || true
for N in 1 2 3; do
    docker run -d \
        --name "honeydrone_0${N}" \
        --network test_net \
        --ip "172.40.0.1${N}" \
        --memory 512m \
        -e "DRONE_ID=honey_0${N}" \
        -e "INDEX=${N}" \
        -e "RESULTS_DIR=/results" \
        -v "$(pwd)/results:/results:rw" \
        mirage-honeydrone:latest > /dev/null
    echo -e "  ${GREEN}honeydrone_0${N} @ 172.40.0.1${N}${NC}"
done

# Wait healthy
echo -e "  ${YELLOW}Waiting for health...${NC}"
for attempt in $(seq 1 20); do
    H=0
    for N in 1 2 3; do
        S=$(docker inspect --format='{{.State.Health.Status}}' "honeydrone_0${N}" 2>/dev/null || echo "starting")
        [ "$S" = "healthy" ] && H=$((H+1))
    done
    [ "$H" -ge 3 ] && break
    sleep 3
done
echo -e "  ${GREEN}${H}/3 healthy${NC}"

# ── Create tmux session ──────────────────────────────────────
echo -e "${CYAN}[4/5] Creating tmux multi-view...${NC}"
tmux kill-session -t "$SESSION" 2>/dev/null || true
touch results/attacker_log.jsonl

# ── Shared log filter (used by all 3 drone panes) ────────────
# Writes to a temp file so tmux can source it
FILTER_SCRIPT=$(mktemp /tmp/mirage_filter_XXXX.py)
cat > "$FILTER_SCRIPT" << 'PYEOF'
import sys, json
# Colors
R='\033[0m'; B='\033[1m'
RED='\033[91m'; GRN='\033[92m'; YLW='\033[93m'; BLU='\033[94m'; MAG='\033[95m'; CYN='\033[96m'; DIM='\033[2m'

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    # Non-JSON lines (startup banner, aiohttp access logs)
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        if 'MIRAGE' in line or '═' in line:
            print(f'{GRN}{line}{R}', flush=True)
        continue

    ev = d.get('event', '')
    drone = d.get('drone_id', '')

    # ── DQN / MAB selection (most important) ──
    if 'mab_action' in ev:
        act = d.get('action', '?')
        mode = d.get('mode', '?')
        q = d.get('q_best', '')
        q_str = f' Q={q}' if q else ''
        print(f'{B}{MAG}[DQN] {act}{q_str} mode={mode} step={d.get("step","")}{R}', flush=True)

    elif 'mab_reward' in ev:
        print(f'{MAG}[REWARD] {d.get("action","?")} r={d.get("reward","")} avg={d.get("avg_reward","")}{R}', flush=True)

    elif 'reward_computed' in ev:
        print(f'{MAG}[R-DETAIL] Δbelief={d.get("delta_belief","")} pkts+{d.get("new_packets","")} → r={d.get("reward","")}{R}', flush=True)

    # ── OODA cycle (per-packet) ──
    elif ev == 'ooda_cycle':
        tool = d.get('tool', '?')
        phase = d.get('phase', '?')
        src = d.get('response_source', '?')
        p = d.get('p_real', 0)
        msg = d.get('msg_type', '?')
        cmds = d.get('cmds', 0)
        # Only show every 5th to reduce noise, or all if tool changes
        if cmds <= 3 or cmds % 5 == 0:
            print(f'{DIM}[OODA] msg={msg} tool={tool} phase={phase} src={src} P(r)={p} cmds={cmds}{R}', flush=True)

    elif ev == 'ooda_ws_cycle':
        print(f'{CYN}[WS-OODA] tool={d.get("tool","")} phase={d.get("phase","")} resp={d.get("response_type","")}{R}', flush=True)

    # ── Agent decisions ──
    elif 'agent_decision' in ev:
        beh = d.get('behavior', '')
        rat = d.get('rationale', '')[:60]
        print(f'{B}{YLW}[DECIDE] {beh}: {rat}{R}', flush=True)

    # ── Tool / phase changes ──
    elif 'tool_identified' in ev:
        print(f'{B}{RED}[TOOL] {d.get("from_tool","")} → {d.get("to_tool","")} evidence={d.get("evidence","")}{R}', flush=True)

    elif 'attack_phase' in ev:
        print(f'{B}{RED}[PHASE] {d.get("old_phase","")} → {d.get("new_phase","")}{R}', flush=True)

    # ── MTD triggers ──
    elif 'mtd_trigger' in ev:
        print(f'{CYN}[MTD] L={d.get("level","")} urg={d.get("urgency","")} #{d.get("count","")}{R}', flush=True)

    # ── Belief state ──
    elif 'belief' in ev and 'updated' in ev:
        print(f'{BLU}[BELIEF] P(r)={d.get("p_real","")} ip={d.get("attacker_ip","")}{R}', flush=True)

    # ── Proactive behaviors ──
    elif 'proactive_' in ev or 'false_flag' in ev:
        detail = ''
        if 'statustext' in ev:
            detail = d.get('message', '')
        elif 'ghost' in ev:
            detail = f'port={d.get("port","")}'
        elif 'reboot' in ev:
            detail = f'{d.get("silence_sec","?")}s silence' if 'start' in ev else f'sysid={d.get("new_sysid","")}'
        elif 'fake_key' in ev:
            detail = f'key={d.get("key_preview","")}'
        elif 'false_flag' in ev:
            detail = f'sysid {d.get("original_sysid","→")}{d.get("fake_sysid","")}'
        print(f'{YLW}[PROACTIVE] {ev} {detail}{R}', flush=True)

    # ── Startup ──
    elif 'started' in ev or 'initialized' in ev or 'loaded' in ev:
        mode = d.get('policy_mode', d.get('mode', ''))
        extra = f' [{mode}]' if mode else ''
        print(f'{GRN}[INIT] {ev}{extra}{R}', flush=True)

    # ── Session events ──
    elif 'session' in ev or 'connect' in ev:
        print(f'{DIM}[CONN] {ev} ip={d.get("attacker_ip",d.get("src_ip",""))}{R}', flush=True)

    # ── Metrics save ──
    elif 'metrics_saved' in ev:
        print(f'{DIM}[SAVE] mtd={d.get("mtd","")} cti={d.get("cti","")} dec={d.get("decisions","")} conf={d.get("confusion","")}{R}', flush=True)
PYEOF

# Create session with first pane (honeydrone_01 logs)
tmux new-session -d -s "$SESSION" -n "MIRAGE" \
    "echo '═══ HONEYDRONE 01 — OpenClaw DQN ═══'; docker logs -f honeydrone_01 2>&1 | python3 -u '$FILTER_SCRIPT'; exec bash"

# Pane 1: honeydrone_02
tmux split-window -h -t "$SESSION" \
    "echo '═══ HONEYDRONE 02 — OpenClaw DQN ═══'; docker logs -f honeydrone_02 2>&1 | python3 -u '$FILTER_SCRIPT'; exec bash"

# Pane 2: honeydrone_03
tmux split-window -h -t "$SESSION" \
    "echo '═══ HONEYDRONE 03 — OpenClaw DQN ═══'; docker logs -f honeydrone_03 2>&1 | python3 -u '$FILTER_SCRIPT'; exec bash"

# Pane 3: Attacker (bottom-left)
tmux split-window -v -t "$SESSION:0.0" \
    "echo '═══ ATTACKER L0→L4 (${LEVEL_DURATION}s/level = ${TOTAL_MIN}min) ═══'; echo 'Starting in 3s...'; sleep 3; \
docker run --rm --name mirage_attacker --network test_net --ip 172.40.0.200 \
    -e ATTACKER_LEVEL_DURATION_SEC=${LEVEL_DURATION} \
    -e 'HONEY_DRONE_TARGETS=172.40.0.11:14550,172.40.0.12:14550,172.40.0.13:14550' \
    -e WEBCLAW_PORT_BASE=18789 -e HTTP_PORT_BASE=79 -e RESULTS_DIR=/results \
    -v '${PROJECT}/results:/results:rw' \
    mirage-attacker:latest 2>&1; \
echo ''; echo '═══ ATTACK COMPLETE ═══'; \
echo 'Computing metrics...'; \
cd '${PROJECT}'; \
python3 -c \"
import sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from evaluation.compute_all import compute_all_metrics
r = compute_all_metrics('results/attacker_log.jsonl','results')
print(f'  Sessions: {r[\\\"total_sessions\\\"]}')
print(f'  DS={r[\\\"deception_score\\\"]:.4f} CS={r[\\\"avg_confusion_score\\\"]:.4f}')
print(f'  MTD={r[\\\"total_mtd_actions\\\"]} TTPs={r[\\\"unique_ttps\\\"]}')
\"; \
python3 -c \"
import sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from omnetpp.trace_exporter import main; main()
\" 2>/dev/null; \
echo 'Done. Press Enter to exit.'; read; exec bash"

# Pane 4: Live packet monitor (bottom-center)
tmux split-window -h -t "$SESSION:0.3" \
    "echo '═══ LIVE PACKET FEED ═══'; \
tail -f '${PROJECT}/results/attacker_log.jsonl' 2>/dev/null | python3 -u -c \"
import sys,json
colors = {'0':'\\033[90m','1':'\\033[94m','2':'\\033[93m','3':'\\033[95m','4':'\\033[91m'}
R='\\033[0m'
counts = {0:0,1:0,2:0,3:0,4:0}
for line in sys.stdin:
    try:
        r = json.loads(line)
        lv = r.get('level',-1)
        if lv < 0: continue
        counts[lv] = counts.get(lv,0)+1
        c = colors.get(str(lv),'')
        ok = 'timeout' not in r.get('action','') and 'fail' not in r.get('action','')
        mark = '++' if ok else 'XX'
        resp = r.get('response_preview','')[:25]
        act = r.get('action','')[:28]
        total = sum(counts.values())
        print(f'{c}L{lv} [{counts[lv]:3d}] {act:28s} {mark} {resp}{R}  (total={total})',flush=True)
    except: pass
\"; exec bash"

# Pane 5: Metrics live monitor (bottom-right)
tmux split-window -h -t "$SESSION:0.4" \
    "echo '═══ LIVE METRICS + BELIEF STATE ═══'; \
while true; do \
    clear; \
    echo '═══ LIVE METRICS ═══'; \
    echo ''; \
    if [ -f '${PROJECT}/results/metrics/confusion_honey_01.json' ]; then \
        python3 -c \"
import json
from pathlib import Path
m = Path('${PROJECT}/results/metrics')

# Confusion / Belief
print('--- Belief State (P_real) ---')
for i in range(1,4):
    f = m / f'confusion_honey_0{i}.json'
    if f.exists():
        d = json.loads(f.read_text())
        avg = d.get('avg_confusion_score',0)
        beliefs = d.get('per_engine',[{}])[0].get('beliefs',[])
        obs = beliefs[0].get('total_observations',0) if beliefs else 0
        tgt = beliefs[0].get('belief_target','?') if beliefs else '?'
        print(f'  honey_0{i}: P(real)={avg:.4f}  obs={obs}  belief={tgt}')

# MTD
print('')
print('--- MTD Results ---')
mtd_f = m / 'live_mtd_results.json'
if mtd_f.exists():
    mtd = json.loads(mtd_f.read_text())
    from collections import Counter
    types = Counter(d.get('action_type','?') for d in mtd)
    for t,c in types.most_common():
        print(f'  {t}: {c}')
    print(f'  Total: {len(mtd)}')

# Agent decisions
print('')
print('--- Agent Decisions ---')
total_dec = 0
from collections import Counter
all_beh = Counter()
for i in range(1,4):
    f = m / f'decisions_honey_0{i}.json'
    if f.exists():
        decs = json.loads(f.read_text())
        total_dec += len(decs)
        for d in decs:
            all_beh[d.get('behavior_triggered',d.get('behavior','?'))] += 1
for b,c in all_beh.most_common(8):
    print(f'  {b}: {c}')
print(f'  Total: {total_dec}')

# CTI
print('')
print('--- CTI Events ---')
all_ttps = set()
total_cti = 0
for i in range(1,4):
    f = m / f'cti_honey_0{i}.json'
    if f.exists():
        d = json.loads(f.read_text())
        total_cti += d.get('total_events',0)
        all_ttps.update(d.get('unique_ttps',[]))
print(f'  Events: {total_cti}  TTPs: {sorted(all_ttps)}')

# Attacker log stats
print('')
print('--- Attacker Log ---')
log = Path('${PROJECT}/results/attacker_log.jsonl')
if log.exists():
    lines = log.read_text().strip().split('\\n')
    valid = 0; fails = 0
    for l in lines:
        try:
            r = json.loads(l)
            lv = r.get('level',-1)
            if lv < 0: continue
            valid += 1
            if 'fail' in r.get('action','') or 'timeout' in r.get('action',''):
                fails += 1
        except: pass
    print(f'  Records: {valid}  Fails: {fails} ({fails*100//max(valid,1)}%)')
\" 2>/dev/null; \
    else \
        echo 'Waiting for data...'; \
    fi; \
    sleep 5; \
done; exec bash"

# ── Layout ───────────────────────────────────────────────────
tmux select-layout -t "$SESSION" tiled 2>/dev/null || true

# ── Attach ───────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  MIRAGE-UAS Multi-View Ready${NC}"
echo -e "${GREEN}  ${TOTAL_MIN}min experiment (${LEVEL_DURATION}s × 5 levels)${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Pane 0-2: Honeydrone 01/02/03 OODA decisions"
echo -e "  Pane 3:   Attacker L0→L4"
echo -e "  Pane 4:   Live packet feed"
echo -e "  Pane 5:   Metrics + Belief + MTD"
echo ""
echo -e "  ${CYAN}Ctrl+B then arrow keys to navigate panes${NC}"
echo -e "  ${CYAN}Ctrl+B then D to detach (experiment continues)${NC}"
echo ""

tmux attach -t "$SESSION"
