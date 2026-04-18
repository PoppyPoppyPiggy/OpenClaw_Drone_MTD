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

for POLICY in "${POLICIES[@]}"; do
    case "$POLICY" in
        dqn|signaling_eq|hybrid) : ;;
        *) echo -e "${RED}  Unknown policy: $POLICY${NC}"; exit 1 ;;
    esac

    step "Running DEFENDER_POLICY=${POLICY}"

    # Clean prior run dir for this policy
    OUT_DIR="results.${POLICY}"
    rm -rf "$OUT_DIR"

    # Point results/ to a fresh dir so the experiment writes there
    rm -rf results
    mkdir -p results

    # Override policy in env only for this run
    DEFENDER_POLICY="$POLICY" bash run.sh || {
        echo -e "${RED}  Policy $POLICY failed — keeping partial output in results/${NC}"
    }

    # Archive
    mv results "$OUT_DIR"
    echo -e "${GREEN}  Archived → ${OUT_DIR}${NC}"
done

# Restore results/ symlink to the last run so follow-up tools still work
ln -s "results.${POLICIES[-1]}" results 2>/dev/null || cp -r "results.${POLICIES[-1]}" results

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
