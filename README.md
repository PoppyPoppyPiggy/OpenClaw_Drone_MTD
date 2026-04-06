# MIRAGE-UAS

**Moving-target Intelligent Responsive Agentic deception enGinE for UAS**

![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue)
![ACM CCS 2026](https://img.shields.io/badge/Venue-ACM%20CCS%202026-red)
![License MIT](https://img.shields.io/badge/License-MIT-green)
![WSL2 + Docker](https://img.shields.io/badge/Platform-WSL2%20%2B%20Docker-purple)
![DeceptionScore](https://img.shields.io/badge/DeceptionScore-0.714-brightgreen)
![Lines](https://img.shields.io/badge/Code-12%2C372%20lines-blue)

---

## Overview

MIRAGE-UAS is a research testbed that integrates **Moving Target Defense (MTD)**, **agentic honeydrone deception**, and an **automated STIX 2.1 CTI pipeline** for Unmanned Aerial System (UAS / drone) cybersecurity.

| | |
|---|---|
| **Target Venue** | ACM CCS 2026 Cycle B (paper under review) |
| **Institution** | Distributed Security Lab (DS Lab), Kyonggi University, South Korea (분산보안연구실, 경기대학교) |
| **Funding** | Defense Acquisition Program Administration — DAPA (국방기술진흥연구소, 과제번호 915024201) |
| **Key Claim** | First framework integrating MTD + OpenClaw-inspired agentic honeydrone + automated STIX 2.1 CTI dataset generation for UAS security |

The system deploys honey drones — Docker-containerized decoy instances with an autonomous AI agent inside — that deceive attackers across all skill levels (L0 script kiddie → L4 APT), while simultaneously generating a labeled cyberthreat intelligence dataset mapped to MITRE ATT&CK for ICS v14.

### Latest Experiment Results

| Metric | Value |
|--------|-------|
| **DeceptionScore** | **0.714** |
| Engine Mode | `real_openclaw` (real OpenClawAgent running) |
| Total Sessions | 228 |
| Engagement Rate | 75.9% |
| Real Drone Breaches | 0 (100% protection) |
| MTD Triggers | 46 |
| Breadcrumbs | 30 planted → 31 followed |
| Ghost Connections | 6 |
| Dataset | 1,007 samples, 12 ATT&CK TTPs |
| Codebase | 36 modules, 12,372 lines |

---

## Quick Start

### One Command — Full Pipeline

```bash
bash run.sh
```

Runs: dry-run experiment → Docker experiment → metrics → figures → dashboard.
Opens **http://localhost:8888** with 17 live charts.

### Run Modes

| Command | What it does | Time |
|---------|-------------|------|
| `bash run.sh` | Full pipeline (stub mode) + dashboard | ~5 min |
| `bash scripts/run_full.sh` | **Real OpenClawAgent** + Docker + attacker | ~5 min |
| `bash scripts/run_multiview.sh` | Real agent + multi-terminal live view | ~5 min |
| `bash scripts/start_obs.sh --run` | **tmux 5-pane observatory** + experiment | ~5 min |
| `bash scripts/start_obs.sh` | Attach observatory to running experiment | instant |
| `bash scripts/start_dashboard.sh` | Dashboard only | instant |

### Setup

```bash
git clone https://github.com/PoppyPoppyPiggy/OpenClaw_Drone_MTD.git
cd OpenClaw_Drone_MTD
cp config/.env.example config/.env    # fill research parameters
pip install -r requirements.txt
bash run.sh
```

---

## Architecture

```
 Attacker (L0-L4)
     │
     ▼
 ┌─────────────────────────────────────────────────────┐
 │        Honey Drone Stack (Docker × 3)               │
 │  ┌──────────┐ ┌────────────┐ ┌──────────────────┐  │
 │  │ FCU Stub │ │  CC Stub   │ │  OpenClawAgent   │  │
 │  │ TCP:5760 │ │ UDP:14550  │─│  (host process)  │  │
 │  │          │ │ HTTP:80    │ │  5 async tasks    │  │
 │  │          │ │ WS:18789   │ │  phase detection  │  │
 │  │          │ │ Ghost:19k+ │ │  fingerprinting   │  │
 │  └──────────┘ └────────────┘ └──────────────────┘  │
 │       │              │               │              │
 │  ┌────┴──────────────┴───────────────┴──────────┐  │
 │  │  Track A: MTD Controller (Docker SDK)         │  │
 │  │  Track B: CTI Pipeline (STIX 2.1 → Dataset)  │  │
 │  └──────────────────────────────────────────────┘  │
 └─────────────────────────────────────────────────────┘
```

**Host-Proxy Pattern**: Attacker packets arrive at Docker cc_stub → forwarded via UDP to real AgenticDecoyEngine on host → OpenClawAgent generates phase-adaptive response → sent back through cc_stub to attacker.

---

## API & Downloads

Start the server: `bash scripts/start_dashboard.sh`

| Endpoint | Description |
|----------|-------------|
| `http://localhost:8888/docs` | **Swagger UI** — interactive API explorer |
| `http://localhost:8888` | **Dashboard** — 17 live charts, 3 tabs |
| `http://localhost:8888/api/v1/download/excel` | **Excel download** — 8-sheet .xlsx workbook |
| `http://localhost:8888/api/v1/download/dataset` | **Dataset CSV** — DVD-CTI-Dataset-v1 |
| `http://localhost:8888/api/v1/experiment/summary` | Experiment summary JSON |
| `http://localhost:8888/api/v1/experiment/deception-score` | DS 5-component breakdown |
| `http://localhost:8888/api/v1/engagement/by-level` | Table II by attacker level |
| `http://localhost:8888/api/v1/mtd/latency` | Table III MTD action latency |
| `http://localhost:8888/api/v1/deception/breadcrumbs` | Breadcrumb funnel stats |
| `http://localhost:8888/api/v1/agent/state` | OpenClaw agent internal state |
| `http://localhost:8888/api/v1/agent/packets` | Per-packet binary decode |

---

## Key Components

| Component | File | Lines | Role |
|-----------|------|-------|------|
| OpenClaw Agent | `src/honey_drone/openclaw_agent.py` | 1,170 | Autonomous deception — 5 behaviors, phase detection, fingerprinting |
| Agentic Decoy Engine | `src/honey_drone/agentic_decoy_engine.py` | 600 | Core engine — integrates agent + tracker + response gen |
| MTD Executor | `src/mtd/mtd_executor.py` | 410 | Docker SDK MTD actions (port/IP/key/service) |
| STIX Converter | `src/cti_pipeline/stix_converter.py` | 320 | STIX 2.1 compliant bundle generation |
| ATT&CK Mapper | `src/cti_pipeline/attck_mapper.py` | 304 | 12 ATT&CK for ICS v14 TTP mappings |
| Deception Scorer | `src/evaluation/deception_scorer.py` | 180 | DS = w₁·time + w₂·breach + w₃·confusion + w₄·breadcrumb + w₅·ghost |
| Dashboard | `results/dashboard/server.py` | 544 | FastAPI with 20+ endpoints + Excel download |
| Attacker Sim | `scripts/attacker_sim.py` | 480 | Automated L0→L4 attacker (UDP/HTTP/WS/RTSP/SSH) |

---

## Paper Contributions

1. **C1**: First MTD + Agentic Decoy framework for UAS — extends D3GF (Seo et al., IEEE Access 2023) with real decoy deployment
2. **C2**: First honeypot-derived labeled UAS CTI dataset (DVD-CTI-Dataset-v1) with STIX 2.1 + ATT&CK for ICS v14
3. **C3**: OpenClaw-inspired agentic honeydrone design pattern — autonomous deception without operator intervention

---

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Complete technical documentation (end-to-end flow) |
| [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md) | Data schemas, MAVLink binary layout, STIX examples |
| [docs/ATTACK_PACKETS.md](docs/ATTACK_PACKETS.md) | Per-level packet specs with hex dumps |
| [docs/OMNETPP_INTEGRATION.md](docs/OMNETPP_INTEGRATION.md) | OMNeT++ replay setup and validation |
| [LAB_REPORT.md](LAB_REPORT.md) | Latest experiment report for lab meetings |

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

## License

MIT License — for research and educational use.

## Contact

**Distributed Security Lab (DS Lab)**, Kyonggi University | DAPA Project 915024201
