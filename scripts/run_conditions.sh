#!/usr/bin/env bash
# run_conditions.sh — 4가지 방어 조건 실험 자동화
# Hou 2025 하이브리드 방어 비교 테이블용
#
# Reference: Hou et al. (2025), Computers 14(12):513
#
# Usage:
#   bash scripts/run_conditions.sh [--trials 30] [--duration 60]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

TRIALS=${1:-30}
DURATION=${2:-60}

echo "═══════════════════════════════════════════════"
echo "  MIRAGE-UAS Hybrid Defense Condition Sweep"
echo "  Trials per condition: $TRIALS"
echo "  Duration per trial:   ${DURATION}s"
echo "═══════════════════════════════════════════════"

# 4 conditions: env overrides disable/enable components
CONDITIONS=(
    "no_defense:MTD_ENABLED=false,OPENCLAW_ENABLED=false,BAYESIAN_ENABLED=false"
    "mtd_only:MTD_ENABLED=true,OPENCLAW_ENABLED=false,BAYESIAN_ENABLED=false"
    "deception_only:MTD_ENABLED=false,OPENCLAW_ENABLED=true,BAYESIAN_ENABLED=true"
    "mirage_full:MTD_ENABLED=true,OPENCLAW_ENABLED=true,BAYESIAN_ENABLED=true"
)

for cond_str in "${CONDITIONS[@]}"; do
    COND="${cond_str%%:*}"
    ENV_VARS="${cond_str##*:}"

    mkdir -p "results/conditions/$COND"
    echo ""
    echo "=== Condition: $COND ($TRIALS trials) ==="

    IFS=',' read -ra VARS <<< "$ENV_VARS"

    for i in $(seq 1 "$TRIALS"); do
        # Build env args
        ENV_CMD=""
        for var in "${VARS[@]}"; do
            ENV_CMD="$ENV_CMD $var"
        done

        env $ENV_CMD PYTHONPATH=src python3 scripts/run_experiment.py \
            --mode dry-run \
            --seed "$i" \
            --duration "$DURATION" \
            --output "results/conditions/$COND/trial_${i}.json" \
            --quiet 2>/dev/null || true

        [ $((i % 10)) -eq 0 ] && echo "  Progress: $i/$TRIALS"
    done
    echo "  Done: $COND"
done

echo ""
echo "Running integrated analysis..."
PYTHONPATH=src python3 scripts/analyze_conditions.py \
    --conditions-dir results/conditions \
    --output results/metrics/hybrid_defense_table.json

echo ""
echo "═══════════════════════════════════════════════"
echo "  CONDITION SWEEP COMPLETE"
echo "  Results: results/metrics/hybrid_defense_table.json"
echo "═══════════════════════════════════════════════"
