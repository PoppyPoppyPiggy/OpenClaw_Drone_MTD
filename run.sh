#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# MIRAGE-UAS — Full Experiment Pipeline
#
# Runs: OpenClaw honeydrones → attacker L0-L4 (10min each)
#       → metrics → OMNeT++ traces → figures → dashboard
#
# Usage: bash run.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${CYAN}[$1/8]${NC} $2"; }

# ── Duration config ──────────────────────────────────────────
LEVEL_DURATION=${ATTACKER_LEVEL_DURATION_SEC:-600}  # 10 min per level
TOTAL_SEC=$((LEVEL_DURATION * 5))
TOTAL_MIN=$((TOTAL_SEC / 60))

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  MIRAGE-UAS Full Experiment${NC}"
echo -e "${GREEN}  OpenClaw AgenticDecoyEngine + L0-L4 Attacker${NC}"
echo -e "${GREEN}  Duration: ${LEVEL_DURATION}s × 5 levels = ${TOTAL_MIN} min${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"

# ── 1. Prerequisites ─────────────────────────────────────────
step 1 "Checking prerequisites..."
python3 --version > /dev/null 2>&1 || { echo -e "${RED}Python3 not found${NC}"; exit 1; }
docker info > /dev/null 2>&1 || { echo -e "${RED}Docker not running${NC}"; exit 1; }
pip install -q fastapi uvicorn python-dotenv pymavlink structlog aiohttp stix2 matplotlib websockets 2>/dev/null
echo -e "${GREEN}  OK${NC}"

# ── 2. Config ─────────────────────────────────────────────────
step 2 "Checking config/.env..."
if [ ! -f config/.env ]; then
    cp config/.env.example config/.env
    echo "  Copied .env.example → .env"
fi
python3 -c "
import sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from shared import constants
print('  .env loaded OK')
" || { echo -e "${RED}  Failed to load config${NC}"; exit 1; }

# ── 3. Clean previous results ────────────────────────────────
step 3 "Cleaning previous results..."
mkdir -p results/metrics results/logs results/figures results/latex results/dataset
rm -f results/attacker_log.jsonl
rm -f results/.engine_running
rm -f results/metrics/live_*.json results/metrics/confusion_*.json
rm -f results/metrics/cti_*.json results/metrics/decisions_*.json
echo -e "${GREEN}  Clean slate${NC}"

# ── 4. Build Docker images ───────────────────────────────────
step 4 "Building Docker images..."
echo "  Building honeydrone (OpenClaw engine)..."
docker build -q -f docker/Dockerfile.honeydrone -t mirage-honeydrone:latest . > /dev/null
echo "  Building attacker (L0-L4 simulator)..."
docker build -q -f docker/Dockerfile.attacker -t mirage-attacker:latest . > /dev/null
echo -e "${GREEN}  Images ready${NC}"

# ── 5. Start honeydrone containers ───────────────────────────
step 5 "Starting OpenClaw honeydrone fleet..."
docker network create --subnet 172.40.0.0/24 test_net 2>/dev/null || true

# Clean any leftover containers
docker rm -f honeydrone_01 honeydrone_02 honeydrone_03 mirage_attacker 2>/dev/null || true

for N in 1 2 3; do
    IP="172.40.0.1${N}"
    docker run -d \
        --name "honeydrone_0${N}" \
        --network test_net \
        --ip "${IP}" \
        --memory 512m \
        -e "DRONE_ID=honey_0${N}" \
        -e "INDEX=${N}" \
        -e "RESULTS_DIR=/results" \
        -v "$(pwd)/results:/results:rw" \
        mirage-honeydrone:latest > /dev/null
    echo "  honeydrone_0${N} @ ${IP} [MAVLink:14550 WS:18789 HTTP:80]"
done

# Wait for health
echo -e "  ${YELLOW}Waiting for honeydrones to be healthy...${NC}"
for attempt in $(seq 1 30); do
    HEALTHY=0
    for N in 1 2 3; do
        S=$(docker inspect --format='{{.State.Health.Status}}' "honeydrone_0${N}" 2>/dev/null || echo "starting")
        [ "$S" = "healthy" ] && HEALTHY=$((HEALTHY+1))
    done
    if [ "$HEALTHY" -ge 3 ]; then
        echo -e "  ${GREEN}All 3 honeydrones healthy${NC}"
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo -e "  ${YELLOW}Warning: Not all drones healthy yet, proceeding anyway${NC}"
        # Show logs for debugging
        for N in 1 2 3; do
            echo "  --- honeydrone_0${N} logs ---"
            docker logs "honeydrone_0${N}" 2>&1 | tail -3
        done
    fi
    sleep 5
done

# ── 6. Run attacker simulation ───────────────────────────────
step 6 "Running attacker L0→L4 (${LEVEL_DURATION}s per level = ${TOTAL_MIN} min total)..."
echo ""
echo -e "  ${YELLOW}Experiment started at $(date '+%H:%M:%S')${NC}"
echo -e "  ${YELLOW}Estimated completion: $(date -d "+${TOTAL_SEC} seconds" '+%H:%M:%S' 2>/dev/null || date -v+${TOTAL_SEC}S '+%H:%M:%S' 2>/dev/null || echo "~${TOTAL_MIN}min from now")${NC}"
echo ""

docker run --rm \
    --name mirage_attacker \
    --network test_net \
    --ip 172.40.0.200 \
    -e "ATTACKER_LEVEL_DURATION_SEC=${LEVEL_DURATION}" \
    -e "HONEY_DRONE_TARGETS=172.40.0.11:14550,172.40.0.12:14550,172.40.0.13:14550" \
    -e "WEBCLAW_PORT_BASE=18789" \
    -e "HTTP_PORT_BASE=79" \
    -e "RESULTS_DIR=/results" \
    -v "$(pwd)/results:/results:rw" \
    mirage-attacker:latest 2>&1 | while IFS= read -r line; do
        echo "  [ATK] $line"
    done

echo ""
echo -e "  ${GREEN}Attacker simulation complete${NC}"

# ── Teardown containers ──────────────────────────────────────
echo "  Stopping honeydrones..."
docker rm -f honeydrone_01 honeydrone_02 honeydrone_03 > /dev/null 2>&1 || true

# ── 7. Compute metrics + OMNeT++ traces ─────────────────────
step 7 "Computing metrics + DeceptionScore + OMNeT++ traces..."

# Merge per-drone metrics into consolidated files
python3 -c "
import sys, json, glob
sys.path.insert(0, 'src')
from pathlib import Path

metrics = Path('results/metrics')

# Merge confusion scores
merged_confusion = {'per_engine': [], 'avg_confusion_score': 0.5}
for f in sorted(metrics.glob('confusion_honey_*.json')):
    data = json.loads(f.read_text())
    merged_confusion['per_engine'].extend(data.get('per_engine', []))
if merged_confusion['per_engine']:
    scores = [e['avg_confusion'] for e in merged_confusion['per_engine']]
    merged_confusion['avg_confusion_score'] = round(sum(scores)/len(scores), 4)
(metrics / 'confusion_scores.json').write_text(json.dumps(merged_confusion, indent=2))

# Merge CTI summaries
all_ttps = set()
total_events = 0
for f in sorted(metrics.glob('cti_honey_*.json')):
    data = json.loads(f.read_text())
    total_events += data.get('total_events', 0)
    all_ttps.update(data.get('unique_ttps', []))
(metrics / 'live_cti_summary.json').write_text(json.dumps({
    'total_events': total_events,
    'unique_ttps': sorted(all_ttps),
    'unique_ttp_count': len(all_ttps),
}, indent=2))

# Merge agent decisions
all_decisions = []
for f in sorted(metrics.glob('decisions_honey_*.json')):
    all_decisions.extend(json.loads(f.read_text()))
(metrics / 'live_agent_decisions.json').write_text(json.dumps(all_decisions, indent=2, default=str))

print(f'  Merged: confusion={merged_confusion[\"avg_confusion_score\"]:.4f} '
      f'CTI={total_events} TTPs={len(all_ttps)} decisions={len(all_decisions)}')
"

# Compute all tables + DeceptionScore
python3 -c "
import sys; sys.path.insert(0, 'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from evaluation.compute_all import compute_all_metrics
result = compute_all_metrics('results/attacker_log.jsonl', 'results')
print(f'  Sessions: {result[\"total_sessions\"]} | Success: {result[\"successful_engagements\"]} ({result[\"engagement_rate\"]:.0%})')
print(f'  DeceptionScore = {result[\"deception_score\"]:.4f} (confusion={result[\"avg_confusion_score\"]:.4f} source={result[\"confusion_source\"]})')
print(f'  MTD actions: {result[\"total_mtd_actions\"]} | TTPs: {result[\"unique_ttps\"]}')
"

# Export OMNeT++ traces from real experiment data
python3 -c "
import sys; sys.path.insert(0, 'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from omnetpp.trace_exporter import main as export_traces
export_traces()
" && echo -e "${GREEN}  OMNeT++ traces → omnetpp_trace/${NC}" || echo "  (OMNeT++ export skipped)"

# ── 8. Generate figures + LaTeX ──────────────────────────────
step 8 "Generating paper figures + LaTeX tables..."
python3 -c "
import sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from evaluation.plot_results import main; main()
" 2>/dev/null && echo -e "${GREEN}  Figures → results/figures/${NC}" || echo "  (figures skipped)"

python3 -c "
import sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from evaluation.statistical_test import run_all_tests; run_all_tests()
" 2>/dev/null && echo -e "${GREEN}  LaTeX → results/latex/${NC}" || echo "  (latex skipped)"

# ── Done ─────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  MIRAGE-UAS Experiment Complete${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo ""
echo "  Results:     results/metrics/*.json"
echo "  Figures:     results/figures/*.pdf"
echo "  LaTeX:       results/latex/*.tex"
echo "  OMNeT++:     omnetpp_trace/"
echo "  Dataset:     results/dataset/DVD-CTI-Dataset-v1/"
echo "  Attacker:    results/attacker_log.jsonl"
echo ""

# Optional: launch dashboard
if [ -f results/dashboard/server.py ]; then
    echo -e "  ${CYAN}Dashboard: http://localhost:8888${NC}"
    echo "  Press Ctrl+C to stop"
    echo ""
    python3 results/dashboard/server.py
fi
