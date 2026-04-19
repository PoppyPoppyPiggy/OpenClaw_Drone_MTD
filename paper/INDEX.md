# MIRAGE-UAS — Paper Asset Index

Last updated: 2026-04-19 after Docker integration task.

## 📄 Abstract
- `CCS2026_abstract.md` — **v2 draft** (V2 numbers + Docker deployment reflected)

## 📚 Related work
- `related_work.md` — §2 draft (HoneyDrone, HoneyGPT, SoK 2510.25939, etc.)

## 🏗 Architecture
- `ARCHITECTURE.md` — 3-tier architecture, **Docker deployment** section
- `GLOSSARY.md` — 한국어 / 영어 용어 표준 (§1–§14)
- `DOCKER_INTEGRATION_REPORT.md` — Docker task report (10-min observation, host vs Docker, gotchas)
- `SYSTEM_REPORT.md` — **NEW** — 기술 보고서 v1.0 (Part I–VIII): 논리 구조 · 모델 설계 · 상호작용 흐름 · 평가 방법론 · 결과 요약

## ✅ Integrity / coherence
- `DATA_INTEGRITY.md` — auto-generated 3-tier consolidated check (includes GCS degeneracy finding)

## 📊 Figures (PNG, 150 DPI)

| File | Purpose | Source |
|---|---|---|
| `figures/fig_architecture.png` | 3-tier **Docker** deployment diagram | `draw_architecture.py` |
| `figures/fig_a_skill_dist_v1_vs_v2.png` | V1 mode-collapse vs V2 mitigation (3-model mean bar) | `make_paper_figures.py` |
| `figures/fig_b_phase_skill_heatmap_v2.png` | Phase × Skill confusion (row-normalised, 3 models) | `make_paper_figures.py` |
| `figures/fig_c_reward_vs_belief_scatter.png` | avg_R vs avg_p_real, V1 circle vs V2 triangle | `make_paper_figures.py` |
| `figures/phase_skill_cm_grid.png` | 3×3 grid all 9 per-run heatmaps (supplementary) | `save_confusion_artifacts.py` |
| `figures/fig_attacker_belief_trajectory.png` | **NEW** — LLM-attacker μ_real trajectory over L0-L4 packets | `analyze_deception_lifetime.py` |

Pre-existing TikZ architecture sketches:
- `figures/fig1_ooda.tex`, `fig2_attack.tex`, `fig3_defense.tex`,
  `fig_ooda_loop.tex`, `fig_attack_model.tex`, `fig_defense_arch.tex`

## 📋 Tables (Markdown → convert to LaTeX at submission)

| File | Purpose |
|---|---|
| `tables/table_llm_v2_bootstrap_ci.md` | Per-model headline + L2/L4/L5 metrics with 95 % CI |
| `tables/table_cross_model_variance.md` | Between-model variance + Cramér's V ranking |
| `tables/table_v1_v2_ablation.md` | V1 → V2 prompt ablation (skill dist Δ) |
| `tables/table_deception_score_v2.md` | DeceptionScore v2 composite + sensitivity grid |
| `tables/table_game_theoretic_evaluation.md` | Table VIII: belief manipulation / information leakage / deception ratio / hypergame stability per LLM policy + Game-EQ exploitability reference |
| `tables/table_ix_attacker_belief.md` | **NEW** — Table IX: LLM attacker belief trajectory, packets-to-disbelief, belief AUC, top suspicion signals |

## 📐 Sections (paper draft — single-source-of-truth per section)

| File | Scope |
|---|---|
| `sections/01_abstract_intro_related.md` | **v2 + 5 fixes applied 2026-04-20** — Abstract + §1 Intro (C1/C2/C3) + §2 Related Work |
| `sections/02_related_work_comparison.md` | §2 Tables I–IV comparison + 16 verification links |
| `sections/03_system_design.md` | **NEW 2026-04-20** — §3 System Design (3-tier, cadences, UDP fabric, MAVLink fidelity, R/S label, heuristic DeceptionEnv) |
| `sections/04_methodology.md` | **NEW 2026-04-20** — §4 Prompt V1→V2 ablation, Ollama config, cross-model protocol, POSG formalisation |
| `sections/05_results.md` | **NEW 2026-04-20** — §5 RQ1–RQ5 results, Table VII/VIII/IX narratives, cross-model variance, DS-v2 |
| `sections/06_future_work.md` | **NEW 2026-04-20** — §6 Limitations F1–F7 + §7 Conclusion |
| `sections/game_theory.md` | §2.5 Related Work (Pawlick/Horák/GH-MTD/Hypergame) + POSG formalism (merged into §4.7 + §5.7) |

## 🔬 Data artefacts

| File | Purpose |
|---|---|
| `data/tier1_directive.json` | Host directive bias test (llama, 4/5 = 100 % hit) |
| `data/tier1_directive_docker_qwen.json` | Docker + qwen (5/5 = 100 %, HARD-RULE slack under directive) |
| `data/tier1_directive_docker_llama.json` | Docker + llama (flight_sim 25 %, HARD-RULE preserved) |
| `data/tier3_lure.json` | Tier 3 OpenClaw-SDK emulator output + breadcrumbs |
| `data/htur.json` | HTUR / CPR / FSR offline synthetic validation |

## 🗂 Full-run data (not copied into paper/ — too large)

- `../results/llm_v2/*_seed*.json` — per-seed raw metrics (9 runs)
- `../results/llm_v2/phase_skill_cm_*.npz` — NumPy confusion matrices
- `../results/llm_multi_seed_v1/*.json` — V1 mode-collapse baseline
- `../results/baseline_matched/*.json` — Random/Greedy/DQN matched
- `../results/logs/container_llm_mode_*.log` — **NEW** Docker 10-min live capture

## ⚠️ Known open issues

1. ~~**GCS strategic-reasoning degeneracy**~~ **RESOLVED 2026-04-19 21:29.**
   Fixed via Option 2 (honey→gcs:19999 UDP fan-out +
   `_start_state_listener` + cache-first `_load_snapshot`). Post-fix
   run: 57 directives, action diversity `{deploy_decoy: 9, observe: 48}`,
   urgency diversity `{0.0: 48, 0.2: 1, 0.3: 8}`. See
   `DATA_INTEGRITY.md §3b`.

2. ~~**flight_sim hit rate Δ host vs Docker**~~ **RESOLVED 2026-04-19 21:38.**
   Added `PYTHONHASHSEED=0` to both Dockerfiles and re-ran with
   `--calls 30`. Result: Docker flight_sim hit rate 13.3 % vs host 12.5 %
   = +0.8 pp (within ±5 pp threshold). Bonus finding at higher sample
   size: statustext 76.7 % under directive — richer HARD-RULE behaviour
   than the earlier 8-call test suggested. Recorded in
   `results/diagnostics/tier1_directive_docker_llama_n30.json`.

3. ~~**HTUR offline-synthetic only**~~ **RESOLVED 2026-04-19 21:45.**
   Fixed auth-routing bug (OpenClawAgent was preempting OpenClawService's
   `_handle_auth`). Live attacker_sim run now triggers `honeytoken_issued`
   + `honeytoken_reused` events in the openclaw_service log. Aggregate
   HTUR = 1.00 on cc_honey_01 (attacker_sim only targets `:18789`,
   which maps to honey_01 — noted as an attacker-sim cleanup for
   multi-drone fleet test; infrastructure is verified E2E).

## 🗃 Deprecated / stale

- `mirage_uas_ccs2026_draft.md` — 766-line earlier full draft
  (pre-V2, pre-Docker). **Use the new per-section docs above as
  single-source-of-truth.** Merge into the draft at final submission time.

## 🧪 Reproducibility commands

```bash
# Regenerate all paper-ready artefacts
bash scripts/finalize_v2_analysis.sh
python scripts/cross_model_variance.py
python scripts/make_paper_figures.py
python scripts/save_confusion_artifacts.py
python scripts/compute_deception_score.py
python scripts/draw_architecture.py

# Docker E2E
docker compose \
  -f config/docker-compose.honey.yml \
  -f config/docker-compose.honey.llm.yml \
  --env-file config/.env up -d --build

# Tier 1 directive test (inside Docker)
docker cp scripts/diagnose_tier1_directive.py cc_honey_01:/app/
docker exec -e PYTHONPATH=/app/src cc_honey_01 \
  python3 /app/diagnose_tier1_directive.py \
  --model llama3.1:8b --ollama-url http://172.23.240.1:11434 --calls 8
```
