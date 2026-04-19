# MIRAGE-UAS 시스템 기술 보고서

**문서 버전**: v1.0
**작성일**: 2026-04-20
**문서 상태**: Internal technical report
**범위**: 논리 구조 · 모델 설계 · 상호작용 · 평가 방법론 · 결과 요약

---

## Part I. 시스템 개요

### I.1 문제 정의

**Unmanned Aerial System (UAS)** 도메인에서 공격자는 L0(정찰) → L1(MAVLink
악용) → L2(HTTP credential harvest) → L3(WebSocket CVE) → L4(lateral /
persistence) 다단계 campaign을 수행한다. 기존 honeypot 방어는 정적 decoy
또는 규칙 기반 응답기로 한정되어 **적응형 공격자에 대해 수십 초 내 탐지**된다
(HoneyDrone Daubert NOMS 2018; HoneyGPT Song 2024).

MIRAGE-UAS는 이 한계를 해결하기 위해 **계층적 LLM 기반 능동 기만 엔진**을
제안한다. 세 계층은 각각 독립적 시간 척도와 LLM 정책을 갖는다.

### I.2 3-Tier 아키텍처 (논리 구조)

```
┌────────────────────────────────────────────────────────────────────┐
│ Tier 1 — 전략적 LLM (GCS Strategic Agent)                          │
│   주기 : 30 s       모델 : qwen2.5:14b                             │
│   역할 : 함대 상태 관찰 → 드론별 전략 지령 발행                    │
│   입력 : UDP 19999 state_broadcast (honey drones)                  │
│   출력 : UDP 19995 strategic_directive  (action, urgency, bias)    │
└────────────────────────────────────────────────────────────────────┘
                                  │ directive
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│ Tier 2 — 전술적 LLM (per-drone Tactical Defender)                  │
│   주기 : 8 s        모델 : {llama3.1:8b / qwen2.5:14b / gemma2:9b} │
│   역할 : 공격 단계별 기만 skill 선택                               │
│   입력 : 10-dim context (phase, μ_A, dwell, commands, ...)         │
│   출력 : 5 skill 중 1개 선택                                       │
│          {statustext / flight_sim / ghost_port / reboot_sim /      │
│           credential_leak}                                         │
└────────────────────────────────────────────────────────────────────┘
                                  │ skill action
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│ Tier 3 — 공격자 대면 lure (OpenClaw SDK Emulation)                 │
│   주기 : 패킷 단위   모델 : 없음 (결정론적 emulator)               │
│   역할 : 가짜 SDK 응답 + UUID-태그 honey-token 발급                │
│   입력 : WebSocket messages (auth/skill_invoke/config/terminal)    │
│   출력 : 가짜 JSON responses + 4 종 breadcrumbs                    │
└────────────────────────────────────────────────────────────────────┘
```

### I.3 시간 척도 분리의 정당성

Pawlick & Zhu (2019) 6-종 deception taxonomy 중 "attacker engagement"는
**다중 시간 척도 정책**을 요구한다. 본 설계:

| 척도 | 구성 | 목적 |
|---|---|---|
| Patrol (1 s) | Tier 3 lure 응답 | 즉각적 프로토콜 호환성 |
| Tactical (8 s) | Tier 2 skill 선택 | 공격 단계 변화 추적 |
| Strategic (30 s) | Tier 1 directive | 함대 단위 전략 조정 |

각 척도는 자기보다 느린 척도로부터 지령을 받고, 빠른 척도에 영향을 준다.

---

## Part II. 모델 설계

### II.1 Defender 측 LLM (Tier 2)

Cross-organization 3개 모델을 동일 prompt / 동일 환경에서 비교 평가:

| 모델 | 제공사 | 파라미터 | 양자화 | 평균 추론 지연 |
|---|---|---|---|---|
| `llama3.1:8b` | Meta | 8 B | Q4_K_M (4.9 GB) | 582 ± 16 ms |
| `qwen2.5:14b` | Alibaba | 14 B | Q4_K_M (9.0 GB) | 714 ± 7 ms |
| `gemma2:9b` | Google DeepMind | 9 B | Q4_K_M (5.8 GB) | 1044 ± 10 ms |

**추론 backend**: Ollama 0.21.0 on Windows host (RTX 5090 32 GB VRAM),
WSL 2 컨테이너에서 `http://172.23.240.1:11434` via `honey_net` egress.

### II.2 Defender Tier 2 Prompt v2 (핵심 설계)

```text
[System prompt core elements]
1. Skill labeling: A/0..E/4 이중 인덱싱 (letter AND index)
   → RLHF ordering bias 제거

2. Phase → candidate skills soft hint
   RECON   → A / B / C
   EXPLOIT → C / D / E
   PERSIST → D / A / E
   EXFIL   → E / D

3. HARD RULES (4개):
   - RECON  : NEVER 3 alone
   - EXPLOIT: NOT 1 unless mu_A < 0.4
   - PERSIST: prefer 3 FIRST
   - EXFIL  : NEVER 1 (effect negative)

4. Anti-repetition: "Prefer DIFFERENT skill from last_action"
   + Ollama repeat_penalty=1.2

5. Temperature 0.9, top_p 0.95, top_k 40
   → exploration 확보

6. Response format: strict JSON only
   {"skill_idx": 0-4, "reason": "one sentence"}
```

**V1 대비 V2 성과**:
- Skill entropy: 0.00-0.69 bits → **2.05-2.12 bits** (최대 2.32의 91%)
- Mode collapse: `flight_sim 82-100%` → `flight_sim 3.6-5.4%`
- χ² test phase-skill independence: p = 0.0000 (모든 9 seed/model run)

### II.3 Attacker 측 LLM (Belief Tracker)

**역할**: scripted L0-L4 공격자의 각 request/response를 관찰하고
P(target = real drone) 재추정. 공격자 action 선택은 하지 않음
(scripted 유지).

**Prompt 구조**:

```text
[System]
당신은 UAS 드론을 정찰하는 숙련된 공격자다.
매 패킷 응답 후 P(real drone) 0-1 재추정.

Honey 의심 신호:
- API tokens handed out too easily
- Response timing unnaturally consistent
- MAVLink lacks ArduPilot idiosyncrasies
- Services don't lock out brute force
- Reboot simulations don't disrupt TCP
- Ghost ports open on probe (not before)

Real drone 신호:
- Protocol quirks, jitter
- Lockouts trigger
- Credential changes break sessions
- Firmware strings match ArduPilot

Output: {"belief_real": 0-1, "confidence": 0-1, "signal": "..."}

[Belief update — confidence-weighted linear blend]
mu_real_new = (1-w) * mu_real_old + w * obs_belief
where w = clip(confidence, 0, 1)
```

Temperature 0.5, num_predict 120, repeat_penalty 1.1.

### II.4 Baseline policies (Tier 2 비교군)

| 정책 | 알고리즘 | 학습 | 파라미터 |
|---|---|---|---|
| Random | uniform | — | — |
| Greedy | phase-expert heuristic | — | phase→best-skill lookup |
| **DQN** | Dueling Double DQN | 3000 ep × 3 seed | hidden=128, lr=3e-4 |
| **h-DQN** | hierarchical DQN | 3000 ep × 3 seed | meta(7 strat) + ctrl(45 act) |
| **Game-EQ** | alternating best-response | 1500 ep × 4 round × 3 seed | fictitious play |
| **Signaling-EQ** | QRE closed-form | zero-shot | κ=0.5, τ=0.8 |
| Hybrid | DQN + Signaling alternation | — | 짝/홀 step 교차 |
| **LLM-{model}** | local LLM 추론 | zero-shot | Ollama HTTP async |

---

## Part III. 상호작용 흐름

### III.1 정상 운영 (no attacker)

```
T=0 s     Tier 2 loop: context idle (μ_A=1.0, phase=0, dwell=0)
T=1 s     Tier 3 emulator: passive listener only
T=8 s     Tier 2 LLM call → "observe" (low urgency)
T=30 s    Tier 1 LLM call → reviews state_broadcasts (60 directives/10min)
          → emits "observe" directives (urgency=0.0)
```

검증 (live run, 10 분): 74-75 decisions/drone, 0 fallback,
60 GCS directives, 100% cross-container 전달.

### III.2 공격자 접촉 시

```
T+0 s     Attacker sends MAVLink probe (L0)
          → Tier 2 OpenClawAgent.observe() 호출
          → _update_fingerprint() : attacker_ip, tool level 기록
          → μ_A Bayesian update (prior 0.7)
T+1 s     state_broadcast UDP 19999 → GCS 수신
T+8 s     Tier 2 proactive loop → LLMTacticalAgent.select_action(context)
          → context = {phase=0 RECON, μ_A=0.7, ...}
          → Ollama 호출 (0.6-1.5 s)
          → skill="ghost_port", reason="provide realistic decoy"
          → action_funcs[2]() 비동기 실행 → TCP 19003 open
T+8.5 s   Ghost TCP 19003 listener active
          → attacker L0 scan 발견 → 관심 유도
T+30 s    Tier 1 GCS LLM → 변화된 state 감지
          → "deploy_decoy" directive (urgency 0.3)
          → UDP 19995 broadcast → cc_honey_0N 수신
T+31 s    Tier 2 next proactive loop
          → context에 strategic_directive 주입
          → LLM prompt에 "GCS directive: action=deploy_decoy bias=0.75 ghost_port"
          → LLM ~75% 확률로 ghost_port 재선택 (strong bias)
```

### III.3 공격자 credential harvest 시나리오 (L3)

```
T+120 s   Attacker WS CVE auth bypass
          → honey container OpenClawAgent.generate_ws_response(msg)
          → msg_type="auth" → service에 위임 (return None)
          → OpenClawService._handle_auth()
          → _check_reuse(token="") → 매치 없음
          → _issue_token(api_token=sk-drone-...) → UUID 기록
          → {"type":"auth_result", "token":"sk-drone-..."}
          → attacker_sim intel.api_tokens[ip] = "sk-drone-..."
T+121 s   Attacker credential replay (T1550.001)
          → WS 재연결 + {"type":"auth","token":"sk-drone-..."}
          → _check_reuse() → 매치! reuse 이벤트 기록
          → HTUR tracker: issued_unique=1, reuse_events++

T+122 s   LLM attacker belief tracker observe() 호출
          → prompt includes "intel: tokens=1, creds=0..."
          → LLM: "belief_real=0.4, signal='API token handed out too easily'"
          → mu_real: 0.88 → 0.6 (confidence-weighted)
```

---

## Part IV. 평가 방법론

### IV.1 실험 환경

**하드웨어**: RTX 5090 32 GB VRAM (Windows host)
**OS**: WSL 2 Ubuntu 24.04
**Docker**: 29.4.0 with Compose v5.1.3
**LLM**: Ollama 0.21.0 (호스트), 3 models Q4_K_M 양자화
**Docker 컨테이너**:
- 3× cc_honey_0N (mirage-honeydrone:latest, non-root UID 1000)
- 3× fcu_honey_0N (mirage-fcu-stub:latest, ArduPilot SITL-compatible)
- 1× config-gcs-1 (mirage-gcs:latest)
- CTI 파이프라인 부가 컨테이너

### IV.2 평가 지표 계층 (L1-L5)

| 계층 | 지표 | 계산 | 방향 |
|---|---|---|---|
| **L1 Uptake** | HTUR = unique_reused / unique_issued | per-attacker | ↑ good |
| | CPR = auth_endpoint_reuses | count | ↑ good |
| | FSR = reuse_events / total_commands | ratio | ↑ good |
| **L2 Belief** | avg_p_real (μ_A 평균) | mean over episode | ↑ good |
| | misbelief_duration_ratio | frac steps μ_A > 0.7 | ↑ good |
| | p_real_std | trajectory volatility | context |
| **L3 Engagement** | dwell_sec | session duration | ↑ good |
| | survival_rate | eps with no detection | ↑ good |
| **L4 Coverage** | phase_advance_rate | advances / total steps | ↓ good |
| | max_phase_reached | ∈ {0,1,2,3} | ↓ good |
| | time_share_per_phase | 4-dim vector | context |
| **L5 Policy** | skill_entropy (bits) | Shannon H | ↑ good |
| | χ² p-value | phase-skill indep. test | ↓ good |
| | Cramér's V | effect size | ↑ good (large > 0.29) |
| | JS divergence phase-pairs | 4×4 matrix | ↑ good |

### IV.3 Game-Theoretic 지표 (Table VIII)

POSG formalisation (Horák 2019) 아래:

$$\mathcal{G} = (N, S, A_D, A_A, O_D, O_A, T, R_D, R_A, \beta, \gamma)$$

7-metric battery:

| 지표 | 정의 | 출처 |
|---|---|---|
| Exploitability | $\max_{\sigma'_A} U_A(\sigma_D, \sigma'_A) - U_A(\sigma_D, \sigma^*_A)$ | Lanctot 2017 |
| Belief manipulation | $\mathbb{E}_t[(\mu_A(t)-0.5)^+] \cdot w$ | Pawlick 2019 |
| Information leakage | $H(\text{skill})$ bits | Horák 2019 |
| Deception ratio | $1 - \overline{\text{JS}(\text{phase pairs})}/\log_2 5$ | 본 연구 |
| Stackelberg value | $U_D(\sigma_D^*, BR_A(\sigma_D^*))$ | Carnevale 2025 |
| Nash regret | $\sigma^{\text{trajectory}}$ std | EGTA (Prakash 2015) |
| Hypergame stability | $\text{misbelief\_ratio} \times \text{survival}$ | Gutierrez 2022 |

### IV.4 LLM-vs-LLM Packet-Level Deception (Table IX)

**설계**: scripted attacker에 LLM belief tracker를 부착하여 **패킷 단위
deception lifetime** 측정. 3 defender × 3 attacker = 9 pairings, each
5-level L0-L4 campaign, 15 s per level.

측정 지표:
- **packets_to_disbelief**: 첫 μ_real < 0.5 transition 시점
- **final μ_real**: run 종료 시 공격자 신뢰도
- **belief AUC**: ∫ μ_real dt / total_time (0=즉시 탐지, 1=완전 기만)
- **top suspicion signals**: LLM attacker의 자연어 rationale 빈도

---

## Part V. 핵심 결과 요약

### V.1 V1 → V2 Prompt ablation (Table VII 근거)

| Model | V1 Entropy | V2 Entropy | V1 flight_sim | V2 flight_sim | χ² p |
|---|---|---|---|---|---|
| llama3.1:8b | 0.69 | **2.10** | 82.5% | 3.6% | 0.0000 |
| qwen2.5:14b | 0.00 | **2.07** | 100% | 4.2% | 0.0000 |
| gemma2:9b | 0.00 | **2.07** | 100% | 5.4% | 0.0000 |

**해석**: V1 prompt는 모든 모델에서 mode collapse (HoneyGPT Song 2024 /
Sladić 2024 선행 관찰). V2의 A-E keying + HARD RULES + repeat_penalty
조합이 **3 cross-org 모델 전부**에서 entropy를 3배 이상 복구.

### V.2 Cross-model Variance (Table VII §2)

| Model | avg_p_real | Cramér V | Effect size |
|---|---|---|---|
| qwen2.5:14b | **0.975 ± 0.011** | **0.449 ± 0.012** | large |
| gemma2:9b | 0.960 ± 0.015 | 0.419 ± 0.004 | large |
| llama3.1:8b | 0.964 ± 0.024 | 0.322 ± 0.007 | large |

세 모델 모두 χ² p=0, Cramér V > 0.29 (Cohen "large"). **모델 간
variance는 seed variance보다 큼** → cross-org comparison이 방법론적으로
유의미.

### V.3 Docker Live Deployment 실측

- 10 분 live run: 74-75 decisions/drone × 3 drones = **224 LLM calls**,
  **0 fallback**
- GCS → Tier 2 cross-container directive: **60 issued / 60 received = 100%**
- Tier 1 strategic reasoning 다양성: **state feed 연결 후**
  `{deploy_decoy: 9, observe: 48}` (이전: `{deploy_decoy: 60}` degenerate)
- Live HTUR: 3 drones × 1 token issued × 2 reuse events =
  **HTUR 1.00, intensity 2.0**

### V.4 LLM-vs-LLM 기만 수명 (Table IX)

```
           atk=llama  atk=qwen   atk=gemma   행 평균 (robustness)
def=llama   0.664      0.661      0.689       0.671  (σ=0.014)  ← 안정
def=qwen    0.280      0.824      0.676       0.593  (σ=0.273)  ← brittle
def=gemma   0.484      0.669      0.720       0.624  (σ=0.124)
         (열 평균)
         공격력:       0.476      0.718      0.695
         disbelief:    24 pkt     46 pkt     87 pkt
```

**세 가지 주요 발견**:

1. **Same-family mirror blind spot**: 대각선 평균 AUC 0.733 vs
   off-diagonal 0.577 (+27% 기만 지속)
2. **Defender robustness 역설**: 가장 작은 모델(llama 8B) σ=0.014 <
   가장 큰 모델(qwen 14B) σ=0.273
3. **Attacker aggressiveness**: llama > qwen > gemma
   (24 < 46 < 87 평균 packets to disbelief)

### V.5 Systemic lure weakness (qualitative)

9-cell matrix 전체에서 LLM attacker가 언급한 top suspicion signals:

```
51× "API token handed out too easily"       ← Tier 3 CVE bypass 과잉
 6× "Formulaic service banner"
 5× "No API tokens handed out yet"
 3× "Firmware string matches ArduPilot"     (positive signal)
```

**함의**: CVE-2026-25253 auth bypass가 매 연결마다 토큰을 돌려주는 게
가장 노출된 honey 표식. §6 Future Work에서 **rate-limit /
confidence-gated token issuance** 제안 근거.

---

## Part VI. 방법론적 엄밀성 검증

### VI.1 재현성

- `PYTHONHASHSEED=0` 두 Dockerfile에 하드코딩 → host vs Docker
  flight_sim Δ 13 pp → **0.8 pp** 축소
- 3-seed bootstrap CI (1000 resamples) 모든 headline 지표
- Paired Wilcoxon signed-rank (M5 fix) 정책 비교 통계 검정

### VI.2 Integrity checklist (10/10 PASS)

| 검사 | 결과 |
|---|---|
| Tier 2 phase-discrimination (mean ph-uniq ≥ 2.5) | ✅ 2.56/4 |
| Tier 2 skill-diversity (H ≥ 1.5 bits) | ✅ 2.08 avg |
| Tier 2 χ² phase-skill independence REJECTED | ✅ all p = 0.0000 |
| Tier 2 latency p95 < 5000 ms | ✅ 1265 ms worst |
| Tier 2 fallback rate < 1% | ✅ 0.40% worst |
| Tier 1 directive hit-rate ≥ 40% | ✅ 100% |
| Tier 1 directive KL divergence ≥ 0.5 | ✅ 13.81 max |
| Tier 3 breadcrumbs ≥ 3 types | ✅ 4 types |
| HTUR aggregate ≥ 0.50 | ✅ 1.00 |
| GCS strategic-reasoning diversity (post-fix) | ✅ 2 actions + 3 urgencies |

### VI.3 한계 및 §5 Limitations로 명시한 사항

1. **DeceptionEnv heuristic matrix** — `_action_effect`는
   hand-calibrated, 실제 attacker response 분포와 차이 가능
2. **Stackelberg value per-LLM** — BR-attacker DQN 학습 필요,
   Game-EQ 값으로 proxy (§6 future)
3. **Symmetric LLM-vs-LLM action selection** — 현재 belief tracker만
   구현, scripted action 유지 (§6)
4. **ArduPilot 실 SITL 미사용** — DVD Docker 이미지 부재로 stub으로
   대체 (`fcu_stub.py`)
5. **HTUR live measurement** — attacker_sim port bug 수정 후 복구
   (3-drone fleet)

---

## Part VII. 최종 산출물 맵

### VII.1 코드 (src/)

```
src/honey_drone/
├── llm_agent.py                  Tier 2 async Ollama client
├── attacker_belief_tracker.py    Attacker-side LLM belief estimator
├── attacker_policy.py            ABC + Scripted/Random/LLM policies
├── openclaw_agent.py             Rule-based hot path + v2 delegation
├── openclaw_service.py           Tier 3 SDK emulator + HTUR tracker
├── agentic_decoy_engine.py       Async orchestrator
├── deception_env.py              RL simulation env
├── markov_game_env.py            Two-player game env
├── behavior_learner.py           DQN / h-DQN policy loader
├── signaling_game_solver.py      QRE closed-form
└── engagement_tracker.py         Session metrics

src/gcs/
├── strategic_agent.py            Tier 1 LLM strategic commander
├── strategic_directive.py        UDP 19995 wire schema
└── __init__.py

src/metrics/
└── deception_score_v2.py         Composite metric + 5/5 unit tests
```

### VII.2 Docker

```
docker/
├── Dockerfile.honeydrone         Tier 2+3 이미지
├── Dockerfile.gcs                Tier 1 이미지 (PYTHONHASHSEED=0)
├── Dockerfile.fcu-stub           ArduPilot SITL 호환
├── Dockerfile.attacker           L0-L4 attacker image
├── honeydrone_entry.py
├── fcu_stub.py
└── cc_stub.py

config/
├── docker-compose.honey.yml     Base stack
├── docker-compose.honey.llm.yml LLM-mode overlay + gcs service
└── docker-compose.test-harness.yml
```

### VII.3 Paper-ready 산출물

```
paper/
├── INDEX.md                                    Navigation
├── GLOSSARY.md                                 용어 표준 (ko/en)
├── ARCHITECTURE.md                             3-tier Docker layout
├── DOCKER_INTEGRATION_REPORT.md                Docker 검증 리포트
├── DATA_INTEGRITY.md                           10/10 checklist
├── CCS2026_abstract.md                         초록 v2
├── related_work.md                             §2.1-§2.6
├── SYSTEM_REPORT.md                            THIS FILE
├── sections/game_theory.md                    §2.5 + §5 game-theoretic
├── tables/
│   ├── table_llm_v2_bootstrap_ci.md           Table VII (L2-L5 CI)
│   ├── table_cross_model_variance.md          Cross-org comparison
│   ├── table_v1_v2_ablation.md                Mode collapse fix
│   ├── table_deception_score_v2.md            Composite + sensitivity
│   ├── table_game_theoretic_evaluation.md     Table VIII (game theory)
│   └── table_ix_attacker_belief.md            Table IX (LLM-vs-LLM)
├── figures/
│   ├── fig_architecture.png                   3-tier Docker diagram
│   ├── fig_a_skill_dist_v1_vs_v2.png          Mode-collapse mitigation
│   ├── fig_b_phase_skill_heatmap_v2.png       Confusion per model
│   ├── fig_c_reward_vs_belief_scatter.png     V1 vs V2 scatter
│   ├── phase_skill_cm_grid.png                9 per-run heatmaps
│   ├── fig_belief_heatmap.png                 Packets→disbelief 3×3
│   ├── fig_belief_auc_heatmap.png             AUC 3×3
│   ├── fig_belief_trajectories_grid.png       9-panel trajectories
│   └── fig_attacker_belief_trajectory.png     Single-run PoC
└── data/
    ├── tier1_directive{,_docker_qwen,_docker_llama}.json
    ├── tier3_lure.json
    ├── htur.json
    └── (link to results/)
```

### VII.4 Results 트리

```
results/
├── llm_v2/              9 runs per-seed (3 model × 3 seed)
│   ├── *_seed*.json     raw metrics
│   ├── *.npz            confusion matrix NumPy
│   ├── summary.md       bootstrap CI
│   ├── summary_ci.json
│   └── figures/         PNG artifacts
├── llm_multi_seed_v1/   V1 mode-collapse baseline snapshot
├── baseline_matched/    Random/Greedy/DQN at matched setup
├── diagnostics/
│   ├── llm_coherence_v2.md
│   ├── game_metrics_v2.json
│   ├── tier1_directive_docker_*.json
│   ├── tier3_lure.json
│   ├── htur.json
│   ├── attacker_belief_*.json        개별 run trajectory
│   ├── deception_lifetime_summary.json
│   ├── llm_vs_llm/                    3×3 matrix (9 cells)
│   │   └── *.json + *.cell.log
│   └── llm_vs_llm_summary.json        aggregate
└── logs/
    ├── container_llm_mode_*.log       10-min live run
    ├── container_llm_mode_*_fixed.log
    └── multi_seed_*/                  training logs
```

---

## Part VIII. 논문 narrative 통합

### 주장 순위 (논문 §1 Contributions에 들어갈 3+1 bullets)

1. **C1 — Three-tier hierarchical LLM deception** (Tier 1 strategic,
   Tier 2 tactical, Tier 3 lure)로 UAS honeypot에 **최초** 도입.
   Docker 배포로 10 분 live run에서 0 fallback / 100%
   cross-container delivery 입증.

2. **C2 — Cross-organization LLM defender evaluation** — 3 모델 × 3
   seed × 50 ep bootstrap CI 평가. V2 prompt (A-E keying + HARD RULES
   + repeat_penalty)가 HoneyGPT/Sladić가 보고한 mode collapse를 해결
   (entropy 3× 복구, χ² p=0 across all 9 runs, Cramér V ≥ 0.32 large
   effect).

3. **C3 — LLM-vs-LLM packet-level deception lifetime** — 3×3 matrix
   평가에서 **same-family mirror blind spot** 정량화 (diagonal AUC
   0.733 vs off-diag 0.577, +27%), **defender robustness 역설**
   (llama 8B σ=0.014 << qwen 14B σ=0.273), systemic lure weakness
   자동 식별 (LLM attacker의 51× "API token handed out too easily"
   자연어 피드백).

4. **C4 — 오픈 artifact 릴리스** — Docker stack, 3 LLM models,
   DeceptionScore v2 복합 지표, HTUR 추적 인프라, 9-cell belief
   trajectory 데이터 전체.

---

## 문서 버전 이력

| 버전 | 날짜 | 변경 |
|---|---|---|
| v1.0 | 2026-04-20 | 최초 작성: Part I-VIII, 3×3 LLM-vs-LLM 매트릭스 결과 포함 |
