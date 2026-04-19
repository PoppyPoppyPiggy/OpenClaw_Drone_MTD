# MIRAGE-UAS — Paper Terminology Standard

논문 집필 시 한국어/영어 용어 일치를 강제하기 위한 표준. 모든 본문·표·그림·
부록은 이 표의 표기를 준수한다. 한국어 발표본과 영어 submission 버전이
같은 개념을 가리키는지 확인하는 단일 source of truth.

## §1 시스템 아키텍처

| 한국어 (발표·논문 한글판) | 영어 (CCS submission) | 약어 / 표기 규칙 |
|---|---|---|
| MIRAGE-UAS | MIRAGE-UAS | 시스템명, 이탤릭 금지 |
| 허니드론 | honeydrone | 소문자, 복합어 |
| 허니드론 플릿 | honeydrone fleet | — |
| 3-계층 아키텍처 | three-tier architecture | T1/T2/T3 약어 사용 가능 |
| Tier 1 — GCS 전략 에이전트 | Tier 1 — GCS Strategic Agent | 대문자 고정 |
| Tier 2 — 전술 LLM 방어자 | Tier 2 — Tactical LLM Defender | — |
| Tier 3 — 공격자 미끼 (OpenClaw SDK 에뮬레이션) | Tier 3 — Attacker Lure (OpenClaw SDK Emulation) | — |
| 지상통제국 (GCS) | Ground Control Station (GCS) | 약어 사용 |
| 전략 지령 | strategic directive | — |
| 전략 지령 채널 (UDP 19995) | strategic directive channel (UDP 19995) | 포트 번호 고정 |

## §2 공격 모델

| 한국어 | 영어 | 비고 |
|---|---|---|
| 공격자 | adversary | ≠ attacker 로 섞지 말 것 — adversary 통일 |
| 공격자 시뮬레이터 | attacker simulator | 파일명에서만 attacker_sim |
| 공격 단계 | attack phase | — |
| 정찰 단계 (phase 0) | RECON phase (phase 0) | 영문은 전 대문자 |
| 악용 단계 (phase 1) | EXPLOIT phase (phase 1) | — |
| 지속화 단계 (phase 2) | PERSIST phase (phase 2) | — |
| 유출 단계 (phase 3) | EXFIL phase (phase 3) | — |
| 공격자 지문 | attacker fingerprint | — |
| 공격자 도구 수준 (L0-L4) | attacker tool level (L0-L4) | L-레벨로 씀 |
| 캠페인 | campaign | L0→L4 전체 sequence |

## §3 기만 행동 (Deception Skills)

| 한국어 | 영어 | 내부 코드 키 | 논문 인용 표기 |
|---|---|---|---|
| 상태문자열 삽입 | STATUSTEXT injection | `statustext` | Skill A (id=0) |
| 비행 시뮬레이션 | flight simulation | `flight_sim` | Skill B (id=1) |
| 유령 포트 | ghost-port decoy | `ghost_port` | Skill C (id=2) |
| 재부팅 시뮬레이션 | reboot simulation | `reboot_sim` | Skill D (id=3) |
| 자격증명 유출 (허니토큰) | credential leak (honey-token) | `credential_leak` / `fake_key` | Skill E (id=4) |

**표기 규칙**: 본문에서 처음 언급 시 "credential leak (honey-token)"처럼
괄호 병기. 이후에는 "credential leak" 단독 허용. 내부 코드에서는
`credential_leak` 과 `fake_key` (legacy) 둘 다 나오지만 **논문에서는
`credential leak` 으로 통일**.

## §4 신념·믿음 (Belief) 용어

| 한국어 | 영어 | 수식 기호 |
|---|---|---|
| 공격자 신념 | attacker belief | μ_A |
| P(real | 관측) | P(real | obs) | p_real |
| 신념 변동성 | belief volatility | σ(p_real) |
| 오신념 지속 비율 | misbelief duration ratio | — (step-fraction) |
| 베이지안 갱신 | Bayesian update | — |
| 우도비 | likelihood ratio | LR |

## §5 평가 지표 (L1-L5 계층)

| 한국어 | 영어 | 수식 / 정의 |
|---|---|---|
| L1 — 미끼 취득 지표 | L1 — Uptake metrics | — |
| 허니토큰 취득 비율 | Honey-Token Uptake Ratio (HTUR) | `reused_unique / issued_unique` |
| 자격증명 탐색 비율 | Credential Probe Ratio (CPR) | auth-endpoint reuse events |
| 가짜 서비스 회귀율 | Fake Service Return Rate (FSR) | `reuse_events / total_commands` |
| L2 — 신념 지표 | L2 — Belief metrics | — |
| 평균 신념 μ_A | mean belief μ_A | — |
| 신념 표준편차 | belief std | σ(p_real) |
| 오신념 지속 | misbelief duration | step frac where μ_A > 0.7 |
| L3 — 관여 지표 | L3 — Engagement metrics | — |
| 체류시간 | dwell time | dwell_sec |
| 생존율 | survival rate | eps with attacker retained |
| 평균 이탈 step | mean step at exit | — |
| L4 — 커버리지 지표 | L4 — Coverage metrics | — |
| 단계 진전 비율 | phase advance rate | phase-advance events / steps |
| 단계 회귀 횟수 | phase reversal count | — |
| 도달 최대 단계 | max phase reached | ∈ {0,1,2,3} |
| 단계별 시간 점유율 | per-phase time share | 4-dim vector |
| L5 — 정책 지표 | L5 — Policy metrics | — |
| 스킬 엔트로피 (비트) | skill entropy (bits) | H(p) |
| 단계-스킬 혼동 행렬 | phase-skill confusion matrix | 4 × 5 matrix |
| 카이제곱 독립성 검정 | chi-square independence test | χ² statistic, p-value |
| 크래머 V | Cramér's V | effect size |
| 크래머 V (보정) | Cramér's V (bias-corrected) | Bergsma-Cressie |
| 단계 쌍 JS 발산 | phase-pair JS divergence | 4×4 pairwise matrix |

## §6 복합 점수 (DeceptionScore v2)

| 한국어 | 영어 | 기호 |
|---|---|---|
| 기만 점수 v2 | DeceptionScore v2 | DS v2 |
| 가중치 | weight | w_htur, w_belief, w_eng, w_cov, w_pol |
| 민감도 분석 | sensitivity analysis | — |
| 균일 가중치 | uniform weights | each = 0.20 |

## §7 방어자 정책 비교

| 한국어 | 영어 | 약어 / 파일 |
|---|---|---|
| 무작위 정책 | Random policy | Random |
| 탐욕 정책 | Greedy policy | Greedy (phase-expert heuristic) |
| 듀얼링 더블 DQN | Dueling Double DQN | DQN |
| 계층적 DQN | hierarchical DQN (h-DQN) | h-DQN |
| 게임 균형 정책 (가상 플레이) | Game-EQ policy (fictitious play) | Game-EQ |
| 신호 균형 정책 | Signaling-EQ policy (QRE) | Signaling-EQ |
| 혼합 정책 | Hybrid policy (DQN + Signaling) | Hybrid |
| LLM 전술 정책 | LLM Tactical policy | LLM-{model} |

## §8 게임이론 용어

| 한국어 | 영어 | 기호 |
|---|---|---|
| 신호 게임 | signalling game | — |
| 양자응답균형 | Quantal Response Equilibrium | QRE |
| 완전 베이지안 균형 | Perfect Bayesian Equilibrium | PBE |
| 가상 플레이 | fictitious play | — |
| 교대 최적대응 | alternating best-response | — |
| 착취가능성 | exploitability | — |
| 제로섬 / 일반합 | zero-sum / general-sum | — |
| 로짓-응답 근사 | logit-response approximation | — |

## §9 LLM / Prompt 용어

| 한국어 | 영어 | 비고 |
|---|---|---|
| 프롬프트 v1 / v2 | prompt v1 / v2 | v2 = 수정본 |
| 시스템 프롬프트 | system prompt | — |
| 모드 붕괴 | mode collapse | HoneyGPT 2024 선행 |
| 모드 붕괴 완화 | mode-collapse mitigation | — |
| 재시도 페널티 | repeat penalty | Ollama option |
| 샘플링 온도 | sampling temperature | T |
| 매크로-행동 | macro-action | N-step 재사용 |
| 추론 지연 | inference latency | ms |
| 폴백율 | fallback rate | JSON parse 실패 비율 |
| 지시문 편향 | directive bias | Tier 1 → Tier 2 |
| 자기 평가 편향 | self-assessment bias | 피해야 할 것 |
| 외부 피드백 주입 | external feedback injection | last_action_effect 삽입 |

## §10 통계 용어

| 한국어 | 영어 | 비고 |
|---|---|---|
| 95% 신뢰구간 | 95 % confidence interval | — |
| 부트스트랩 CI | bootstrap CI | percentile method |
| 대응 Wilcoxon 부호순위검정 | paired Wilcoxon signed-rank test | — |
| Cohen의 d_z | Cohen's d_z | paired effect size |
| 시드 변량 | seed variance | — |
| 모델 간 변량 | between-model variance | — |
| 교차 검증 | cross-validation | — |
| 편향 보정 | bias correction | — |

## §11 환경·시뮬레이션

| 한국어 | 영어 | 비고 |
|---|---|---|
| 기만 환경 | DeceptionEnv | — |
| 몬테카를로 시뮬레이션 | Monte Carlo simulation | — |
| 행동 효과 매트릭스 | action-effect matrix | `_action_effect` |
| 휴리스틱 보정 | heuristic calibration | 한계로 명시 |
| Docker 스택 | Docker stack | — |
| ArduPilot SITL 호환 스텁 | ArduPilot SITL-compatible stub | 실제 SITL 아님 |
| 단일 프로세스 테스트 | single-process test | — |

## §12 약어 일람 (자주 쓰는 것)

| 약어 | 원어 | 한국어 |
|---|---|---|
| UAS | Unmanned Aerial System | 무인항공시스템 |
| MAVLink | Micro Air Vehicle Link | MAVLink (고유명) |
| GCS | Ground Control Station | 지상통제국 |
| CVE | Common Vulnerabilities and Exposures | CVE |
| MTD | Moving Target Defense | 이동표적방어 |
| DQN | Deep Q-Network | 심층 Q-네트워크 |
| h-DQN | hierarchical Deep Q-Network | 계층적 심층 Q-네트워크 |
| QRE | Quantal Response Equilibrium | 양자응답균형 |
| PBE | Perfect Bayesian Equilibrium | 완전 베이지안 균형 |
| LLM | Large Language Model | 대규모 언어모델 |
| RL | Reinforcement Learning | 강화학습 |
| SITL | Software In The Loop | SITL |
| OODA | Observe-Orient-Decide-Act | OODA |
| HTUR | Honey-Token Uptake Ratio | 허니토큰 취득 비율 |
| CPR | Credential Probe Ratio | 자격증명 탐색 비율 |
| FSR | Fake Service Return Rate | 허위 서비스 회귀율 |
| DS | DeceptionScore | 기만 점수 |
| JS | Jensen-Shannon | JS (발산) |
| KL | Kullback-Leibler | KL (발산) |

## §13 사용 금지 / 지양 용어

| 금지 | 이유 | 대체 |
|---|---|---|
| "attacker" 와 "adversary" 혼용 | 의미 혼동 | **adversary 고정** |
| "실제 OpenClaw 통합" 주장 | 사실과 다름 | "OpenClaw-inspired"; Tier 3만 "SDK emulation" |
| "real ArduPilot SITL" | stub 사용중 | "ArduPilot SITL-compatible stub" |
| "실측 HTUR" (Docker 미통합 시) | offline ceiling 불과 | "offline synthetic HTUR (ceiling)" |
| "자명하게 phase-aware" | 통계 없음 | "χ² / Cramér V 로 통계적 유의" |
| "LLM이 스스로 평가" | self-assessment bias | "env가 계산한 belief_delta 주입" |
| "완벽한 재현성" | seed 변량 존재 | "3-seed bootstrap CI 보고" |
| "Docker E2E with LLM defender" | 미구현 | "future work" 명시 |

## §14 §-참조 표기

한글판 발표본:
- "논문 §2 Related Work" → 영어 논문 §2 Related Work 와 동일 내용
- 표 번호는 영어본 기준 (Table VI, VII…)
- 그림 번호도 영어본 기준 (Figure 1, 2…)

---

**운영 규칙**

1. 새 용어 도입 시 이 GLOSSARY에 먼저 추가하고 리뷰.
2. 한/영 둘 다 없는 용어는 사용하지 말 것.
3. 영어본 초고에서 `grep` 해서 금지 단어 검출 가능하도록 유지.
4. 모든 수식 기호는 이 파일의 "기호" 열에 등록.
