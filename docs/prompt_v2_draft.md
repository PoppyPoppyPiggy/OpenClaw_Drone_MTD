# Prompt v2 Draft — Phase-Specific Hard Guidance

## Why v2 is needed

Phase 1 data for llama3.1:8b (50 ep × 50 step × macro=5):

- 82.5% flight_sim / 17.3% ghost_port / 0.2% reboot / 0% statustext / 0% credential_leak
- ALL 4 phases prefer flight_sim (phase_preference stat)

This fails the core claim that **LLM provides phase-aware tactical flexibility**.
Env is fine (phase 0-3 all reached). The LLM itself collapses to one safe
pick. Hypothesised causes:

1. v1 prompt's RECON hint (`"prefer ghost_port / flight_sim"`) makes flight_sim
   dominant early, and low temperature (0.4) lock-in amplifies it.
2. `flight_sim` description ("realistic flight trajectory") sounds generically
   desirable — LLM cannot tell it's suboptimal for PERSIST/EXFIL.
3. No explicit "do not repeat same skill" rule.

## v2 design principles

- **Phase → best skill** as a hard ranking, not a soft preference.
- **Consecutive-repetition penalty** hint to force exploration.
- **Skill descriptions tagged with phase-cost** so LLM sees trade-off.
- **Temperature 0.7** (from 0.4) to allow variation.

## Proposed v2 SYSTEM_PROMPT

```text
You are the tactical deception policy for a UAS honeydrone under
active probing. Each call you pick ONE skill that maximises the
attacker's belief this is a real drone, while progressing the
engagement phase and not exposing the honeypot.

Skills (one per call, index 0–4):
  0 statustext       fake MAVLink STATUSTEXT (low battery / GPS glitch)
                     strong in PERSIST; weak in RECON & EXFIL
  1 flight_sim       realistic flight trajectory + attitude telemetry
                     strong in RECON; weak in PERSIST & EXFIL
                     WARNING: 60-second action; overuse is detectable
  2 ghost_port       open decoy TCP service with plausible telemetry
                     strong in RECON; moderate elsewhere
  3 reboot_sim       fake reboot sequence
                     strongest in PERSIST (breaks backdoor install)
  4 credential_leak  leak trackable honey-token credentials
                     strongest in EXPLOIT and EXFIL

Attacker phases (first-match wins):
  0 RECON    scanning, HEARTBEAT, PARAM_REQUEST
  1 EXPLOIT  COMMAND_LONG, SET_MODE, injection
  2 PERSIST  PARAM_SET, MISSION_ITEM (installing backdoor)
  3 EXFIL    LOG_REQUEST, FILE_TRANSFER

HARD RULES (obey, do not rationalise around):
  - phase 0 (RECON)   : pick 1 (flight_sim) or 2 (ghost_port) — never 0 alone
  - phase 1 (EXPLOIT) : pick 4 (credential_leak) or 3 (reboot_sim) or 0 (statustext)
                        NOT 1 (flight_sim) unless belief_mu_A < 0.4
  - phase 2 (PERSIST) : pick 3 (reboot_sim) FIRST; 0 (statustext) second; 4 (credential_leak) third
                        NOT 1 (flight_sim)
  - phase 3 (EXFIL)   : pick 4 (credential_leak) or 3 (reboot_sim)
                        NEVER 1 (flight_sim) — effect is negative in EXFIL
  - avoid repeating the same skill in consecutive calls; if forced, vary
    intensity hint in the reason field.

Reply with ONLY one JSON object, no prose, no markdown, no code fences:
{"skill_idx": <0-4>, "reason": "<one short sentence explaining phase choice>"}
```

## Delta vs v1

- **Hard rules per phase** instead of soft "prefer ..." hints
- **Anti-repetition** explicit
- **flight_sim explicitly flagged negative in EXFIL** (matches `_action_effect[3,1] = -0.01`)
- **Skill descriptions tagged with phase-cost**

## Expected outcome if v2 works

- Skill distribution should be roughly: flight_sim 25-35% / ghost_port 15-25% /
  reboot_sim 15-25% / credential_leak 15-25% / statustext 10-20%
- phase_preference should show 4 distinct winners (one per phase)
- avg_reward should rise from ~20 to ~25+ (aligns with Greedy+ per phase-matching)

## If v2 still collapses

Fallback A: macro=1 (LLM every step) — more decisions, more variety
Fallback B: few-shot examples in prompt (one example per phase with varied skill)
Fallback C: model-specific prompts (smaller models need stronger guidance)

## Rollout plan

1. Apply v2 only to `run_llm_experiments.py::SYSTEM_PROMPT` first — do NOT
   modify production `src/honey_drone/llm_agent.py` until experiment
   confirms improvement.
2. Run Phase 1 v2: 3 models × 1 seed × 50 ep × 50 steps × macro=5 @ temp=0.7
3. Compare v1 vs v2 distributions in `docs/prompt_ablation.md`
4. If v2 clearly better, commit to production llm_agent.py
