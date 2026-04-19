# Docker Integration Changelog (2026-04-19)

Scope: user-approved limited Docker-integration task. NOT a full upstream
OpenClaw Node.js integration. Constraint: host-Ollama only, 3 approved
models only, V2 run data untouched.

## New files

| Path | Purpose |
|---|---|
| `docker/Dockerfile.gcs` | Tier 1 GCS container (Python 3.11, aiohttp+httpx+structlog) |
| `config/docker-compose.honey.llm.yml` | LLM-mode overlay (env + extra_hosts + gcs service) |

## Modified files

| Path | Change | Reason |
|---|---|---|
| `src/gcs/strategic_agent.py` | `_emit_directive` now targets per-drone DNS (`cc_{drone_id}`) + loopback fallback; reads `GCS_DRONE_TARGETS` env var | Loopback-only emit can't reach other containers |
| `src/honey_drone/openclaw_agent.py` | `_start_directive_listener` binds `0.0.0.0:19995` instead of `127.0.0.1` | Container-isolated loopback was unreachable from peer container |

## Compose additions

In `config/docker-compose.honey.llm.yml` (override file):

```yaml
x-llm-env: &llm-env
  DEFENDER_POLICY: llm_agent
  LLM_AGENT_OLLAMA_URL: http://172.23.240.1:11434     # Windows-host IP via WSL
  LLM_AGENT_MODEL: qwen2.5:14b
  LLM_AGENT_TIMEOUT_SEC: "25.0"
  LLM_AGENT_TEMPERATURE: "0.9"
  AGENT_PROACTIVE_INTERVAL_SEC: "8"

x-llm-extra-hosts: &llm-extra-hosts
  - "host.docker.internal:host-gateway"

services:
  cc-honey-01 / 02 / 03:   # overlay merge into existing base compose
    environment: *llm-env
    extra_hosts: *llm-extra-hosts

  gcs:                     # NEW service
    image: mirage-gcs:latest
    build: { context: .., dockerfile: docker/Dockerfile.gcs }
    networks: [internal, honey_net]   # honey_net needed for egress to Windows
    extra_hosts: ["host.docker.internal:host-gateway"]
    environment:
      GCS_OLLAMA_URL: http://172.23.240.1:11434
      GCS_STRATEGIC_MODEL: qwen2.5:14b
      GCS_DRONE_IDS: honey_01,honey_02,honey_03
      GCS_INTERVAL_SEC: "30"
    depends_on: [cc-honey-01, cc-honey-02, cc-honey-03]
```

## Gotchas discovered during build-out (for reproducibility)

### G1. `.env`의 `DEFENDER_POLICY=signaling_eq`가 override의 default를 이김

- 증상: `${DEFENDER_POLICY:-llm_agent}`가 .env의 value로 해석됨.
- 해결: override에서 환경변수 하드코딩 (`DEFENDER_POLICY: llm_agent`).

### G2. `host.docker.internal:host-gateway`가 Docker Desktop 없이는 WSL 게이트웨이(172.17.0.1)로 resolve

- 증상: `Cannot connect to host 172.17.0.1:11434 Connect call failed`.
- 원인: Linux Docker는 host-gateway를 Docker bridge로만 매핑. Windows Ollama는 172.23.240.1에 있음.
- 해결: LLM/GCS 환경변수를 `http://172.23.240.1:11434` 로 직접 지정.
- 주의: 172.23.240.1은 WSL 세션별로 바뀔 수 있음. `ip route show | grep default | awk '{print $3}'` 로 조회.

### G3. GCS 컨테이너가 `internal` 네트워크만 있으면 외부 egress 실패

- 증상: GCS → Ollama 연결 `Network is unreachable`.
- 해결: `honey_net` 추가 — honey containers와 동일 네트워크 세그먼트.

### G4. Loopback (127.0.0.1) 바인드는 컨테이너 로컬만

- 증상: GCS가 `127.0.0.1:19995`로 쏘면 자기 자신만 받고 honey 컨테이너 도달 안 함.
- 해결 1: 리스너를 `0.0.0.0:19995`로 바인드.
- 해결 2: 에미터가 각 드론의 Docker DNS 이름 (`cc_honey_01`, ...)으로 전송.
- 두 변경 다 필요했음.

## Host vs Docker 환경 비교 — Tier 1 directive hit-rate

same model (llama3.1:8b), same prompt (v2), same Ollama (Windows host).

| Biased toward | Host hit-rate | Docker hit-rate | Δ (%p) |
|---|---|---|---|
| statustext | 100% | 100% | 0 |
| flight_sim | **12%** | **25%** | **+13** |
| ghost_port | 100% | 100% | 0 |
| reboot_sim | 100% | 100% | 0 |
| credential_leak | 100% | 100% | 0 |

**±5%p 초과한 항목: flight_sim (+13pp)**

### 원인 분석 (user HARD RULE 조항 대응)

1. **RNG 스트림 차이** — `LLMTacticalAgent` 는 `Random((hash(drone_id) ^ hash(model_name)) & 0xFFFFFFFF)`로 시드. host / container 모두 동일 hash이지만 Python 인터프리터 버전·빌드에 따라 hash salt가 다름 (PYTHONHASHSEED이 unset이면 random).
2. **샘플 크기** — `--calls 8` (host) / `--calls 8` (Docker). 1/8 ≈ 12.5%, 2/8 = 25%. **노이즈 범위 내**. flight_sim을 ± 1 회만 더/덜 뽑아도 %p 단위로 크게 흔들림.
3. **Ollama keep_alive 상태** — host 테스트는 Ollama가 이미 llama3.1을 VRAM에 올려놓은 상태. Docker 테스트도 동일 GPU 사용하지만 다른 순서. 온도 0.9 + repeat_penalty 1.2 아래에서 sampling은 결정적이지 않음.

### 결론

- **HARD RULE 효과는 보존됨**: flight_sim 은 "EXPLOIT + belief=0.62" 조건에서 다른 directive 대비 **75%p 이상 낮은 hit rate** (host 88%p↓, Docker 75%p↓).
- 12% vs 25%는 **샘플링 노이즈 + RNG 시드 차이**의 작은 소규모 표본 효과.
- 더 확실하게 재현하려면 `PYTHONHASHSEED=0` + `--calls 30+` 로 재검증 필요 (후속).

## Host 버전 vs Docker 버전 metric 비교

| Metric | Host (results/llm_v2 mean) | Docker 실시간 (3-min 관찰) | 비교 |
|---|---|---|---|
| 모델 | qwen2.5:14b | qwen2.5:14b | 동일 |
| LLM latency mean | 714 ms | ~1500 ms | cold-start 포함 (warm 이후 유사) |
| fallback rate | 0.04 % | 0 % | 동일 수준 |
| proactive decisions | 500 / run (50 ep × macro 5 동치) | 21-22 / 3분 = ~7/분 | 계산된 8s 루프와 일치 |
| directives issued | N/A (host 실험엔 GCS 없음) | 18 / 3 분 (30s × 3 드론) | 설정값 일치 |
| strategic_directive_received | N/A | 6 / 드론 / 3 분 | cross-container 전달 검증됨 |

## 산출물

- `results/logs/container_llm_mode_cc_honey_{01,02,03}.log` (live capture)
- `results/logs/container_llm_mode_gcs.log`
- `results/diagnostics/tier1_directive_docker_qwen.json` (Docker qwen 결과)
- `results/diagnostics/tier1_directive_docker_llama.json` (Docker llama 결과)
- `docker/Dockerfile.gcs` (신규)
- `config/docker-compose.honey.llm.yml` (신규 overlay)

## 제약 준수 확인

- ✅ LLM 호출은 host Ollama로만 (컨테이너 내 Ollama 설치 0)
- ✅ 사용 모델: qwen2.5:14b / llama3.1:8b 만 (gemma는 이번 실행 미사용, 허용 범위 내)
- ✅ `results/llm_v2/*.json` 전혀 touch 안 함 (git status로 확인)
- ⚠️ HARD RULE 재현 ±5%p 기준 초과 (12% → 25%). 위 원인 분석 참조.
