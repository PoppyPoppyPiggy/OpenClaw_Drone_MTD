# Prompt V1 → V2 — Ablation Report

Measurement of the mode-collapse mitigation in V2 (A-E keying + HARD RULES + last_action feedback + repeat_penalty + temp 0.9).

## Headline

| Model | metric | V1 | V2 | Δ |
|---|---|---|---|---|
| `gemma2:9b` | H(skill) bits | 0.000 | 2.072 | ↑ +2.072 |
| `gemma2:9b` | phase-uniq /4 | 1.00 | 2.67 | ↑ +1.670 |
| `gemma2:9b` | avg_R | 20.62 | 17.26 | ↓ -3.362 |
| `gemma2:9b` | avg_p_real | 0.917 | 0.960 | ↑ +0.043 |
| `gemma2:9b` | survival | 0.98 | 0.79 | ↓ -0.193 |
| `llama3.1:8b` | H(skill) bits | 0.685 | 2.097 | ↑ +1.412 |
| `llama3.1:8b` | phase-uniq /4 | 1.00 | 2.67 | ↑ +1.670 |
| `llama3.1:8b` | avg_R | 19.75 | 17.96 | ↓ -1.789 |
| `llama3.1:8b` | avg_p_real | 0.924 | 0.964 | ↑ +0.040 |
| `llama3.1:8b` | survival | 0.94 | 0.84 | ↓ -0.100 |
| `qwen2.5:14b` | H(skill) bits | 0.000 | 2.072 | ↑ +2.072 |
| `qwen2.5:14b` | phase-uniq /4 | 1.00 | 2.33 | ↑ +1.330 |
| `qwen2.5:14b` | avg_R | 20.62 | 18.15 | ↓ -2.469 |
| `qwen2.5:14b` | avg_p_real | 0.917 | 0.975 | ↑ +0.058 |
| `qwen2.5:14b` | survival | 0.98 | 0.85 | ↓ -0.133 |

## Skill distribution diff (V2 − V1, absolute percentage points)

### `gemma2:9b`

| skill | V1 % | V2 % | Δ pp |
|---|---|---|---|
| `proactive_fake_key` | 0.0 | 14.0 | ↑ +14.0 |
| `proactive_flight_sim` | 100.0 | 5.4 | ↓ -94.6 |
| `proactive_ghost_port` | 0.0 | 37.6 | ↑ +37.6 |
| `proactive_reboot` | 0.0 | 28.8 | ↑ +28.8 |
| `proactive_statustext` | 0.0 | 14.3 | ↑ +14.3 |

### `llama3.1:8b`

| skill | V1 % | V2 % | Δ pp |
|---|---|---|---|
| `proactive_fake_key` | 0.0 | 24.9 | ↑ +24.9 |
| `proactive_flight_sim` | 82.5 | 3.6 | ↓ -78.9 |
| `proactive_ghost_port` | 17.3 | 34.6 | ↑ +17.3 |
| `proactive_reboot` | 0.2 | 20.8 | ↑ +20.6 |
| `proactive_statustext` | 0.0 | 16.2 | ↑ +16.2 |

### `qwen2.5:14b`

| skill | V1 % | V2 % | Δ pp |
|---|---|---|---|
| `proactive_fake_key` | 0.0 | 13.6 | ↑ +13.6 |
| `proactive_flight_sim` | 100.0 | 4.1 | ↓ -95.9 |
| `proactive_ghost_port` | 0.0 | 37.6 | ↑ +37.6 |
| `proactive_reboot` | 0.0 | 25.3 | ↑ +25.3 |
| `proactive_statustext` | 0.0 | 19.3 | ↑ +19.3 |
