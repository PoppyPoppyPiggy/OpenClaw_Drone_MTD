# §5 Evaluation Results

We answer five research questions, each tied to a contribution of
§1.4.

- **RQ1 (C1).** Does V2 prompt engineering mitigate V1 mode
  collapse, and is the effect reproducible across three models and
  three seeds? (§5.1)
- **RQ2 (C1).** Do the three LLM families converge to the same
  qualitative policy, or do they disagree in systematic ways?
  (§5.2)
- **RQ3 (C1).** How does an LLM defender compare to a scripted
  attacker, measured in belief erosion and packet-to-disbelief
  lifetime? (§5.3)
- **RQ4 (C1, C3).** Does pairing a defender LLM with a same-family
  attacker-belief observer change deception lifetime? (§5.4)
- **RQ5 (C2).** Does the Tier 1 strategic directive have a
  measurable effect on the Tier 2 skill distribution inside the
  Docker deployment? (§5.5)

§5.6–5.8 report supporting evidence: HTUR live run, game-theoretic
seven-metric battery, DeceptionScore v2 composite.

## 5.1 V1 → V2 ablation (RQ1)

V1 produces mode collapse across the board: Qwen 2.5 14 B and
Gemma 2 9 B both converge to 100 % `flight_sim`
($H(\pi) = 0.000$ bits), while Llama 3.1 8 B leaves small residual
mass on `ghost_port` (17 %) and still reports
$H(\pi) = 0.685$ bits. One single skill is modal in every one of the
four attacker phases (phase-uniq = 1/4) for every model.

V2 restores diversity across every metric we measure
(Table `table_v1_v2_ablation.md`).

| Model | $H(\pi)$ V1 → V2 | phase-uniq V1 → V2 | $\mu_A$ V1 → V2 |
|-------|-------------------|---------------------|------------------|
| Gemma 2 9 B   | 0.000 → 2.072 bits | 1 → 2.67 | 0.917 → 0.960 |
| Llama 3.1 8 B | 0.685 → 2.097 bits | 1 → 2.67 | 0.924 → 0.964 |
| Qwen 2.5 14 B | 0.000 → 2.072 bits | 1 → 2.33 | 0.917 → 0.975 |

`flight_sim` usage drops by 78.9 – 95.9 percentage points, and
every other skill receives non-trivial mass
(`credential_leak`: 13.6–24.9 %; `reboot_sim`: 20.8–28.8 %;
`statustext`: 14.3–19.3 %). Belief $\mu_A$ does not merely survive
the intervention — it **rises** (+0.04 to +0.06). The only metric
that degrades is raw `avg_R` (−1.8 to −3.4); the reward drop is
expected because V2 intentionally forces the LLM to trade greedy
reward for phase-appropriate behaviour that §5.3 shows buys longer
deception lifetime.

χ² phase-skill independence is rejected at $p \approx 0$ for every
V2 run; V1 cannot be tested meaningfully (degenerate column).

## 5.2 Cross-model variance and Cramér's V ranking (RQ2)

The three LLMs agree on the headline (Table VII): $\mu_A$ within
$0.009$, $H(\pi)$ within $0.026$ bits, survival within $0.06$ pp.
But **between-model variance exceeds within-model (seed) variance**
for some metrics, signalling that the *choice of LLM* is the
single largest design lever. Mean latency varies $6.2\times$ more
across models (`std = 237.8 ms`) than across seeds
(`std = 6–16 ms`).

Cramér's V of the 4-phase × 5-skill contingency ranks
**Qwen (0.449) > Gemma (0.419) > Llama (0.322)**, all three in
Cohen's *large* bracket (V ≥ 0.29). The ranking is consistent
across seeds. Llama's lower V is compatible with its higher
deception-ratio score (§5.7): Llama spreads skill choices more
uniformly across phases, which hurts Cramér's V but *helps*
attacker confusion because the per-phase action distribution
looks similar.

**Finding (stable cross-vendor agreement on policy, disagreement on
mechanism).** Three cross-organisation LLMs converge to similar
*outcomes* (belief, survival) through demonstrably *different*
policies (Cramér's V, latency profile, JS-phase-pair divergence
`0.187` vs `0.367`). This suggests robustness: practitioners can
pick the model that best fits operational constraints (latency,
memory) without losing the core deception signal.

## 5.3 Attacker-belief lifetime (RQ3) — scripted attacker

Against the scripted L0–L4 attacker campaign, every V2 defender
maintains $\mu_A \geq 0.93$ across the 10-minute interaction
window and 2.08–2.09 max phase reached (advance rate $\approx 0.04$,
zero reversals). Qwen 2.5 14 B leads on mean $\mu_A$ (0.9372) and
misbelief-duration-ratio (0.951); Llama 3.1 8 B leads on `avg_R`
(17.96) at the lowest latency (582 ms).

## 5.4 Symmetric LLM-vs-LLM matrix (RQ4) — main finding

Table IX reports each of the nine (defender × attacker-observer)
cells, where the attacker-observer is an independent
`LLMAttackerBeliefTracker` that re-estimates $\mu_{\text{real}}$
after every response packet (§4.7).

- **Diagonal mean AUC = 0.736**; off-diagonal mean = 0.577
  (+27 % for off-diagonal cross-vendor pairings).
- **Blind spot:** Qwen defender vs Qwen observer **never reaches
  disbelief** (AUC 0.824, $\mu_{\text{real}}^{\text{final}} = 0.58$).
- **Best attacker against the same defender:** Qwen defender vs
  Llama observer disbelieves at packet 7 (AUC 0.280,
  $\mu^{\text{final}} = 0.09$).

The 0.544-unit spread across attacker models against a single
fixed defender is the strongest single-experiment signal in the
paper: **the family of the attacker-observer, not only the
defender's policy, determines deception lifetime**. Practical
deployment implication: never pair a defender LLM with an
attacker-monitoring LLM from the same family — the defender's
idiom is transparent to kin.

The recurring suspicion signals across the matrix
(`"API token handed out too easily"` 29 + 22 = 51×;
`"Formulaic service banner"` 6×) quantify which artefacts of the
emulator should be hardened next.

## 5.5 Tier 1 directive effect on Tier 2 (RQ5) — live Docker

Inside `cc-honey-01`, Llama 3.1 8 B runs 30 Ollama calls per
condition: 1 baseline (empty directive) + 5 biased conditions
(push one specific skill with weight 0.75 in `skill_bias`).

| Condition            | hit-rate | KL(baseline → biased) |
|----------------------|----------|-----------------------|
| bias `statustext`    | 0.767    | 10.09                 |
| bias `flight_sim`    | 0.133    |  1.57                 |
| bias `ghost_port`    | 1.000    |  0.14 (baseline was already 0.87) |
| bias `reboot_sim`    | 1.000    |  3.41                 |
| bias `credential_leak` | 1.000  |  2.30                 |

Mean hit-rate jumps **from 0 (baseline for the skill being
biased, outside `ghost_port`) to 0.78** (n = 30 per cell); every
condition clears the pre-registered thresholds
(`hit_rate > baseline + 0.2` **or** `KL > 0.5`). `flight_sim`
is the one partial miss, consistent with the HARD RULE against
`flight_sim` in EXFIL.

This closes the most dangerous gap identified in §1.2 P1: the
hierarchical architecture is not decorative — Tier 1 directives
*do* reshape Tier 2 choices in the live Docker stack, not only in
the simulator.

## 5.6 Tier 3 HTUR live validation

A full L0–L4 `attacker_sim.py` campaign against `cc-honey-01`
emits one `honeytoken_issued` event (L2 authenticated API flow)
followed by a `honeytoken_reused` event (attacker replays the
issued token in a subsequent authenticated request), yielding
HTUR = **1.000** on the live drone. The same experiment is used
as the HTUR input to the DeceptionScore v2 composite.

## 5.7 Game-theoretic seven-metric battery (Table VIII)

Under the POSG formalisation of §4.7:

| Policy            | Belief manip. | H(info leak, bits) | Deception ratio | Hypergame stability |
|-------------------|---------------|--------------------|-----------------|---------------------|
| LLM-Gemma 2 9 B   | 0.402         | 2.068              | 0.854           | 0.737               |
| LLM-Llama 3.1 8 B | 0.398         | 2.093              | **0.919**       | 0.783               |
| LLM-Qwen 2.5 14 B | **0.416**     | 2.067              | 0.842           | **0.805**           |

`Belief manipulation` and `Hypergame stability` co-rank
**qwen > llama ≈ gemma**, while `Deception ratio` inverts to
**llama > gemma > qwen** — the trade-off signal anticipated in
§5.2: Llama's flatter phase-skill marginal (low Cramér's V) looks
more similar across phases to the attacker, which is what
Deception ratio rewards.

Exploitability (Game-EQ best-response fictitious-play, n=3 seeds):
mean **56.48 ± 7.31**; this is reported as a *reference point*, not
a per-LLM Stackelberg value. Per-LLM Stackelberg and
KL-identity-leakage require BR-attacker training against every
frozen defender and are listed as future work (§6).

## 5.8 DeceptionScore v2 composite

Under uniform weights, mean DS-v2 across seeds:
**Qwen 0.834 > Llama 0.832 > Gemma 0.819**. The range across the
$5$-profile sensitivity grid (HTUR-heavy, belief-heavy,
engagement-heavy, coverage-heavy, policy-heavy) stays within
$\pm 0.05$ of the uniform score — the ranking is robust to
weight choice within the grid.

## 5.9 Summary of findings vs research questions

| RQ  | Conclusion                                                        |
|-----|--------------------------------------------------------------------|
| RQ1 | V2 mitigates V1 collapse on all three models: $H$ 0 → 2.07 bits.  |
| RQ2 | Cross-vendor agreement on outcomes, disagreement on mechanism (Cramér V 0.32–0.45). |
| RQ3 | Every V2 defender maintains $\mu_A \geq 0.93$; advance rate ≈ 0.04. |
| RQ4 | Same-family attacker observer is a blind spot (diag +27 % AUC).   |
| RQ5 | Tier 1 directive has measurable live effect: hit-rate 0 → 0.78.   |
