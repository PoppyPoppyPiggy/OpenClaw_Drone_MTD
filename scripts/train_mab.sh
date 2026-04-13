#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# MIRAGE-UAS — MAB Training with Live Progress
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

PROACTIVE_SEC=3
LEVEL_SEC=10
ROUND_SEC=$((LEVEL_SEC * 5))
MIN_STEPS=50
TARGET_REWARD=0.65
MAX_ROUNDS=30

clear
echo -e "${BOLD}${GREEN}"
echo "  ╔═══════════════════════════════════════════════════╗"
echo "  ║       MIRAGE-UAS  LinUCB MAB Training            ║"
echo "  ╠═══════════════════════════════════════════════════╣"
echo "  ║  Algorithm : LinUCB (Li et al., WWW 2010)        ║"
echo "  ║  Arms      : 5 proactive behaviors               ║"
echo "  ║  Context   : 8-dim (attacker state + belief)      ║"
echo "  ║  Reward    : 0.7×ΔP(real) + 0.3×engagement       ║"
echo "  ╠═══════════════════════════════════════════════════╣"
echo "  ║  Interval  : ${PROACTIVE_SEC}s (production: 45s)              ║"
echo "  ║  Round     : ${ROUND_SEC}s (L0-L4 × ${LEVEL_SEC}s)                   ║"
echo "  ║  Target    : best_arm avg_reward ≥ ${TARGET_REWARD}          ║"
echo "  ║  Min steps : ${MIN_STEPS}                                    ║"
echo "  ║  Max rounds: ${MAX_ROUNDS}                                    ║"
echo "  ║  ETA       : ~$((MAX_ROUNDS * ROUND_SEC / 60))min worst / ~$((3 * ROUND_SEC / 60))min typical  ║"
echo "  ╚═══════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Build ────────────────────────────────────────────────────
echo -e "${DIM}[prep] Installing dependencies...${NC}"
pip install -q python-dotenv pymavlink structlog aiohttp stix2 websockets numpy 2>/dev/null
echo -e "${DIM}[prep] Building Docker images...${NC}"
docker build -q -f docker/Dockerfile.honeydrone -t mirage-honeydrone:latest . > /dev/null 2>&1
docker build -q -f docker/Dockerfile.attacker -t mirage-attacker:latest . > /dev/null 2>&1
docker network create --subnet 172.40.0.0/24 test_net 2>/dev/null || true

cleanup() {
    docker rm -f honeydrone_01 honeydrone_02 honeydrone_03 mirage_attacker 2>/dev/null || true
    rm -f results/.engine_running
}
trap cleanup EXIT

# ── Start honeydrones ────────────────────────────────────────
cleanup
mkdir -p results/models results/metrics results/logs
rm -f results/attacker_log.jsonl

echo -e "${DIM}[prep] Starting 3 honeydrones (interval=${PROACTIVE_SEC}s)...${NC}"
for N in 1 2 3; do
    docker run -d --name "honeydrone_0${N}" --network test_net --ip "172.40.0.1${N}" \
        --memory 512m -e "DRONE_ID=honey_0${N}" -e "INDEX=${N}" -e "RESULTS_DIR=/results" \
        -e "AGENT_PROACTIVE_INTERVAL_SEC=${PROACTIVE_SEC}" \
        -v "$(pwd)/results:/results:rw" mirage-honeydrone:latest > /dev/null
done
echo -n -e "${DIM}[prep] Health check...${NC}"
for _ in $(seq 1 20); do
    H=0
    for N in 1 2 3; do
        S=$(docker inspect --format='{{.State.Health.Status}}' "honeydrone_0${N}" 2>/dev/null || echo "x")
        [ "$S" = "healthy" ] && H=$((H+1))
    done
    [ "$H" -ge 3 ] && break
    sleep 2
done
echo -e " ${GREEN}${H}/3 healthy${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════
START_TIME=$(date +%s)
ROUND=0
CONVERGED=false
PREV_BEST_REWARD=0

while [ "$ROUND" -lt "$MAX_ROUNDS" ] && [ "$CONVERGED" = "false" ]; do
    ROUND=$((ROUND + 1))
    ELAPSED=$(( $(date +%s) - START_TIME ))
    ELAPSED_MIN=$((ELAPSED / 60))
    ELAPSED_SEC=$((ELAPSED % 60))
    ETA_LEFT=$(( (MAX_ROUNDS - ROUND) * ROUND_SEC ))
    ETA_MIN=$((ETA_LEFT / 60))

    echo -e "${BOLD}${CYAN}┌─────────────────────────────────────────────────┐${NC}"
    echo -e "${BOLD}${CYAN}│  Round ${ROUND}/${MAX_ROUNDS}    elapsed: ${ELAPSED_MIN}m${ELAPSED_SEC}s    worst ETA: ${ETA_MIN}m │${NC}"
    echo -e "${BOLD}${CYAN}└─────────────────────────────────────────────────┘${NC}"

    rm -f results/attacker_log.jsonl

    # Run attacker with countdown
    echo -n -e "  ${DIM}Attacker L0→L4 [${ROUND_SEC}s] ${NC}"
    docker run --rm --name mirage_attacker \
        --network test_net --ip 172.40.0.200 \
        -e "ATTACKER_LEVEL_DURATION_SEC=${LEVEL_SEC}" \
        -e "HONEY_DRONE_TARGETS=172.40.0.11:14550,172.40.0.12:14550,172.40.0.13:14550" \
        -e WEBCLAW_PORT_BASE=18789 -e HTTP_PORT_BASE=79 -e RESULTS_DIR=/results \
        -v "$(pwd)/results:/results:rw" \
        mirage-attacker:latest > /dev/null 2>&1 || true
    echo -e "${GREEN}done${NC}"

    # ── Parse MAB state ──────────────────────────────────────
    python3 -c "
import json, sys
from pathlib import Path

actions = ['statustext', 'flight_sim', 'ghost_port', 'reboot', 'fake_key']
models = sorted(Path('results/models').glob('mab_honey_*.json'))

# Aggregate across drones
agg_n = [0]*5
agg_r = [0.0]*5
total_steps = 0

for f in models:
    d = json.loads(f.read_text())
    sels = d.get('total_selections', [0]*5)
    rews = d.get('total_reward', [0.0]*5)
    for i in range(5):
        agg_n[i] += sels[i] if i < len(sels) else 0
        agg_r[i] += rews[i] if i < len(rews) else 0
    total_steps += sum(sels)

best_idx = -1
best_reward = 0.0
for i in range(5):
    avg = agg_r[i] / max(agg_n[i], 1)
    if avg > best_reward and agg_n[i] >= 3:
        best_reward = avg
        best_idx = i

converged = best_reward >= ${TARGET_REWARD} and total_steps >= ${MIN_STEPS}

# ── Pretty print ──
print()
print('  ┌───────────────┬──────┬─────────┬──────────────────────┐')
print('  │ Behavior      │  n   │ avg_r   │ bar                  │')
print('  ├───────────────┼──────┼─────────┼──────────────────────┤')
for i in range(5):
    avg = agg_r[i] / max(agg_n[i], 1)
    bar_len = int(avg * 20)
    bar = '\033[32m' + '█' * bar_len + '\033[0m' + '░' * (20 - bar_len)
    marker = ' ◀ BEST' if i == best_idx else ''
    print(f'  │ {actions[i]:13s} │ {agg_n[i]:4d} │ {avg:.4f}  │ {bar} │{marker}')
print('  └───────────────┴──────┴─────────┴──────────────────────┘')
print()

# ── Progress bar ──
progress = min(total_steps / ${MIN_STEPS}, 1.0) if ${MIN_STEPS} > 0 else 1.0
reward_progress = min(best_reward / ${TARGET_REWARD}, 1.0) if ${TARGET_REWARD} > 0 else 1.0
overall = min(progress, reward_progress)

bar_w = 30
filled = int(overall * bar_w)
pbar = '\033[32m' + '█' * filled + '\033[0m' + '░' * (bar_w - filled)
pct = int(overall * 100)

print(f'  Steps:  {total_steps}/${MIN_STEPS}')
print(f'  Reward: {best_reward:.4f}/${TARGET_REWARD} ({actions[best_idx] if best_idx>=0 else \"none\"})')
print(f'  [{pbar}] {pct}%')
print()

if converged:
    print('  \033[1m\033[32m✓ CONVERGED\033[0m')
    print('CONVERGED_FLAG=true')
else:
    steps_gap = max(0, ${MIN_STEPS} - total_steps)
    reward_gap = max(0, ${TARGET_REWARD} - best_reward)
    print(f'  \033[33mNeeds: steps +{steps_gap}, reward +{reward_gap:.4f}\033[0m')
    print('CONVERGED_FLAG=false')
" 2>/dev/null | tee /tmp/mab_out.txt

    if grep -q "CONVERGED_FLAG=true" /tmp/mab_out.txt 2>/dev/null; then
        CONVERGED=true
    fi
done

# ═══════════════════════════════════════════════════════════════
ELAPSED=$(( $(date +%s) - START_TIME ))
ELAPSED_MIN=$((ELAPSED / 60))
ELAPSED_SEC=$((ELAPSED % 60))

echo ""
echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════╗${NC}"
if [ "$CONVERGED" = "true" ]; then
    echo -e "${BOLD}${GREEN}║  ✓ TRAINING COMPLETE — CONVERGED                 ║${NC}"
else
    echo -e "${BOLD}${YELLOW}║  ⚠ TRAINING COMPLETE — MAX ROUNDS                ║${NC}"
fi
echo -e "${BOLD}${GREEN}║  Rounds: ${ROUND}    Time: ${ELAPSED_MIN}m${ELAPSED_SEC}s                        ║${NC}"
echo -e "${BOLD}${GREEN}║  Models: results/models/mab_honey_*.json          ║${NC}"
echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════╝${NC}"
echo ""
echo "Next: ATTACKER_LEVEL_DURATION_SEC=120 bash scripts/run_multiview.sh"
