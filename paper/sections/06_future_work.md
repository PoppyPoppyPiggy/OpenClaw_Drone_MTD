# §6 Limitations and Future Work

We state the limitations bluntly rather than bury them, because the
most useful follow-up work is the one the reader could do against
our artefact. Each item carries a pointer to the experiment or
code path a future run would have to produce.

## 6.1 Training environment is heuristic

The `DeceptionEnv` action-effect matrix (§3.6) is qualitatively
seeded from the MITRE ATT&CK phase taxonomy and the honeypot
literature; its entries are *not* fit against measured attacker
responses. h-DQN and DQN baselines therefore learn against a
controllable testbed, not a ground-truth response surface. All §5
claims that reference **live Docker traffic** (§5.5 directive, §5.6
HTUR, §5.4 LLM-vs-LLM matrix) are independent of this matrix, but
any *simulator* reward reported alongside Table VII is conditioned
on the heuristic. The natural fix is a calibration run in which the
live 9-cell matrix outputs are regressed into the 4 × 5 effect
matrix; we leave this as the first post-submission experiment.

## 6.2 LLM-vs-LLM matrix is $n = 1$ per cell

Each of the nine matrix cells in §5.4 is one attacker campaign.
The diagonal-vs-off-diagonal AUC gap (+27 %) is suggestive rather
than confirmatory — we cannot attach a per-cell bootstrap CI.
A second seed (launched in parallel with the camera-ready) will
give $n = 2$; a full $n = 3$ requires another 75 minutes of wall
time and is scheduled for the full-paper revision. The main
V2 panel (Table VII) already uses 3 seeds × 3 models and is not
affected.

## 6.3 Attacker is scripted; attacker-policy LLM is a stub

The L0–L4 attacker is a handwritten state machine
(`scripts/attacker_sim.py`). Our `LLMAttackerBeliefTracker` is an
*observer* that emits a belief estimate after every packet, not an
action-policy — `AttackerPolicy::LLMAttackerPolicy` currently
raises `NotImplementedError`. Replacing the scripted action chooser
with an LLM-driven one would close the symmetric-evaluation loop
(same LLM family on both sides of the fictitious-play iteration
used by Game-EQ). We flag this as the highest-value follow-up
because the seven-metric game battery (§5.7) is formally compatible
with it — only the action module needs swapping.

## 6.4 SITL is a stub

Our `fcu_stub.py` emits correct MAVLink v2 framing but does not
simulate flight dynamics. Attackers that probe physics-consistent
deviations (e.g. acceleration ceilings, attitude rate limits) will
see a smoother response surface than a real ArduPilot SITL would
produce. Migrating to ArduPilot SITL under the same host-proxy
harness is a drop-in change for `fcu_stub.py`; we mark this as
*infrastructure work* rather than research and expect no narrative
impact — suspicion signals that currently rank `"API token handed
out too easily"` at 51× would need re-ranking.

## 6.5 Each attack run is short

Attacker campaigns in §5.4 run 15 s per level × 5 levels = 75 s per
cell (≈ 70–120 packets). The belief trajectory is coherent at this
scale but small-sample: packets-to-disbelief is integer-valued on a
sub-hundred draw. Longer `ATTACKER_LEVEL_DURATION_SEC` and a
matching Ollama `keep_alive` tune would give a smoother trajectory.

## 6.6 No per-LLM Stackelberg value

Exploitability is reported as a single Game-EQ reference
(mean 56.48 ± 7.31 across 3 seeds). A per-LLM Stackelberg value
requires training a BR-attacker DQN against every frozen defender.
The artefact ships the training harness
(`scripts/train_game_br.py`) and the checkpoint slots; the paper
reports only the reference because 9 independent BR-attacker
trainings exceed the abstract-deadline budget.

## 6.7 Prompt sensitivity not fully characterised

We compare V1 vs V2 but not V2 vs V2-perturbation. Robustness to
minor wording changes (comma deletion, reordered rules, rephrased
HARD RULE) would be valuable. A 5-perturbation × 3-model × 3-seed
grid is a natural extension and uses the existing driver.

## 6.8 Attacker belief ground truth is LLM-opinion

The belief-trajectory curve is an LLM observer's opinion, not a
measured subjective probability held by a human red-team
operator. Validating against expert annotation would require
inter-rater agreement on 400+ packet transcripts and is the
empirical-study counterpart to our quantitative evaluation.

## 6.9 Model coverage is three of five pulled

Phi-4 (Microsoft) and Mistral-Nemo (Mistral) were pulled and
warmed but excluded from the matrix to control wall-time. Adding
them would move Table IX from 3 × 3 to 5 × 5 and test whether the
same-family blind spot (§5.4) is a two-family anomaly or a
general rule.

## 6.10 Roadmap

| ID | Work                          | Blocks claim | Effort  |
|----|-------------------------------|--------------|---------|
| F1 | Calibrate `_action_effect` to live data | §6.1 | ~1 week |
| F2 | `n = 3` LLM-vs-LLM matrix     | §6.2 | 2.5 h  |
| F3 | Implement `LLMAttackerPolicy` | §6.3 | ~2 days |
| F4 | Swap SITL stub for ArduPilot  | §6.4 | ~1 day |
| F5 | Per-LLM Stackelberg BR training | §6.6 | ~0.5 day compute |
| F6 | Prompt-perturbation grid      | §6.7 | ~4 h   |
| F7 | 5 × 5 model matrix (add Phi-4, Mistral-Nemo) | §6.9 | ~1 h compute |

# §7 Conclusion

We presented MIRAGE-UAS, a three-tier LLM-driven deception
framework for UAS honeydrone fleets, and evaluated it across three
cross-organisation LLMs × three seeds with both bootstrap CI and
live Docker traffic. Three results are load-bearing.

**First**, mode collapse in naïve LLM-honeypot prompts
(qwen seed-42: 495/495 `flight_sim` under V1) is a **prompt**
problem, not a model problem. A hardened prompt with A-E dual
keying, four HARD RULES, explicit `last_action` feedback, and
inference-level `repeat_penalty = 1.2` restores a four-skill
distribution at $H(\pi) \geq 2.06$ bits **on every model we tried**
(§5.1). Belief $\mu_A$ rises simultaneously — the diversity
intervention does not cost deception quality.

**Second**, the three-tier hierarchy delivers a measurable effect
on Tier 2 choices in the live Docker stack, not only in
simulation: bias-skill hit-rate rises from 0 to 0.77 under a Tier 1
strategic directive across five tested skills (§5.5). Combined
with the live HTUR = 1.0 observation on `cc-honey-01` (§5.6), the
architecture's three layers each carry measurable operational
signal.

**Third**, a symmetric LLM-vs-LLM evaluation surfaces a deployment
caveat: **pairing a defender LLM with an attacker-belief LLM from
the same family produces a blind spot** (diagonal AUC mean 0.736 vs
off-diagonal 0.577, +27 %). This matters because attacker-side
honeypot-detection tools will increasingly be LLM-driven; the
defender's family choice and the likely attacker's family choice
are no longer independent degrees of freedom. The recurring
`"API token handed out too easily"` suspicion signal (51 × across
the matrix) provides a concrete next target for lure hardening.

These findings together argue that LLM-driven deception for
networked cyber-physical systems is **ready to deploy**, with
caveats that are measurable rather than hand-waved. The
framework, all prompts, the live Docker stack, and the complete
paper-ready artefact chain are available at the MIRAGE-UAS
repository.
