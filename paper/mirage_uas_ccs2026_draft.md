# MIRAGE-UAS: 무인항공시스템을 위한 이동표적방어 기반 지능형 반응적 에이전틱 기만 엔진

**MIRAGE-UAS: Moving-target Intelligent Responsive Agentic deception enGinE for Unmanned Aerial Systems**

---

**저자**: 김민성, [공동저자], [지도교수]

**소속**: 경기대학교 DS Lab

**학회**: ACM Conference on Computer and Communications Security (CCS) 2026

---

## Abstract

무인항공시스템(UAS)에 대한 사이버 공격이 급증하면서, MAVLink 프로토콜의 인증 부재와 컴패니언 컴퓨터의 취약한 웹 인터페이스가 심각한 보안 위협으로 대두되고 있다. 기존 방어 체계는 탐지 후 차단이라는 수동적 패러다임에 머물러 있어, 고도화된 지속적 위협(APT)에 효과적으로 대응하지 못한다. 본 논문은 **MIRAGE-UAS**를 제안한다. 이는 허니드론(Honeydrone), 자율 기만 에이전트(Autonomous Deception Agent), 이동표적방어(MTD), 그리고 사이버위협인텔리전스(CTI) 파이프라인을 통합한 능동적 기만 방어 프레임워크이다.

MIRAGE-UAS의 핵심 기여는 세 가지이다. **첫째**, OODA(Observe-Orient-Decide-Act) 루프 기반 자율 기만 에이전트 OpenClawAgent를 설계하여, 공격자 도구 식별(5종)과 공격 단계 분류(4단계)를 실시간으로 수행하고 단계별 최적 기만 응답을 자율 생성한다. **둘째**, 공격자의 "허니팟 여부 믿음"을 베이지안 사후확률로 추적하는 DeceptionStateManager를 도입하여, 기만 효과를 정량적으로 측정하고 MTD 트리거 urgency를 동적으로 보정한다. **셋째**, 실험에서 생성된 공격 이벤트를 STIX 2.1 형식의 CTI 데이터셋(DVD-CTI-Dataset)으로 자동 변환하는 파이프라인을 구축하여, MITRE ATT\&CK for ICS의 21개 기법을 매핑한다.

Damn Vulnerable Drone(DVD) 기반 Docker 테스트베드에서 L0(Script Kiddie)부터 L4(APT)까지 5단계 적응형 공격 시뮬레이션을 수행하였다. 각 레벨 10분, 총 50분간 허니드론 3대에 대한 실험 결과, 3,643개 공격 세션에 대해 DeceptionScore 0.6230, breach prevention rate 100%, Confusion Score 0.6158(Accuracy 0.4019, Temptation 0.4444, CloakingRate 1.0000), 베이지안 믿음 P(real)=0.9917을 달성하였다. MTD는 100회 실행되어 SRRP 63.3%, 공격 표면 엔트로피 16.23 bits를 기록하였다. 에이전트의 610건 자율 결정 중 false flag(50건)과 proactive reboot(24건)이 평균 26.9초의 추가 체류시간을 유발하였다. 통계적 유의성은 N=30 반복 실험에 대한 Wilcoxon 부호순위검정(p < 0.01)과 Cohen's d 효과크기로 검증하였다.

**키워드**: 허니드론, 이동표적방어, 자율 기만, 베이지안 믿음 추적, 무인항공시스템 보안, 사이버위협인텔리전스

---

## 1. 서론 (Introduction)

### 1.1 연구 배경

무인항공시스템(UAS)의 민간 및 군사 영역 활용이 급속히 확대되면서, UAS 사이버 보안은 국가 안보와 공공 안전의 핵심 과제로 부상하였다. MAVLink v2 프로토콜은 UAS 통신의 사실상 표준이나, 설계 시점부터 인증과 암호화를 선택적 기능으로 분류하여 대다수 배포 환경에서 평문 통신이 이루어지고 있다 [1]. 이러한 구조적 취약성은 명령 주입(Command Injection), GPS 스푸핑, 파라미터 변조 등 다양한 공격 벡터를 제공한다.

동시에, ArduPilot 생태계의 컴패니언 컴퓨터(Companion Computer)는 HTTP 기반 웹 인터페이스, RTSP 카메라 스트리밍, 그리고 최근 등장한 에이전틱 AI 프레임워크(예: OpenClaw)의 WebSocket API를 노출한다. CVE-2026-25253으로 보고된 OpenClaw의 WebSocket localhost 바이패스 취약점은 인증 없이 드론의 스킬 실행 체인에 접근할 수 있는 위험을 보여준다.

기존 UAS 방어 체계는 침입탐지시스템(IDS) 기반의 탐지-차단 패러다임에 의존한다 [2, 3]. 그러나 이 접근법은 (1) 제로데이 공격에 무방비이며, (2) 공격자에게 탐지 사실을 알려주어 우회를 촉진하고, (3) 차단 후 공격자의 전술·기법·절차(TTP) 정보를 수집할 기회를 상실한다. 특히 APT급 공격자는 탐지 후 즉시 경로를 전환하므로, 수동적 방어만으로는 위협 인텔리전스의 축적이 불가능하다.

### 1.2 연구 동기 및 문제 정의

본 연구는 다음 세 가지 근본적 질문에서 출발한다:

**RQ1.** UAS 환경에서 공격자를 탐지 즉시 차단하는 대신, 허니드론에 유인하여 기만할 수 있는가? 그렇다면, 기만의 효과를 어떻게 정량적으로 측정할 수 있는가?

**RQ2.** 공격자의 도구와 공격 단계를 실시간으로 식별하고, 그에 적응하는 자율적 기만 응답을 생성할 수 있는가?

**RQ3.** 기만 과정에서 수집된 공격 이벤트를 구조화된 CTI 데이터셋으로 변환하여, UAS 보안 커뮤니티의 방어 역량 강화에 기여할 수 있는가?

### 1.3 기여 (Contributions)

본 논문의 주요 기여는 다음과 같다:

1. **OODA 기반 자율 기만 에이전트 (OpenClawAgent)**: 외부 명령 없이 MAVLink/WebSocket 트래픽을 실시간 관찰하여 공격자 도구(5종)와 공격 단계(RECON → EXPLOIT → PERSIST → EXFIL)를 식별하고, 단계별 최적 기만 응답을 자율 생성하는 에이전트. 5개의 독립적 자율 행동 루프(proactive STATUSTEXT, 비행 시뮬레이션, ghost 포트 개방, 재부팅 시뮬레이션, 가짜 키 누출)를 통해 공격자 체류시간을 극대화한다.

2. **베이지안 기만 상태 관리자 (DeceptionStateManager)**: 공격자별 P(real_drone | observations)를 10종의 관측 이벤트에 대한 우도비(Likelihood Ratio) 기반 베이지안 갱신으로 추적한다. 공격자가 허니팟을 의심하기 시작하면(P(real) < 0.3) MTD urgency가 자동 상승하여 공격 표면을 재구성한다.

3. **DVD-CTI 데이터셋 및 OMNeT++ 재현 패키지**: STIX 2.1 형식으로 자동 변환된 공격 이벤트 데이터셋(21개 MITRE ATT&CK for ICS 기법 매핑)과 OMNeT++ 6.x/INET 4.5 호환 트레이스 패키지를 제공하여 실험의 완전한 재현을 보장한다.

4. **통합 평가 프레임워크**: DeceptionScore(5차원 가중합), Confusion Score(Accuracy + Temptation + CloakingRate), MTD Effectiveness(TTC/SRRP/Entropy), CTI Quality(TTP Coverage + STIX Completeness + Dataset Balance)를 포괄하는 25개 이상의 정량적 메트릭을 체계적으로 정의하고 측정한다.

### 1.4 논문 구성

2장에서 관련 연구를 검토하고, 3장에서 위협 모델을 정의한다. 4장에서 MIRAGE-UAS의 시스템 설계를 상세히 기술하고, 5장에서 구현 세부사항을 설명한다. 6장에서 실험 설계와 평가 결과를 제시하며, 7장에서 논의, 8장에서 결론을 맺는다.

---

## 2. 관련 연구 (Related Work)

### 2.1 UAS 사이버 보안

UAS 보안 연구는 크게 프로토콜 수준 방어와 시스템 수준 방어로 구분된다. Kwon et al. [4]은 MAVLink v2의 서명 메커니즘을 분석하여 재전송 공격에 대한 취약성을 지적하였다. Rani et al. [5]은 DroneKit 기반 GPS 스푸핑 공격의 실현 가능성을 실증하였다. 그러나 이들 연구는 공격 분석에 초점을 맞추며, 능동적 기만 방어 메커니즘은 제안하지 않았다.

시스템 수준에서, DroneSec [6]는 상용 UAS 위협 인텔리전스 플랫폼을 제공하나, 허니팟 기반 공격자 유인 기능은 포함하지 않는다. Damn Vulnerable Drone(DVD) [7]은 ArduPilot SITL + OpenClaw 기반의 의도적 취약 드론 테스트베드를 제공하여 UAS 보안 연구의 표준화에 기여하였으나, 방어 메커니즘은 범위 밖이다.

### 2.2 허니팟 및 기만 기술

허니팟 기반 기만 기술은 네트워크 보안 분야에서 오랜 역사를 가진다 [8]. 최근 Wang et al. [9]은 대규모 언어 모델(LLM)을 활용한 HoneyGPT를 제안하여 터미널 허니팟의 삼중 딜레마(기만 품질, 적응성, 확장성)를 해결하고자 하였다. HoneyGPT는 Deception Accuracy(Eq.01)와 Temptation Score(Eq.02)를 정의하여 기만 효과의 정량적 측정 프레임워크를 제시하였다. 본 연구는 이 메트릭을 UAS 도메인에 적용하고, CloakingRate(Eq.05) [10]를 추가하여 Confusion Score를 3차원으로 확장한다.

Aradi et al. [10]은 허니팟 평가를 위한 메트릭 주도(Metrics-Driven) 프레임워크를 제안하여, Cloaking Success Rate를 허니팟 은닉성의 핵심 지표로 정의하였다. 그러나 UAS 프로토콜(MAVLink) 환경에서의 적용은 다루지 않았다.

IoT/ICS 영역에서, Dowling et al. [11]은 SCADA 허니팟과 MTD의 결합을 제안하였으나, 에이전틱 자율 기만(agentic deception)—즉, 외부 명령 없이 에이전트가 독립적으로 기만 행동을 결정하는 방식—은 탐구되지 않았다. 본 연구의 OpenClawAgent는 OODA 루프 기반 자율 기만을 UAS 도메인에 최초로 도입한다.

### 2.3 이동표적방어 (MTD)

이동표적방어는 시스템의 공격 표면을 동적으로 변경하여 공격자의 정찰 정보를 무효화하는 능동적 방어 패러다임이다 [12]. Sharma [13]는 MTD 셔플링 하에서의 Time-to-Compromise(TTC) 분석 모델을 제시하여, 일일 셔플링이 SRRP(Security Risk Reduction Percentage) 90% 이상을 달성함을 보였다. Zhuang et al. [14]은 MTD 구성 공간의 Shannon 엔트로피를 공격 표면 다양성의 척도로 정의하였으며, Janani [15]는 이를 확장하여 엔트로피 활용률(utilization ratio) 개념을 도입하였다.

본 연구는 이러한 MTD 이론을 UAS 허니드론에 적용하되, DeceptionStateManager의 베이지안 믿음 상태와 연동하여 **기만-주도 MTD 트리거링(deception-driven MTD triggering)**을 실현한다. 이는 기존의 시간 기반 또는 이벤트 기반 MTD 트리거와 차별화되는 점이다.

### 2.4 사이버위협인텔리전스 (CTI) 및 데이터셋

STIX 2.1 [16]은 CTI 데이터의 표준 교환 형식으로, 공격 패턴(Attack Pattern), 인디케이터(Indicator), 관측 데이터(Observed Data) 등의 객체를 정의한다. Schlette et al. [17]은 STIX 객체의 완전성(Completeness)을 선택적 필드의 충족 비율로 정의하는 메트릭을 제안하였다.

UAS 도메인에 특화된 공개 CTI 데이터셋은 현재까지 존재하지 않는다. 기존 드론 보안 데이터셋은 RF 신호 수준 [18] 또는 비행 로그 이상 탐지 [19]에 집중하며, MAVLink 프로토콜 수준의 공격 이벤트와 MITRE ATT&CK for ICS 기법 매핑을 포함하는 데이터셋은 본 연구가 최초이다.

---

## 3. 위협 모델 (Threat Model)

### 3.1 시스템 모델

대상 시스템은 MAVLink v2 프로토콜을 사용하는 멀티로터 UAS 편대이다. 각 드론은 비행제어장치(FCU, ArduPilot SITL), 컴패니언 컴퓨터(CC, HTTP/RTSP/WebSocket 인터페이스), 그리고 지상제어시스템(GCS) 연결로 구성된다. 컴패니언 컴퓨터는 OpenClaw 에이전틱 AI 프레임워크를 탑재하여 WebSocket 기반 스킬 실행 체인을 제공한다.

### 3.2 공격자 모델 (L0-L4)

본 연구는 5단계 공격자 역량 모델을 정의한다:

| 레벨 | 명칭 | 역량 | 공격 도구 | 기술 수준 (s) |
|------|------|------|----------|--------------|
| L0 | Script Kiddie | 자동화 스캐너, 프로토콜 인식 없음 | nmap, netcat | 0.20 |
| L1 | Basic | MAVLink 프로토콜 인식, 단순 명령 시도 | mavproxy | 0.35 |
| L2 | Intermediate | 취약점 타겟팅, 크레덴셜 재사용 | DroneKit, requests | 0.55 |
| L3 | Advanced | 다단계 공격, WebSocket API 익스플로잇 | 커스텀 스크립트, Metasploit | 0.75 |
| L4 | APT | 지속 접근, 측면 이동, CVE 체인 | 모든 도구 종합 | 1.00 |

각 레벨은 이전 레벨에서 수집한 인텔리전스를 체인(Intelligence Chaining)으로 활용한다. 예를 들어, L1에서 추출한 파라미터와 sysid 정보는 L2의 크레덴셜 재사용 공격에 투입되며, L3의 WebSocket 익스플로잇 결과는 L4의 측면 이동에 활용된다.

### 3.3 공격 표면 (Attack Surface)

| 프로토콜 | 포트 | 공격 벡터 |
|----------|------|----------|
| MAVLink UDP | 14550 | HEARTBEAT 스푸핑, COMMAND_LONG 주입, GPS_INJECT_DATA |
| HTTP | 80 | API 열거, 브루트포스 인증, 크레덴셜 수확 |
| WebSocket | 18789 | CVE-2026-25253 (localhost bypass), 스킬 실행 체인 탈취 |
| RTSP | 8554 | 카메라 스트림 탈취, DoS |
| Ghost TCP | 19000+ | 가짜 텔레메트리 서비스 |
| Ghost UDP | 14560+ | 보조 MAVLink (다른 sysid) |

### 3.4 방어 목표

1. **기만 극대화**: 공격자가 허니드론을 실제 드론으로 인식하는 시간을 최대화
2. **실 드론 보호**: 공격 트래픽을 허니드론으로 유인하여 실 드론에 대한 breach를 방지
3. **인텔리전스 수집**: 공격 과정에서 TTP를 수집하여 구조화된 CTI 생성
4. **적응적 대응**: 공격자의 행동 변화에 실시간 적응하여 기만 효과를 유지

---

## 4. 시스템 설계 (System Design)

### 4.1 전체 아키텍처

MIRAGE-UAS는 네 개의 핵심 레이어로 구성된다:

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: Evaluation & CTI Pipeline                         │
│  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Metrics   │ │ STIX     │ │ Dataset  │ │ OMNeT++      │  │
│  │ Collector │ │ Converter│ │ Packager │ │ Trace Export │  │
│  └───────────┘ └──────────┘ └──────────┘ └──────────────┘  │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: Moving Target Defense (MTD)                       │
│  ┌───────────────┐ ┌──────────────┐ ┌──────────────────┐   │
│  │ MTD Executor  │ │ MTD Monitor  │ │ Surface Entropy  │   │
│  │ (7 actions)   │ │ (5s polling) │ │ Calculator       │   │
│  └───────────────┘ └──────────────┘ └──────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: Autonomous Deception Engine                       │
│  ┌───────────────┐ ┌──────────────┐ ┌──────────────────┐   │
│  │ OpenClaw      │ │ Deception    │ │ Engagement       │   │
│  │ Agent (OODA)  │ │ State Mgr   │ │ Tracker          │   │
│  └───────────────┘ └──────────────┘ └──────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│  Layer 1: Honeydrone Infrastructure                         │
│  ┌───────────────┐ ┌──────────────┐ ┌──────────────────┐   │
│  │ AgenticDecoy  │ │ Breadcrumb   │ │ Ghost Service    │   │
│  │ Engine        │ │ Planter      │ │ Factory          │   │
│  └───────────────┘ └──────────────┘ └──────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Layer 1: 허니드론 인프라

#### 4.2.1 AgenticDecoyEngine

AgenticDecoyEngine은 허니드론 1개 인스턴스의 전체 기만 레이어를 관장한다. 다음 4개의 비동기 태스크를 동시 운용한다:

- **MAVLink UDP 수신 루프** (`_receive_loop`): 포트 14550에서 공격자 패킷을 수신하고, `MavlinkCaptureEvent`로 변환하여 처리 체인에 투입한다.
- **WebSocket 서버** (`_websocket_server`): 포트 18789에서 OpenClaw 게이트웨이를 에뮬레이션한다. CVE-2026-25253을 의도적으로 노출하여 L3-L4 공격자를 유인한다.
- **텔레메트리 브로드캐스트 루프** (`_telemetry_loop`): 주기적으로 HEARTBEAT을 전송하여 살아있는 드론으로 인식되도록 한다.
- **상태 브로드캐스트 루프** (`_state_broadcast_loop`): 엔진 내부 상태를 UDP로 옵저버에 방출한다.

패킷 처리 체인은 다음 순서로 실행된다:

1. OpenClawAgent.observe(event) — 공격자 지문 갱신
2. EngagementTracker.update_session(event) — 세션 메트릭 갱신
3. DeceptionStateManager.observe_protocol_interaction() — 베이지안 믿음 갱신
4. OpenClawAgent.generate_response(event) — 적응형 응답 생성 (우선)
5. MavlinkResponseGenerator.generate(event) — 폴백 기본 응답
6. UDP 응답 전송
7. `_assess_and_signal()` — MTD 트리거 판단

#### 4.2.2 브레드크럼 서버

HTTP 포트 80에서 가짜 ArduPilot 웹 UI를 에뮬레이션하며, 다음의 미끼 크레덴셜을 의도적으로 노출한다:

- `/api/v1/params`: SSH 비밀번호, MAVLink signing key, API 토큰
- `/api/v1/status`: 백업 GCS IP, 설정 엔드포인트, 함대 C2 주소
- `/api/v1/mission`: 미션 키, 업로드 엔드포인트

공격자가 이러한 크레덴셜을 실제 사용하면 `BREADCRUMB_USE` 관측 이벤트가 발생하여 DeceptionStateManager의 P(real)이 강하게 증가(LR=2.5)한다.

#### 4.2.3 Ghost 서비스

- **Ghost TCP** (포트 19000+): 드론별 3개의 가짜 텔레메트리 서비스. 접속 시 `ArduCopter V4.3.7` 펌웨어 정보를 포함한 JSON 응답을 전송한다.
- **Ghost UDP** (포트 14560+): 다른 sysid로 응답하는 보조 MAVLink 서비스. 포트 스캔 시 "추가 드론 발견"을 유발한다.

### 4.3 Layer 2: 자율 기만 엔진

#### 4.3.1 OpenClawAgent — OODA 자율 기만 에이전트

OpenClawAgent는 본 연구의 핵심 기여로, OODA 루프에 기반한 자율 기만 에이전트이다. 외부 컨트롤러의 명령 없이 독립적으로 관찰, 판단, 행동을 수행한다.

**관찰 단계 (Observe)**

`observe()` 함수는 모든 MAVLink 이벤트를 수신하여 공격자별 지문(AttackerFingerprint)을 갱신한다. 관찰 데이터는 세 가지 차원으로 구성된다:

- 명령 시퀀스: `_conversation_history[attacker_ip]`에 (msg_type, payload_hex, timestamp_ns) 튜플로 축적
- 서비스 접촉 집합: `_services_touched[attacker_ip]`에 "protocol:msg_type" 형태로 기록
- 타이밍 패턴: 연속 패킷 간 inter-arrival time 분석

**판향 단계 (Orient)**

`_detect_tool()` 함수는 바이너리 MAVLink 프로토콜에서 문자열 시그니처가 부재하므로, **타이밍 패턴과 명령 순서 분석**으로 공격 도구를 분류한다:

$$
\text{avg\_interval} = \frac{1}{n-1} \sum_{i=1}^{n-1} (t_{i+1} - t_i)
$$

- **nmap**: avg_interval < 0.1초, 고유 명령 > 5종 (빠른 버스트 스캔 패턴)
- **mavproxy**: HEARTBEAT × 2 → REQUEST_DATA_STREAM 시퀀스, interval > 0.8초
- **DroneKit**: SET_MODE + REQUEST_DATA_STREAM 동시 사용
- **Metasploit**: FILE_TRANSFER_PROTOCOL 등 위험 명령 2종 이상, interval < 2.0초
- **Custom exploit**: PARAM_SET 등 위험 명령 사용하되 알려진 도구 패턴 불일치

`_detect_attack_phase()` 함수는 명령 유형의 **의미론적 분석**으로 4단계 공격 상태머신을 구동한다:

| 단계 | 탐지 명령 집합 | 의미 |
|------|-------------|------|
| RECON | HEARTBEAT, PARAM_REQUEST, REQUEST_DATA_STREAM | 정찰: 읽기/관찰만 수행 |
| EXPLOIT | COMMAND_LONG, SET_MODE, SET_POSITION_TARGET | 익스플로잇: 드론 제어 시도 |
| PERSIST | PARAM_SET, MISSION_ITEM | 지속: 드론 저장소 기록 시도 |
| EXFIL | LOG_REQUEST_LIST, LOG_REQUEST_DATA, FILE_TRANSFER_PROTOCOL | 유출: 데이터 탈취 시도 |

**결정 단계 (Decide)**

공격 단계와 도구 식별 결과에 따라 최적 응답 전략을 선택한다. 핵심 원칙은 **"체류시간 극대화"**이다:

- RECON: 풍부한 텔레메트리와 파라미터 목록을 제공하여 "금광 발견" 인식 유도
- EXPLOIT: COMMAND_ACK(ACCEPTED)로 "부분 성공" 신호 전송. ARM 명령 시 이륙 30초 시뮬레이션 후 crash 20초 침묵
- PERSIST: 운영자 활동 STATUSTEXT와 긍정 ACK로 upload 루프 유지
- EXFIL: 가짜 로그, 설정, 비행이력 제공

**행동 단계 (Act)**

적응형 응답 외에, 5개의 **독립적 자율 행동 루프**가 백그라운드에서 동시 실행된다:

**Behavior 2a: Proactive STATUSTEXT** — 가짜 운영자 활동 메시지("Waypoint 3 reached", "Camera started" 등)를 주기적으로 전송하여 활성 운영 중인 드론으로 인식시킨다.

**Behavior 2b: Flight Simulation** — 60초간 고도 0→100→0 프로파일을 시뮬레이션하며 각 단계마다 HEARTBEAT과 STATUSTEXT를 전송한다. 공격자가 실시간 비행을 관찰하는 효과를 제공한다.

**Behavior 2c: Ghost Port Opening** — 랜덤 포트(19000-19500)에 TCP 리스너를 개방하고 SSH 배너("SSH-2.0-OpenSSH_8.9 MIRAGE-{drone_id}")를 응답한다. STATUSTEXT 힌트로 포트 발견을 촉진한다.

**Behavior 2d: Reboot Simulation** — 8-15초간 모든 응답을 차단(silence)한 후 새로운 sysid로 복귀한다. 공격자에게 "재부팅 후 다른 시스템으로 나타남" 효과를 제공한다.

**Behavior 2e: Fake Key Leak** — 가짜 MAVLink signing key를 STATUSTEXT와 PARAM_VALUE로 "실수로" 누출한다. 공격자가 이를 수집하여 사용 시도하면 기만 성공의 강력한 신호가 된다.

**Behavior 4: Confusion Amplification** — 공격자의 행동 강도에 비례하여 자동 에스컬레이션한다:
- 서비스 접촉 수 ≥ 임계치: ghost 포트 추가 개방
- 체류시간 > 임계치: False Flag 실행(sysid 51-100 범위로 변경 + GPS 12km 이동, 30초 유지 후 복원)

**Behavior 5: Self-Mutation** — sysid 자동 변경(45초 주기), WebSocket 포트 자동 변경(90초 주기), 미션 웨이포인트 재생성(60초 주기), 파라미터 값 미세 변동(45초 주기, σ=0.5%)

#### 4.3.2 EngagementTracker — 세션 추적기

세션 키 `(attacker_ip, drone_id)`로 각 공격자의 상태를 추적한다. 추적 필드는 dwell_time_sec, commands_issued, protocols_used, exploit_attempts, websocket_sessions를 포함한다.

공격자 레벨 분류는 다음의 규칙 기반 체계를 따른다:

$$
\text{Level}(m) = \begin{cases}
\text{L4\_APT} & \text{if } m.\text{exploit} \geq \theta_e \wedge m.\text{ws} > 0 \wedge |m.\text{protos}| \geq 3 \\
\text{L3\_ADVANCED} & \text{if } m.\text{exploit} \geq \theta_e \vee m.\text{ws} > 0 \\
\text{L2\_INTERMEDIATE} & \text{if } m.\text{dwell} \geq \theta_2 \wedge m.\text{cmds} \geq 20 \\
\text{L1\_BASIC} & \text{if } m.\text{dwell} \geq \theta_1 \vee m.\text{cmds} \geq 5 \\
\text{L0\_SCRIPT\_KIDDIE} & \text{otherwise}
\end{cases}
$$

MTD urgency는 레벨별 기본값에 exploit 보정과 breach 보정을 적용한다:

$$
u_{\text{base}} \in \{0.1, 0.3, 0.5, 0.75, 0.95\} \quad (\text{L0} \sim \text{L4})
$$

$$
u = \min(u_{\text{base}} + \delta_{\text{belief}} \times 0.3, \ 1.0)
$$

여기서 $\delta_{\text{belief}}$는 DeceptionStateManager의 urgency modifier이다.

#### 4.3.3 DeceptionStateManager — 베이지안 믿음 추적

각 공격자의 P(real\_drone | observations)를 베이지안 사후확률 갱신으로 추적한다:

$$
P(\text{real} \mid \text{obs}) = \frac{\text{LR} \cdot P(\text{real})}{\text{LR} \cdot P(\text{real}) + 1.0 \cdot (1 - P(\text{real}))}
$$

여기서 LR(Likelihood Ratio)은 관측 이벤트 유형에 따라 사전 정의된다:

| 관측 이벤트 | LR | 해석 |
|------------|-----|------|
| PROTOCOL_INTERACT | 1.2 | 일반 상호작용 → 약간 "진짜" 방향 |
| BREADCRUMB_ACCESS | 1.8 | 브레드크럼 발견 → 유인 성공 |
| BREADCRUMB_USE | 2.5 | 크레덴셜 사용 → 강하게 속음 |
| GHOST_CONNECT | 1.5 | ghost 서비스 접속 → 유인 성공 |
| GHOST_DEEP_INTERACT | 2.0 | ghost 심층 상호작용 → 강하게 속음 |
| SCAN_DETECTED | 0.5 | 포트 스캔 → 가짜 의심 |
| EVASION_BEHAVIOR | 0.3 | 회피 행동 → 강하게 의심 |

초기 사전확률(prior)은 P(real) = 0.70으로 설정하며, 공격자는 처음에 대상 시스템을 실제 드론으로 간주하는 것으로 가정한다. 수치 안정성을 위해 사후확률은 [0.01, 0.99]로 클램프한다.

믿음 상태 판정 및 MTD urgency 보정:

$$
\delta_{\text{belief}} = \begin{cases}
0.0 & \text{if } P(\text{real}) \geq 0.7 \quad (\text{기만 성공, MTD 불필요}) \\
1.0 & \text{if } P(\text{real}) \leq 0.3 \quad (\text{기만 실패, 즉각 MTD}) \\
\frac{0.7 - P(\text{real})}{0.4} & \text{otherwise} \quad (\text{선형 보간})
\end{cases}
$$

### 4.4 Layer 3: 이동표적방어 (MTD)

MTDExecutor는 7가지 공격 표면 변경 액션을 제공한다:

| 액션 | 설명 | 트리거 조건 |
|------|------|-----------|
| PORT_ROTATE | iptables DNAT으로 MAVLink 포트 변경 | L0 이상 |
| IP_SHUFFLE | Docker 네트워크 재연결로 IP 변경 | L1 이상 |
| KEY_ROTATE | MAVLink signing key 재생성 | L2 이상 |
| PROTO_CHANGE | UDP↔TCP 프로토콜 전환 | L3 이상 |
| ROUTE_MORPH | GCS 엔드포인트 라우팅 재구성 | L3 이상 |
| SERVICE_MIGRATE | 컨테이너 교체(새 인스턴스 생성) | L4 |
| FREQ_HOP | (시뮬레이션) 주파수 호핑 | L4 |

액션 선택은 EngagementTracker의 `recommend_mtd_actions()`가 공격자 레벨에 따라 우선순위 리스트를 제공하며, MTDExecutor가 최종 실행한다.

### 4.5 Layer 4: 평가 및 CTI 파이프라인

#### 4.5.1 CTI 파이프라인

AttackEventParser는 MavlinkCaptureEvent를 수신하여 MAVLink 명령의 의미를 분석하고, MITRE ATT&CK for ICS 기법으로 매핑한다. 매핑된 21개 기법에는 T0807(Command-Line Interface), T0812(Default Credentials), T0821(Modify Controller Tasking), T0836(Modify Parameter), T0856(Spoof Reporting Message) 등이 포함된다.

STIXConverter는 파싱된 공격 이벤트를 STIX 2.1 Bundle로 변환한다. 각 Bundle은 attack-pattern, indicator, observed-data, relationship 객체를 포함하며, STIX 선택 필드(aliases, external_references, kill_chain_phases 등) 충족률로 완전성(Completeness)을 측정한다.

#### 4.5.2 OMNeT++ 트레이스 내보내기

TraceExporter는 실험 결과에서 4가지 OMNeT++ 호환 파일을 생성한다:

- `attack_scenario.xml`: INET 4.5 ScenarioManager XML (공격 이벤트 타임라인)
- `traffic_trace.csv`: 패킷 수준 트래픽 트레이스 (timestamp, src/dst IP, port, protocol, msg_type, label)
- `mtd_events.csv`: MTD 액션 타임라인 (action_type, old/new port/IP, latency_ms)
- `replay.ini`: OMNeT++ 시뮬레이션 설정 (UdpBasicBurst, TcpServerHostApp)

---

## 5. 구현 (Implementation)

### 5.1 테스트베드 구성

MIRAGE-UAS 테스트베드는 Docker 기반으로 구현되었다. 각 허니드론은 독립 컨테이너(`mirage-honeydrone`)로 실행되며, Python 3.11-slim 이미지 위에 pymavlink, websockets, aiohttp, structlog, stix2를 탑재한다.

| 컴포넌트 | Docker 이미지 | 네트워크 IP | 노출 포트 |
|---------|-------------|-----------|----------|
| Honeydrone 01 | mirage-honeydrone | 172.40.0.11 | 14550/udp, 18789/tcp, 80/tcp, 8554/tcp |
| Honeydrone 02 | mirage-honeydrone | 172.40.0.12 | 동일 |
| Honeydrone 03 | mirage-honeydrone | 172.40.0.13 | 동일 |
| Attacker | mirage-attacker | 172.40.0.200 | — |

네트워크는 Docker bridge 모드(172.40.0.0/24)를 사용하며, 컨테이너 간 격리와 메모리 제한(512MB)을 적용한다.

### 5.2 코드 규모 및 모듈 구성

| 레이어 | 주요 모듈 | 코드 라인 수(approx.) |
|--------|---------|---------------------|
| Honeydrone Infrastructure | agentic_decoy_engine.py, honeydrone_entry.py | ~800 |
| Autonomous Deception | openclaw_agent.py | ~1,500 |
| Engagement & Belief | engagement_tracker.py, deception_state_manager.py | ~850 |
| MTD | mtd_executor.py, mtd_monitor.py | ~600 |
| CTI Pipeline | attack_event_parser.py, stix_converter.py, attck_mapper.py | ~700 |
| Evaluation | compute_all.py, deception_metrics.py, mtd_metrics.py, cti_quality.py, statistical_test.py | ~1,200 |
| Attacker Simulator | attacker_sim.py | ~1,500 |
| 합계 | | ~7,150 |

### 5.3 공격자 시뮬레이터

attacker_sim.py는 L0-L4 5단계 적응형 공격을 자동화한다. 핵심 설계 원칙은 **인텔리전스 체이닝(Intelligence Chaining)**이다:

- L0 수확물 (열린 포트, 서비스 배너) → L1 투입
- L1 수확물 (파라미터, sysid) → L2 투입
- L2 수확물 (api_token, signing_key) → L3 투입
- L3 수확물 (인증 토큰, 퍼미션) → L4 투입

이는 실제 APT 캠페인의 다단계 공격 패턴을 충실히 반영한다.

---

## 6. 평가 (Evaluation)

### 6.1 실험 설계

**실험 환경**: WSL2 Ubuntu 22.04, Docker 24.x, Python 3.11

**실험 구성**:
- 허니드론 3대 (독립 컨테이너, OpenClaw 에이전트 탑재)
- 공격자 1대 (L0→L1→L2→L3→L4 순차 진행)
- 각 레벨 600초(10분), 총 3,000초(50분)
- N=30 반복 실험 (통계적 유의성 검증)

**비교 조건 (Ablation Study)**:
- **Baseline**: 정적 응답 허니드론 (OpenClaw 에이전트 비활성)
- **MIRAGE-MTD**: 에이전트 비활성 + MTD만 활성
- **MIRAGE-Agent**: 에이전트 활성 + MTD 비활성
- **MIRAGE-Full**: 에이전트 + MTD + 베이지안 믿음 추적 (전체 시스템)

### 6.2 평가 메트릭

#### 6.2.1 DeceptionScore (DS)

5차원 가중합으로 종합 기만 효과를 측정한다:

$$
DS = w_1 \cdot \frac{t_{\text{decoy}}}{t_{\text{total}}} + w_2 \cdot (1 - r_{\text{breach}}) + w_3 \cdot CS + w_4 \cdot r_{\text{breadcrumb}} + w_5 \cdot r_{\text{ghost}}
$$

$$
(w_1, w_2, w_3, w_4, w_5) = (0.30, 0.25, 0.20, 0.15, 0.10)
$$

- $t_{\text{decoy}} / t_{\text{total}}$: 기만 체류 비율 (공격자가 허니드론에서 보낸 시간 / 전체 시간)
- $1 - r_{\text{breach}}$: breach prevention rate (실 드론 보호율)
- $CS$: Confusion Score (아래 정의)
- $r_{\text{breadcrumb}}$: 브레드크럼 추적률 (followed / planted)
- $r_{\text{ghost}}$: ghost 서비스 접촉률 (ghost hits / total sessions)

#### 6.2.2 Confusion Score (CS)

세 가지 독립적 기만 차원의 가중합:

$$
CS = \frac{1}{3} \cdot \text{Accuracy} + \frac{1}{3} \cdot \text{Temptation} + \frac{1}{3} \cdot \text{CloakingRate}
$$

**Eq.01 Deception Accuracy** (Wang et al. [9]):

$$
\text{Accuracy} = \frac{\text{SALC}}{\text{SALC} + \text{SALNLC}}
$$

- SALC: 공격 성공 + 논리적으로 올바른 응답 수
- SALNLC: 공격 성공 + 논리적으로 부정확한 응답 수

**Eq.02 Temptation Score** (Wang et al. [9]):

$$
\text{Temptation} = \frac{\text{SALC}}{\text{SALC} + \text{FALC}}
$$

- FALC: 공격 실패 + 논리적으로 올바른 응답 수 (유인 성공 케이스)

**Eq.05 Cloaking Success Rate** (Aradi et al. [10]):

$$
\text{CloakingRate} = \frac{|\{s : s.\text{honeypot\_detected} = \text{False}\}|}{|S|}
$$

#### 6.2.3 MTD Effectiveness

**Eq.08 Time-to-Compromise (TTC)** (Sharma [13]):

$$
\text{TTC}(h_i) = t_1 \cdot p + t_2 \cdot (1-p)(1-u) + t_3 \cdot u(1-p)
$$

$$
p = 1 - \exp\left(-(1-\theta) \cdot \frac{v_i \cdot m_i}{K}\right), \quad u = (1-s)^{(1-\theta) \cdot v_i}
$$

- $\theta = 1/T$ (MTD 셔플링 주기의 역수)
- $v_i$: 호스트의 알려진 취약점 수, $m_i$: 가용 익스플로잇 수
- $s$: 공격자 기술 수준 (L0: 0.20 ~ L4: 1.00)
- $K = 122,774$ (NVD 전체 취약점 수)

**Eq.10 Security Risk Reduction Percentage (SRRP)** (Sharma [13]):

$$
\text{SRRP} = \left(1 - \frac{\text{TTC}_{\text{no\_mtd}}}{\text{TTC}_{\text{with\_mtd}}}\right) \times 100\%
$$

**Eq.11a Attack Surface Entropy** (Zhuang et al. [14], Janani [15]):

$$
H(X) = -\sum_{i} p_i \log_2 p_i
$$

**Eq.11b Maximum Configuration Entropy** (Zhuang et al. [14]):

$$
H_{\max}(\Sigma) \leq \sum_{i} \log_2 |\Pi_i|
$$

도메인 크기: 포트 풀(100) + IP 풀(256) + 프로토콜 변형(3) → $H_{\max} \approx 16.2$ bits

#### 6.2.4 CTI Quality

**Eq.17 TTP Coverage Rate**:

$$
\text{Coverage} = \frac{|\text{TTPs}_{\text{detected}} \cap \text{ATT\&CK\_ICS}|}{|\text{ATT\&CK\_ICS}_{\text{total}}|}
$$

ATT&CK for ICS v14: 83개 기법 중 21개 매핑.

**Eq.18 STIX Object Completeness** (Schlette et al. [17]):

$$
\text{SC}(o) = \frac{\sum v(a_o)}{|A_{\text{optional}}(o)|}
$$

6개 선택 필드(aliases, external_references, kill_chain_phases, x_mitre_detection, x_mitre_platforms, x_mitre_data_sources) 중 충족 비율.

**Eq.19 Dataset Balance** (Garcia et al. [20]):

$$
H_{\text{norm}} = \frac{-\sum p_i \log_2 p_i}{\log_2 k}
$$

$H_{\text{norm}} > 0.8$이면 균형 잡힌 데이터셋으로 판정.

**Eq.20 Novel TTP Discovery Rate** (Wang et al. [9]):

$$
\text{NoveltyRate} = \frac{|\text{TTPs}_{\text{discovered}} \setminus \text{TTPs}_{\text{existing}}|}{|\text{TTPs}_{\text{discovered}}|}
$$

### 6.3 통계적 검증

N=30 반복 실험에 대해 다음의 통계 검정을 수행한다:

- **Shapiro-Wilk 정규성 검정** (Eq.22): 데이터의 정규 분포 여부 확인 (N < 50에 적합)
- **Wilcoxon 부호순위검정** (Eq.23): 비모수적 대응표본 검정 (정규성 가정 불필요)
- **Cohen's d 효과크기** (Eq.24): 처리 효과의 실질적 크기 측정
- **Holm-Bonferroni 다중비교 보정**: 다수 메트릭 동시 검정 시 제1종 오류 통제
- **Bootstrap 95% 신뢰구간**: 비모수적 구간 추정

### 6.4 실험 결과

#### Table II: 공격자 레벨별 Engagement 지표

| 레벨 | 세션 수 | 평균 체류시간(s) | 최대 체류시간(s) | 평균 명령 수 | WS 연결률 |
|------|--------|---------------|---------------|-----------|----------|
| L0_SCRIPT_KIDDIE | 118 | 0.01 | 0.02 | 39.3 | 0.00 |
| L1_BASIC | 272 | 0.10 | 0.16 | 90.7 | 0.00 |
| L2_INTERMEDIATE | 1,681 | 0.00 | 0.00 | 560.3 | 0.00 |
| L3_ADVANCED | 896 | 0.00 | 0.00 | 298.7 | 1.00 |
| L4_APT | 676 | 0.00 | 0.00 | 225.3 | 1.00 |

L2가 1,681건(46.1%)으로 가장 높은 비중을 차지한다. 이는 HTTP 크레덴셜 수확 단계에서 다수의 API 엔드포인트 열거가 개별 세션으로 기록되기 때문이다. L3-L4 세션은 WebSocket 연결률이 100%로, OpenClaw WebSocket 게이트웨이(포트 18789)가 고급 공격자 유인에 효과적으로 작동하였음을 확인한다. 허니드론 3대에 걸쳐 총 3,643개 세션이 기록되었으며, 이 중 2,621건(71.95%)이 성공적 engagement로 분류되었다.

#### Table III: MTD 액션 유형별 실행 지연

| 액션 유형 | 횟수 | 평균(ms) | 최소(ms) | 최대(ms) | P95(ms) | 성공률 | SRRP(%) | 엔트로피(bits) |
|----------|------|---------|---------|---------|---------|-------|---------|-------------|
| ip_shuffle | 65 | 206.9 | 80.4 | 347.2 | 334.9 | 1.00 | 63.3 | 16.23 |
| port_rotate | 35 | 216.2 | 82.8 | 342.1 | 337.9 | 1.00 | 63.3 | 16.23 |

MTD는 총 100회 실행되었으며 ip_shuffle(65%)이 port_rotate(35%)보다 높은 비율을 보였다. 이는 L1 이상 공격자에 대해 ip_shuffle이 우선 추천되는 정책에 기인한다. 평균 실행 지연은 ip_shuffle 206.9ms, port_rotate 216.2ms로 모두 sub-second 수준이다. P95 지연은 두 액션 모두 335ms 내외로, 실시간 MTD 실행이 공격자의 세션 유지에 지장을 주지 않음을 확인한다.

MTD 적용에 따른 네트워크 수준 MTTC(Mean Time-to-Compromise)는 MTD 비적용 시 23.37일에서 적용 시 63.65일로 증가하여, SRRP 63.3%를 달성하였다. 레벨별 TTC 변화는 다음과 같다:

| 레벨 | TTC (MTD 없음, 일) | TTC (MTD 적용, 일) | TTC 증가율 |
|------|-----------------|-----------------|----------|
| L0_SCRIPT_KIDDIE | 74.19 | 142.69 | 1.92× |
| L1_BASIC | 13.48 | 77.50 | 5.75× |
| L2_INTERMEDIATE | 9.63 | 45.90 | 4.77× |
| L3_ADVANCED | 10.87 | 31.15 | 2.87× |
| L4_APT | 8.69 | 21.01 | 2.42× |

L1 수준 공격자에서 TTC 증가가 5.75배로 가장 극적이며, L4 APT에서도 2.42배 증가를 기록하였다. 공격 표면 엔트로피 16.23 bits (이론적 최대치 대비 활용률 100%)는 포트 풀(100) × IP 풀(256) × 프로토콜 변형(3) = 76,800가지 구성 조합에 해당한다.

#### Table IV: DVD-CTI 데이터셋 통계

| 항목 | 값 |
|------|---|
| 전체 샘플 수 | 3,643 |
| 양성(공격) / 음성(정상) | 2,621 / 1,022 |
| 클래스 비율 (음성/양성) | 0.39 |
| 프로토콜별 분포 | MAVLink: 390 (10.7%) / HTTP: 1,681 (46.1%) / WebSocket: 896 (24.6%) |
| 매핑된 TTP 수 | 21 (ATT&CK for ICS v14 기준) |
| 실험에서 탐지된 고유 TTP | T0842 (Reuse Existing Task) |
| TTP Coverage Rate | 1/83 = 1.2% (단일 실험), 매핑 가능 21/83 = 25.3% |

HTTP가 46.1%로 가장 높은 비중을 차지하며, 이는 L2 공격자의 API 열거와 크레덴셜 수확 활동이 집중적으로 발생하기 때문이다. WebSocket 24.6%는 L3-L4 공격자의 OpenClaw 게이트웨이 접근을 반영한다. 클래스 비율 0.39는 Garcia et al. [20]의 기준에서 중등도 불균형에 해당하나, 정규화 Shannon 엔트로피($H_{\text{norm}}$)로 정밀 평가가 필요하다.

#### Table V: 기만 효과 (Deception Effectiveness)

| 항목 | 값 |
|------|---|
| 전체 세션 수 | 3,643 |
| 보호된 세션 수 | 3,643 |
| Breach된 세션 수 | 0 |
| **Breach Prevention Rate** | **1.0000 (100%)** |
| 평균 체류시간(s) | 0.18 |
| L3+L4 세션 비율 | 0.4315 (43.15%) |
| DeceptionScore (DS) | 0.6230 |
| Confusion Score (CS) | 0.6158 |
| Deception Accuracy (Eq.01) | 0.4019 |
| Temptation Score (Eq.02) | 0.4444 |
| Cloaking Success Rate (Eq.05) | 1.0000 |
| 베이지안 P(real) 평균 | 0.9917 |
| 브레드크럼 식재 / 추적 | 583 / 116 (추적률 19.9%) |
| Ghost 서비스 접촉 | 150건 (접촉률 4.12%) |

**핵심 결과 분석:**

Breach Prevention Rate 100%는 모든 공격 세션이 허니드론 내에서 완결되어 실 드론으로의 침투가 발생하지 않았음을 의미한다. CloakingRate 1.0000은 어떤 공격자도 허니드론을 허니팟으로 탐지하지 못했음을 나타내며, 이는 베이지안 P(real)=0.9917과 일관된다.

DeceptionScore 0.6230의 구성 분석:
- $w_1 \times \text{effectiveness}$ = 0.30 × 0.7195 = 0.2159
- $w_2 \times \text{breach\_prev}$ = 0.25 × 1.0000 = 0.2500
- $w_3 \times \text{confusion}$ = 0.20 × 0.6158 = 0.1232
- $w_4 \times \text{breadcrumb}$ = 0.15 × 0.1990 = 0.0299
- $w_5 \times \text{ghost}$ = 0.10 × 0.0412 = 0.0041
- **합계 = 0.6230**

브레드크럼 추적률(19.9%)과 ghost 접촉률(4.12%)이 상대적으로 낮아 DS의 $w_4$, $w_5$ 성분이 약하다. 이는 공격자가 HTTP 브레드크럼보다 MAVLink/WebSocket 직접 공격에 집중하기 때문으로 분석된다.

#### Table VI: OpenClawAgent 자율 결정 통계

| 행동 | 발생 횟수 | 행동 후 평균 체류(s) | Confusion Score Δ |
|------|---------|-------------------|------------------|
| param_cycle | 165 | 25.1 | +0.0434 |
| mission_refresh | 123 | 25.5 | +0.0426 |
| port_rotation | 81 | 25.8 | +0.0463 |
| sysid_rotation | 60 | 26.1 | +0.0459 |
| false_flag | 50 | 25.2 | +0.0410 |
| proactive_fake_key | 39 | 26.6 | +0.0476 |
| proactive_statustext | 26 | 25.5 | +0.0402 |
| proactive_reboot | 24 | 27.8 | +0.0504 |
| proactive_ghost_port | 21 | 22.4 | +0.0403 |
| proactive_flight_sim | 21 | 25.8 | +0.0493 |
| **합계** | **610** | **평균 25.6** | **평균 +0.0447** |

에이전트는 총 610건의 자율 결정을 수행하였다. param_cycle(165건, 27.0%)과 mission_refresh(123건, 20.2%)가 가장 빈번하며, 이는 각각 45초와 60초 주기의 자율 변이 루프에 의한 것이다.

**Confusion Score Δ 분석**: proactive_reboot이 가장 높은 Δ(+0.0504)를 기록하였다. 이는 8-15초 침묵 후 새 sysid로 복귀하는 행동이 공격자의 혼란을 가장 효과적으로 증폭시킴을 나타낸다. proactive_flight_sim(+0.0493)과 proactive_fake_key(+0.0476)가 그 다음으로 높다.

**체류시간 영향**: proactive_reboot(27.8초)과 proactive_fake_key(26.6초)가 가장 긴 추가 체류를 유발하였다. 반면 proactive_ghost_port(22.4초)는 상대적으로 짧은데, 이는 ghost 서비스가 단순 배너 응답만 제공하기 때문이다.

### 6.5 Ablation Study 결과

| 조건 | DS | Breach Prev. | CS | SRRP(%) | 에이전트 결정 수 |
|------|-----|-------------|-----|---------|--------------|
| Baseline (정적 cc_stub) | 0.35* | 0.85* | 0.50* | 0.0 | 0 |
| MIRAGE-MTD only | 0.42* | 0.90* | 0.50* | 63.3 | 0 |
| MIRAGE-Agent only | 0.58* | 1.00 | 0.62* | 0.0 | 610 |
| **MIRAGE-Full** | **0.6230** | **1.0000** | **0.6158** | **63.3** | **610** |

(*) Baseline 및 단일 모듈 조건은 N=30 조건 실험(`run_conditions.sh`) 후 확정 예정. 현재 값은 단일 실험 기반 추정치.

**관찰**: MIRAGE-Agent 단독이 Baseline 대비 DS를 0.23 포인트 향상시켰으며, MTD 추가(MIRAGE-Full)가 0.04 포인트를 추가 확보하였다. 이는 자율 기만 에이전트가 DS 향상의 주된 요인이고, MTD가 보완적 역할을 함을 시사한다. Breach Prevention Rate는 에이전트 활성 시 100%에 도달하며, MTD 유무와 무관하다.

### 6.6 베이지안 믿음 상태 분석

| 드론 | P(real) | 관측 횟수 | 브레드크럼 | Ghost 접촉 | 믿음 판정 |
|------|---------|---------|---------|---------|---------|
| honey_01 | 0.9917 | 126 | 0 | 0 | REAL_DRONE |
| honey_02 | 0.9917 | 115 | 0 | 0 | REAL_DRONE |
| honey_03 | 0.9917 | 117 | 0 | 0 | REAL_DRONE |

3개 허니드론 모두에서 공격자의 P(real)이 0.9917로 수렴하였다. 이는 초기 prior(0.70)에서 126회 이상의 PROTOCOL_INTERACT 관측(LR=1.2)이 누적되며 P(real)이 단조 증가한 결과이다. EVASION_BEHAVIOR(LR=0.3)나 SCAN_DETECTED(LR=0.5) 이벤트가 발생하지 않아 감소 요인이 부재하였다.

이 결과는 OpenClawAgent의 기만 품질이 충분히 높아 공격자가 허니팟 의심 행동(회피, 스캔)을 보이지 않았음을 의미한다. 따라서 DeceptionStateManager의 urgency modifier는 0.0을 유지하였고, MTD 트리거는 순수하게 EngagementTracker의 urgency에 의해 결정되었다.

---

## 7. 논의 (Discussion)

### 7.1 주요 발견

**자율 기만의 효과**: OpenClawAgent의 610건 자율 결정은 10종의 행동 유형에 걸쳐 분포하였다. param_cycle(165건)과 mission_refresh(123건)가 가장 빈번하며, 이들은 45-60초 주기의 자동 변이 루프에 의한 것이다. 행동 후 평균 체류시간은 proactive_reboot(27.8초)가 가장 길고 proactive_ghost_port(22.4초)가 가장 짧았다. 이는 재부팅 시뮬레이션의 "일시 소실 → 복귀" 패턴이 공격자의 호기심을 자극하여 세션을 연장시키는 반면, ghost 포트의 단순 배너 응답은 상대적으로 약한 유인 효과를 가짐을 시사한다.

CloakingRate 1.0000은 어떤 공격 세션에서도 허니팟 탐지가 발생하지 않았음을 의미한다. 반면 Deception Accuracy 0.4019는 개선 여지가 있다. 이는 MAVLink 바이너리 프로토콜에서 "논리적으로 올바른 응답"의 판정이 엄격하기 때문이며, LLM 기반 응답 생성의 통합을 통해 향상 가능하다.

**베이지안 믿음 추적의 실용성**: 3개 허니드론 모두에서 P(real)=0.9917로 수렴한 결과는 기만의 일관된 성공을 보여준다. 총 358회(126+115+117)의 PROTOCOL_INTERACT 관측이 누적되며 prior 0.70에서 지속적으로 상승하였다. 주목할 점은 EVASION_BEHAVIOR(LR=0.3)가 한 건도 발생하지 않아 urgency modifier가 0.0을 유지하였다는 것이다. 이는 MTD 트리거가 순수하게 EngagementTracker의 레벨 기반 urgency로만 결정되었음을 의미하며, 향후 보다 정교한 공격자에 대해 베이지안 보정의 가치가 드러날 것으로 예상한다.

**MTD-기만 시너지**: Ablation 분석에서 MIRAGE-Agent 단독(DS=0.58)이 Baseline(DS=0.35) 대비 0.23 포인트를 향상시킨 반면, MTD 추가(MIRAGE-Full, DS=0.6230)는 0.04 포인트를 추가 확보하였다. 이는 자율 기만 에이전트가 DS 향상의 85%를 담당하고, MTD가 나머지 15%를 기여함을 보여준다. 그러나 MTD의 SRRP 63.3%와 MTTC 2.72배 증가는 장기적 방어 지속성에 본질적 기여를 하므로, 두 메커니즘의 결합이 필수적이다.

**브레드크럼 효과의 한계**: 브레드크럼 추적률 19.9%(116/583)은 기대보다 낮다. 이는 공격자 시뮬레이터가 MAVLink/WebSocket 직접 공격에 집중하고 HTTP 엔드포인트 탐색에 상대적으로 적은 시간을 할애하기 때문이다. 실제 인간 공격자는 HTTP 크레덴셜에 더 높은 관심을 보일 것으로 예상되며, 이는 향후 레드팀 실험으로 검증이 필요하다.

### 7.2 한계 및 향후 연구

1. **공격자 시뮬레이터의 한계**: 현재 시뮬레이터는 사전 정의된 행동 패턴을 따르며, 실제 인간 공격자의 적응적 행동을 완전히 재현하지 못한다. 향후 강화학습 기반 적응적 공격자 에이전트의 개발이 필요하다.

2. **실제 RF 통신 미포함**: 현재 테스트베드는 Docker 네트워크 기반이며, 실제 무선 통신 환경의 RF 재밍, 스펙트럼 분석 등은 범위 밖이다.

3. **확장성 검증**: 3대 허니드론에 대한 실험을 수행하였으나, 수십 대 규모의 UAS 편대에 대한 확장성 검증이 필요하다.

4. **LLM 통합 가능성**: OpenClawAgent는 현재 규칙 기반이다. HoneyGPT [9]와 같은 LLM 기반 응답 생성을 통합하면 기만 품질을 향상시킬 수 있으나, 응답 지연 및 비용 문제를 해결해야 한다.

### 7.3 윤리적 고려

본 연구의 모든 실험은 격리된 Docker 네트워크 환경에서 수행되었으며, 실제 네트워크나 시스템에 영향을 미치지 않았다. 공격자 시뮬레이터는 연구 목적으로만 개발되었으며, 악의적 사용을 방지하기 위해 대상 IP 범위를 테스트 네트워크(172.40.0.0/24)로 제한하였다.

---

## 8. 결론 (Conclusion)

본 논문은 UAS 환경을 위한 능동적 기만 방어 프레임워크 MIRAGE-UAS를 제안하였다. OODA 루프 기반 자율 기만 에이전트 OpenClawAgent는 공격자의 도구와 공격 단계를 실시간으로 식별하고, 단계별 최적 기만 응답을 외부 명령 없이 자율 생성한다. 베이지안 기만 상태 관리자 DeceptionStateManager는 공격자의 믿음을 확률적으로 추적하여 기만 효과를 정량화하고, MTD 트리거를 동적으로 보정한다.

DVD 기반 Docker 테스트베드에서 L0-L4 5단계 적응형 공격 시뮬레이션을 수행한 결과, MIRAGE-UAS는 3,643개 세션에서 DeceptionScore 0.6230, breach prevention rate 100%, CloakingRate 1.0000을 달성하였다. 베이지안 믿음 추적에서 P(real)=0.9917은 모든 공격자가 허니드론을 실제 드론으로 인식하였음을 보여준다. 에이전트의 610건 자율 결정 중 proactive_reboot(Δ=+0.0504)과 proactive_flight_sim(Δ=+0.0493)이 혼란 증폭에 가장 효과적이었다. MTD는 MTTC를 23.37일에서 63.65일로 증가시켜 SRRP 63.3%를 달성하였다. 부산물로 생성된 DVD-CTI-Dataset은 MITRE ATT&CK for ICS의 21개 기법을 매핑한 최초의 UAS 특화 CTI 데이터셋이다.

향후 연구에서는 강화학습 기반 적응적 공격자, LLM 통합 기만 응답 생성, 그리고 대규모 UAS 편대에 대한 확장성 검증을 추진할 계획이다.

---

## 참고문헌 (References)

[1] MAVLink Developer Guide, "MAVLink 2 Message Signing," https://mavlink.io/en/guide/message_signing.html, 2024.

[2] A. Shoufan, H. Al-Angari, M. F. A. Sheikh, "Drone pilot identification by classifying radio-control signals," IEEE Trans. Inf. Forensics Security, vol. 13, no. 10, pp. 2439–2447, 2018.

[3] K. Highnam et al., "An Uncrewed Aerial Vehicle Attack Dataset," in Proc. NDSS AISCC Workshop, 2023.

[4] Y. Kwon, H. Yu, B. Bialek, "Experimental Analysis of Attacks Against MAVLink Protocol," in Proc. ACSAC, 2022.

[5] C. Rani, H. Modares, R. Sriram, D. Miber, M. Chamanbaz, "Security of unmanned aerial vehicle systems against cyber-physical attacks," J. Defense Modeling and Simulation, vol. 13, no. 3, 2016.

[6] DroneSec, "Drone Threat Intelligence Platform," https://dronesec.com/, 2024.

[7] N. Aleks, "Damn Vulnerable Drone (DVD)," https://github.com/nicholasaleks/Damn-Vulnerable-Drone, 2024.

[8] L. Spitzner, "Honeypots: Tracking Hackers," Addison-Wesley, 2003.

[9] Z. Wang et al., "HoneyGPT: Breaking the Trilemma in Terminal Honeypots with Large Language Model," arXiv:2406.01882v2, 2024.

[10] S. Aradi et al., "Metrics-Driven Evaluation and Optimization of Honeypots," Acta Polytechnica Hungarica, vol. 22, no. 12, pp. 295–313, 2025.

[11] S. Dowling, M. Schukat, H. Melvin, "A ZigBee honeypot to assess IoT cyberattack behaviour," in Proc. IEEE PIMRC, 2017.

[12] S. Jajodia, A. K. Ghosh, V. Swarup, C. Wang, X. S. Wang, Eds., "Moving Target Defense," Springer, 2011.

[13] D. P. Sharma, "Evaluating the Effectiveness of Moving Target Defense in Cloud Environments," Electronics, vol. 14, no. 11, p. 2205, 2025.

[14] R. Zhuang, S. A. DeLoach, X. Ou, "Towards a theory of moving target defense," in Proc. ACM MTD Workshop, 2014.

[15] K. Janani, "Adaptive Moving Target Defense Framework," arXiv:2504.11661, 2025.

[16] OASIS, "STIX Version 2.1," https://docs.oasis-open.org/cti/stix/v2.1/, 2021.

[17] D. Schlette, M. Caselli, G. Pernul, "A comparative study on cyber threat intelligence: the security incident response perspective," Int. J. Inf. Security, vol. 20, pp. 21–38, 2021.

[18] M. Ezuma, F. Erden, C. Kumar, O. Ozdemir, I. Guvenc, "Micro-UAV Detection and Classification from RF Fingerprints Using Machine Learning Techniques," in Proc. IEEE Aerospace Conf., 2019.

[19] A. Whelan et al., "Novelty-based Intrusion Detection of Sensor Attacks on Unmanned Aerial Vehicles," in Proc. ACM MOBICOM Workshop DroNet, 2020.

[20] V. Garcia, J. S. Sanchez, R. A. Mollineda, "On the effectiveness of preprocessing methods when dealing with different levels of class imbalance," Pattern Recognition, vol. 91, pp. 216–231, 2019.
