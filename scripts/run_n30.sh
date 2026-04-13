#!/usr/bin/env bash
# run_n30.sh — N=30 repeated experiments for statistical validation
# Wilcoxon requires N>=20, paper standard N=30
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

OUTDIR="results/n30"
TRIALS=30
DURATION=${1:-60}

echo "═══════════════════════════════════════════════"
echo "  MIRAGE-UAS N=30 Statistical Experiment"
echo "  Trials: $TRIALS per condition"
echo "  Duration: ${DURATION}s per trial"
echo "═══════════════════════════════════════════════"

for COND in no_defense mtd_only deception_only mirage_full; do
    mkdir -p "$OUTDIR/$COND"
    echo ""
    echo "--- Condition: $COND ---"
    case "$COND" in
        no_defense)     OPTS="MTD_ENABLED=false OPENCLAW_ENABLED=false BAYESIAN_ENABLED=false" ;;
        mtd_only)       OPTS="MTD_ENABLED=true  OPENCLAW_ENABLED=false BAYESIAN_ENABLED=false" ;;
        deception_only) OPTS="MTD_ENABLED=false OPENCLAW_ENABLED=true  BAYESIAN_ENABLED=true"  ;;
        mirage_full)    OPTS="MTD_ENABLED=true  OPENCLAW_ENABLED=true  BAYESIAN_ENABLED=true"  ;;
    esac

    for i in $(seq 1 $TRIALS); do
        env $OPTS PYTHONPATH=src python3 scripts/run_experiment.py \
            --mode dry-run --seed "$i" --duration "$DURATION" --quiet \
            --output "$OUTDIR/$COND/trial_${i}.json" 2>/dev/null || true
        [ $((i % 10)) -eq 0 ] && echo "  Progress: $i/$TRIALS"
    done
    echo "  Done: $COND ($TRIALS trials)"
done

echo ""
echo "=== Running statistical analysis ==="
PYTHONPATH=src python3 scripts/statistical_analysis.py \
    --n30-dir "$OUTDIR" \
    --output results/metrics/statistics.json

echo ""
echo "═══════════════════════════════════════════════"
echo "  N=30 EXPERIMENT COMPLETE"
echo "  Results: results/metrics/statistics.json"
echo "═══════════════════════════════════════════════"
