#!/bin/bash
# run_phase1_v2.sh — Apply prompt v2 + re-run Phase 1 + coherence analysis
#
# Intended to run AFTER Phase 1 v1 has completed.
# Backs up v1 results, applies v2 prompt, re-runs same 3×1 grid, analyses both.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

V1_DIR="results/llm_multi_seed_v1"
V2_DIR="results/llm_multi_seed_v2"

# 1. Snapshot v1 results
if [[ -d results/llm_multi_seed && ! -d ${V1_DIR} ]]; then
  cp -r results/llm_multi_seed "${V1_DIR}"
  echo "[snapshot] v1 → ${V1_DIR}"
fi

# 2. Coherence report for v1
python scripts/analyze_llm_coherence.py \
  --llm-dir "${V1_DIR}" \
  --output-md results/diagnostics/llm_coherence_v1.md \
  --output-json results/diagnostics/llm_coherence_v1.json | tail -40

# 3. Apply v2 prompt + bump temp to 0.7
python scripts/_prompt_v2_patch.py

# 4. Run v2 experiment into fresh dir
mkdir -p "${V2_DIR}"
python scripts/run_llm_experiments.py \
  --models llama3.1:8b,qwen2.5:14b,gemma2:9b \
  --seeds 42 \
  --episodes 50 --max-steps 50 --macro 5 \
  --temperature 0.7 \
  --output-dir "${V2_DIR}"

# 5. Coherence report for v2
python scripts/analyze_llm_coherence.py \
  --llm-dir "${V2_DIR}" \
  --output-md results/diagnostics/llm_coherence_v2.md \
  --output-json results/diagnostics/llm_coherence_v2.json

# 6. Restore v1 prompt (so production path stays on stable v1 until we commit)
python scripts/_prompt_v2_patch.py --revert

echo ""
echo "=== Phase 1 v2 complete ==="
echo "v1 coherence:  results/diagnostics/llm_coherence_v1.md"
echo "v2 coherence:  results/diagnostics/llm_coherence_v2.md"
echo "v1 runs:       ${V1_DIR}/"
echo "v2 runs:       ${V2_DIR}/"
