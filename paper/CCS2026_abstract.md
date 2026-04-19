# MIRAGE-UAS — CCS 2026 Abstract (Draft v2, 2026-04-19)

**Target venue:** ACM CCS 2026
**Abstract deadline:** 2026-04-23 00:00 KST (hard — cannot be revised)
**Full paper deadline:** 2026-04-30

---

## Title

**MIRAGE-UAS: Hierarchical LLM-Driven Deception for Autonomous UAS Honeydrones**

---

## Abstract (~290 words)

Unmanned Aerial Systems (UAS) are increasingly targeted by multi-stage
adversaries that combine network reconnaissance, MAVLink-protocol abuse,
and credential-harvesting lateral movement. While prior LLM-honeypot
systems (HoneyGPT, HoneyLLM, LLMHoney) demonstrate the viability of
LLM-mediated deception for single-host services, and a recent hierarchical
RL–LLM framework targets generic networks, the intersection of
**UAV-fleet deception, MAVLink-protocol fidelity, and operator-gateway
coordination** remains unexplored. Existing honeydrone testbeds (since
HoneyDrone, Daubert et al., NOMS 2018) rely on static decoys or narrow
rule-based responders that decohere under adaptive attackers and expose
themselves within seconds once engaged.

We present **MIRAGE-UAS**, a hierarchical UAS deception framework where
strategic (Tier 1), tactical (Tier 2), and interaction-level (Tier 3)
decisions are coordinated through a three-tier Docker-deployed
architecture. Tier 1 is an OpenClaw-inspired GCS Strategic Agent that
emits typed directives over a dedicated UDP control channel; Tier 2 is a
per-honeydrone Tactical LLM Defender driven by a locally-served 8–14 B
model (Ollama); Tier 3 is an OpenClaw-SDK emulator that plants
UUID-tagged honey-tokens and tracks their reuse.

We evaluate Tier 2 across three cross-organisation LLMs (Llama 3.1 8B,
Qwen 2.5 14B, Gemma 2 9B) against learned and game-theoretic baselines
(DQN, h-DQN, Game-EQ, Signaling-QRE, Hybrid). Over 9 runs (3 models × 3
seeds, 50 episodes × 50 steps, 95 % bootstrap CI), all models reach skill
entropy ≥ 2.06 bits, χ² phase-skill-independence p = 0.0 with large
Cramér's V (0.32–0.45), and maintain attacker belief μ_A ≥ 0.94 — while
a naïve V1 prompt collapses to 82-100 % single-skill use. Live Docker
deployment sustains 8 s decision cadence with zero fallbacks over
224 LLM calls in a 10-min run and 100 % cross-container directive
delivery.

We release the full system, DeceptionScore v2 composite metric,
HTUR / CPR / FSR honey-token tracking infrastructure, and Docker stack
as artefacts. A symmetric LLM adversary trained via fictitious play is
identified as future work and implemented as a pluggable policy stub.

---

## What the abstract commits us to (already delivered)

| Claim | Evidence |
|---|---|
| Three-tier architecture | `src/gcs/strategic_agent.py` + `src/honey_drone/llm_agent.py` + `openclaw_service.py` |
| 3 LLMs compared | `results/llm_v2/*.json` — 9 runs, bootstrap-CI table |
| 4 baselines compared | DQN multi-seed + Game-EQ + Signaling-EQ + Greedy/Random (matched) |
| UDP 19995 directive channel | `src/gcs/strategic_directive.py` + cross-container delivery (60/60 in 10 min) |
| DeceptionScore v2 | `src/metrics/deception_score_v2.py` + 5/5 unit test |
| Paired Wilcoxon + 95 % CI | `scripts/compare_policies.py` + `scripts/summarize_llm_v2.py` |
| ArduPilot-compat SITL | `docker/fcu_stub.py` (clearly labelled stub) |
| L0–L4 scripted attacker | `scripts/attacker_sim.py` |
| Docker artefact release | `config/docker-compose.honey.yml` + `config/docker-compose.honey.llm.yml` |
| Future LLM attacker stub | `src/honey_drone/attacker_policy.py::LLMAttackerPolicy` |

## Explicit non-claims (§5 Limitations)

- *(Resolved 2026-04-19 21:29)*. An earlier Docker run reported
  identical GCS directives because the honey→GCS state feed had not
  been wired. After adding the `STATE_BROADCAST_EXTRA_TARGETS=gcs:19999`
  fan-out and a UDP 19999 listener in the GCS container, a 9-minute
  run shows action diversity `{deploy_decoy: 9, observe: 48}` and
  urgency diversity `{0.0: 48, 0.2: 1, 0.3: 8}`. The paper will report
  the post-fix numbers; the pre-fix measurement is retained in the
  integrity log as a documented engineering step.
- We **do not** claim the LLM-attacker side is trained or evaluated.
- We **do not** integrate the upstream OpenClaw Node.js binary. Tier 1 is
  OpenClaw-inspired but runs locally via Ollama. Tier 3 emulates the
  OpenClaw SDK wire format purely for lure purposes.
- We **do not** claim real ArduPilot firmware — a SITL-compatible stub is
  used (§3.2, clearly labelled).
- HTUR is reported as an offline-synthetic ceiling (1.00); a live-Docker
  partial-reuse measurement is listed as future work.

## Updates since initial draft (2026-04-19)

- V2 prompt engineering (A-E keying + HARD RULES + repeat_penalty) resolved
  the mode collapse observed in V1. Entropy 0.69 → 2.10 bits (3× improvement).
- Docker integration completed (Tier 1 GCS + LLM-mode Tier 2 honey containers).
- GCS strategic-reasoning content diversity identified as open issue; paper
  to frame as "demonstrated in system design; live strategic reasoning
  requires honey→GCS state feed — engineering future-work".
