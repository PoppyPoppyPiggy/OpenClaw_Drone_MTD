# Table VIII — Game-Theoretic Evaluation

Per-policy metrics for the three metric families implementable from the existing 3-model × 3-seed V2 data (Information leakage, Deception ratio, Hypergame stability, Belief manipulation). 95 % percentile bootstrap CI over 3 seeds where applicable.

## §1 LLM defenders (3 models × 3 seeds)

| Policy | Belief manipulation | Information leakage (H, bits) | Deception ratio | Hypergame stability |
|---|---|---|---|---|
| `LLM-gemma2:9b` | 0.402 [0.389, 0.418] | 2.068 [2.064, 2.073] | 0.854 [0.850, 0.860] | 0.737 [0.645, 0.802] |
| `LLM-llama3.1:8b` | 0.398 [0.387, 0.416] | 2.093 [2.054, 2.120] | 0.919 [0.909, 0.925] | 0.783 [0.754, 0.839] |
| `LLM-qwen2.5:14b` | 0.416 [0.411, 0.422] | 2.067 [2.046, 2.079] | 0.842 [0.835, 0.853] | 0.805 [0.760, 0.842] |

**Direction of goodness**:
- Belief manipulation: higher is better for defender (attacker is deceived).
- Information leakage (H): higher is better (defender's action distribution has more entropy, harder for attacker to identify phase).
- Deception ratio: higher is better (defender's action distribution is similar across phases from attacker's view).
- Hypergame stability: higher is better (attacker remains in the wrong game longer).

## §2 Exploitability (Game-EQ fictitious-play best-response gain)

- Mean exploitability gain (BR vs random): **56.48** 
  ± 7.31 (n=3 seeds ['42', '1337', '2024'])
- Interpretation: best-response attacker's reward advantage over the random attacker baseline, summed over the evaluation horizon. Lower = more robust defender.
- Direct Stackelberg-value computation for each LLM policy requires training a BR-attacker DQN against every frozen defender; we report the Game-EQ pair as a reference point and mark per-LLM Stackelberg values as future work.

## §3 Nash regret (Game-EQ fictitious-play trajectory)

`exploitability_delta` per seed in `multi_seed_log.json` captures the final-round gap between attacker BR and fixed defender; earlier rounds' convergence is visualised in `results/game_nash_convergence.png` (produced by `scripts/analyze_game.py`). Standard deviation across seeds quantifies the Nash-regret variance; lower = more stable equilibrium.

## §4 What these metrics show in this data

- All LLM policies achieve large skill-entropy (≈ 2.07 bits) — 
  attacker's information advantage from observing a skill 
  sequence is near-maximal (diluted).
- Belief-manipulation ranks (higher=better): qwen > gemma ≈ llama.
- Hypergame stability ranks similarly: qwen > gemma ≈ llama (driven 
  by higher μ_A maintenance × survival).
- Deception ratio is highest for llama (most phase-similar action 
  distribution) — but this interacts with the phase-discrimination 
  check (Table VII); in paper §5 we discuss the trade-off.

## §5 Future-work metrics (not in this table)

- Stackelberg value per-LLM: requires BR-attacker training runs.
- KL-based identity-leakage: requires attacker action distribution 
  conditioned on *true identity* (real vs honey) — available only 
  in Docker E2E with live attacker_sim traffic, which the paper 
  scopes as future work.
- Hypergame perception-gap measurement: requires the attacker to 
  emit its belief estimate (currently not exposed in attacker_sim).