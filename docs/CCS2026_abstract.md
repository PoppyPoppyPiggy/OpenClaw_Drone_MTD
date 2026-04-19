# MIRAGE-UAS — CCS 2026 Abstract (Draft)

**Target venue:** ACM CCS 2026
**Abstract deadline:** 2026-04-23 00:00 KST (hard: cannot be revised)
**Full paper deadline:** 2026-04-30

---

## Title

**MIRAGE-UAS: Hierarchical LLM-Driven Deception for Autonomous UAS Honeydrones**

---

## Abstract (≈280 words)

Unmanned Aerial Systems (UAS) are increasingly targeted by multi-stage
adversaries that combine network reconnaissance, MAVLink-protocol abuse, and
credential-harvesting lateral movement. While prior LLM-honeypot systems
(HoneyGPT, HoneyLLM, LLMHoney) demonstrate the viability of LLM-mediated
deception for single-host services, and a recent hierarchical RL-LLM
framework targets generic networks, the intersection of **UAV-fleet
deception, MAVLink-protocol fidelity, and operator-gateway coordination**
remains unexplored. Existing honeydrone testbeds (since the seminal HoneyDrone
by Daubert et al., NOMS 2018) rely on static decoys or narrow rule-based
responders, which decohere under sophisticated or adaptive attackers and
expose themselves within seconds once engaged. We present **MIRAGE-UAS**, a
hierarchical UAS deception framework in which strategic, tactical, and
interaction-level decisions are coordinated through a three-tier architecture.

**Tier 1 (GCS Strategic Agent)** is an OpenClaw-inspired LLM commander that
observes fleet-wide engagement state and issues per-drone strategic directives
over a UDP control channel. **Tier 2 (Honeydrone Tactical Agent)** replaces the
conventional DQN or signaling-game policy in each drone's proactive loop with
a locally-served LLM (Ollama) that selects among five MAVLink-fidelity
deception skills on 8-second cadence. **Tier 3 (Attacker Lure)** exposes a
forged OpenClaw SDK endpoint that engages L3–L4 attackers with multi-turn
honey-token responses.

We evaluate Tier 2 across five cross-organization LLMs (Llama 3.1 8B, Qwen 2.5
14B, Phi-4 14B, Mistral-Nemo 12B, Gemma 2 9B) and compare against four
learned/game-theoretic baselines (Dueling DQN, hierarchical DQN, QRE signaling
solver, alternating best-response game policy) over 500-episode × 3-seed runs
in a Dockerized ArduPilot-compatible testbed with a scripted L0–L4 attacker.
Evaluation uses the DeceptionScore v2 composite (diversion, breach
prevention, interaction quality, temporal cost, MTD effectiveness) with
paired Wilcoxon signed-rank tests at 95 % CI.

We release the full system, pulled LLM weights, training pipelines, and
Docker stack as an artifact. A symmetric LLM attacker learned via
fictitious play is identified as future work and implemented as a stub
integration point.

---

## What the abstract commits us to (must deliver by 04-30)

| Claim | Deliverable |
|---|---|
| Three-tier architecture | `src/gcs/strategic_agent.py` + `src/honey_drone/llm_agent.py` + existing `openclaw_service.py` |
| 5 LLMs compared | Full 5-model × 3-seed evaluation matrix |
| 4 baselines compared | DQN, h-DQN, Game-EQ, Signaling-QRE already trained |
| UDP 19995 directive channel | `src/gcs/strategic_directive.py` + listener in `openclaw_agent.py` |
| DeceptionScore v2 | `src/evaluation/deception_score.py` (already present) |
| Paired Wilcoxon + 95 % CI | `scripts/compare_policies.py` (already updated per M5 fix) |
| ArduPilot-compat SITL | `docker/fcu_stub.py` (already working) |
| L0–L4 scripted attacker | `scripts/attacker_sim.py` (already working) |
| Artifact release | Repo + weights + Docker Hub images |
| Future LLM attacker stub | `src/honey_drone/attacker_policy.py::LLMAttackerPolicy` |

---

## Explicit non-claims (safe-boundary)

- We do **not** claim the LLM-attacker side is trained or evaluated.
- We do **not** claim field deployment on live RF spectrum.
- We do **not** claim real ArduPilot firmware — a SITL-compatible stub is used (§3.2, labeled).
- We do **not** integrate the upstream OpenClaw Node.js binary; Tier 1 is
  OpenClaw-inspired but runs locally via Ollama. Tier 3 emulates the OpenClaw
  SDK wire format purely for lure purposes.
