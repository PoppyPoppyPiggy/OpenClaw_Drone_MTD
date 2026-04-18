#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# run_all_policies.sh — Sweep over DEFENDER_POLICY ∈ {dqn, signaling_eq, hybrid}
#
# Runs the full MIRAGE-UAS experiment 3× with different defender policies
# and stores each run under results.<policy>/ for later side-by-side
# analysis (paper Table VII + signaling-game C3 evidence).
#
# Usage:
#   bash scripts/run_all_policies.sh                    # full run (default duration)
#   ATTACKER_LEVEL_DURATION_SEC=60 bash scripts/run_all_policies.sh   # quick smoke
#
# Output layout:
#   results.dqn/          — DEFENDER_POLICY=dqn (DQN/LinUCB baseline)
#   results.signaling_eq/ — DEFENDER_POLICY=signaling_eq (C3 contribution)
#   results.hybrid/       — DEFENDER_POLICY=hybrid (alternation)
#   results/              — symlinked to the last run so run.sh defaults still work
#
# After completion:
#   python3 scripts/compare_runs.py results.dqn results.signaling_eq results.hybrid
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${CYAN}▶${NC} $1"; }

POLICIES=("${POLICIES:-dqn signaling_eq hybrid}")
# shellcheck disable=SC2206
POLICIES=( ${POLICIES[*]} )

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  MIRAGE-UAS Policy Sweep${NC}"
echo -e "${GREEN}  Policies: ${POLICIES[*]}${NC}"
echo -e "${GREEN}  Per-level: ${ATTACKER_LEVEL_DURATION_SEC:-600}s${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"

if [ ! -x run.sh ]; then
    echo -e "${RED}  run.sh not executable — chmod +x run.sh${NC}"
    exit 1
fi

# Preserve trained models across runs — we only rotate runtime metrics,
# NOT training checkpoints (train_dqn / train_hdqn / train_game outputs).
MODELS_BACKUP=""
if [ -d results/models ]; then
    MODELS_BACKUP="$(mktemp -d)/models"
    cp -r results/models "$MODELS_BACKUP"
    echo -e "${YELLOW}  Backed up results/models → ${MODELS_BACKUP}${NC}"
fi

_restore_models() {
    if [ -n "$MODELS_BACKUP" ] && [ -d "$MODELS_BACKUP" ]; then
        mkdir -p results
        cp -r "$MODELS_BACKUP" results/models
    fi
}

for POLICY in "${POLICIES[@]}"; do
    case "$POLICY" in
        dqn|signaling_eq|hybrid) : ;;
        *) echo -e "${RED}  Unknown policy: $POLICY${NC}"; exit 1 ;;
    esac

    step "Running DEFENDER_POLICY=${POLICY}"

    # Clean prior archive for this policy
    OUT_DIR="results.${POLICY}"
    rm -rf "$OUT_DIR"

    # Fresh results/ — but preserve models/
    rm -rf results
    mkdir -p results
    _restore_models

    # Override policy in env only for this run
    DEFENDER_POLICY="$POLICY" bash run.sh || {
        echo -e "${RED}  Policy $POLICY failed — keeping partial output in results/${NC}"
    }

    # Archive (include a copy of models for reproducibility)
    mv results "$OUT_DIR"
    echo -e "${GREEN}  Archived → ${OUT_DIR}${NC}"
done

# Restore a usable results/ with the models intact so follow-up tools work
rm -rf results
mkdir -p results
_restore_models
# Also copy the last run's metrics into results/ for quick inspection
LAST_DIR="results.${POLICIES[-1]}"
if [ -d "$LAST_DIR/metrics" ]; then
    cp -r "$LAST_DIR/metrics" results/metrics
fi

step "Summary"
for POLICY in "${POLICIES[@]}"; do
    OUT_DIR="results.${POLICY}"
    if [ -d "$OUT_DIR/metrics" ]; then
        MTD=$(find "$OUT_DIR/metrics" -name 'live_mtd_results.json' | wc -l)
        SG=$(find "$OUT_DIR/metrics" -name 'signaling_game_*.json' | wc -l)
        DEC=$(find "$OUT_DIR/metrics" -name 'decisions_*.json' | wc -l)
        echo "  ${POLICY}:  mtd_files=${MTD}  signaling_files=${SG}  decisions_files=${DEC}"
    else
        echo -e "  ${RED}${POLICY}:  NO METRICS${NC}"
    fi
done

echo ""
echo -e "${GREEN}  Next:${NC}"
echo -e "    python3 scripts/compare_runs.py ${POLICIES[*]/#/results.}"
echo -e "    python3 scripts/compare_policies.py --episodes 500"
