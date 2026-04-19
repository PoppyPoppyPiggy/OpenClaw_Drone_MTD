# MIRAGE-UAS — Game-Theoretic Foundations and Evaluation

Draft for paper §2.5 (Related Work) and §5 (Evaluation).

---

## §2.5 Game-Theoretic Foundations of Cyber Deception

Defensive deception has been studied as an asymmetric-information game
for over a decade. Pawlick, Colbert and Zhu
*(ACM Computing Surveys, 2019)* consolidate this line of work into a
six-species taxonomy — **perturbation, moving target defense,
obfuscation, mixing, honey-x, and attacker engagement** — and survey
the Stackelberg, Nash and signalling-game formulations that solve
each species. Our three-tier architecture sits at the
honey-x / attacker-engagement boundary: Tier 3 plants UUID-tagged
honey-tokens (honey-x), while Tier 1 / Tier 2 direct live attacker
engagement through strategic-directive and tactical-skill channels.

Two strands of prior work are especially load-bearing for our design:

**(a) POSG honeypot placement.** Horák *et al.* (Elsevier *Computers
& Security*, 2019) formulate dynamic-lateral-movement honeypot
allocation as a Partially Observable Stochastic Game (POSG) in which
the defender has full state visibility and the attacker observes
only local network responses. The resulting defender policy
reallocates honeypots as the attacker traverses the attack graph.
MIRAGE-UAS inherits this POSG view: state includes per-drone phase,
attacker-tool level, dwell time, and honey-token issuance; the
defender observes all of it, while the attacker sees only
response-quality proxies derived from MAVLink / HTTP / WS replies.

**(b) MTD + honeynet integration.** Li *et al.* (*Information
Sciences*, 2025) introduce **GH-MTD**, a framework combining traffic
detection, a game-theoretic action selector, MTD mutation, and
honeynet probes; they report a 5.5× diversion improvement over
conventional honeypots and a 3.4× capture rate. Our system covers
the analogous blocks — detection via EngagementTracker, game-
theoretic action via Signaling-EQ / DQN / LLM, MTD via sysid-and-port
rotation, and the honeynet via Tier 3 OpenClaw-SDK emulation — and
extends the prescription by (i) using LLM tactical policies in the
place of hand-crafted MTD mutation rules, and (ii) adding a Tier 1
strategic layer.

**Recent adaptive timing work.** Carnevale *et al.*
(arXiv:2505.21244, 2025) — "When to Deceive: A Cross-Layer
Stackelberg Game Framework for Strategic Timing of Cyber Deception"
— pairs Stackelberg commitment with deep RL (AASGRL) to decide *when*
a deception should be triggered. Our proactive loop cadence
(8 s tactical, 30 s strategic) plays the same role in a fixed
scheduling regime; an adaptive-timing extension is future work.

**UAV-specific.** Zhang *et al.* (2023) — "Collaborative Honeypot
Defense in UAV Networks: A Learning-Based Game Approach" — models
fleet-level honeypot coordination as a Markov game and solves it via
alternating best-response. MIRAGE-UAS differs by (i) using
MAVLink-fidelity telemetry and a realistic attacker-tool hierarchy,
(ii) coordinating through an LLM-mediated strategic layer, and
(iii) evaluating against a cross-organisation suite of LLMs.

**Hypergame view.** Gutierrez *et al.* (2022) apply hypergame theory
to honeypot deception, modelling the *perception gap* between
attacker and defender: the two players are playing different games
because they believe different things. We adopt this lens when
measuring *hypergame stability*, i.e., the fraction of engagement
time during which the attacker's perceived game (thinking they are
attacking a real UAS) diverges from the actual game (they are in a
honeydrone).

**LLM-agent games.** A 2024 arXiv line of work (arXiv:2507.10621 and
adjacent) formalises LLM agents as game players, opening the door to
defender-LLM-vs-attacker-LLM experiments. Our pluggable
`AttackerPolicy` abstraction instantiates this framing — the scripted
L0–L4 adversary used in this paper can be replaced with an
LLM-driven one to run the same fictitious-play protocol we already
use for DQN, closing the symmetric-evaluation loop in future work.

---

## §5 Game-Theoretic Evaluation Metrics

We evaluate each defender policy under a common POSG formalisation
and seven metrics that together cover the four concerns identified
by the Pawlick survey — attacker belief manipulation, engagement,
resource cost, and detectability.

**Game definition.**

> $\mathcal{G} = (N, S, A_D, A_A, O_D, O_A, T, R_D, R_A, \beta, \gamma)$

with $N = \{D,A\}$, $S$ the product space over per-drone (phase,
level, $\mu_A$, dwell, services, exploits, ghost, time-in-phase,
evasion), $O_D = S$ (defender sees full state), $O_A$ a noisy
projection of $S$ (attacker observes response quality, timing,
surface footprint — not $\mu_A$ directly). Transitions $T$ follow
the DeceptionEnv Bayesian-update rules (§3.3); rewards
$R_D, R_A$ are the deception and intel-gain functions respectively;
$\beta$ is the attacker's prior P(real), $\gamma$ the horizon
discount.

### 5.1 Seven-metric battery

| Metric | Definition | Game-theoretic role |
|---|---|---|
| **1. Exploitability** | $\min_{\sigma_A} U_A(\sigma_D, \sigma_A) - \max_{\sigma'_A} U_A(\sigma_D, \sigma'_A)$ (Lanctot 2017 / our `analyze_game.py`) | Nash-distance: lower ⇒ more robust defender |
| **2. Belief manipulation** | $\mathbb{E}_t[(\mu_A(t)-0.5)^+]$ | Pawlick (2019) primary deception quality |
| **3. Information leakage** | $I(\text{identity};\,O_A) = H(O_A) - H(O_A \mid \text{identity})$ | Horák (2019) upper bound on attacker discrimination |
| **4. Deception ratio** | $1 - \text{KL}(P(a_A \mid \text{real}) \,\|\, P(a_A \mid \text{honey}))$ normalised | How similarly does A act against honey vs real |
| **5. Stackelberg value** | $U_D(\sigma_D^*, BR_A(\sigma_D^*))$ | Leader-follower upper bound on defender utility |
| **6. Nash regret** | $\max_t \sum_i \lvert U_i(\sigma_t) - U_i(\sigma^*) \rvert$ | Equilibrium-convergence rate (fictitious play) |
| **7. Hypergame stability** | $\Pr[\text{A's perceived game} \neq \text{actual game}] \cdot \text{dwell}$ (Gutierrez 2022) | Time attacker remains deceived, weighted |

### 5.2 Mapping to MIRAGE measurements

Metrics 1, 2, 6 are computable from existing 3-model × 3-seed V2
data (`results/llm_v2/*.json`) plus the Game-EQ checkpoints
(`results/models/game_*.pt`). Metrics 3, 4, 7 require an additional
*post-hoc* pass over the per-step trajectory log; a helper script
`scripts/compute_game_metrics.py` produces a consolidated table.

Metric 5 requires a best-response solver: for each defender policy
we train a DQN best-responder on the frozen defender. This is the
alternating best-response iteration already used in the Game-EQ
baseline; we extend it to also BR each of the LLM policies with 1000
episodes of the attacker DQN.

### 5.3 Reporting plan

Table VIII of the paper lists all seven metrics across the nine
defender policies {Random, Greedy, DQN, h-DQN, Game-EQ,
Signaling-EQ, Hybrid, LLM-Llama 3.1 8B, LLM-Qwen 2.5 14B,
LLM-Gemma 2 9B}, with 95 % percentile bootstrap CI (1000 resamples)
over the three seeds per learned/LLM policy (Random and Greedy have
single deterministic scores).

### 5.4 Expected direction

- LLM policies should dominate `belief manipulation` (their reasoning
  is tuned to maintain $\mu_A$).
- Game-EQ should dominate `exploitability` (trained explicitly by
  best-response fictitious play).
- Hybrid should be competitive on both.
- Random / Greedy set the floor and ceiling for a "skill but no
  strategy" reference.
- **Null hypothesis being tested**: in a simulated environment with
  heuristic reward, LLM policies provide measurable deception gains
  only when evaluated through game-theoretic lenses (belief, hypergame
  stability, information leakage) rather than raw reward.
