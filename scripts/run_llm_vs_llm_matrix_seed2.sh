#!/bin/bash
# run_llm_vs_llm_matrix.sh — Symmetric LLM-vs-LLM 3×3 matrix
#
# For each (defender × attacker-belief) combo:
#   1. Recreate the Docker stack with DEFENDER_LLM_MODEL env
#   2. Wait for the LLM-mode honeydrones to come up (~15 s)
#   3. Run attacker_sim with ATTACKER_LLM_BELIEF_MODEL env
#   4. Save the resulting belief trajectory under a tagged filename
#
# Each cell is ~2 min attack + 30 s recreate = ~2.5 min.
# 9 cells × 2.5 min ≈ 23 min total.
#
# Requires: Docker stack already built; sg wrapper for the docker group.

set -e
cd "$(dirname "$0")/.."

MODELS=(llama3.1:8b qwen2.5:14b gemma2:9b)
LEVEL_DURATION=${LEVEL_DURATION:-15}
OUT_DIR=results/diagnostics/llm_vs_llm_seed2
mkdir -p "${OUT_DIR}"

echo "=== LLM-vs-LLM 3x3 matrix ==="
echo "Defenders : ${MODELS[*]}"
echo "Attackers : ${MODELS[*]}"
echo "Level dur : ${LEVEL_DURATION}s × 5 = $((LEVEL_DURATION * 5))s per run"
echo "Out dir   : ${OUT_DIR}"
echo "Start     : $(date -Iseconds)"
echo

for defender in "${MODELS[@]}"; do
  echo ""
  echo "================================================================"
  echo "  DEFENDER = ${defender}"
  echo "================================================================"

  # Swap defender model and recreate cc-honey containers.
  DEFENDER_LLM_MODEL="${defender}" \
  sg docker -c "docker compose \
      -f config/docker-compose.honey.yml \
      -f config/docker-compose.honey.llm.yml \
      --env-file config/.env \
      up -d --force-recreate cc-honey-01 cc-honey-02 cc-honey-03" \
      >/dev/null 2>&1

  # Wait for health + a few proactive loops so the defender has warmed up.
  sleep 20

  for attacker in "${MODELS[@]}"; do
    tag="def_$(echo "${defender}" | tr ':.' '__')__atk_$(echo "${attacker}" | tr ':.' '__')"
    ts=$(date +%s)
    echo ""
    echo "  -- def=${defender}  atk=${attacker}  tag=${tag}  start=${ts}"

    # Write each cell's full log so failures are diagnosable.
    cell_log="${OUT_DIR}/${tag}.cell.log"
    ATTACKER_LLM_BELIEF_ENABLED=1 \
      ATTACKER_LLM_BELIEF_MODEL="${attacker}" \
      ATTACKER_LLM_BELIEF_OLLAMA_URL=http://172.23.240.1:11434 \
      ATTACKER_LLM_BELIEF_TIMEOUT_SEC=8 \
      HONEY_DRONE_TARGETS="127.0.0.1:14551,127.0.0.1:14552,127.0.0.1:14553" \
      WEBCLAW_PORT_BASE=18789 HTTP_PORT_BASE=8080 \
      ATTACKER_LEVEL_DURATION_SEC="${LEVEL_DURATION}" \
      RESULTS_DIR=results \
      timeout 300 .venv/bin/python -u scripts/attacker_sim.py \
        >"${cell_log}" 2>&1 || true

    # The latest belief file is the one attacker_sim just wrote;
    # move-and-rename it under the matrix tag.
    newest=$(ls -t results/diagnostics/attacker_belief_*.json 2>/dev/null | head -1 || true)
    if [ -n "${newest}" ]; then
      mv "${newest}" "${OUT_DIR}/${tag}.json"
      # Print quick summary
      python3 - <<PY
import json, sys
d = json.load(open("${OUT_DIR}/${tag}.json"))
s = d["summary"]
print(f"     → {s['packets_observed']} pkts  disbelief@{s['packets_to_disbelief']}  "
      f"final_mu={s['final_mu_real']:.3f}  AUC={s['belief_auc_normalised']:.3f}")
PY
    else
      echo "     (no belief file produced)"
    fi

    sleep 2
  done
done

echo ""
echo "=== Matrix complete: $(date -Iseconds) ==="
ls -la "${OUT_DIR}/" | tail -12
