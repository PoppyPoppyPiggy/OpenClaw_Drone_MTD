# DeceptionScore v2 — Applied to V2 experiment

HTUR source: `results/diagnostics/htur.json` → HTUR = **1.000** (offline synthetic; applied uniformly).

## §1 Per-run scores under uniform weights

| Model | seed | HTUR | belief | engage | coverage | policy | **score** |
|---|---|---|---|---|---|---|---|
| `gemma2:9b` | 1337 | 1.000 | 0.467 | 0.800 | 0.957 | 0.893 | **0.823** |
| `gemma2:9b` | 2024 | 1.000 | 0.469 | 0.860 | 0.955 | 0.889 | **0.835** |
| `gemma2:9b` | 42 | 1.000 | 0.443 | 0.700 | 0.958 | 0.891 | **0.798** |
| `llama3.1:8b` | 1337 | 1.000 | 0.437 | 0.820 | 0.961 | 0.913 | **0.826** |
| `llama3.1:8b` | 2024 | 1.000 | 0.470 | 0.820 | 0.955 | 0.885 | **0.826** |
| `llama3.1:8b` | 42 | 1.000 | 0.484 | 0.880 | 0.955 | 0.906 | **0.845** |
| `qwen2.5:14b` | 1337 | 1.000 | 0.467 | 0.860 | 0.956 | 0.894 | **0.835** |
| `qwen2.5:14b` | 2024 | 1.000 | 0.487 | 0.880 | 0.957 | 0.896 | **0.844** |
| `qwen2.5:14b` | 42 | 1.000 | 0.470 | 0.800 | 0.961 | 0.881 | **0.823** |

## §2 Per-model aggregate (mean across seeds, uniform weights)

| Model | n_seeds | mean DeceptionScore |
|---|---|---|
| `gemma2:9b` | 3 | **0.819** |
| `llama3.1:8b` | 3 | **0.832** |
| `qwen2.5:14b` | 3 | **0.834** |

## §3 Sensitivity analysis (first run, all 6 weight profiles)

Interpretation: if the score ranking is stable across profiles, the conclusion is robust to weight choice. Profile names indicate which component is up-weighted to 0.40 (others 0.15 each); `uniform` is 0.20 each.

| Profile | score (gemma2:9b, s=1337) | score (gemma2:9b, s=2024) | score (gemma2:9b, s=42) |
|---|---|---|---|
| HTUR-heavy | 0.868 | 0.876 | 0.849 |
| belief-heavy | 0.734 | 0.743 | 0.710 |
| engagement-heavy | 0.818 | 0.841 | 0.774 |
| coverage-heavy | 0.857 | 0.865 | 0.838 |
| policy-heavy | 0.841 | 0.848 | 0.822 |
| uniform | 0.823 | 0.835 | 0.798 |

## §4 Weights used (uniform default)

```json
{
  "w_htur": 0.2,
  "w_belief": 0.2,
  "w_eng": 0.2,
  "w_cov": 0.2,
  "w_pol": 0.2
}
```
