# Cross-Model Variance (V2, 3 seeds per model)

Within-model `mean ± std` computed over 3 seeds (42 / 1337 / 2024).
Between-model line reports std of the three per-model means.

## §1 Headline metrics

| Model | avg_R | avg_p_real | survival | H(skill) | Cramér V | V (bc) | χ² p | misbelief | latency (ms) |
|---|---|---|---|---|---|---|---|---|---|
| `gemma2:9b` | 17.26 ± 0.98 | 0.9597 ± 0.0145 | 0.787 ± 0.081 | 2.069 ± 0.005 | 0.419 ± 0.004 | 0.417 ± 0.004 | 0.0000 | 0.936 ± 0.017 | 1044 ± 10 |
| `llama3.1:8b` | 17.96 ± 0.57 | 0.9635 ± 0.0242 | 0.840 ± 0.035 | 2.093 ± 0.034 | 0.322 ± 0.007 | 0.319 ± 0.007 | 0.0000 | 0.932 ± 0.019 | 582 ± 16 |
| `qwen2.5:14b` | 18.15 ± 0.34 | 0.9748 ± 0.0108 | 0.847 ± 0.042 | 2.067 ± 0.019 | 0.449 ± 0.012 | 0.448 ± 0.012 | 0.0000 | 0.951 ± 0.004 | 714 ± 7 |

## §2 Between-model variance (std of per-model means across models)

Interpretation: high between-model std = models differ more than seeds do.

| Metric | mean across models | between-model std | range |
|---|---|---|---|
| `avg_reward` | 17.79 | 0.4693 | 0.8933 |
| `avg_p_real` | 0.966 | 0.007854 | 0.0151 |
| `survival_rate` | 0.8244 | 0.03289 | 0.06 |
| `skill_entropy_bits` | 2.076 | 0.01439 | 0.02557 |
| `cramers_v` | 0.3966 | 0.06652 | 0.1273 |
| `cramers_v_bias_corrected` | 0.3946 | 0.067 | 0.1282 |
| `chi_square_pvalue` | 0 | 0 | 0 |
| `misbelief_duration_ratio_mean` | 0.9398 | 0.01009 | 0.01933 |
| `p_real_std_mean` | 0.09029 | 0.0013 | 0.0024 |
| `phase_advance_rate_mean` | 0.0428 | 0.0007211 | 0.0014 |
| `max_phase_reached_mean` | 2.056 | 0.04823 | 0.08667 |
| `mean_latency_ms` | 780 | 237.8 | 461.6 |
| `fallback_rate` | 0.0004444 | 0.0007698 | 0.001333 |

## §3 Cross-model Cramér's V ranking

Cramér's V quantifies the strength of phase-skill dependence. Higher = more phase-aware. Cohen thresholds (df=3): V<0.06 negligible; 0.06–0.17 small; 0.17–0.29 medium; ≥0.29 large.

| Rank | Model | Cramér V mean | effect |
|---|---|---|---|
| 1 | `qwen2.5:14b` | 0.449 | large |
| 2 | `gemma2:9b` | 0.419 | large |
| 3 | `llama3.1:8b` | 0.322 | large |