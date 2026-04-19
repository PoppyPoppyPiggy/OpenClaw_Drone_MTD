# §4 Methodology

We evaluate MIRAGE-UAS under two complementary regimes. §4.1
specifies the prompt (V1 naïve vs V2 hardened) whose ablation
isolates the engineering contribution. §4.2 gives the Ollama
inference configuration. §4.3 describes the simulator-based
cross-model protocol used to generate the 9-run panel. §4.4 gives
the live Docker protocol (strategic-directive delivery, HTUR,
LLM-vs-LLM matrix). §4.5 states the statistical tests. §4.6 defines
the composite DeceptionScore v2. §4.7 formalises the POSG game
used by the seven-metric battery of §5.

## 4.1 Prompt ablation — V1 naïve vs V2 hardened

**V1 (soft hint)** — three lines of "prefer X, Y, or Z per phase",
no hard prohibition, one-line JSON format. Under Qwen 2.5 14 B
seed-42, this collapsed to `flight_sim` on 495/495 tactical calls
(§1.2 P1). V1 is retained **only** as an ablation baseline.

**V2 (hardened)** — the production prompt embeds four mitigations:

1. **A-E keying.** Skills are labelled `A/0 = statustext`,
   `B/1 = flight_sim`, ..., `E/4 = credential_leak`. The JSON reply
   may return either letter or number. Dual keying breaks a failure
   mode where LLMs tied to one lexical form repeat it unconditionally.
2. **CRITICAL CONSTRAINTS ("HARD RULES").** Four imperative clauses:
   (i) prefer a DIFFERENT skill from `last_action`; (ii) if
   `phase_changed_since_last`, CHANGE class; (iii) **never** pick
   `flight_sim` in EXFIL; (iv) in PERSIST, strongly prefer
   `reboot_sim` on first entry.
3. **Contextual feedback.** Each call receives `last_action` and
   `phase_changed_since_last` fields; the LLM thereby sees its own
   recent behaviour in-prompt, a cheap form of implicit history.
4. **Inference-level repetition damping.** `repeat_penalty = 1.2`
   and `temperature = 0.9` at the Ollama API layer (§4.2). The
   combination of a hard rule *and* a softer probability mass penalty
   is what pushes Qwen 2.5 14 B from 100 % `flight_sim` (V1) to a
   four-skill distribution with entropy 2.06 bits (V2).

The two-prompt ablation is measured on the same 3-model × 3-seed
panel; §5.1 reports skill distributions and χ² phase-skill
independence for both.

## 4.2 Inference configuration

| Parameter        | Value                     | Rationale                                   |
|------------------|---------------------------|---------------------------------------------|
| backend          | Ollama `/api/chat`        | local, reproducible, GPU-efficient          |
| format           | `json`                    | server-side JSON schema enforcement          |
| `num_predict`    | 200 tokens                | enough for one JSON object + short reason   |
| `temperature`    | 0.9                       | avoids greedy lock-in (V1 used 0.4)         |
| `top_p`          | 0.95                      | standard nucleus                            |
| `top_k`          | 40                        | standard                                     |
| `repeat_penalty` | 1.2                       | reinforces HARD RULE 1                       |
| `timeout`        | 6 s                       | exceeds P95 latency; fallback to uniform random on miss |
| `keep_alive`     | 1 h                       | warm model reuse across the 8-s tactical loop |

Fallbacks (parse / network / timeout errors) return a uniform
random skill and emit a `llm_agent_fallback` event. Across the 9
V2 runs, fallback rate is **< 0.4 %** per seed (gemma 0.0 %, llama
0.2 %, qwen 0.3 %).

## 4.3 Cross-model simulator protocol

**Panel.** Three LLMs drawn from three distinct organisations to
de-confound family-specific idiosyncrasies:
Llama 3.1 8 B (Meta), Qwen 2.5 14 B (Alibaba), Gemma 2 9 B (Google).
Each is crossed with three random seeds (42, 43, 44), giving a
$3 \times 3 = 9$-run panel.

**Episodes.** 50 episodes × 50 environment steps per seed. Each
step fires one tactical-skill decision; each episode is one
attacker campaign (L0→L4 over ~200 stochastic transitions). The
action space is the 45-action parameterised mode (5 skills ×
3 intensities × 3 variants); reward is the deception-composite of
§3.6.

**Hardware & environment.**  LLM inference runs on a single RTX-4090
via Ollama (Windows host at 172.23.240.1:11434). The honey stack and
driver run inside WSL 2 Docker. `PYTHONHASHSEED=0` is pinned in both
Dockerfiles to remove a host-vs-Docker dictionary-ordering hazard
previously observed.

## 4.4 Live Docker protocol

Three live experiments validate what the simulator cannot:

1. **Tier 1 directive ON/OFF ablation (§5.5).** 6 conditions
   (1 baseline + 5 bias-one-skill) × 30 Ollama calls, Llama 3.1 8 B
   inside `cc-honey-01`. Metric: skill-hit-rate and KL divergence
   from baseline distribution.
2. **HTUR live attacker run (§5.6).** `scripts/attacker_sim.py`
   runs the full L0–L4 campaign against `cc-honey-01`; the
   OpenClaw service logs `honeytoken_issued` and
   `honeytoken_reused` events from which HTUR is computed.
3. **Symmetric LLM-vs-LLM matrix (§5.4).** A 3 defenders × 3
   attacker-belief-observer LLMs cell grid. Each cell recreates the
   honey stack with the chosen `DEFENDER_LLM_MODEL` then runs
   `attacker_sim.py` (300 s budget) while the
   `LLMAttackerBeliefTracker` records a belief trajectory at every
   packet. Outputs: per-cell belief AUC, packets-to-disbelief,
   final $\mu_{\text{real}}$.

## 4.5 Statistical tests

**Bootstrap 95 % CI.** 1000 percentile resamples over per-seed
scalar statistics. All numbers in §5 Table VII carry percentile
95 % CI; "not significant" is judged by CI overlap with the
ablation null.

**χ² phase-skill independence.** For each run we tabulate the
5-skill × 4-phase contingency and compute the χ² statistic under
independence. V2 rejects independence at $p \approx 0$ for every
model (§5.1), V1 cannot reject (the `flight_sim` column absorbs all
mass).

**Cramér's V (between-model variance).** To quantify how much
skill distributions differ across the three LLMs, we aggregate
per-model 4×5 contingencies and compute Cramér's V on the
model-skill marginal. Larger V ⇒ larger model effect; we use V to
rank "model disagreement" per phase and report the ranking in
Table `table_cross_model_variance.md`.

**Shannon entropy.** Skill distributions are summarised by
$H(\pi) = -\sum_s p_s \log_2 p_s$; we report per-model-per-seed
entropy in bits, lower bound by 0 (collapse), upper bound by
$\log_2 5 \approx 2.32$ bits (uniform).

## 4.6 DeceptionScore v2

The composite score (paper Table `table_deception_score_v2.md`)
combines five components with weights in `config/.env`:

$$
\mathrm{DS_{v2}} = w_1 \text{DR} + w_2 \text{BP} + w_3 \text{IQ}
                 + w_4 \text{TC} + w_5 \text{ME}
$$

with $(w_1,\ldots,w_5) = (0.20, 0.25, 0.25, 0.20, 0.10)$ taken from
prior honeypot literature:

- **DR** — Diversion Ratio (arXiv:2603.17272)
- **BP** — Breach Prevention (proportion of non-real-asset hits)
- **IQ** — Interaction Quality (HoneyGPT H1–H4; replaces the
  hard-coded 0.72 `confusion_score`)
- **TC** — Temporal Cost (1 − Zhuang MTTC / T_max)
- **ME** — MTD Effectiveness (arXiv:2504.11661)

A $5^5 = 3125$-cell weight-sensitivity grid accompanies the
headline result so robustness-to-weights is visible.

## 4.7 POSG formalisation

All seven game-theoretic metrics (§5) are evaluated under the
common POSG

$$
\mathcal{G} = (N, S, A_D, A_A, O_D, O_A, T, R_D, R_A, \beta, \gamma)
$$

with $N = \{D, A\}$; $S$ the product over per-drone (phase, level,
$\mu_A$, dwell, services, exploits, ghost, time-in-phase, evasion);
$O_D = S$; $O_A$ a noisy projection of $S$ (attacker observes only
response quality, timing, surface footprint — not $\mu_A$); $T$
follows the DeceptionEnv Bayesian-update rules (§3.6); $R_D, R_A$
are the deception and intel-gain functions respectively; $\beta$
the attacker prior $P(\text{real})$; $\gamma = 0.99$ the horizon
discount. Attacker-belief estimation uses a confidence-weighted
Bayesian update

$$
\mu_A^{(t+1)} = (1 - w_t) \, \mu_A^{(t)} + w_t \, o_t,
$$

where $o_t \in [0,1]$ is the per-packet LLM-observer opinion and
$w_t$ its self-reported confidence. The tracker is thus an
**LLM-observer**, not an LLM action-policy: action selection by the
attacker remains the scripted L0–L4 state machine
(`scripts/attacker_sim.py`). §6 lists the symmetric
LLM-action-policy extension as future work.
