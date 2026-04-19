# LLM V2 — Experiment Summary

Aggregated across 3 seeds per model (95 % percentile bootstrap CI, 1000 resamples).

## §1 Headline metrics

| Model | avg_R | avg_p_real | survival | H(skill, bits) | χ² p | phase-uniq /4 |
|---|---|---|---|---|---|---|
| `gemma2:9b` | 17.259 ± 0.971 | 0.9597 ± 0.0130 | 0.787 ± 0.080 | 2.068 ± 0.005 | 0.0000 | 2.67 ± 0.50 |
| `llama3.1:8b` | 17.956 ± 0.560 | 0.9635 ± 0.0235 | 0.840 ± 0.030 | 2.093 ± 0.033 | 0.0000 | 2.67 ± 0.50 |
| `qwen2.5:14b` | 18.152 ± 0.318 | 0.9748 ± 0.0101 | 0.847 ± 0.040 | 2.067 ± 0.017 | 0.0000 | 2.33 ± 0.50 |

χ² p < 0.05 indicates that skill choice is NOT independent of the attacker phase — i.e., the LLM is selecting phase-appropriately.
phase-uniq = number of distinct skills appearing as the modal choice in each of the 4 phases (max = 4).

## §2 L2 Belief metrics

| Model | mean p_real | p_real std | misbelief duration ratio |
|---|---|---|---|
| `gemma2:9b` | 0.9287 ± 0.0079 | 0.0912 ± 0.0063 | 0.936 ± 0.015 |
| `llama3.1:8b` | 0.9267 ± 0.0081 | 0.0909 ± 0.0025 | 0.932 ± 0.011 |
| `qwen2.5:14b` | 0.9372 ± 0.0035 | 0.0888 ± 0.0023 | 0.951 ± 0.004 |

## §3 L4 Coverage metrics (lower phase_advance_rate is better for defender)

| Model | phase_advance_rate | max_phase_reached | reversals | time share (R/EXP/PRS/EXF) |
|---|---|---|---|---|
| `gemma2:9b` | 0.043 ± 0.002 | 2.08 ± 0.11 | 0.00 ± 0.00 | 0.39 / 0.33 / 0.19 / 0.08 |
| `llama3.1:8b` | 0.043 ± 0.003 | 2.09 ± 0.14 | 0.00 ± 0.00 | 0.39 / 0.36 / 0.17 / 0.08 |
| `qwen2.5:14b` | 0.042 ± 0.003 | 2.00 ± 0.15 | 0.00 ± 0.00 | 0.43 / 0.33 / 0.17 / 0.07 |

## §4 L5 Policy diversity (Phase × Skill)

| Model | skill entropy (bits) | JS divergence phase-pair mean | phase-uniq |
|---|---|---|---|
| `gemma2:9b` | 2.068 ± 0.005 | 0.3382 ± 0.0111 | 2.67 ± 0.50 |
| `llama3.1:8b` | 2.093 ± 0.033 | 0.1872 ± 0.0181 | 2.67 ± 0.50 |
| `qwen2.5:14b` | 2.067 ± 0.017 | 0.3676 ± 0.0209 | 2.33 ± 0.50 |

## §5 Reward decomposition (per-step contribution)

Dominant contributor reveals what the LLM is actually optimising for. Negative r_safety indicates evasion pressure the defender is under.

| Model | r_belief | r_engage | r_dwell | r_safety |
|---|---|---|---|---|
| `gemma2:9b` | 0.1033 ± 0.0014 | 0.1291 ± 0.0020 | 0.0750 ± 0.0000 | -0.0140 ± 0.0015 |
| `llama3.1:8b` | 0.1037 ± 0.0039 | 0.1327 ± 0.0021 | 0.0750 ± 0.0000 | -0.0139 ± 0.0013 |
| `qwen2.5:14b` | 0.1018 ± 0.0023 | 0.1320 ± 0.0035 | 0.0750 ± 0.0000 | -0.0133 ± 0.0010 |

## §6 Operational (LLM latency + fallback)

| Model | mean latency (ms) | p95 latency (ms) | fallback rate | calls/seed |
|---|---|---|---|---|
| `gemma2:9b` | 1044 ± 9 | 1253 ± 14 | 0.0000 ± 0.0000 | 476 ± 10 |
| `llama3.1:8b` | 582 ± 16 | 706 ± 25 | 0.0013 ± 0.0020 | 479 ± 14 |
| `qwen2.5:14b` | 714 ± 6 | 841 ± 8 | 0.0000 ± 0.0000 | 486 ± 4 |