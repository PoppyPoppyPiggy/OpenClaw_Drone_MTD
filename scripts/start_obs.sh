#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# MIRAGE-UAS 5-Pane tmux Observatory
#
# Layout:
#   ┌──────────────────┬──────────────────┬──────────────────┐
#   │  OPENCLAW AGENT  │   OMNET++ TRACE  │   EXPERIMENT     │
#   │  (phase, flags,  │   (trace files,  │   (DS, sessions, │
#   │   breadcrumbs)   │    line counts)  │    progress)     │
#   ├──────────────────┴──────────────────┴──────────────────┤
#   │         MTD CONTROLLER         │    ATTACKER SIM       │
#   │   (triggers, port/ip/key)      │  (L0-L4 live feed)   │
#   └────────────────────────────────┴──────────────────────┘
#
# Usage:
#   bash scripts/start_obs.sh              # observatory only (attach to running experiment)
#   bash scripts/start_obs.sh --run        # start experiment + observatory
#
# Requires: tmux
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

SESSION="mirage-obs"

# Check tmux
if ! command -v tmux &> /dev/null; then
    echo "ERROR: tmux not installed. Install with: sudo apt install tmux"
    exit 1
fi

# Kill existing session
tmux kill-session -t "$SESSION" 2>/dev/null || true

# If --run flag: start the experiment in background first
if [[ "${1:-}" == "--run" ]]; then
    echo "Starting experiment in background..."
    mkdir -p results/logs results/metrics
    rm -f results/logs/engines.log results/attacker_log.jsonl results/.engine_running

    # Kill any leftover engine/attacker processes from previous runs
    pkill -f "run_engines.py" 2>/dev/null || true
    pkill -f "mirage_attacker" 2>/dev/null || true
    sleep 1

    # Start engines
    python3 scripts/run_engines.py > results/logs/engines.log 2>&1 &
    echo $! > /tmp/mirage_engine_pid
    sleep 4

    # Start Docker containers
    docker network create --subnet 172.40.0.0/24 test_net 2>/dev/null || true
    docker rm -f fcu_test_01 fcu_test_02 fcu_test_03 cc_test_01 cc_test_02 cc_test_03 2>/dev/null || true
    docker build -q -f docker/Dockerfile.fcu-stub -t mirage-fcu-stub:latest . > /dev/null 2>&1
    docker build -q -f docker/Dockerfile.cc-stub  -t mirage-cc-stub:latest  . > /dev/null 2>&1
    docker build -q -f docker/Dockerfile.attacker -t mirage-attacker:latest . > /dev/null 2>&1

    ENGINE_HOST=""
    ss -ulnp 2>/dev/null | grep -q ":14551 " && ENGINE_HOST="host.docker.internal"

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
    sleep 5

    # Start attacker in background
    docker run --rm --name mirage_attacker --network test_net --ip 172.40.0.200 \
        -e ATTACKER_LEVEL_DURATION_SEC=30 \
        -e "HONEY_DRONE_TARGETS=172.40.0.10:14550,172.40.0.11:14550,172.40.0.12:14550" \
        -e WEBCLAW_PORT_BASE=18789 -e HTTP_PORT_BASE=79 -e RESULTS_DIR=/results \
        -v "$(pwd)/results:/results:rw" \
        mirage-attacker:latest > /dev/null 2>&1 &
    echo $! > /tmp/mirage_attacker_pid

    # Background: wait for attacker to finish, then compute metrics
    (
        wait $(cat /tmp/mirage_attacker_pid 2>/dev/null) 2>/dev/null
        sleep 2
        cd "$PROJECT"
        python3 -c "
import sys; sys.path.insert(0, 'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from evaluation.compute_all import compute_all_metrics
result = compute_all_metrics('results/attacker_log.jsonl', 'results')
print(f'Metrics updated: DS={result[\"deception_score\"]:.4f} sessions={result[\"total_sessions\"]} ok={result[\"successful_engagements\"]}')
" 2>/dev/null
    ) &

    echo "Experiment launched. Attaching observatory..."
    sleep 2
fi

# Create tmux session with 5 panes
# Pane 0: OpenClaw (top-left)
tmux new-session -d -s "$SESSION" -x 200 -y 50
tmux send-keys -t "$SESSION" "cd $(pwd) && echo '  Real-time agent decisions via UDP:19998/19999' && python3 scripts/obs/obs_openclaw.py" C-m

# Pane 1: OMNeT++ (top-center)
tmux split-window -h -t "$SESSION"
tmux send-keys -t "$SESSION" "cd $(pwd) && bash scripts/obs/obs_omnetpp.sh" C-m

# Pane 2: Experiment Status (top-right)
tmux split-window -h -t "$SESSION"
tmux send-keys -t "$SESSION" "cd $(pwd) && python3 scripts/obs/obs_full.py" C-m

# Make top row equal width
tmux select-layout -t "$SESSION" tiled

# Pane 3: MTD (bottom-left)
tmux split-window -v -t "$SESSION:0.0"
tmux send-keys -t "$SESSION" "cd $(pwd) && python3 scripts/obs/obs_mtd.py" C-m

# Pane 4: Attacker (bottom-right)
tmux split-window -h -t "$SESSION:0.3"
tmux send-keys -t "$SESSION" "cd $(pwd) && python3 scripts/obs/obs_attacker.py" C-m

# Set pane titles (tmux 3.1+)
tmux select-pane -t "$SESSION:0.0" -T "OPENCLAW"
tmux select-pane -t "$SESSION:0.1" -T "OMNET++"
tmux select-pane -t "$SESSION:0.2" -T "EXPERIMENT"
tmux select-pane -t "$SESSION:0.3" -T "MTD"
tmux select-pane -t "$SESSION:0.4" -T "ATTACKER"

# Enable pane titles display
tmux set-option -t "$SESSION" pane-border-status top 2>/dev/null || true
tmux set-option -t "$SESSION" pane-border-format " #{pane_title} " 2>/dev/null || true

# Attach
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  MIRAGE-UAS Observatory — 5 panes in tmux"
echo "═══════════════════════════════════════════════════════════"
echo "  Session: $SESSION"
echo "  Detach:  Ctrl+B then D"
echo "  Kill:    tmux kill-session -t $SESSION"
echo "  Reattach: tmux attach -t $SESSION"
echo ""

tmux attach -t "$SESSION"
