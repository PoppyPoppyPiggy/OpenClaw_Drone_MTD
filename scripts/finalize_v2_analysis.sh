#!/bin/bash
# finalize_v2_analysis.sh — Run all post-experiment analyses in one shot
#
# Intended to be invoked AFTER results/llm_v2/ is populated (all 9 runs done).
# Produces every deliverable the user asked for in the data-integrity block.
set -e
cd "$(dirname "$0")/.."

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi

echo "=========================================="
echo "  Post-V2 analysis pipeline"
echo "=========================================="
echo

# 1. V2 summary with bootstrap CI (Block A final output)
echo "[1/5] V2 summary.md (bootstrap 95% CI, L1-L5)"
python scripts/summarize_llm_v2.py > /dev/null
ls -la results/llm_v2/summary.md results/llm_v2/summary_ci.json

# 2. V1 vs V2 ablation report
echo
echo "[2/5] V1 vs V2 ablation (mode-collapse mitigation evidence)"
python scripts/compare_v1_v2.py > /dev/null
ls -la docs/prompt_ablation.md

# 3. DeceptionScore v2 per-run + sensitivity
echo
echo "[3/5] DeceptionScore v2 per-run + weight sensitivity"
python scripts/compute_deception_score.py > /dev/null
ls -la docs/deception_score_v2_analysis.md \
       results/llm_v2/deception_score_per_run.json

# 4. Coherence analyzer
echo
echo "[4/5] Coherence / lock-in analyzer (entropy, phase-pref)"
python scripts/analyze_llm_coherence.py --llm-dir results/llm_v2 \
  --output-md results/diagnostics/llm_coherence_v2.md \
  --output-json results/diagnostics/llm_coherence_v2.json > /dev/null
ls -la results/diagnostics/llm_coherence_v2.md

# 5. Final integrity report (aggregates everything)
echo
echo "[5/5] Final DATA_INTEGRITY.md (3-tier consolidated)"
python scripts/summarize_data_integrity.py > /dev/null
ls -la docs/DATA_INTEGRITY.md

echo
echo "=========================================="
echo "  Deliverables"
echo "=========================================="
echo "  docs/DATA_INTEGRITY.md            — 3-tier consolidated (§5 checklist)"
echo "  docs/prompt_ablation.md           — V1→V2 mode-collapse mitigation"
echo "  docs/deception_score_v2_analysis.md — composite metric table"
echo "  results/llm_v2/summary.md         — bootstrap-CI per-model table"
echo "  results/diagnostics/llm_coherence_v2.md — entropy / phase heat"
echo "  results/diagnostics/htur.json     — HTUR offline validation"
echo "  results/diagnostics/tier3_lure.json — Tier 3 breadcrumb leak"
echo
echo "Next actions (not run here):"
echo "  - Run Tier 1 directive E2E test (scripts/diagnose_tier1_directive.py)"
echo "  - Update docs/CCS2026_abstract.md with V2 numbers"
