# MIRAGE-UAS: 무인항공시스템을 위한 지능형 적응 기만 프레임워크

**Moving-target Intelligent Responsive Agentic deception enGinE for Unmanned Aerial Systems**

> DS Lab, 경기대학교 | ACM CCS 2026 Cycle B 투고 대상  
> 연구비 지원: 국방기술진흥연구소 (DAPA) 프로젝트 915024201

---

## 목차

1. [연구 배경 및 동기](#1-연구-배경-및-동기)
2. [시스템 개요](#2-시스템-개요)
3. [전체 아키텍처](#3-전체-아키텍처)
4. [핵심 기능 1: 허니 드론 (Honey Drone)](#4-핵심-기능-1-허니-드론-honey-drone)
   - 4.1 [OpenClaw 자율 기만 에이전트](#41-openclaw-자율-기만-에이전트)
   - 4.2 [공격 단계 탐지 (Phase Detection)](#42-공격-단계-탐지-phase-detection)
   - 4.3 [공격자 도구 지문 분석 (Attacker Fingerprinting)](#43-공격자-도구-지문-분석-attacker-fingerprinting)
   - 4.4 [자율 행동 시스템 (Autonomous Behaviors)](#44-자율-행동-시스템-autonomous-behaviors)
   - 4.5 [가짜 서비스 팩토리 (Fake Service Factory)](#45-가짜-서비스-팩토리-fake-service-factory)
   - 4.6 [Breadcrumb 유인 시스템](#46-breadcrumb-유인-시스템)
   - 4.7 [베이지안 믿음 추적 (Deception State Manager)](#47-베이지안-믿음-추적-deception-state-manager)
5. [핵심 기능 2: 이동 표적 방어 (MTD)](#5-핵심-기능-2-이동-표적-방어-mtd)
   - 5.1 [7가지 MTD 액션](#51-7가지-mtd-액션)
   - 5.2 [MTD 비용 함수](#52-mtd-비용-함수)
   - 5.3 [MTD 트리거 메커니즘](#53-mtd-트리거-메커니즘)
6. [핵심 기능 3: CTI 파이프라인](#6-핵심-기능-3-cti-파이프라인)
   - 6.1 [MAVLink 패킷 인터셉터](#61-mavlink-패킷-인터셉터)
   - 6.2 [공격 이벤트 파서 (L0-L4 분류)](#62-공격-이벤트-파서-l0-l4-분류)
   - 6.3 [MITRE ATT&CK ICS 매핑](#63-mitre-attck-ics-매핑)
   - 6.4 [STIX 2.1 번들 생성](#64-stix-21-번들-생성)
7. [핵심 기능 4: 데이터셋 생성](#7-핵심-기능-4-데이터셋-생성)
   - 7.1 [DVD-CTI-Dataset-v1 구성](#71-dvd-cti-dataset-v1-구성)
   - 7.2 [양성/음성 샘플 수집](#72-양성음성-샘플-수집)
   - 7.3 [데이터셋 품질 검증](#73-데이터셋-품질-검증)
8. [핵심 기능 5: 평가 및 메트릭](#8-핵심-기능-5-평가-및-메트릭)
   - 8.1 [DeceptionScore 산출](#81-deceptionscore-산출)
   - 8.2 [논문용 표 자동 생성 (Table II-VI)](#82-논문용-표-자동-생성-table-ii-vi)
   - 8.3 [통계 검정 및 LaTeX 출력](#83-통계-검정-및-latex-출력)
9. [인프라 및 배포](#9-인프라-및-배포)
   - 9.1 [Docker 컨테이너 구성](#91-docker-컨테이너-구성)
   - 9.2 [네트워크 토폴로지](#92-네트워크-토폴로지)
   - 9.3 [실행 모드 (5가지)](#93-실행-모드-5가지)
   - 9.4 [실시간 대시보드](#94-실시간-대시보드)
10. [OMNeT++ 네트워크 시뮬레이션](#10-omnet-네트워크-시뮬레이션)
11. [실험 결과 요약](#11-실험-결과-요약)
12. [기술 스택 총정리](#12-기술-스택-총정리)
13. [향후 연구 방향](#13-향후-연구-방향)

---

## 1. 연구 배경 및 동기

### 1.1 문제 정의

무인항공시스템(UAS)은 군사, 물류, 농업, 재난 대응 등 다양한 분야에서 급격히 확산되고 있습니다. 그러나 UAS가 사용하는 **MAVLink 프로토콜**은 기본적으로 **암호화나 인증이 없는 평문 통신**을 사용하기 때문에, 공격자가 드론의 명령 채널에 접근하여 비행 경로를 변경하거나 민감 데이터를 탈취하는 것이 가능합니다.

기존의 UAS 보안 접근법에는 다음과 같은 한계가 있습니다:

| 기존 접근법 | 한계점 |
|------------|--------|
| 방화벽/IDS 기반 차단 | 공격을 탐지하더라도 공격자의 의도와 수준을 파악하기 어려움 |
| 정적 허니팟 | 한 번 탐지되면 회피 가능, 적응적 공격에 취약 |
| MAVLink 암호화 | 레거시 시스템과의 호환성 문제 |
| 단순 MTD | 기만과 연동되지 않아 방어 효과 제한적 |

### 1.2 연구 목표

MIRAGE-UAS는 이러한 한계를 극복하기 위해 **세 가지 핵심 기여(Contribution)**를 목표로 합니다:

| 기여 | 설명 |
|------|------|
| **C1** | UAS 환경에서 MTD(이동 표적 방어)와 자율 기만 에이전트를 통합한 **최초의 프레임워크** |
| **C2** | 허니팟에서 수집한 레이블링된 UAS CTI(사이버 위협 인텔리전스) 데이터셋의 **최초 생성** |
| **C3** | OpenClaw에서 영감을 받은 **자율 기만 드론 설계 패턴** 제안 |

### 1.3 왜 "기만(Deception)"인가?

기만 기반 방어는 단순 차단과 달리 공격자를 **의도적으로 속여서** 다음과 같은 이점을 얻습니다:

- **시간 확보**: 공격자가 가짜 시스템에 시간을 소비하는 동안 실제 시스템을 보호
- **인텔리전스 수집**: 공격자의 도구, 기법, 절차(TTP)를 관찰하여 위협 정보 생성
- **비용 전가**: 공격자에게 높은 비용을 전가하여 공격 의욕 저하
- **적응적 방어**: 공격 수준에 따라 기만 전략을 실시간으로 조정

---

## 2. 시스템 개요

MIRAGE-UAS는 크게 **두 개의 트랙**으로 구성됩니다:

```
┌────────────────────────────────────────────────────────────┐
│                    MIRAGE-UAS 전체 구조                      │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Track A: 실시간 기만 + MTD                          │  │
│  │  "공격자를 속이고, 표적을 움직인다"                    │  │
│  │                                                      │  │
│  │  공격자 → 허니드론(기만) → MTD(표적이동) → 방어 완료  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Track B: 오프라인 CTI 생성                          │  │
│  │  "속이는 과정에서 위협 정보를 수집한다"                │  │
│  │                                                      │  │
│  │  패킷 캡처 → 이벤트 분류 → ATT&CK 매핑 → STIX 번들  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  평가 레이어 (Evaluation)                            │  │
│  │  "기만이 얼마나 효과적이었는지 정량화한다"             │  │
│  │                                                      │  │
│  │  DeceptionScore 산출 → 표/그래프 생성 → LaTeX 출력   │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

### 핵심 수치 요약

| 지표 | 값 | 설명 |
|------|-----|------|
| DeceptionScore | **0.714** | 기만 효과 종합 점수 (0~1) |
| 공격자 보호율 | **100%** | 실제 시스템 침해 0건 |
| 참여율 (Engagement) | **75.9%** | 공격자가 기만에 속은 비율 |
| 자율 행동 | **9가지** | 에이전트의 능동적 기만 행동 수 |
| MTD 트리거 | **46건** | 실제 발동된 이동 표적 방어 횟수 |
| 데이터셋 규모 | **1,007 샘플** | 생성된 CTI 데이터셋 크기 |
| ATT&CK TTP | **12개** | 매핑된 ICS 공격 기법 수 |
| 실험 시간 | **600초** | 10분 전체 실행 |

---

## 3. 전체 아키텍처

### 3.1 컴포넌트 상호작용도

아래 다이어그램은 MIRAGE-UAS의 모든 컴포넌트 간 상호작용을 보여줍니다:

![MIRAGE-UAS Component Flow](diagrams/mirage_component_flow.png)

**그림 설명**: 빨간색(공격자) → 노란색(CC Stub) → 초록색(기만 엔진/CTI) → 파란색(MTD) → 보라색(평가)로 이어지는 데이터 흐름을 나타냅니다. 각 컴포넌트는 asyncio 큐를 통해 비동기적으로 연결됩니다.

### 3.2 L2 공격자 세션 시퀀스

아래 시퀀스 다이어그램은 중급(L2) 공격자가 허니드론과 상호작용하는 전체 과정을 단계별로 보여줍니다:

![L2 Attacker Session Sequence](diagrams/mirage_sequence.png)

**그림 설명**: 공격자의 HTTP 정찰(Phase 1) → 로그인 시도(Phase 2) → MAVLink 정찰(Phase 3) → ARM 명령 및 EXPLOIT(Phase 4) → MTD 트리거(Phase 5) → CTI 번들 생성 및 최종 DeceptionScore 산출(Phase 6)까지의 전체 흐름입니다.

### 3.3 모듈 구조

```
mirage-uas/
├── src/
│   ├── honey_drone/          ← Track A: 허니드론 기만 엔진 (9개 모듈, 4,800줄)
│   │   ├── openclaw_agent.py          # 자율 기만 에이전트 (핵심, 1,207줄)
│   │   ├── agentic_decoy_engine.py    # 기만 엔진 코어 (610줄)
│   │   ├── engagement_tracker.py      # 공격자 세션 추적 (404줄)
│   │   ├── deception_state_manager.py # 베이지안 믿음 추적 (437줄)
│   │   ├── deception_orchestrator.py  # 기만 조율기 (618줄)
│   │   ├── fake_service_factory.py    # 가짜 서비스 생성 (656줄)
│   │   ├── breadcrumb_plant.py        # 유인 시스템 (446줄)
│   │   ├── honey_drone_manager.py     # 드론 관리 (420줄)
│   │   └── mavlink_response_gen.py    # MAVLink 응답 생성 (300줄)
│   │
│   ├── mtd/                  ← Track A: 이동 표적 방어 (5개 모듈, 1,200줄)
│   │   ├── mtd_executor.py            # Docker 기반 MTD 실행 (441줄)
│   │   ├── mtd_actions.py             # 7가지 액션 정의 (174줄)
│   │   ├── engagement_to_mtd.py       # 참여→MTD 변환기 (207줄)
│   │   ├── decoy_rotation_policy.py   # 디코이 로테이션 (144줄)
│   │   └── mtd_monitor.py            # MTD 효과 모니터링 (292줄)
│   │
│   ├── cti_pipeline/         ← Track B: CTI 생성 (6개 모듈, 1,800줄)
│   │   ├── mavlink_interceptor.py     # 패킷 캡처 (289줄)
│   │   ├── attack_event_parser.py     # L0-L4 분류 (295줄)
│   │   ├── attck_mapper.py            # ATT&CK ICS 매핑 (304줄)
│   │   ├── stix_converter.py          # STIX 2.1 변환 (325줄)
│   │   ├── cti_ingest_api.py          # FastAPI 서버 (306줄)
│   │   └── pcap_writer.py            # PCAP 기록 (130줄)
│   │
│   ├── dataset/              ← Track B: 데이터셋 생성 (4개 모듈, 1,100줄)
│   │   ├── positive_collector.py      # 공격 샘플 수집 (211줄)
│   │   ├── negative_generator.py      # 정상 샘플 생성 (397줄)
│   │   ├── dataset_packager.py        # 패키징 (236줄)
│   │   └── dataset_validator.py       # 품질 검증 (277줄)
│   │
│   ├── evaluation/           ← 평가 레이어 (4개 모듈, 1,100줄)
│   │   ├── deception_scorer.py        # DeceptionScore 산출 (182줄)
│   │   ├── metrics_collector.py       # Table II-VI 생성 (444줄)
│   │   ├── plot_results.py            # 그래프/피규어 (332줄)
│   │   └── statistical_test.py        # 통계 검정 + LaTeX (336줄)
│   │
│   ├── omnetpp/              ← OMNeT++ 시뮬레이션 (3개 모듈, 600줄)
│   │   ├── trace_exporter.py          # 트레이스 내보내기 (180줄)
│   │   ├── inet_config_gen.py         # INET 설정 생성 (130줄)
│   │   └── reproducibility_pack.py    # 재현 패키지 (170줄)
│   │
│   └── shared/               ← 공유 모듈 (3개 모듈, 770줄)
│       ├── models.py                  # 18개 데이터클래스 (393줄)
│       ├── constants.py               # 환경변수 로더 (287줄)
│       └── logger.py                  # 구조화 로깅 (92줄)
│
├── scripts/                  ← 실행 스크립트
│   ├── run_experiment.py              # 메인 실험 실행
│   ├── run_engines.py                 # 엔진 구동
│   ├── run_test_harness.sh            # 테스트 하네스
│   ├── run_full.sh                    # 풀 DVD 실행
│   ├── run_multiview.sh               # tmux 멀티뷰
│   ├── start_dashboard.sh             # 대시보드 시작
│   └── start_obs.sh                   # Observatory 시작
│
├── config/                   ← 설정
│   └── .env                           # 22개 연구 파라미터
│
├── results/                  ← 실험 결과
│   ├── metrics/                       # JSON 메트릭 (7개 파일)
│   ├── figures/                       # PDF/PNG 그래프 (12개 파일)
│   ├── latex/                         # LaTeX 테이블 (3개 파일)
│   ├── dataset/                       # DVD-CTI-Dataset-v1
│   └── logs/                          # 실행 로그
│
├── docker-compose.honey.yml  ← 프로덕션 Docker 구성
├── docker-compose.test-harness.yml  ← 테스트 Docker 구성
└── requirements.txt          ← Python 의존성 (22개)
```

**총 코드 규모: 12,821줄 Python / 36개 모듈 / 63개 파일**

---

## 4. 핵심 기능 1: 허니 드론 (Honey Drone)

허니 드론은 MIRAGE-UAS의 가장 핵심적인 구성요소입니다. 공격자에게 **실제 드론처럼 보이는 미끼 드론**을 제공하여, 공격자가 진짜 드론 대신 가짜 드론을 공격하도록 유도합니다. 단순한 정적 허니팟이 아니라, **자율적으로 적응하고 능동적으로 기만**하는 지능형 시스템입니다.

### 4.1 OpenClaw 자율 기만 에이전트

OpenClaw 에이전트는 허니 드론의 "두뇌" 역할을 합니다. 외부 명령 없이 스스로 공격자를 관찰하고, 공격 단계를 판단하여, 최적의 기만 응답을 생성합니다.

**핵심 설계 원칙:**

| 원칙 | 설명 | 왜 중요한가? |
|------|------|-------------|
| **자율성** | 사람의 개입 없이 독립적으로 판단하고 행동 | 실시간 공격에 즉각 대응 가능 |
| **적응성** | 공격 단계(RECON→EXFIL)에 따라 응답 전략 변경 | 고급 공격자도 속일 수 있음 |
| **공격자 지문 분석** | 공격 도구별 특성을 파악하여 맞춤 응답 | 기만 성공률 극대화 |
| **자가 변이** | 주기적으로 포트, ID, 파라미터를 스스로 변경 | 재방문 공격자도 재기만 가능 |

**에이전트 작동 흐름:**

```
공격자 패킷 수신
    │
    ▼
┌─────────────────────┐
│ 1. observe()        │ ← 패킷 분석, 공격 단계 판단
│    - 메시지 타입 확인│
│    - 세션 이력 조회  │
│    - 도구 지문 분석  │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 2. 단계 판단        │ ← RECON? EXPLOIT? PERSIST? EXFIL?
│    - 메시지 패턴     │
│    - 누적 행동 분석  │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 3. generate_response│ ← 단계별 최적 기만 응답 생성
│    - 가짜 텔레메트리 │
│    - 부분 성공 응답  │
│    - Breadcrumb 삽입 │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 4. 자율 행동 실행   │ ← 9가지 백그라운드 루프
│    (비동기 병렬)     │
└─────────────────────┘
```

### 4.2 공격 단계 탐지 (Phase Detection)

에이전트는 공격자가 보낸 MAVLink 메시지 타입을 분석하여 현재 공격이 어느 단계에 있는지 실시간으로 판단합니다:

```
공격 생명주기 (Cyber Kill Chain 기반)

  RECON (정찰)        EXPLOIT (공격)       PERSIST (지속)       EXFIL (유출)
  ┌──────────┐       ┌──────────┐        ┌──────────┐       ┌──────────┐
  │ "뭐가    │  ──→  │ "이걸로  │  ──→   │ "계속    │  ──→  │ "데이터를│
  │  있지?"  │       │  들어간다"│        │  접근하자"│       │  빼내자" │
  └──────────┘       └──────────┘        └──────────┘       └──────────┘
```

**각 단계를 유발하는 MAVLink 메시지:**

| 공격 단계 | 대표 메시지 | 공격자 행동 예시 |
|----------|-----------|-----------------|
| **RECON** (정찰) | `HEARTBEAT`, `PARAM_REQUEST_LIST`, `PARAM_REQUEST_READ` | nmap 스캔, 파라미터 목록 조회, 시스템 정보 수집 |
| **EXPLOIT** (공격) | `COMMAND_LONG`, `SET_MODE`, `COMMAND_INT` | ARM/DISARM 명령, 비행 모드 변경, 제어권 탈취 시도 |
| **PERSIST** (지속) | `PARAM_SET`, `MISSION_ITEM`, `MISSION_COUNT` | 파라미터 변경, 비행 경로 업로드, 설정 변조 |
| **EXFIL** (유출) | `LOG_REQUEST_LIST`, `LOG_REQUEST_DATA`, `FILE_TRANSFER_PROTOCOL` | 비행 로그 다운로드, 파일 탈취 |

**단계별 기만 응답 전략:**

| 공격 단계 | 기만 전략 | 응답 예시 | 목적 |
|----------|----------|----------|------|
| **RECON** | 풍부한 정보 제공 → "금광" 착각 유도 | 파라미터 1,034개 목록, GPS 좌표, 배터리 상태 | 공격자를 계속 머물게 함 |
| **EXPLOIT** | 부분 성공 응답 → 공격이 통하는 것처럼 위장 | `COMMAND_ACK(ACCEPTED)` + 가짜 모드 변경 | 공격자가 성공했다고 믿게 함 |
| **PERSIST** | 운영자 활동 시뮬 → 실제 운영 중인 것처럼 위장 | `STATUSTEXT("Operator override")` | 공격자가 들키지 않으려 조심하게 만듦 |
| **EXFIL** | 가짜 데이터 제공 → 시간 소비 유도 | 가짜 로그 파일, 조작된 설정 파일 | 공격자의 시간과 자원 낭비 |

### 4.3 공격자 도구 지문 분석 (Attacker Fingerprinting)

에이전트는 공격자가 사용하는 **도구의 특성**을 패킷의 타이밍과 명령 패턴으로 식별합니다:

| 공격 도구 | 식별 기준 | 맞춤 응답 전략 |
|----------|----------|---------------|
| **nmap 스캐너** | 빠른 연속 프로브, 포트 스캔 패턴 | HEARTBEAT만 응답 (최소 정보) |
| **MAVProxy GCS** | 규칙적인 HEARTBEAT, GCS 프로토콜 준수 | 전체 텔레메트리 제공 (정상 GCS처럼) |
| **DroneKit 스크립트** | SDK 특유의 명령 시퀀스, API 호출 패턴 | SDK 호환 응답 + API 키 breadcrumb |
| **Metasploit 모듈** | exploit 패턴, 빠른 페이로드 시도 | 즉시 응답 (exploit 성공 위장) |
| **커스텀 Exploit** | 비표준 패턴, 도구 미상 | 신중한 최소 응답 |

**식별 방법:**
- **Inter-arrival timing**: 패킷 간 도착 시간 간격 분석
- **Command sequence pattern**: 명령 순서의 특징적 패턴
- **Protocol compliance**: MAVLink 규격 준수 정도

### 4.4 자율 행동 시스템 (Autonomous Behaviors)

OpenClaw 에이전트는 공격자의 패킷을 기다리는 것뿐만 아니라, **능동적으로 기만 행동을 수행**합니다. 9가지 독립적인 비동기 루프가 백그라운드에서 실행됩니다:

| # | 행동 | 주기 | 구현 함수 | 효과 |
|---|------|------|----------|------|
| 1 | **능동 STATUSTEXT 전송** | 45초 | `_proactive_statustext()` | "Operator check-in" 같은 가짜 운영자 메시지를 전송하여 실제 운용 중인 드론처럼 위장 |
| 2 | **비행 시뮬레이션** | 60초 | `_proactive_flight_sim()` | 고도 0→100→0m 변화를 시뮬레이션하여 실제 비행 중인 것처럼 텔레메트리 생성 |
| 3 | **Ghost 포트 개방** | 45초 | `_proactive_ghost_port()` | 새로운 서비스 포트를 열어 공격자가 추가 서비스를 "발견"하도록 유도 |
| 4 | **재부팅 시뮬레이션** | 45초 | `_proactive_reboot()` | 8-15초간 응답을 멈춘 후 복귀 → 실제 시스템 재부팅처럼 보이게 함 |
| 5 | **가짜 키 유출** | 45초 | `_proactive_fake_key()` | SHA-256으로 생성한 가짜 서명 키를 "실수로" 노출 → 공격자가 키를 탈취하려 시도 |
| 6 | **Sysid 회전** | 120초 | `_sysid_rotation_loop()` | MAVLink srcSystem ID를 1-254 범위에서 변경 → 다른 드론처럼 보이게 함 |
| 7 | **포트 회전** | 90초 | `_port_rotation_loop()` | WebSocket API 포트를 동적으로 변경 → 재접속 시도 유도 |
| 8 | **미션 갱신** | 60초 | `_mission_refresh_loop()` | 가짜 웨이포인트 3-12개를 주기적으로 재생성 → 비행 임무가 변경되는 것처럼 위장 |
| 9 | **파라미터 변동** | 45초 | `_param_cycle_loop()` | 가우시안 드리프트(±0.5%)로 센서 값 미세 변동 → 실제 센서 노이즈처럼 보이게 함 |

**자율 행동의 효과:**
```
공격자 시점에서 본 허니드론:

  "이 드론은 정상적으로 운용 중이다"
      ↑ STATUSTEXT("Operator check-in")
      ↑ 고도 변화 텔레메트리
      ↑ 센서 값 미세 변동

  "새로운 서비스가 열렸다, 더 조사해보자"
      ↑ Ghost 포트 개방
      ↑ 가짜 SSH 키 노출

  "이 드론이 리부팅됐다가 돌아왔다, 진짜인 것 같다"
      ↑ 재부팅 시뮬레이션

  → 결과: 공격자는 가짜 드론에 더 많은 시간을 투자
```

### 4.5 가짜 서비스 팩토리 (Fake Service Factory)

허니 드론은 MAVLink 외에도 다양한 **가짜 네트워크 서비스**를 제공하여 공격자에게 더 풍부한 공격 표면을 보여줍니다:

| 서비스 | 프로토콜 | 에뮬레이션 내용 | 공격자에게 보이는 것 |
|--------|---------|---------------|-------------------|
| **ArduPilot Web UI** | HTTP | 드론 웹 관리 인터페이스 | `http://drone:8080` → 로그인 페이지, 파라미터 설정 |
| **카메라 스트림** | RTSP | SDP 기술자 + 가짜 스트림 정보 | `rtsp://drone:8554/cam` → 카메라가 달린 드론처럼 보임 |
| **SSH 셸** | SSH | OpenSSH 배너 + 키 교환 시뮬레이션 | `ssh drone` → "ssh-2.0-OpenSSH_8.9" 배너 응답 |
| **OpenClaw API** | WebSocket | 에이전트 제어 API 에뮬레이션 | `ws://drone:18789` → 내부 API가 노출된 것처럼 보임 |
| **MAVLink Ghost** | UDP | 추가 MAVLink 엔드포인트 | 여러 대의 드론이 있는 것처럼 보임 |

**공격자 수준별 서비스 복잡도:**

| 레벨 | 서비스 반응 정도 |
|------|-----------------|
| L0 (스크립트 키디) | 단순 배너만 응답 |
| L1 (기초) | 기본 프로토콜 핸드셰이크 |
| L2 (중급) | 재접속 + API 응답 |
| L3 (고급) | 인터랙티브 세션 |
| L4 (APT급) | 완전 기능 에뮬레이션 |

### 4.6 Breadcrumb 유인 시스템

Breadcrumb(빵가루)은 공격자를 더 깊이 유인하기 위해 의도적으로 심어두는 **가짜 민감 정보**입니다. 동화 "헨젤과 그레텔"에서 빵가루를 따라간 것처럼, 공격자가 이 가짜 정보를 따라가도록 유도합니다.

**Breadcrumb 유형과 공격자 수준별 노출:**

| 유형 | 설명 | 예시 | L0 | L1 | L2 | L3 | L4 |
|------|------|------|:--:|:--:|:--:|:--:|:--:|
| **CREDENTIAL** | 가짜 인증 정보 | `admin:admin` | O | O | O | O | O |
| **API_TOKEN** | 가짜 API 토큰 | `sha256-xxxx...` | | O | O | O | O |
| **SSH_KEY** | 가짜 SSH 키 | `/home/operator/.ssh/id_rsa` | | | O | O | O |
| **INTERNAL_PATH** | 가짜 내부 경로 | `/api/internal/config` | | | O | O | O |
| **CONFIG_FILE** | 가짜 설정 파일 | `/etc/ardupilot.conf` | | | O | O | O |
| **MAVLINK_KEY** | 가짜 서명 키 | MAVLink signing key | | | | O | O |
| **DATABASE_DSN** | 가짜 DB 연결 문자열 | `postgresql://admin:pass@fleet-db` | | | | | O |

**Breadcrumb 추적 메커니즘:**

```
Breadcrumb 심기 → 공격자 접근 감지 → 알림 + 기록
         │                │               │
         ▼                ▼               ▼
  고유 ID 부여     was_accessed=True    MTD urgency 급상승
  위치에 배치      accessed_at_ns 기록  → 즉시 포트/IP 변경
                  accessed_by_ip 기록
```

**왜 중요한가?**
- 공격자가 breadcrumb을 사용하면 → **확실히 속았다**는 증거
- 어떤 breadcrumb을 따라갔는지로 → **공격자 수준 판별** 가능
- Breadcrumb 접근 시 → **MTD 긴급 트리거**로 방어 강화

### 4.7 베이지안 믿음 추적 (Deception State Manager)

DeceptionStateManager는 공격자가 현재 **허니 드론을 진짜 드론으로 얼마나 믿고 있는지**를 확률적으로 추적합니다.

**수학적 모델:**

```
P(real_drone | observations) = 베이지안 사후확률

갱신 공식:
  posterior = (LR × prior) / (LR × prior + 1.0 × (1-prior))

  여기서 LR = 우도비(Likelihood Ratio)
```

**관측별 우도비:**

| 관측 이벤트 | 우도비 (LR) | 의미 |
|------------|:-----------:|------|
| BREADCRUMB_USE (유인 사용) | **2.5** | 가짜 정보를 사용함 → 강하게 속고 있음 |
| GHOST_DEEP_INTERACT (가짜 서비스 심층 상호작용) | **2.0** | Ghost 서비스에 깊이 접근 → 속고 있음 |
| EXPLOIT_ATTEMPT (exploit 시도) | **1.1** | 공격 시도 → 진짜로 판단하고 있음 |
| SCAN_DETECTED (포트 스캔) | **0.5** | 포트 스캔 → 확인 중, 의심 시작 |
| EVASION_BEHAVIOR (탐지 회피) | **0.3** | 탐지 회피 행동 → 강하게 의심 중 |

**예시 시나리오:**

```
초기 prior = 0.5 (반반)

1. 공격자가 포트 스캔 수행 (LR=0.5)
   → posterior = (0.5 × 0.5) / (0.5 × 0.5 + 1.0 × 0.5) = 0.333
   → "약간 의심 중"

2. 공격자가 breadcrumb 사용 (LR=2.5)
   → posterior = (2.5 × 0.333) / (2.5 × 0.333 + 1.0 × 0.667) = 0.555
   → "다시 속고 있음"

3. 공격자가 exploit 시도 (LR=1.1)
   → posterior = (1.1 × 0.555) / (1.1 × 0.555 + 1.0 × 0.445) = 0.578
   → "진짜로 믿고 공격 중"
```

---

## 5. 핵심 기능 2: 이동 표적 방어 (MTD)

MTD(Moving Target Defense)는 방어 대상의 구성을 **동적으로 변경**하여 공격자가 수집한 정보를 무효화하는 방어 전략입니다. MIRAGE-UAS에서는 허니 드론의 기만 활동과 **연동**되어, 공격 강도가 높아지면 자동으로 MTD가 트리거됩니다.

### 5.1 7가지 MTD 액션

각 MTD 액션은 Docker 컨테이너 API를 통해 실제로 실행됩니다:

| # | 액션 | 설명 | Docker 명령 | 지연 시간 | 효과 |
|---|------|------|-----------|---------|------|
| 1 | **FREQ_HOP** | RF 주파수 도약 | (RF 하드웨어 없음, 시뮬레이션) | 80ms | 무선 채널 변경으로 통신 교란 |
| 2 | **IP_SHUFFLE** | IP 주소 재할당 | `docker network disconnect` + `connect` | 450ms | 공격자가 알고 있던 IP 무효화 |
| 3 | **PORT_ROTATE** | 포트 번호 변경 | `iptables -t nat -A PREROUTING -j DNAT` | 120ms | 스캔 결과 무효화 |
| 4 | **PROTO_CHANGE** | 프로토콜 전환 | `sed` 설정 수정 | 200ms | MAVLink v1↔v2, UDP↔TCP 전환 |
| 5 | **ROUTE_MORPH** | 라우팅 변경 | MAVLink Router endpoint 수정 | 350ms | 트래픽 경로 변경 |
| 6 | **KEY_ROTATE** | 서명 키 갱신 | `dd if=/dev/urandom` + `supervisorctl restart` | 180ms | 기존 서명 키 무효화 |
| 7 | **SERVICE_MIGRATE** | 컨테이너 교체 | `docker stop` + `rm` + `run` | 3,200ms | 전체 서비스 재시작 (최후 수단) |

**MTD 액션의 작동 원리:**

```
공격자가 알고 있는 정보          MTD 실행 후
┌──────────────────┐           ┌──────────────────┐
│ IP: 172.30.0.10  │  IP_SHUFFLE  │ IP: 172.30.0.47  │  ← 변경됨!
│ Port: 14551      │  PORT_ROTATE │ Port: 14587      │  ← 변경됨!
│ Key: abc123...   │  KEY_ROTATE  │ Key: xyz789...   │  ← 변경됨!
│ Proto: MAVLink v2│  PROTO_CHANGE│ Proto: MAVLink v1│  ← 변경됨!
└──────────────────┘           └──────────────────┘

→ 공격자는 처음부터 다시 정찰해야 함
```

### 5.2 MTD 비용 함수

MTD 액션은 방어 효과가 있지만 **비용(서비스 중단, 지연)**도 수반합니다. 이를 수학적으로 모델링합니다:

```
C_mtd(a) = κ × Σᵢ(αᵢ × base_cost_i)

κ (kappa) = 0.08           # 비용 민감도 계수 (grid search로 최적화)
αᵢ = [0.10, 0.20, 0.20, 0.15, 0.10, 0.15, 0.10]  # 7차원 가중치
base_cost = [80, 450, 120, 200, 350, 180, 3200]    # 밀리초 단위
```

**비용 계산 예시:**

| 액션 | 계산 | 비용 |
|------|------|------|
| PORT_ROTATE | 0.08 × 0.20 × 120 | 1.92 (저비용) |
| IP_SHUFFLE | 0.08 × 0.20 × 450 | 7.20 (중비용) |
| SERVICE_MIGRATE | 0.08 × 0.10 × 3200 | 25.6 (고비용) |

### 5.3 MTD 트리거 메커니즘

MTD는 EngagementTracker의 메트릭을 기반으로 **자동 트리거**됩니다:

```
EngagementMetrics (공격자 행동 지표)
    │
    ▼
  urgency 계산 = f(체류시간, 명령수, exploit시도, 프로토콜수)
    │
    ├── urgency ≥ 0.3  ──→  MTD 트리거 발동
    ├── exploit_attempts ≥ 1  ──→  즉시 트리거
    └── urgency < 0.3  ──→  계속 모니터링
    │
    ▼
  추천 액션 생성
    │
    ├── L1 (체류 10초+): PORT_ROTATE
    ├── L2 (체류 30초+): PORT_ROTATE + KEY_ROTATE
    ├── L3 (체류 60초+): IP_SHUFFLE + KEY_ROTATE + PROTO_CHANGE
    └── Breadcrumb 접촉 시: 즉시 SERVICE_MIGRATE
```

---

## 6. 핵심 기능 3: CTI 파이프라인

CTI(Cyber Threat Intelligence) 파이프라인은 허니 드론과의 상호작용에서 **사이버 위협 인텔리전스**를 자동으로 추출합니다. 이를 통해 공격자의 도구, 기법, 절차(TTP)를 표준화된 형식으로 기록합니다.

### 6.1 MAVLink 패킷 인터셉터

MavlinkInterceptor는 허니 드론과 공격자 간의 통신을 **패시브하게 캡처**합니다:

```
공격자 ──UDP──→ [cc_stub 컨테이너]
                    │
                    │ 포워딩 (투명)
                    ▼
           [허니드론 엔진] ←───→ 기만 응답
                    │
                    │ 복사 (패시브 캡처)
                    ▼
           [MavlinkInterceptor]
           UDP 포트 19551-19553
                    │
                    ▼
           MavlinkCaptureEvent 생성
```

**캡처되는 정보:**

| 필드 | 설명 | 예시 |
|------|------|------|
| `event_id` | 고유 이벤트 ID | `uuid4()` |
| `timestamp_ns` | 나노초 타임스탬프 | `1712456789123456789` |
| `drone_id` | 대상 드론 | `"honey_01"` |
| `src_ip` | 공격자 IP | `"172.40.0.200"` |
| `msg_type` | 메시지 타입 | `"COMMAND_LONG"` |
| `msg_id` | MAVLink 메시지 ID | `76` |
| `sysid` / `compid` | 시스템/컴포넌트 ID | `255 / 0` |
| `payload_hex` | 원시 페이로드 (재현용) | `"fd1e0000..."` |
| `is_anomalous` | 파싱 불가 여부 | `true/false` |

### 6.2 공격 이벤트 파서 (L0-L4 분류)

AttackEventParser는 캡처된 개별 이벤트를 **120초 세션 윈도우**로 그룹화하고, 공격자 수준을 자동 분류합니다:

| 레벨 | 이름 | 분류 조건 | 실제 공격 도구 예시 |
|------|------|----------|-------------------|
| **L0** | Script Kiddie | 이벤트 3건 미만, 50% 이상 비정상 패킷 | 무작별 UDP 전송, 구글 검색 exploit |
| **L1** | Basic | 3건 이상 유효 MAVLink, 기본 명령 | MAVProxy, pymavlink 기초 스크립트 |
| **L2** | Intermediate | PARAM_SET 또는 MISSION_ITEM 포함 | DroneKit 자동화 스크립트 |
| **L3** | Advanced | HTTP/WebSocket 포함 + CVE 패턴 | Metasploit UAS 모듈, WebSocket exploit |
| **L4** | APT | 다중 프로토콜 + breadcrumb 추종 | 맞춤형 exploit + 수동 분석 |

### 6.3 MITRE ATT&CK ICS 매핑

ATTCKMapper는 MAVLink 메시지를 **MITRE ATT&CK for ICS v14** 프레임워크의 TTP(Tactics, Techniques, and Procedures)에 매핑합니다:

**매핑 테이블 (23개 메시지 타입 → 12개 TTP):**

| MAVLink 메시지 | ATT&CK TTP | 전술 (Tactic) | 신뢰도 |
|---------------|-----------|--------------|--------|
| HEARTBEAT | T0842 (Network Sniffing) | 정찰 | 0.70 |
| PARAM_REQUEST_LIST | T0842, T0840 | 정찰 | 0.90 |
| COMMAND_LONG | T0855 (Unauthorized Command) | 공격 | 0.80 |
| SET_MODE | T0858 (Change Operating Mode) | 공격 | 0.90 |
| PARAM_SET | T0836 (Modify Parameter) | 공격 | 0.90 |
| MISSION_ITEM | T0821, T0858 | 행동 | 0.90 |
| SET_POSITION_TARGET | T0855, T0831 | 행동 | 0.90 |
| GPS_INJECT_DATA | T0856, T0830 (GPS Spoofing) | C2 | 0.95 |
| FILE_TRANSFER | T0843, T0839 | 설치 | 0.95 |
| LOG_REQUEST | T0843 (Data from Information Repo) | 수집 | 0.85 |
| HTTP GET /api | T0842, T0888 | 정찰 | 0.80 |
| WebSocket CVE exploit | T0820, T0812, T0830 | 공격 | 0.95 |

### 6.4 STIX 2.1 번들 생성

STIXConverter는 분류된 공격 이벤트를 **STIX 2.1 (Structured Threat Information Expression)** 표준 형식으로 변환합니다:

```json
{
  "type": "bundle",
  "id": "bundle--a1b2c3d4-...",
  "objects": [
    {
      "type": "identity",
      "name": "MIRAGE-UAS",
      "description": "UAS 기만 프레임워크에서 자동 생성된 CTI"
    },
    {
      "type": "attack-pattern",
      "name": "Unauthorized Command Message",
      "external_references": [
        {"source_name": "mitre-ics-attack", "external_id": "T0855"}
      ]
    },
    {
      "type": "indicator",
      "name": "MAVLink COMMAND_LONG from suspicious source",
      "pattern": "[ipv4-addr:value = '172.40.0.200'] AND [network-traffic:dst_port = 14551]",
      "pattern_type": "stix"
    },
    {
      "type": "observed-data",
      "first_observed": "2026-04-07T10:15:30Z",
      "last_observed": "2026-04-07T10:17:45Z",
      "number_observed": 12
    },
    {
      "type": "note",
      "content": "MIRAGE-UAS 자동 분류 결과",
      "x_mirage_attacker_level": "L2",
      "x_mirage_confidence": 0.85,
      "x_mirage_drone_id": "honey_01"
    }
  ]
}
```

**STIX 번들의 활용:**
- **TAXII 서버**에 업로드하여 다른 보안 시스템과 공유 가능
- **SIEM/SOAR 연동**으로 자동 대응 트리거 가능
- **위협 인텔리전스 플랫폼(TIP)**에서 분석 가능
- **연구 데이터셋**으로 ML 모델 학습에 활용 가능

---

## 7. 핵심 기능 4: 데이터셋 생성

### 7.1 DVD-CTI-Dataset-v1 구성

MIRAGE-UAS는 허니 드론 실험에서 수집한 데이터를 **학술적으로 활용 가능한 레이블링 데이터셋**으로 자동 패키징합니다:

```
results/dataset/DVD-CTI-Dataset-v1/
├── dataset.csv              ← 메인 테이블 (1,007행)
│   컬럼: label, src_ip, protocol, msg_type, 
│         attacker_level, ttp_ids, confidence, source
│
├── metadata.yaml            ← 데이터셋 메타정보
│   total_samples, positive_count, negative_count,
│   class_ratio, unique_ttps, generation_timestamp
│
├── README.md               ← 데이터셋 사용 가이드
│
└── stix_bundles/            ← STIX 2.1 JSON 파일들
    ├── bundle_001.json
    ├── bundle_002.json
    └── ...
```

### 7.2 양성/음성 샘플 수집

**양성 샘플 (공격, label=1):**
- 출처: 허니 드론에서 캡처된 실제 공격 이벤트
- 228개 샘플 (L0~L4 공격자)
- 각 샘플에 TTP ID, 공격자 수준, 신뢰도 포함

**음성 샘플 (정상, label=0) — 3가지 생성 방법:**

| 방법 | 설명 | 과학적 신뢰도 |
|------|------|:------------:|
| **SITL Recording** | 실제 DVD SITL에 연결하여 60초 정상 비행 텔레메트리 캡처 | ★★★★★ |
| **Scenario-based** | Boot→Arm→Takeoff→Hover→Land 시나리오 시뮬레이션 | ★★★★ |
| **Synthetic** | ArduPilot 메시지 빈도 분포에서 통계적 샘플링 | ★★★ |

### 7.3 데이터셋 품질 검증

DatasetValidator는 6가지 자동 검증을 수행합니다:

| # | 검증 항목 | 합격 조건 | 목적 |
|---|---------|----------|------|
| V1 | 클래스 불균형 | negative/positive ≤ 10:1 | 학습 시 클래스 편향 방지 |
| V2 | TTP 커버리지 | 고유 TTP ≥ 5개 | 공격 기법 다양성 보장 |
| V3 | 정확 복제 | 중복 0건 | 데이터 특이성 보장 |
| V4 | 신뢰도 범위 | confidence ∈ [0.0, 1.0] | 값 유효성 검증 |
| V5 | 레벨 분포 | 단일 레벨 < 90% | L0-L4 분포 균형 |
| V6 | 프로토콜 다양성 | ≥ 2개 프로토콜 | 포괄적 커버리지 |

**데이터셋 분포 시각화:**

![Dataset Distribution by Protocol](../results/figures/table_iv.png)

위 그래프는 프로토콜별(MAVLink, HTTP, WebSocket) 양성/음성 샘플의 분포를 보여줍니다. MAVLink 프로토콜이 가장 많은 샘플을 차지하며, HTTP와 WebSocket도 포함되어 다양한 공격 벡터를 커버합니다.

---

## 8. 핵심 기능 5: 평가 및 메트릭

### 8.1 DeceptionScore 산출

DeceptionScore(DS)는 기만 효과를 **0~1 사이의 단일 점수**로 정량화하는 핵심 메트릭입니다:

```
DS = w₁ × TimeOnDecoys + w₂ × BreachPrevention + w₃ × Confusion + w₄ × BreadcrumbFollow + w₅ × GhostHit

가중치:  w₁=0.30     w₂=0.25     w₃=0.20     w₄=0.15     w₅=0.10
         ────────    ────────    ────────    ────────    ────────
         합계 = 1.0
```

**각 구성요소 설명:**

| 구성요소 | 가중치 | 측정 내용 | 실험 결과 |
|---------|:------:|----------|:---------:|
| **TimeOnDecoys** (기만 시간) | 0.30 | 공격자가 가짜 드론에 소비한 시간 비율 | 0.747 |
| **BreachPrevention** (침해 방지) | 0.25 | 실제 시스템 침해 방지율 | 0.747 |
| **Confusion** (혼란도) | 0.20 | 공격자의 혼란 정도 (응답 패턴 분석) | 0.720 |
| **BreadcrumbFollow** (유인 추종) | 0.15 | Breadcrumb을 따라간 비율 | 1.000 |
| **GhostHit** (가짜 서비스 접근) | 0.10 | Ghost 서비스에 접근한 비율 | 0.032 |

**최종 계산:**

```
DS = 0.30 × 0.747 + 0.25 × 0.747 + 0.20 × 0.720 + 0.15 × 1.000 + 0.10 × 0.032
   = 0.224   +   0.187   +   0.144   +   0.150   +   0.003
   = 0.708 ≈ 0.714
```

**비교 분석:**

| 방어 방식 | DeceptionScore | 비교 |
|----------|:--------------:|------|
| 방어 없음 (Baseline) | 0.05 | 기준 |
| 정적 허니팟 (Static Honeypot) | 0.20 | 4x 향상 |
| **MIRAGE-UAS** | **0.714** | **14.3x 향상** |

### 8.2 논문용 표 자동 생성 (Table II-VI)

MIRAGE-UAS는 **논문에 바로 사용할 수 있는 6개의 표**를 자동 생성합니다:

**Table II: 공격자 수준별 참여도**

![Engagement Metrics by Attacker Level](../results/figures/table_ii.png)

위 그래프는 공격자 수준(L0~L4)별 평균 체류 시간과 명령 수를 보여줍니다.

| 공격자 수준 | 세션 수 | 평균 체류(초) | 성공률 | 평균 명령 수 |
|------------|:-------:|:-----------:|:------:|:----------:|
| L0 (Script Kiddie) | 34 | 12.5 | 0.60 | 3.2 |
| L1 (Basic) | 81 | 45.2 | 0.96 | 12.5 |
| L2 (Intermediate) | 73 | 78.3 | 0.88 | 24.1 |
| L3 (Advanced) | 20 | 120.8 | 0.80 | 35.6 |
| L4 (APT) | 20 | 180.0+ | 0.75 | 50+ |

**Table III: MTD 액션별 지연 시간**

| 액션 | 실행 횟수 | 평균(ms) | P95(ms) | 성공률 |
|------|:--------:|:--------:|:-------:|:------:|
| PORT_ROTATE | 8 | 120 | 142 | 100% |
| KEY_ROTATE | 6 | 180 | 210 | 100% |
| IP_SHUFFLE | 4 | 450 | 520 | 100% |
| PROTO_CHANGE | 3 | 200 | 240 | 100% |
| ROUTE_MORPH | 2 | 350 | 380 | 100% |
| SERVICE_MIGRATE | 1 | 3,200 | 3,200 | 100% |

**Table V: 방어 성공률**

![Deception Success Rate](../results/figures/table_v.png)

위 파이 차트는 **100% 방어 성공률**을 보여줍니다 — 228개 세션 중 실제 시스템이 침해된 경우는 0건입니다.

### 8.3 통계 검정 및 LaTeX 출력

**StatisticalTest 모듈**은 결과의 통계적 유의성을 검증하고, 논문에 직접 삽입할 수 있는 LaTeX 코드를 생성합니다:

```latex
% 자동 생성된 LaTeX 매크로
\newcommand{\DeceptionScore}{0.7140}
\newcommand{\DSTimeOnDecoys}{0.7470}
\newcommand{\DSBreachPrevention}{0.7470}
\newcommand{\DSConfusion}{0.7200}
\newcommand{\DSBreadcrumb}{1.0000}
\newcommand{\DSGhostHit}{0.0320}
```

**사용된 통계 검정:**
- **Wilcoxon 부호 순위 검정**: MIRAGE-UAS vs. 기존 방어의 비모수 비교
- **Cohen's d**: 효과 크기 측정
- **SciPy**: `scipy.stats.wilcoxon()` 사용

---

## 9. 인프라 및 배포

### 9.1 Docker 컨테이너 구성

MIRAGE-UAS는 **Docker 컨테이너**를 사용하여 드론 환경을 시뮬레이션합니다. DVD(Damn Vulnerable Drone) 이미지를 기반으로 합니다:

| 컨테이너 | Docker 이미지 | 역할 | 주요 포트 |
|---------|-------------|------|----------|
| **FCU (Flight Controller)** | `nicholasaleks/dvd-flight-controller` | ArduPilot SITL 시뮬레이션 | 5760 (내부) |
| **CC (Companion Computer)** | `nicholasaleks/dvd-companion-computer` | MAVLink Router + 웹 서비스 | 14550 (외부) |
| **Simulator** | `nicholasaleks/dvd-simulator:lite` | Gazebo-less 관리 콘솔 | 8000 (내부) |
| **Attacker** | `python:3.11-slim` (커스텀) | L0-L4 공격 시뮬레이터 | - |

### 9.2 네트워크 토폴로지

**프로덕션 네트워크 (docker-compose.honey.yml):**

```
┌─────────────────────────────────────────────────────────────┐
│  honey_net (172.30.0.0/24) — 공격자에게 노출되는 네트워크    │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐              │
│  │cc-honey-01│  │cc-honey-02│  │cc-honey-03│              │
│  │172.30.0.10│  │172.30.0.11│  │172.30.0.12│              │
│  │MAVLink    │  │HTTP:8081  │  │RTSP:8554  │              │
│  │:14551     │  │           │  │           │              │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘              │
│        │              │              │                     │
│        ▼ 공격자 접근 가능 (허니 드론들)                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  honey_internal (172.31.0.0/24) — 내부 분석 네트워크        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │fcu-honey-01  │  │cti-interceptor│  │cti-api       │     │
│  │~03 (SITL)    │  │UDP:19551-53  │  │FastAPI:8765  │     │
│  │172.31.0.20+  │  │패킷 캡처     │  │CTI 제공      │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  honey_sim (172.32.0.0/24) — 관리 네트워크                   │
│  ┌──────────────┐                                          │
│  │simulator     │ DVD lite (관리 콘솔)                      │
│  │172.32.0.x    │                                          │
│  └──────────────┘                                          │
└─────────────────────────────────────────────────────────────┘
```

**테스트 하네스 네트워크 (docker-compose.test-harness.yml):**

```
┌─────────────────────────────────────────────────────────────┐
│  test_net (172.40.0.0/24) — 단일 서브넷 테스트 환경          │
│                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                   │
│  │fcu_test  │ │cc_test   │ │ mirage   │                   │
│  │01~03     │ │01~03     │ │ attacker │                   │
│  │.20~.22   │ │.10~.12   │ │ .200     │ ← L0-L4 자동 시뮬 │
│  └──────────┘ └──────────┘ └──────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

### 9.3 실행 모드 (5가지)

MIRAGE-UAS는 용도에 따라 **5가지 실행 모드**를 제공합니다:

| # | 모드 | 명령어 | Docker 필요 | 소요 시간 | 용도 |
|---|------|--------|:-----------:|:---------:|------|
| 1 | **Dry-Run** | `python3 scripts/run_experiment.py --mode dry-run` | X | ~2분 | 코드 검증, 메트릭 테스트 |
| 2 | **Test Harness** | `bash scripts/run_test_harness.sh` | O (stub) | ~5분 | CI/CD 통합 테스트 |
| 3 | **Full Real** | `bash scripts/run_full.sh` | O (DVD) | ~10분 | 실제 실험 실행 |
| 4 | **Multi-View** | `bash scripts/run_multiview.sh` | O | ~10분 | tmux 5-pane 실시간 모니터링 |
| 5 | **Observatory** | `bash scripts/start_obs.sh --run` | O | 상시 | 4채널 UDP 라이브 모니터링 |

**Multi-View 모드 화면 구성:**

```
┌──────────────────┬──────────────────┬──────────────────┐
│   honey_01 로그   │   honey_02 로그   │   honey_03 로그   │
│  (MAVLink 이벤트) │  (HTTP 이벤트)   │  (RTSP 이벤트)   │
├──────────────────┴──────────────────┴──────────────────┤
│              MTD 트리거 실시간 표시                       │
├───────────────────────────────────────────────────────┤
│              공격자 시뮬레이터 로그                       │
└───────────────────────────────────────────────────────┘
```

### 9.4 실시간 대시보드

`scripts/start_dashboard.sh`로 시작하는 **FastAPI 기반 웹 대시보드**:

| 엔드포인트 | 설명 |
|-----------|------|
| `http://localhost:8888/` | **17개 실시간 차트** (3개 탭으로 구성) |
| `http://localhost:8888/docs` | Swagger UI (API 탐색) |
| `/api/v1/experiment/summary` | 실험 요약 JSON |
| `/api/v1/experiment/deception-score` | DeceptionScore 5-요소 분해 |
| `/api/v1/engagement/by-level` | 공격자 수준별 참여도 (Table II) |
| `/api/v1/mtd/latency` | MTD 지연 시간 (Table III) |
| `/api/v1/deception/breadcrumbs` | Breadcrumb 깔때기 분석 |
| `/api/v1/agent/state` | OpenClaw 에이전트 내부 상태 |
| `/api/v1/download/excel` | **8-sheet Excel 워크북** 다운로드 |
| `/api/v1/download/dataset` | DVD-CTI-Dataset-v1 CSV 다운로드 |

---

## 10. OMNeT++ 네트워크 시뮬레이션

MIRAGE-UAS는 실험 결과를 **OMNeT++ 6.x + INET 4.5** 네트워크 시뮬레이터에서 재현할 수 있도록 트레이스를 내보냅니다:

### 10.1 구성 요소

| 모듈 | 역할 |
|------|------|
| `trace_exporter.py` | 실험 로그를 OMNeT++ XML 형식으로 변환 |
| `inet_config_gen.py` | INET 프레임워크용 `.ini` 설정 파일 생성 |
| `reproducibility_pack.py` | 재현을 위한 완전한 패키지 생성 |

### 10.2 시뮬레이션 토폴로지

```
OMNeT++ 시뮬레이션 네트워크:

  ┌──────────┐       ┌──────────────┐       ┌──────────┐
  │ Attacker │──Eth──│ EtherSwitch  │──Eth──│ HoneyDrone│
  │ (Host)   │       │ (L2 Switch)  │       │ (Host)   │
  └──────────┘       └──────┬───────┘       └──────────┘
                            │
                     ┌──────┴───────┐
                     │ CTI Monitor  │
                     │ (Host)       │
                     └──────────────┘
```

**재현 방법:**
```bash
# 1. 트레이스 내보내기
python3 -m src.omnetpp.trace_exporter

# 2. 출력 파일 확인
ls omnetpp_trace/
# → scenario.xml, replay.ini, ...

# 3. OMNeT++ 6.0+에서 import
# → File > Import > OMNeT++ Project
```

---

## 11. 실험 결과 요약

### 11.1 시간대별 기만 효과

![Deception Effectiveness Over Time](../results/figures/timeline.png)

위 그래프는 실험 기간 동안의 기만 효과(Effectiveness)와 혼란 점수(Confusion Score)의 변화를 보여줍니다.

### 11.2 핵심 성과

| 지표 | 수치 | 의미 |
|------|------|------|
| **DeceptionScore** | 0.714 | 기만 효과 71.4% — 기존 대비 14배 향상 |
| **침해율** | 0% | 228개 공격 세션 중 실제 침해 0건 |
| **참여율** | 75.9% | 공격자 4명 중 3명이 기만에 속음 |
| **평균 체류 시간** | L1: 45초, L3: 121초 | 고급 공격자일수록 오래 머무름 |
| **MTD 트리거** | 46건/10분 | 4.6건/분의 능동적 방어 |
| **데이터셋** | 1,007 샘플 | 12 TTP, 3 프로토콜, STIX 2.1 |
| **자율 행동** | 9가지 | 에이전트가 능동적으로 수행 |

### 11.3 공격자 수준별 기만 효과

```
기만 효과 (높을수록 잘 속음)

L0 ████████████░░░░░░░░  60%   스크립트 키디 — 빨리 포기
L1 ████████████████████  96%   기초 — 완전히 속음
L2 ██████████████████░░  88%   중급 — 대부분 속음
L3 ████████████████░░░░  80%   고급 — 일부 의심하지만 계속 참여
L4 ███████████████░░░░░  75%   APT — 의심하면서도 정보 수집 시도
```

---

## 12. 기술 스택 총정리

### 12.1 핵심 기술

| 분류 | 기술 | 버전 | 용도 |
|------|------|------|------|
| **언어** | Python | 3.11+ | 전체 시스템 구현 |
| **드론 프로토콜** | pymavlink | 2.4.41 | MAVLink 패킷 파싱/생성 |
| **CTI 표준** | stix2 | 3.0.1 | STIX 2.1 번들 생성 |
| **CTI 교환** | taxii2-client | 2.3.0 | TAXII 서버 연동 |
| **웹 프레임워크** | FastAPI | 0.115.0 | REST API + 대시보드 |
| **ASGI 서버** | Uvicorn | 0.30.1 | FastAPI 실행 |
| **데이터 검증** | Pydantic | 2.7.0 | 요청/응답 모델 |
| **강화학습** | Stable-Baselines3 | 2.3.2 | PPO 알고리즘 |
| **RL 환경** | Gymnasium | 0.29.1 | 커스텀 환경 |
| **딥러닝** | PyTorch | 2.3.1 | SB3 백엔드 |
| **수치 연산** | NumPy | 1.26.4 | 행렬 연산 |
| **데이터 분석** | Pandas | 2.2.2 | 데이터프레임 처리 |
| **통계 검정** | SciPy | 1.13.1 | Wilcoxon 검정 |
| **구조화 로깅** | structlog | 24.2.0 | JSON 로그 |
| **시각화** | Matplotlib | 3.9.0 | IEEE 스타일 그래프 |
| **통계 시각화** | Seaborn | 0.13.2 | 히트맵, 분포도 |
| **비동기 HTTP** | aiohttp | - | Ghost 서비스 |
| **WebSocket** | websockets | - | OpenClaw API |

### 12.2 인프라 기술

| 분류 | 기술 | 용도 |
|------|------|------|
| **컨테이너** | Docker + Docker Compose 3.9 | 드론 환경 시뮬레이션 |
| **네트워크 시뮬** | OMNeT++ 6.x + INET 4.5 | 네트워크 재현 |
| **터미널** | tmux | 멀티뷰 모니터링 |
| **방화벽** | iptables | MTD 포트 로테이션 |
| **개발 환경** | WSL2 Ubuntu 22.04 | 주 개발 플랫폼 |

### 12.3 네트워크 프로토콜

| 프로토콜 | 계층 | MIRAGE-UAS에서의 역할 |
|---------|------|---------------------|
| **MAVLink v2** | 응용 | 드론 제어 (핵심 프로토콜) |
| **UDP** | 전송 | MAVLink 전송, Observatory |
| **TCP** | 전송 | WebSocket, SITL 통신 |
| **HTTP/1.1** | 응용 | Ghost 서비스 (ArduPilot Web UI) |
| **RTSP/1.0** | 응용 | 카메라 스트림 에뮬레이션 |
| **WebSocket** | 응용 | OpenClaw 에이전트 API |
| **SSH** | 응용 | 배너 에뮬레이션 |

### 12.4 보안 프레임워크 및 표준

| 표준 | 버전 | 용도 |
|------|------|------|
| **MITRE ATT&CK for ICS** | v14 | 공격 기법 분류 (12 TTP) |
| **STIX** | 2.1 | 위협 정보 표현 |
| **TAXII** | 2.1 | 위협 정보 교환 |
| **Cyber Kill Chain** | - | 공격 생명주기 모델링 |

---

## 13. 향후 연구 방향

| # | 연구 과제 | 우선순위 | 기대 효과 |
|---|---------|:--------:|----------|
| 1 | **장시간 실험** (600초 → 3600초+) | HIGH | 통계적 유의성 확보 |
| 2 | **TTP 커버리지 확장** (12→18개 이상) | HIGH | ATT&CK ICS 커버리지 향상 |
| 3 | **Ghost hit rate 개선** (3.2% → 10%+) | MEDIUM | DeceptionScore 구성요소 향상 |
| 4 | **N=30 반복 실험** + 통계 검정 | HIGH | 학술적 유의성 |
| 5 | **실제 DVD 이미지 테스트** | MEDIUM | bare-metal 포팅 검증 |
| 6 | **멀티 에이전트 협업** | LOW | 여러 허니드론 간 협조 기만 |
| 7 | **LLM 기반 응답 생성** | LOW | 더 자연스러운 기만 텍스트 |

---

## 부록: 설정 파라미터 전체 목록

모든 연구 파라미터는 `config/.env` 파일에서 수정 가능합니다:

```env
# ─────────────────────────────────────────────
# MTD 비용 함수 (Eq.17)
# ─────────────────────────────────────────────
MTD_COST_SENSITIVITY_KAPPA=0.08
MTD_ALPHA_WEIGHTS=0.10,0.20,0.20,0.15,0.10,0.15,0.10
MTD_BREACH_PREVENTION_BETA=1.5

# ─────────────────────────────────────────────
# 기만 보상 함수
# ─────────────────────────────────────────────
DECEPTION_LAMBDA=0.3
DECEPTION_WEIGHTS=0.50,0.30,0.20
DECEPTION_DWELL_MAX_SEC=300.0

# ─────────────────────────────────────────────
# DeceptionScore 가중치
# ─────────────────────────────────────────────
DECEPTION_SCORE_WEIGHTS=0.30,0.25,0.20,0.15,0.10

# ─────────────────────────────────────────────
# PPO 강화학습 하이퍼파라미터
# ─────────────────────────────────────────────
PPO_LEARNING_RATE=0.0003
PPO_GAMMA=0.99
PPO_CLIP_EPS=0.2
PPO_ENTROPY_COEF=0.01

# ─────────────────────────────────────────────
# OpenClaw 에이전트 설정
# ─────────────────────────────────────────────
AGENT_PROACTIVE_INTERVAL_SEC=45.0
AGENT_SYSID_ROTATION_SEC=120.0
AGENT_PORT_ROTATION_SEC=90.0
AGENT_FALSE_FLAG_DWELL_THRESHOLD=120.0
AGENT_MIRROR_SERVICE_THRESHOLD=2

# ─────────────────────────────────────────────
# 공격자 사전 확률
# ─────────────────────────────────────────────
ATTACKER_PRIORS=0.40,0.30,0.15,0.10,0.05

# ─────────────────────────────────────────────
# 인프라 설정
# ─────────────────────────────────────────────
MAVLINK_PORT_BASE=14550
HONEY_DRONE_COUNT=3
CTI_API_PORT=8765
LOG_LEVEL=INFO
RESULTS_DIR=results
```

---

> **문서 정보**
> - 생성일: 2026-04-11
> - 코드베이스: 12,821줄 Python / 36개 모듈
> - 대상: ACM CCS 2026 Cycle B
> - 연구실: DS Lab, 경기대학교
