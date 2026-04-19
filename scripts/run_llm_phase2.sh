#!/bin/bash
# run_llm_phase2.sh — Full 3-seed LLM experiment for CCS Table VII
#
# Runs 3 models × 3 seeds × 100 episodes × 50 max_steps × macro=5
#   = 3000 LLM calls per model (9000 total)
#   @ ~1-2s per call ≈ 2.5-5 hours wall-clock
#
# Output:
#   results/llm_multi_seed/<model>_seed<seed>.json per run
#   results/llm_multi_seed/summary.json            aggregate
#
# Usage:
#   bash scripts/run_llm_phase2.sh            # foreground
#   nohup bash scripts/run_llm_phase2.sh > results/llm_multi_seed/phase2.log 2>&1 &
#
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

MODELS="${MODELS:-llama3.1:8b,qwen2.5:14b,gemma2:9b}"
SEEDS="${SEEDS:-42,1337,2024}"
EPISODES="${EPISODES:-100}"
MAX_STEPS="${MAX_STEPS:-50}"
MACRO="${MACRO:-5}"
TIMEOUT_SEC="${TIMEOUT_SEC:-25.0}"

echo "=== Phase 2: Full 3-seed LLM evaluation ==="
echo "Models    : ${MODELS}"
echo "Seeds     : ${SEEDS}"
echo "Episodes  : ${EPISODES}"
echo "Max steps : ${MAX_STEPS}"
echo "Macro     : ${MACRO}"
echo "Started   : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

python scripts/run_llm_experiments.py \
  --models "${MODELS}" \
  --seeds "${SEEDS}" \
  --episodes "${EPISODES}" \
  --max-steps "${MAX_STEPS}" \
  --macro "${MACRO}" \
  --timeout-sec "${TIMEOUT_SEC}"

echo "=== Phase 2 complete: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "Aggregating..."
python scripts/aggregate_all_policies.py
