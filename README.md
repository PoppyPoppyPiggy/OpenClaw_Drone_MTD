# MIRAGE-UAS

**Moving-target Intelligent Responsive Agentic deception enGinE for UAS**

![Python 3.11](https://img.shields.io/badge/Python-3.11-blue)
![ACM CCS 2026](https://img.shields.io/badge/Venue-ACM%20CCS%202026-red)
![License MIT](https://img.shields.io/badge/License-MIT-green)
![WSL2 + Docker](https://img.shields.io/badge/Platform-WSL2%20%2B%20Docker-purple)

---

## Overview

MIRAGE-UAS is a research testbed that integrates **Moving Target Defense (MTD)**, **agentic honeydrone deception**, and an **automated STIX 2.1 CTI pipeline** for Unmanned Aerial System (UAS / drone) cybersecurity.

| | |
|---|---|
| **Target Venue** | ACM CCS 2026 Cycle B (paper under review) |
| **Institution** | Distributed Security Lab (DS Lab), Kyonggi University, South Korea (분산보안연구실, 경기대학교) |
| **Funding** | Defense Acquisition Program Administration — DAPA (국방기술진흥연구소, 과제번호 915024201) |
| **Key Claim** | First framework integrating MTD + OpenClaw-inspired agentic honeydrone + automated STIX 2.1 CTI dataset generation for UAS security |

The system deploys honey drones (fake drone instances built on [Damn Vulnerable Drone](https://github.com/nicholasaleks/DamnVulnerableDrone) images) that autonomously deceive attackers across all skill levels (L0 script kiddie → L4 APT), while simultaneously generating a labeled cyberthreat intelligence dataset mapped to MITRE ATT&CK for ICS v14.

---

## Architecture

```
                         ┌─────────────────────────────────────────────┐
                         │            MIRAGE-UAS Architecture          │
                         └─────────────────────────────────────────────┘

 Attacker (L0-L4)
     │
     ▼
 ┌───────────────────────────────────────────────────────┐
 │              Agentic Honey Drone Stack                │
 │                                                       │
 │  ┌─────────────────┐  ┌──────────────────────────┐   │
 │  │  DVD FCU (SITL)  │  │  DVD CC (MAVLink/HTTP)   │   │
 │  │  ArduPilot SITL  │  │  MAVLink Router + Web    │   │
 │  └────────┬─────────┘  └──────────┬───────────────┘   │
 │           │                       │                   │
 │  ┌────────┴───────────────────────┴───────────────┐   │
 │  │         AgenticDecoyEngine (핵심 엔진)          │   │
 │  │                                                 │   │
 │  │  ┌────────────────┐  ┌──────────────────────┐  │   │
 │  │  │ OpenClawAgent  │  │  MavlinkResponseGen  │  │   │
 │  │  │ (자율 기만)     │  │  (ArduPilot 응답)     │  │   │
 │  │  └────────────────┘  └──────────────────────┘  │   │
 │  │  ┌────────────────┐  ┌──────────────────────┐  │   │
 │  │  │ FakeService    │  │  BreadcrumbPlanter   │  │   │
 │  │  │ Factory        │  │  (가짜 인증정보)       │  │   │
 │  │  └────────────────┘  └──────────────────────┘  │   │
 │  │  ┌────────────────┐  ┌──────────────────────┐  │   │
 │  │  │ DeceptionState │  │  EngagementTracker   │  │   │
 │  │  │ Manager(Bayes) │  │  (세션 추적)          │  │   │
 │  │  └────────────────┘  └──────────────────────┘  │   │
 │  └─────────────────────────────────────────────────┘   │
 └───────────┬───────────────────────────┬────────────────┘
             │                           │
     ┌───────┴────────┐        ┌────────┴─────────────────────┐
     │   Track A       │        │   Track B                    │
     │   (Real-time)   │        │   (CTI Pipeline)             │
     ▼                 │        ▼                              │
 ┌──────────────┐      │   ┌──────────────┐                   │
 │ MTDTrigger   │      │   │ MavlinkInter │                   │
 │ Queue        │      │   │ ceptor       │                   │
 └──────┬───────┘      │   └──────┬───────┘                   │
        ▼              │          ▼                            │
 ┌──────────────┐      │   ┌──────────────┐                   │
 │ Engagement   │      │   │ AttackEvent  │                   │
 │ ToMTD        │      │   │ Parser       │                   │
 └──────┬───────┘      │   └──────┬───────┘                   │
        ▼              │          ▼                            │
 ┌──────────────┐      │   ┌──────────────┐   ┌────────────┐ │
 │ MTDExecutor  │      │   │ STIXConvert  │──▶│ CTI Ingest │ │
 │ (Docker SDK) │      │   │ er (2.1)     │   │ API (:8765)│ │
 └──────┬───────┘      │   └──────────────┘   └────────────┘ │
        ▼              │          │                            │
 ┌──────────────┐      │   ┌──────┴───────┐                   │
 │ Real Drone   │      │   │ DVD-CTI-     │                   │
 │ (Protected)  │      │   │ Dataset-v1   │                   │
 └──────────────┘      │   └──────────────┘                   │
                       │                                      │
     ┌─────────────────┴──────────────────────────────────────┘
     ▼
 ┌────────────────────────────────────────┐
 │  DeceptionOrchestrator                 │
 │  + MetricsCollector (Table II-VI)      │
 │  + DeceptionScorer (DS formula)        │
 └────────────────────────────────────────┘
```

---

## Key Components

| Component | File | Role |
|-----------|------|------|
| **OpenClaw Agent** | `src/honey_drone/openclaw_agent.py` | Autonomous deception agent — 5 behaviors: adaptive response (RECON→EXPLOIT→PERSIST→EXFIL), proactive deception (unsolicited STATUSTEXT, flight simulation, ghost ports, reboot sim, fake keys), attacker fingerprinting (nmap/mavproxy/dronekit/metasploit/custom), confusion amplification (service mirror, false flag, ARM crash), self-mutation (sysid/port/param/mission rotation) |
| **Agentic Decoy Engine** | `src/honey_drone/agentic_decoy_engine.py` | Core honeydrone engine — integrates OpenClawAgent + MavlinkResponseGen + EngagementTracker; manages MAVLink UDP + OpenClaw WebSocket simultaneously |
| **Fake Service Factory** | `src/honey_drone/fake_service_factory.py` | Dynamic ghost service spawner — generates fake MAVLink, HTTP, RTSP, OpenClaw WebSocket, and SSH services scaled to attacker level |
| **Breadcrumb Planter** | `src/honey_drone/breadcrumb_plant.py` | Plants fake credentials, API tokens, SSH keys, config files, and MAVLink signing keys — escalates by attacker level (L0: basic creds → L4: database DSNs) |
| **Deception State Manager** | `src/honey_drone/deception_state_manager.py` | Bayesian belief tracker — maintains P(real_drone \| observations) per attacker using likelihood ratios for each observation type |
| **Deception Orchestrator** | `src/honey_drone/deception_orchestrator.py` | Coordinates FakeServiceFactory + BreadcrumbPlanter + DeceptionStateManager; handles MTD rotation reset and deception escalation |
| **Engagement Tracker** | `src/honey_drone/engagement_tracker.py` | Per-session attacker metrics — dwell time, command count, exploit attempts, WebSocket sessions; rule-based L0-L4 classification |
| **MAVLink Response Gen** | `src/honey_drone/mavlink_response_gen.py` | ArduPilot Copter v4.3.x response emulation with anti-fingerprint jitter and Gaussian position nudging |
| **MTD Executor** | `src/mtd/mtd_executor.py` | Docker SDK-based MTD surface mutations — port rotate (iptables DNAT), IP shuffle (network reconnect), key rotate, protocol change, route morph, service migrate |
| **ATT&CK Mapper** | `src/cti_pipeline/attck_mapper.py` | MITRE ATT&CK for ICS v14 TTP mapping from MAVLink/HTTP/WS attack events |
| **STIX Converter** | `src/cti_pipeline/stix_converter.py` | STIX 2.1 bundle generation from parsed attack events |
| **Attacker Simulator** | `scripts/attacker_sim.py` | Automated L0→L4 attacker for testbed evaluation — UDP scan, valid MAVLink, HTTP enumeration, WebSocket CVE exploit, breadcrumb chasing |
| **Deception Scorer** | `src/evaluation/deception_scorer.py` | Composite DeceptionScore: DS = w₁·(time_on_decoys/total) + w₂·(1−breach_rate) + w₃·avg_confusion + w₄·breadcrumb_follow + w₅·ghost_hit |
| **Deception Monitor** | `src/evaluation/deception_monitor.py` | Real-time effectiveness tracking — polls CTI API, computes sliding-window confusion score, outputs JSONL timeline |
| **Metrics Collector** | `src/evaluation/metrics_collector.py` | Paper table generator — Table II (engagement), III (MTD latency), IV (dataset), V (deception success), VI (agent decisions) |

---

## DVD Integration

MIRAGE-UAS extracts only Docker images from [Damn Vulnerable Drone](https://github.com/nicholasaleks/DamnVulnerableDrone) — no source modification, no Kali VM:

| Image | Role | Mode |
|-------|------|------|
| `nicholasaleks/dvd-flight-controller:latest` | ArduPilot SITL (FCU) | TCP :5760 internal |
| `nicholasaleks/dvd-companion-computer:latest` | MAVLink Router + Web UI + RTSP | UDP :14550 (attacker-facing) |
| `nicholasaleks/dvd-simulator:lite` | Management console | No Gazebo, no X11 |

**Design constraints (WSL2 호환):**
- No WiFi kernel modules (`mac80211_hwsim`) — pure network-layer simulation
- No Gazebo/X11 — `lite` simulator mode only
- No Kali required — built-in attacker simulator replaces external red team
- 3 honey drone stacks running simultaneously on `172.30.0.0/24` (production) or `172.40.0.0/24` (test harness)

---

## Quick Start

### Prerequisites

- **WSL2 Ubuntu 22.04** (kernel ≥ 5.15)
- **Docker Desktop** with WSL2 backend enabled
- **Python 3.11+** with pip
- ~8 GB RAM allocated to WSL2 (see `config/.wslconfig`)

### Setup

```bash
# 1. Clone repository
git clone https://github.com/PoppyPoppyPiggy/OpenClaw_Drone_MTD.git
cd OpenClaw_Drone_MTD

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Configure environment
cp config/.env.example config/.env
# Edit config/.env — fill in all empty research parameters
# (values with = and nothing after are REQUIRED, no defaults)

# 4. Verify environment
python scripts/verify_env.py

# 5. Pull DVD Docker images
docker pull nicholasaleks/dvd-flight-controller:latest
docker pull nicholasaleks/dvd-companion-computer:latest
docker pull nicholasaleks/dvd-simulator:lite

# 6. Dry-run experiment (no Docker needed — validates pipeline logic)
python scripts/run_experiment.py --mode dry-run --duration 120

# 7. Full test harness (Docker required)
bash scripts/run_test_harness.sh
```

### Test Harness Architecture

The test harness (`config/docker-compose.test-harness.yml`) runs a self-contained experiment:

```
┌─────────────────────────────────────────────────────────┐
│                  test_net (172.40.0.0/24)                │
│                                                         │
│  Honey Drones          Attacker          Monitor        │
│  ┌──────────┐          ┌──────────┐     ┌──────────┐   │
│  │ CC .10   │◄─────────│ Attacker │     │ Deception│   │
│  │ CC .11   │◄─────────│ Sim      │     │ Monitor  │   │
│  │ CC .12   │◄─────────│ .200     │     │ .250     │   │
│  └──────────┘          └──────────┘     └──────────┘   │
│       │                 L0→L1→L2            │           │
│  ┌────┴─────┐           →L3→L4         ┌───┴────┐      │
│  │ FCU .20  │                          │CTI API │      │
│  │ FCU .21  │                          │ .240   │      │
│  │ FCU .22  │                          └────────┘      │
│  └──────────┘                                          │
└─────────────────────────────────────────────────────────┘
```

---

## Research Parameters (.env)

All research parameters are loaded from `config/.env` with **no hardcoded defaults** — the system raises `ConfigError` if any are missing. This enforces explicit configuration for reproducibility.

| Parameter | Formula Reference | Description |
|-----------|-------------------|-------------|
| `MTD_COST_SENSITIVITY_KAPPA` | κ_ℓ in Eq.17: C_mtd(a) = κ_ℓ · Σᵢ αᵢ · cost_i(a) | MTD action cost sensitivity coefficient |
| `MTD_ALPHA_WEIGHTS` | αᵢ in Eq.17 (7D, sum=1.0) | Per-action type weight vector [freq_hop, ip_shuffle, port_rotate, proto_change, route_morph, key_rotate, service_migrate] |
| `MTD_BREACH_PREVENTION_BETA` | β in Eq.18 | Breach prevention reward weight |
| `COMPROMISE_P_BASE` | p_base in Eq.18: P_comp = p_base · Π_i(1−e_i)^(1/n) | Base compromise probability ∈ (0, 1) |
| `DES_WEIGHT_LIST` | wⱼ in Eq.19: DES = Σⱼ wⱼ · δⱼ(s) (4D, sum=1.0) | Defense effectiveness score weights [breach_rate, mttc_ratio, cost_ratio, deception_engagement] |
| `DECEPTION_LAMBDA` | λ_d in r_total = r_mtd + λ_d · r_dec | Deception reward scaling factor |
| `DECEPTION_WEIGHTS` | w_dwell, w_cmd, w_prot (3D, sum=1.0) | Deception reward component weights |
| `DECEPTION_DWELL_MAX_SEC` | T_max in min(t/T_max, 1) | Dwell time normalization ceiling |
| `ATTACKER_PRIORS` | P(level) for L0-L4 (5D, sum=1.0) | Prior probability distribution over attacker levels |
| `DECEPTION_SCORE_WEIGHTS` | w1-w5 in DS formula (5D, sum=1.0) | DeceptionScore component weights |
| `AGENT_PROACTIVE_INTERVAL_SEC` | §4.3 Behavior 2 | Autonomous proactive deception action interval (seconds) |
| `AGENT_SYSID_ROTATION_SEC` | §4.3 Behavior 5 | MAVLink sysid self-mutation interval (seconds) |
| `AGENT_PORT_ROTATION_SEC` | §4.3 Behavior 5 | WebSocket port self-mutation interval (seconds) |
| `AGENT_FALSE_FLAG_DWELL_THRESHOLD` | §4.3 Behavior 4 | Dwell time threshold (sec) to trigger false flag — briefly responds as different drone (different sysid + GPS coordinates) |
| `AGENT_MIRROR_SERVICE_THRESHOLD` | §4.3 Behavior 4 | Number of unique services touched before triggering service mirroring on new port |

---

## Paper Contributions

1. **C1: First MTD + Agentic Decoy framework for UAS** — extends D3GF (Seo et al., IEEE Access 2023) from game-theoretic simulation to real decoy deployment with Docker-containerized honey drones and autonomous deception agents

2. **C2: First honeypot-derived labeled UAS CTI dataset** — DVD-CTI-Dataset-v1 with STIX 2.1 bundles mapped to MITRE ATT&CK for ICS v14, generated from 5 attacker levels across 4 drone protocols (MAVLink/HTTP/RTSP/WebSocket)

3. **C3: OpenClaw-inspired agentic honeydrone design pattern** — autonomous deception without operator intervention: the agent observes attacker behavior, classifies attack phase and tool, and independently decides what to show, when to show it, and when to mutate

---

## Related Work

| Work | Year | Contribution | Gap Addressed by MIRAGE-UAS |
|------|------|--------------|----------------------------|
| **D3GF** — Seo et al. | IEEE Access 2023 | Game-theoretic drone MTD framework | No real deployment, no honeypot, no dataset |
| **HoneyDrone** — Daubert et al. | IEEE NOMS 2018 | First static UAV honeypot concept | Static responses only, no adaptive behavior, no MTD |
| **HoneyGPT** — Wang et al. | 2024 | LLM-powered SSH honeypot | Not drone-specific, no MAVLink/RTSP protocols |
| **Mirra et al.** | arXiv 2026 | Agentic honeynet with LLM reasoning | Not drone-specific, no UAS protocol emulation |
| **OpenClaw** — vuln. disclosure | 2026 | Vulnerable agentic AI in drones | Attack vector — MIRAGE-UAS inverts it as a deception mechanism |
| **TIFS T-IFS-25285-2026** | 2026 | CTI-RL-MTD for drone security | Predecessor — MIRAGE-UAS adds agentic deception + real decoys |

---

## Project Structure

```
mirage-uas/
├── config/
│   ├── .env.example                    # Research parameter template (모든 수식 변수)
│   ├── .wslconfig                      # WSL2 resource limits (메모리/CPU)
│   ├── docker-compose.honey.yml        # Production honey drone stack (3 drones)
│   └── docker-compose.test-harness.yml # Self-contained test environment
│
├── docker/
│   └── Dockerfile.attacker             # Automated attacker simulator image
│
├── scripts/
│   ├── attacker_sim.py                 # L0→L4 automated attacker (test harness)
│   ├── run_experiment.py               # Experiment entry point (dry-run / full)
│   ├── run_test_harness.sh             # One-command test execution
│   ├── verify_env.py                   # WSL2 + Docker environment check
│   └── verify_ports.py                 # MAVLink port availability check
│
├── src/
│   ├── shared/
│   │   ├── constants.py                # .env loader with validation (연구 파라미터 필수)
│   │   ├── logger.py                   # structlog JSON configuration
│   │   └── models.py                   # 15 dataclasses + 7 enums (공유 데이터 모델)
│   │
│   ├── honey_drone/                    # Track A: Agentic Deception Layer
│   ���   ├── agentic_decoy_engine.py     # Core engine (UDP + WebSocket + OpenClawAgent)
│   │   ├── openclaw_agent.py           # Autonomous deception agent (5 behaviors)
│   │   ├── engagement_tracker.py       # Per-session attacker metrics + L0-L4 classification
│   │   ├── mavlink_response_gen.py     # ArduPilot Copter v4.3.x MAVLink responses
│   │   ├── fake_service_factory.py     # Dynamic ghost service spawner
│   │   ├── breadcrumb_plant.py         # Fake credential / lure endpoint planter
│   │   ├── deception_orchestrator.py   # Deception layer coordinator
│   │   ├── deception_state_manager.py  # Bayesian P(real|obs) belief tracker
│   │   └── honey_drone_manager.py      # Docker container lifecycle (spawn/teardown/rotate)
│   │
│   ├── mtd/                            # Track A: MTD Surface Controller
│   │   ├── mtd_executor.py             # Docker SDK MTD actions
│   │   ├── mtd_actions.py              # Action types + cost calculation (Eq.17)
���   │   ├── engagement_to_mtd.py        # MTDTrigger → list[MTDAction] converter
│   │   └── decoy_rotation_policy.py    # Full drone replacement decision logic
│   │
│   ├── cti_pipeline/                   # Track B: CTI Generation
│   │   ├── mavlink_interceptor.py      # Passive MAVLink traffic tap
│   │   ├── http_rtsp_capture.py        # HTTP/RTSP traffic capture
│   │   ├── attack_event_parser.py      # Raw → ParsedAttackEvent (L0-L4 + TTP)
│   │   ├── attck_mapper.py             # MITRE ATT&CK for ICS v14 mapping
│   │   ├── stix_converter.py           # STIX 2.1 bundle generation
│   │   └── cti_ingest_api.py           # FastAPI ingest endpoint (:8765)
│   │
│   ├── dataset/                        # Track B: Dataset Construction
│   │   ├── positive_collector.py       # Attack sample collection (label=1)
│   │   ├── negative_generator.py       # Benign sample generation (label=0)
│   │   ├── dataset_packager.py         # CSV + STIX bundle packaging
│   │   └── dataset_validator.py        # Schema + balance validation
│   │
│   └── evaluation/                     # Experiment Evaluation
│       ├── metrics_collector.py        # Paper Tables II-VI JSON generator
│       ├── deception_monitor.py        # Real-time effectiveness monitor
│       └── deception_scorer.py         # DeceptionScore computation + LaTeX macros
│
├── tests/
│   └── integration/
│       └── test_e2e_mirage.py          # E2E integration tests (E2E-01~08)
│
├── results/                            # Experiment outputs (git-ignored)
│   ├── metrics/                        # Table II-VI JSON + deception_timeline.jsonl
│   └── logs/                           # Structured JSON logs
│
├── .gitignore
├── README.md
└── requirements.txt
```

---

## Citation

```bibtex
@inproceedings{mirage-uas-2026,
  title     = {{MIRAGE-UAS}: Moving-target Intelligent Responsive Agentic
               Deception Engine for {UAS} Security},
  author    = {{DS Lab, Kyonggi University}},
  booktitle = {Proceedings of the 2026 ACM SIGSAC Conference on Computer
               and Communications Security (CCS '26)},
  year      = {2026},
  note      = {Under review --- Cycle B submission}
}
```

---

## License

MIT License — for research and educational use.

Damn Vulnerable Drone Docker images (`nicholasaleks/dvd-*`) are subject to their own license ([MIT](https://github.com/nicholasaleks/DamnVulnerableDrone/blob/main/LICENSE)).

---

## Contact

**Distributed Security Lab (DS Lab)**
분산보안연구실, 경기대학교 (Kyonggi University)

DAPA Project 915024201 (국방기술진흥연구소)
