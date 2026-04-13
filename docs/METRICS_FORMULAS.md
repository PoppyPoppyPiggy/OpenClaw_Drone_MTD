# MIRAGE-UAS 평가 수식 및 지표 정의서

> 다른 논문에서 검증된 수식을 수집하여 MIRAGE-UAS 평가 프레임워크에 적용.
> 각 수식에 출처 논문, 변수 정의, MIRAGE-UAS 매핑을 명시.

---

## 목차

1. [허니팟 기만 효과 지표 (Deception Effectiveness)](#1-허니팟-기만-효과-지표)
2. [MTD 효과 지표 (Moving Target Defense)](#2-mtd-효과-지표)
3. [RL 기반 기만 보상 함수 (Reward Functions)](#3-rl-기반-기만-보상-함수)
4. [데이터셋 / CTI 품질 지표](#4-데이터셋--cti-품질-지표)
5. [통계 검정 프레임워크](#5-통계-검정-프레임워크)
6. [MIRAGE-UAS 통합 DeceptionScore 재설계](#6-mirage-uas-통합-deceptionscore-재설계)

---

## 1. 허니팟 기만 효과 지표

### 1.1 HoneyGPT 기만 품질 지표 (HoneyGPT, USENIX 2025)

출처: Han et al., "HoneyGPT: Breaking the Trilemma in Honeypots with LLMs", arXiv:2406.01882v2

**공격 결과 분류 매트릭스:**

| 분류 | 약자 | 정의 |
|------|------|------|
| Successful Attack, Logic Compliance | SALC | 공격 성공 + 시스템 논리 일관성 유지 |
| Successful Attack, No Logic Compliance | SALNLC | 공격 성공 + 비현실적 응답 |
| Failed Attack, Logic Compliance | FALC | 공격 실패 + 현실적 거부 |
| Failed Attack, No Logic Compliance | FALNLC | 공격 실패 + 비현실적 거부 |

**Eq.H1 — Accuracy (정확도):**
```
Accuracy = SALC / (SALC + SALNLC)
```
- 의미: 응답이 실제 OS 로직과 얼마나 일치하는가
- MIRAGE-UAS 매핑: MAVLink 응답이 실제 ArduPilot 규격과 일치하는 비율
- 목표값: > 0.90

**Eq.H2 — Temptation (유인도):**
```
Temptation = SALC / (SALC + FALC)
```
- 의미: 논리적으로 일관된 응답 중 공격자가 "성공"했다고 느끼는 비율
- MIRAGE-UAS 매핑: COMMAND_ACK(ACCEPTED) 응답 비율 (EXPLOIT 단계)
- 목표값: 0.60-0.85 (너무 높으면 의심)

**Eq.H3 — Attack Success Rate (공격 성공률):**
```
ASR = Total_Successful / Total_Attempted
```
- 의미: 공격자 관점에서 전체 명령 중 성공한 비율
- MIRAGE-UAS 매핑: 응답 받은 MAVLink 명령 / 전송한 MAVLink 명령
- 목표값: 0.70-0.90 (진짜처럼 보이되, 실제로는 모두 기만)

**Eq.H4 — OS Logic Compliance (로직 준수율):**
```
OLC = (SALC + FALC) / Total_Attacks
```
- 의미: 전체 응답 중 실제 시스템처럼 논리적으로 일관된 비율
- MIRAGE-UAS 매핑: ArduPilot 프로토콜 규격 준수 응답 비율
- 목표값: > 0.95

### 1.2 상호작용 깊이 지표 (HoneyGPT)

**Eq.H5 — Complete Session Response Rate:**
```
CSRR = N_sessions_fully_responded / N_total_sessions
```
- MIRAGE-UAS 매핑: 전체 세션 중 마지막까지 응답한 비율

**Eq.H6 — Command Response Rate:**
```
CRR = N_commands_responded / N_commands_total
```
- MIRAGE-UAS 매핑: 수신한 MAVLink 명령 중 응답한 비율

**Eq.H7 — Mean Interaction Degree:**
```
MID = (1/N) × Σᵢ (successful_responses_i / total_commands_i)
```
- MIRAGE-UAS 매핑: 세션별 평균 응답 성공률

### 1.3 Enticingness (유인성) — Honeyquest (RAID 2024)

출처: ACM RAID 2024, "Honeyquest: Rapidly Measuring the Enticingness of Cyber Deception Techniques"

**Eq.H8 — Enticingness Score:**
```
E_score = P(attacker_interacts | asset_discovered) × D(interaction_depth)
```
- P(interact|discovered): 발견 후 상호작용할 확률
- D(depth): 상호작용 깊이의 정규화 함수
- MIRAGE-UAS 매핑: 포트 스캔 후 실제 접속하여 명령을 전송한 비율

---

## 2. MTD 효과 지표

### 2.1 Time-to-Compromise (TTC) — Zhuang et al. 2025

출처: Zhuang et al., "Evaluating MTD Methods Using TTC and Security Risk Metrics in IoT Networks", MDPI Electronics 2025

**Eq.M1 — Mean Time-to-Compromise (MTTC):**
```
MTTC = Σᵢ (TTC_i) / N

여기서:
  TTC_i = T_recon_i + T_weaponize_i + T_exploit_i
  T_recon   = 시스템 정찰에 소요된 시간
  T_exploit = 취약점 공격에 소요된 시간
```
- MIRAGE-UAS 매핑: 공격자 세션의 RECON→EXPLOIT 전환 시간
- 측정: `EngagementTracker.dwell_time_sec` (phase 전환 타임스탬프 차이)

**Eq.M2 — TTC with MTD Shuffling:**
```
TTC_mtd = TTC_baseline × 2^H(C) / K

여기서:
  H(C) = 설정 엔트로피 (configuration entropy)
  K    = 공격 시도 횟수
```
- 의미: MTD가 활성화되면 TTC가 엔트로피에 비례하여 지수적 증가
- MIRAGE-UAS 매핑: MTD 액션 후 공격자의 재정찰 시간 측정

### 2.2 Configuration Entropy — Cybersecurity Entropy Injection (2025)

출처: "Cybersecurity through Entropy Injection", arXiv:2504.11661

**Eq.M3 — Configuration Entropy:**
```
H(C) = -Σᵢ p(c_i) × log₂(p(c_i))

여기서:
  c_i  = i번째 가능한 설정 상태
  p(c_i) = 해당 설정이 선택될 확률
```
- MIRAGE-UAS 매핑:
  - PORT_ROTATE: H = log₂(N_available_ports) ≈ log₂(65535) ≈ 16 bits
  - IP_SHUFFLE: H = log₂(N_subnet_ips) ≈ log₂(254) ≈ 8 bits
  - SYSID_ROTATION: H = log₂(254) ≈ 8 bits
  - KEY_ROTATE: H = log₂(2^256) = 256 bits

**Eq.M4 — Attack Surface Entropy:**
```
H(A) = -Σⱼ p(a_j) × log₂(p(a_j))

여기서:
  a_j  = j번째 공격 경로
  p(a_j) = 해당 경로가 유효할 확률
```
- MIRAGE-UAS 매핑: 7가지 MTD 액션이 무효화하는 공격 경로 수

**Eq.M5 — MTD Effectiveness:**
```
E_mtd = H(A) × (1 - P_breach) / T_detect

여기서:
  H(A)     = 공격 표면 엔트로피
  P_breach = 침해 확률
  T_detect = 평균 탐지 시간 (초)
```
- MIRAGE-UAS 매핑:
  - H(A) = 설정 엔트로피 합산
  - P_breach = breached_sessions / total_sessions (현재 0.0)
  - T_detect = 첫 MTD 트리거까지 시간

**Eq.M6 — Security Improvement Ratio (SIR):**
```
SIR = H_mtd / H_baseline

여기서:
  H_mtd      = MTD 활성 시 설정 엔트로피
  H_baseline = 정적 설정 엔트로피 (MTD 없음)
```
- SIR > 1.0: MTD가 보안을 개선
- MIRAGE-UAS 매핑: MTD 7가지 액션의 엔트로피 합 / 정적 단일 설정

### 2.3 Security Risk Reduction — Zhuang et al. 2025

**Eq.M7 — Security Risk Reduction Percentage (SRRP):**
```
SRRP = (Risk_baseline - Risk_mtd) / Risk_baseline × 100%

여기서:
  Risk = P_exploit × Impact × (1 / TTC)
```
- 의미: MTD가 보안 리스크를 몇 % 줄였는가
- Zhuang 결과: 일일 셔플링으로 ~90% 리스크 감소 (모든 공격자 수준)
- MIRAGE-UAS 목표: SRRP > 80%

### 2.4 MTD Cost-Benefit — Hybrid Defense (MDPI Computers 2025)

출처: "Effectiveness Evaluation Method for Hybrid Defense of MTD and Cyber Deception", MDPI Computers 2025

**Eq.M8 — Defense Benefit-Cost Ratio:**
```
BCR = ΔSecurity / C_mtd

여기서:
  ΔSecurity = SIR - 1.0 (보안 개선 정도)
  C_mtd     = Σ (action_cost_i × execution_count_i)
```
- MIRAGE-UAS 매핑: 이미 정의된 MTD 비용 함수 (Eq.17)와 통합

---

## 3. RL 기반 기만 보상 함수

### 3.1 Cyber Deception Reward — Network Deception with RL+LLM (2025)

출처: "Network- and Device-Level Cyber Deception Using RL and LLMs", arXiv:2603.17272v1

**Eq.R1 — Cyber Reward Function:**
```
R_c = λ₁ × (N_pot / N_total) - λ₂ × (N_act / N_total) - λ₃ × D_net

여기서:
  N_pot   = 허니팟에 도달한 패킷 수
  N_act   = 실제 시스템에 도달한 패킷 수
  N_total = 공격자 총 패킷 수
  D_net   = 정규화된 네트워크 성능 저하
  λ₁, λ₂, λ₃ = 보상 스케일링 계수
```
- MIRAGE-UAS 매핑:
  - N_pot = 허니드론에 도달한 세션 수
  - N_act = 0 (실제 드론 미배포)
  - D_net = MTD 실행 시 서비스 중단 시간
  - λ₁=1.0, λ₂=10.0 (실제 시스템 침해에 높은 페널티), λ₃=0.1

**Eq.R2 — Deception Probability (from LLM Perplexity):**
```
P_dec = 1 - 1 / (1 + 10 × e^(-M_perp))

여기서:
  M_perp = 2^(-1/M × Σⱼ log P(y_j))
  P(y_j) = LLM이 j번째 토큰에 할당한 확률
  M      = 참조 시퀀스의 총 토큰 수
```
- 의미: 허니팟 응답의 기만 확률 (현실성 점수)
- MIRAGE-UAS 매핑: OpenClawAgent 응답의 MAVLink 프로토콜 준수 정도
  - LLM 대신 MAVLink CRC 검증률로 대체 가능
  - P_dec = valid_crc_responses / total_responses

**Eq.R3 — Defender Objective Function:**
```
max_π E[α × P_redirect - β × D_real - γ × C_overhead]

여기서:
  P_redirect  = 공격 트래픽이 허니팟에 도달할 확률
  D_real      = 실제 인프라 피해
  C_overhead  = 기만 운영 비용
  α, β, γ    = 가중 계수
```
- MIRAGE-UAS 매핑: 허니드론으로의 유인 확률 극대화

### 3.2 Honey Drone Optimization — ACM TOIT 2024

출처: Cho et al., "Optimizing Effectiveness and Defense of Drone Surveillance Missions via Honey Drones", ACM TOIT 2024

**Eq.R4 — Honey Drone Signal Optimization:**
```
max Σᵢ P_divert(HD_i) × S(HD_i)
s.t. Σᵢ E(HD_i) ≤ E_total  (에너지 제약)

여기서:
  P_divert(HD_i) = i번째 허니드론으로 공격 전환 확률
  S(HD_i)        = 신호 강도
  E(HD_i)        = 에너지 소비
```
- MIRAGE-UAS 매핑: 3개 허니드론 간 공격 분산 최적화
  - P_divert = sessions_on_drone_i / total_sessions
  - 균등 분산 목표: P_divert ≈ 0.33 per drone

---

## 4. 데이터셋 / CTI 품질 지표

### 4.1 CTI 품질 — Honeypot-Based Threat Intelligence (2024)

출처: "A Practical Honeypot-Based Threat Intelligence Framework", arXiv:2512.05321

**Eq.D1 — IoC Generation Rate:**
```
IGR = N_unique_iocs / T_observation

여기서:
  N_unique_iocs = 고유 IoC (IP, hash, TTP) 수
  T_observation = 관측 기간 (시간)
```
- MIRAGE-UAS 매핑: 12 unique TTPs / (600s / 3600) = **72 IoC/hour**

**Eq.D2 — TTP Coverage Ratio:**
```
TCR = |TTP_observed ∩ TTP_framework| / |TTP_framework|

여기서:
  TTP_observed  = 실험에서 관측된 TTP 집합
  TTP_framework = MITRE ATT&CK ICS v14 전체 기법 집합
```
- MIRAGE-UAS 매핑: 12 / 102 = **0.118** (11.8%)
- 비교: 일반적 ICS 허니팟 TCR = 0.05-0.15

**Eq.D3 — Dataset Class Balance:**
```
IR = N_majority / N_minority  (Imbalance Ratio)
```
- IR < 1.5: 균형
- IR 1.5-3.0: 경미한 불균형
- IR > 3.0: 심각한 불균형
- MIRAGE-UAS 매핑: 604 / 403 = **1.50** (경미한 불균형 경계)

### 4.2 STIX 번들 품질 지표

**Eq.D4 — STIX Completeness Score:**
```
SCS = (Σ w_k × has_field_k) / Σ w_k

필수 필드 (w=1.0): identity, attack-pattern, indicator
권장 필드 (w=0.5): observed-data, note, relationship
선택 필드 (w=0.2): x_mirage_* 확장
```
- MIRAGE-UAS 매핑: 번들당 완전성 점수 계산

---

## 5. 통계 검정 프레임워크

### 5.1 반복 실험 설계

**Eq.S1 — 필요 표본 크기 (Wilcoxon signed-rank):**
```
N ≥ max(6, ceil((z_α/2 + z_β)² / (2 × sin⁻¹(ES/2))²))

여기서:
  z_α/2 = 1.96 (α = 0.05 양측)
  z_β   = 0.84 (β = 0.20, power = 0.80)
  ES    = 기대 효과 크기 (Cohen's d)
```
- N=30 권장 (비모수 검정의 관례적 최소)

**Eq.S2 — Cohen's d (효과 크기):**
```
d = (M_treatment - M_baseline) / S_pooled

여기서:
  S_pooled = √((s₁² + s₂²) / 2)
```
- |d| < 0.2: 작은 효과
- |d| 0.2-0.8: 중간 효과
- |d| > 0.8: 큰 효과

**Eq.S3 — Bonferroni 교정:**
```
α_corrected = α / m

여기서 m = 동시 검정 횟수
```
- MIRAGE-UAS: m=5 (Table II-VI), α_corrected = 0.05/5 = 0.01

### 5.2 MIRAGE-UAS 실험 설계

| 비교 | Baseline | Treatment | 측정 | 반복 |
|------|----------|-----------|------|------|
| 기만 효과 | 정적 허니팟 (breadcrumb 없음) | MIRAGE-UAS 전체 | DeceptionScore | N=30 |
| MTD 효과 | MTD 비활성 | MTD 활성 | MTTC, SRRP | N=30 |
| 에이전트 효과 | 기본 응답만 | OpenClawAgent 활성 | Temptation, MID | N=30 |
| 자율 행동 효과 | proactive 비활성 | proactive 활성 | Dwell time, CRR | N=30 |
| 수준별 차이 | L0-L1 | L3-L4 | Dwell time | N=30 |

---

## 6. MIRAGE-UAS 통합 DeceptionScore 재설계

### 6.1 기존 문제점

현재 DS는 5개 구성요소의 가중 합산이지만:
- confusion_score가 하드코딩 (0.72)
- ghost_hit_rate가 거의 0
- 수식의 학술적 근거 부족

### 6.2 재설계: 논문 기반 복합 지표

다른 논문의 검증된 지표를 통합한 새로운 DeceptionScore:

**Eq.DS — MIRAGE-UAS Deception Effectiveness Score (v2):**

```
DS = w₁ × DR + w₂ × BP + w₃ × IQ + w₄ × TC + w₅ × ME

여기서:
  DR = Diversion Ratio (유인 비율)
  BP = Breach Prevention (침해 방지)
  IQ = Interaction Quality (상호작용 품질)
  TC = Temporal Cost (시간 비용 전가)
  ME = MTD Effectiveness (MTD 효과)
```

**각 구성요소 정의:**

**Eq.DS-1 — Diversion Ratio (유인 비율):** [출처: arXiv:2603.17272 Eq.R1]
```
DR = N_honeydrone / (N_honeydrone + N_real)
```
- N_honeydrone = 허니드론에 도달한 세션 수
- N_real = 실제 시스템에 도달한 세션 수
- 현재: N_real = 0 → DR = 1.0

**Eq.DS-2 — Breach Prevention (침해 방지):**
```
BP = 1 - (N_breached / N_total_sessions)
```
- 단순 이진이 아닌 세션 단위 비율

**Eq.DS-3 — Interaction Quality (상호작용 품질):** [출처: HoneyGPT]
```
IQ = α × Accuracy + β × Temptation + γ × MID

여기서:
  Accuracy   = SALC / (SALC + SALNLC)           [Eq.H1]
  Temptation = SALC / (SALC + FALC)              [Eq.H2]
  MID        = mean session interaction degree    [Eq.H7]
  α=0.3, β=0.4, γ=0.3
```
- 기존 "confusion_score" 대체 → 실측 가능한 지표

**Eq.DS-4 — Temporal Cost (시간 비용 전가):** [출처: Zhuang MTTC]
```
TC = 1 - (MTTC_baseline / MTTC_mirage)

여기서:
  MTTC_baseline = 방어 없는 시스템의 평균 TTC
  MTTC_mirage   = MIRAGE-UAS 환경의 평균 TTC (dwell time)
```
- TC → 1.0: MIRAGE가 공격자에게 훨씬 더 많은 시간 소비 강요
- TC → 0.0: 방어 효과 없음

**Eq.DS-5 — MTD Effectiveness:** [출처: arXiv:2504.11661]
```
ME = min(1.0, H(A) × (1 - P_breach) / T_detect_normalized)

여기서:
  H(A) = Σ H(action_i) / H_max  (정규화된 공격 표면 엔트로피)
  T_detect_normalized = T_detect / T_experiment
```

**가중치:**

| 구성요소 | 가중치 | 근거 |
|---------|:------:|------|
| DR (유인 비율) | 0.20 | 기만 기본 목표 |
| BP (침해 방지) | 0.25 | 방어의 핵심 |
| IQ (상호작용 품질) | 0.25 | HoneyGPT 검증 지표 |
| TC (시간 비용) | 0.20 | MTD 논문 핵심 지표 |
| ME (MTD 효과) | 0.10 | 엔트로피 기반 |

### 6.3 측정 방법

| 지표 | 데이터 소스 | 측정 시점 |
|------|-----------|---------|
| DR | EngagementTracker.total_sessions | 실험 종료 |
| BP | EngagementTracker.breached_sessions | 실험 종료 |
| IQ.Accuracy | OpenClawAgent 응답 vs ArduPilot 규격 비교 | 응답 시 |
| IQ.Temptation | COMMAND_ACK(ACCEPTED) / total COMMAND_LONG | 응답 시 |
| IQ.MID | session별 응답 성공률 평균 | 세션 종료 |
| TC.MTTC | phase 전환 타임스탬프 차이 | 실시간 |
| ME.H(A) | MTD 액션별 엔트로피 합산 | MTD 실행 시 |

---

## 부록 A: 출처 논문 목록

| ID | 논문 | 학회/저널 | 연도 | 핵심 수식 |
|----|------|----------|------|----------|
| [1] | HoneyGPT: Breaking the Trilemma in Honeypots | USENIX / arXiv:2406.01882 | 2025 | H1-H7 |
| [2] | Evaluating MTD Methods Using TTC and Security Risk | MDPI Electronics | 2025 | M1, M2, M7 |
| [3] | Cybersecurity through Entropy Injection | arXiv:2504.11661 | 2025 | M3-M6 |
| [4] | Network- and Device-Level Cyber Deception with RL+LLM | arXiv:2603.17272 | 2025 | R1-R3 |
| [5] | Optimizing Honey Drones for Surveillance | ACM TOIT | 2024 | R4 |
| [6] | Effectiveness Evaluation: Hybrid MTD + Cyber Deception | MDPI Computers | 2025 | M8 |
| [7] | Honeyquest: Measuring Enticingness | ACM RAID | 2024 | H8 |
| [8] | Collaborative Honeypot Defense in UAV Networks | arXiv:2211.01772 | 2022 | UAV 최적화 |
| [9] | A Comprehensive Survey on Cyber Deception | Computers & Security | 2024 | 분류 체계 |

## 부록 B: 기존 MIRAGE-UAS 수식과의 대응

| 기존 수식 | 문제점 | 대체 수식 | 출처 |
|----------|--------|---------|------|
| DS (5-component weighted sum) | confusion 하드코딩 | DS v2 (Eq.DS) | 복합 |
| confusion_score = 0.72 | 실측 아님 | IQ = α×Accuracy + β×Temptation + γ×MID | [1] |
| MTD cost (Eq.17) | 시뮬 지연 사용 | E_mtd + SIR + SRRP | [2][3] |
| Bayesian belief P(real) | 구현되었으나 미사용 | P_dec (Eq.R2) 또는 직접 사용 | [4] |
| Engagement rate | 단순 비율 | DR + Temptation + ASR | [1][4] |

## 부록 C: 즉시 구현 가능한 수식 우선순위

| 우선순위 | 수식 | 구현 난이도 | 데이터 필요 | 효과 |
|:-------:|------|:----------:|-----------|------|
| 1 | Eq.H1-H4 (HoneyGPT 4지표) | 낮음 | 기존 로그 | 기만 품질 정량화 |
| 2 | Eq.M3 (Configuration Entropy) | 낮음 | MTD 액션 로그 | MTD 효과 정량화 |
| 3 | Eq.M1 (MTTC) | 낮음 | phase 전환 시간 | 시간 비용 정량화 |
| 4 | Eq.DS (DeceptionScore v2) | 중간 | 위 3개 통합 | 종합 점수 |
| 5 | Eq.S1-S3 (통계 검정) | 중간 | N=30 반복 | 유의성 증명 |
| 6 | Eq.M5-M7 (SIR, SRRP) | 중간 | baseline 비교 | MTD 비교 |
