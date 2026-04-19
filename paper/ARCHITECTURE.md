# MIRAGE-UAS — 3-Tier Architecture

Paper figure: `figures/fig_architecture.png`

## ASCII block (high-fidelity)

```
╔════════════════════════════════════════════════════════════════════════════════╗
║  TIER 1 — STRATEGIC GCS                                        (host Python)   ║
║                                                                                ║
║  ┌─────────────────┐    ┌──────────────────────────┐   ┌────────────────────┐  ║
║  │  Human operator │    │  OpenClaw-inspired       │   │  Ollama (Windows)  │  ║
║  │  PowerShell /   │◀──▶│  Strategic LLM Agent     │◀─▶│  qwen2.5:14b       │  ║
║  │  Web UI         │    │  strategic_agent.py      │   │  (14B strategic)   │  ║
║  └─────────────────┘    └────────────┬─────────────┘   └────────────────────┘  ║
║                                      │ issues JSON directive                   ║
║                            UDP 19995 │ {target, action, skill_bias,            ║
║                                      │  urgency, reason, ttl_sec}              ║
╚══════════════════════════════════════│═════════════════════════════════════════╝
                                       ▼
╔════════════════════════════════════════════════════════════════════════════════╗
║  TIER 2 — TACTICAL LLM DEFENDER      (3 × Docker container  honey_ctnr_1..3)   ║
║                                                                                ║
║  ┌──────────────────────────────────────────────────────────────────────────┐  ║
║  │ Container  honey_ctnr_N   (UID 1000, ArduPilot-alike host)               │  ║
║  │ ┌──────────────────┐    ┌────────────────────┐    ┌───────────────────┐  │  ║
║  │ │ OpenClawAgent    │───▶│ _proactive_loop    │───▶│ LLMTacticalAgent  │  │  ║
║  │ │ (hot path rules) │    │ every 8s           │    │ (async Ollama HTTP)│ │  ║
║  │ │ MAVLink replies  │    │ _build_mab_context │    │                    │ │  ║
║  │ │ per packet       │    │                    │    │ v2 prompt, A-E     │ │  ║
║  │ │                  │    │ consumes UDP 19995 │    │ hard rules,        │ │  ║
║  │ │                  │    │ directive          │    │ repeat_penalty 1.2 │ │  ║
║  │ └──────────────────┘    └────────────────────┘    └──────────┬────────┘  │  ║
║  │                                                              │            │  ║
║  │ ┌──────────────────────────────────────────────────────────┐ │            │  ║
║  │ │ OpenClawService (Tier 3 attacker-facing SDK emulation)   │◀┘            │  ║
║  │ │ skill_invoke / agent.run / terminal / auth / config      │              │  ║
║  │ │ issues UUID-tagged honey-tokens → HTUR tracker           │              │  ║
║  │ └──────────────────────────────────────────────────────────┘              │  ║
║  └──────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                ║
║           │ Ollama calls (HTTP)              ▲  proactive skills (MAVLink)     ║
║           ▼                                  │                                  ║
║      ┌─────────────────────────────┐         │                                  ║
║      │  Ollama @ Windows host      │         │                                  ║
║      │  llama3.1:8b / qwen2.5:14b  │         │                                  ║
║      │  gemma2:9b                  │         │                                  ║
║      └─────────────────────────────┘         │                                  ║
╚══════════════════════════════════════════════│═════════════════════════════════╝
                                               ▼
╔════════════════════════════════════════════════════════════════════════════════╗
║  TIER 3 — Attacker Lure (inside Tier-2 container)                              ║
║                                                                                ║
║   - 4 honey-token types planted: ssh_pass, signing_key, api_token, cred         ║
║   - HTUR / CPR / FSR tracked per-attacker                                       ║
║   - Attacker: scripted attacker_sim.py  L0 (nmap) → L4 (lateral, credential)    ║
║   - Attacker and Tier 2 exchange over: MAVLink UDP 14551-53, HTTP 8081-83,      ║
║                                         WebSocket 18789-91, RTSP 8554-56        ║
╚════════════════════════════════════════════════════════════════════════════════╝

Internal event bus (fire-and-forget, JSON over UDP 127.0.0.1):
  19999  state_broadcast (1 s interval)
  19998  agent_decisions (every skill selection)
  19997  state_diff      (sysid/port/param changes)
  19996  packet_events   (every MAVLink response)
  19995  strategic_directive   (Tier 1 → Tier 2)   ← NEW in v2
```

## 구현 현황 (2026-04-19 업데이트)

| 상태 | 컴포넌트 | 비고 |
|---|---|---|
| 🟢 Complete (Docker) | Tier 1 GCS Strategic Agent (`mirage-gcs:latest`) | `docker/Dockerfile.gcs`, 30s 주기 directive 발행 검증 |
| 🟢 Complete (Docker, LLM-mode) | Tier 2 Tactical LLM Defender (cc_honey_01/02/03) | `DEFENDER_POLICY=llm_agent` override 가능 |
| 🟢 Complete (Docker) | Tier 3 OpenClaw SDK Lure (honey 컨테이너 내부) | 4 종 honey-token + HTUR tracker |
| 🟢 Complete | Cross-container directive 전달 | GCS → Docker DNS (`cc_honey_0N:19995`), 3ms 이내 수신 |
| 🟠 Offline ceiling only | HTUR 실측값 | Docker E2E live attacker run은 후속 작업 |
| 🚫 Out of scope | OpenClaw Node.js upstream 통합 | Anthropic OAuth 자동화 포함, 별도 사이클 |

## Docker 배포 요약 (새 컨테이너)

| 컨테이너 | 이미지 | 역할 |
|---|---|---|
| `config-gcs-1` | `mirage-gcs:latest` | Tier 1 GCS — `python -m gcs.strategic_agent` |
| `cc_honey_01 / 02 / 03` | `mirage-honeydrone:latest` | Tier 2 + Tier 3 (LLM mode when override applied) |
| `fcu_honey_01 / 02 / 03` | `mirage-fcu-stub:latest` | ArduPilot SITL-compatible stub |

실행:
```bash
docker compose \
  -f config/docker-compose.honey.yml \
  -f config/docker-compose.honey.llm.yml \
  --env-file config/.env up -d
```

Docker 통합 이력은 `docs/docker_integration_changelog.md` 참조.
