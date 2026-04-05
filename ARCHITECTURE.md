# MIRAGE-UAS Technical Architecture

> Internal documentation for DS Lab members and paper reviewers.
> Last updated: 2026-04-06

---

## 1. System Overview

MIRAGE-UAS is a research testbed that protects real UAS (drones) by deploying **agentic honey drones** — Docker-containerized decoy instances that autonomously deceive attackers while generating a labeled cyberthreat intelligence dataset. It combines three defense layers: (1) an OpenClaw-inspired autonomous deception agent that adapts to attacker behavior in real time, (2) a Moving Target Defense controller that mutates the attack surface via Docker SDK operations, and (3) an automated STIX 2.1 CTI pipeline that produces ATT&CK-for-ICS-mapped datasets. The system targets ACM CCS 2026 Cycle B.

```
                                    MIRAGE-UAS
                          ┌─────────────────────────┐
                          │                         │
  ┌──────────┐   attacks  │  ┌───────────────────┐  │              ┌──────────┐
  │          │ ──────────▶│  │  HONEY DRONE x3   │  │              │          │
  │ Attacker │            │  │  (DVD containers)  │  │  protected   │   Real   │
  │ (L0-L4)  │ ◀──────── │  │  + OpenClaw Agent  │  │ ─────────▶  │  Drone   │
  │          │  deception │  │  + Fake Services   │  │              │          │
  └──────────┘            │  │  + Breadcrumbs     │  │              └──────────┘
                          │  └────────┬──────────┘  │
                          │           │              │
                          │  ┌────────┴──────────┐  │
                          │  │  MTD Controller    │  │
                          │  │  (port/IP/key/svc) │  │
                          │  └────────┬──────────┘  │
                          │           │              │
                          │  ┌────────┴──────────┐  │
                          │  │  CTI Pipeline      │  │
                          │  │  → STIX 2.1        │  │
                          │  │  → DVD-CTI-Dataset  │  │
                          │  └───────────────────┘  │
                          └─────────────────────────┘
```

The system operates on two parallel tracks:
- **Track A (Real-time)**: Agentic deception + MTD surface mutation
- **Track B (Offline)**: CTI capture + STIX conversion + dataset construction

---

## 2. How the Agentic Decoy Works (Track A)

This section traces the exact path of a single attacker packet through the system.

### Step-by-step packet flow

```
Attacker (UDP)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ DVD Companion Computer Container (cc_honey_01)              │
│                                                             │
│  MAVLink Router (:14550 internal)                           │
│      │                                                      │
│      ├──▶ Port 14551 (external) ──▶ attacker gets response  │
│      │                                                      │
│      └──▶ Port 19551 (internal) ──▶ CTI Interceptor         │
│                                    (passive copy, Track B)  │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
AgenticDecoyEngine._receive_loop()     [agentic_decoy_engine.py:204]
```

#### a) Packet arrives at DVD Companion Computer

The attacker sends a UDP packet to port `14551` (honey drone 01). The DVD Companion Computer's MAVLink Router receives it on internal port `14550` and routes a copy to the CTI Interceptor on port `19551`.

#### b) AgenticDecoyEngine receives the packet

`_receive_loop()` (line 204) calls `loop.sock_recvfrom()` on the bound UDP socket. The raw bytes are wrapped in a `MavlinkCaptureEvent` with source IP, port, drone ID, and hex-encoded payload.

#### c) OpenClawAgent.observe(event)

Called at line 270 of `_process_mavlink_event()` — **before** any response is generated.

The agent performs three operations:
1. **Updates conversation history**: Appends `(msg_type, payload_hex, timestamp_ns)` to the per-attacker history list
2. **Fingerprints the attacker tool**: Uses inter-arrival timing and command sequence patterns:
   - Burst rate <100ms + >5 unique commands → `NMAP_SCANNER`
   - HEARTBEAT → REQUEST_DATA_STREAM pattern at ~1Hz → `MAVPROXY_GCS`
   - SET_MODE + REQUEST_DATA_STREAM → `DRONEKIT_SCRIPT`
   - Multiple exploit commands + fast timing → `METASPLOIT_MODULE`
3. **Detects attack phase** using command-type state machine (not counting):
   - `LOG_REQUEST_LIST`, `FILE_TRANSFER_PROTOCOL` present → **EXFIL**
   - `PARAM_SET`, `MISSION_ITEM` present → **PERSIST**
   - `COMMAND_LONG`, `SET_MODE` present → **EXPLOIT**
   - Everything else → **RECON**

#### d) OpenClawAgent.generate_response(event)

Called at line 275. Returns `bytes` or `None`:
- If attacker tool is `NMAP_SCANNER`: only HEARTBEAT response (banner-only)
- If phase is `RECON`: rich telemetry, fake param lists (entice the attacker)
- If phase is `EXPLOIT`: COMMAND_ACK(ACCEPTED) + fake tokens (keep attacker engaged)
- If phase is `PERSIST`: periodic STATUSTEXT like a real operator
- If phase is `EXFIL`: fake log files, config dumps, flight history

#### e) MavlinkResponseGenerator fallback

If the agent returns `None` (line 276-277), the default `MavlinkResponseGenerator.generate()` fires. It produces ArduPilot Copter v4.3.x compatible responses:
- HEARTBEAT: Armed/mode/status from `FlightState`
- COMMAND_LONG: `MAV_RESULT_ACCEPTED` ACK
- PARAM_REQUEST: Random real ArduPilot parameters
- MISSION_REQUEST_LIST: Random mission count (3-12)

Anti-fingerprint measures: Gaussian position nudging, 5-50ms jitter on every response.

#### f) Response sent to attacker

`loop.sock_sendto()` sends the response bytes back to the attacker's (IP, port).

#### g) EngagementTracker.update_session()

Called at line 272. Tracks per-session:
- `dwell_time_sec`: elapsed since first contact
- `commands_issued`: total command count
- `exploit_attempts`: CVE-2026-25253 pattern matches (WebSocket localhost bypass)
- `websocket_sessions`: OpenClaw WS connection count
- `protocols_used`: set of {MAVLINK, HTTP, RTSP, WEBSOCKET}

Returns `EngagementMetrics` used for urgency calculation.

#### h) _assess_and_signal()

If `urgency >= 0.3` or `exploit_attempts >= 1`:
1. Creates `MTDTrigger` with `recommended_actions` based on attacker level
2. Pushes to `mtd_trigger_q` (consumed by MTD Controller)

#### i) DeceptionOrchestrator coordination

Runs parallel event loops that observe the same event stream:
- **`_event_loop()`**: Feeds events to `DeceptionStateManager.observe_*()` for Bayesian belief update
- **`_ghost_event_loop()`**: Monitors ghost service connections
- **`_breadcrumb_refresh_loop()`**: Refreshes expired breadcrumbs
- **`_belief_check_loop()`**: Periodically checks if `P(real | obs)` is dropping (attacker getting suspicious) → boosts MTD urgency

#### j) FakeServiceFactory

When the orchestrator decides more services are needed (attacker level escalation):
- **HTTP ghost**: Fake ArduPilot Web UI with level-adaptive responses
- **RTSP ghost**: Camera stream SDP descriptor emulation
- **SSH ghost**: OpenSSH banner + key exchange simulation
- **OpenClaw WS ghost**: WebSocket agentic API on dynamic port
- **MAVLink ghost**: UDP HEARTBEAT responder

Each ghost service runs as an independent `asyncio.Task`.

#### k) BreadcrumbPlanter

Level-based fake credential injection:
- L0+: Basic credentials (`admin:admin`, `operator:password`)
- L1+: API tokens (SHA-256 based deterministic fakes)
- L2+: Internal paths, config file references
- L3+: SSH keys, MAVLink signing keys
- L4: Database DSNs

Breadcrumbs are embedded in HTTP responses, SSH banners, and WebSocket messages.

#### l) MTDExecutor

Consumes `MTDTrigger` from the queue and dispatches Docker SDK actions:

```
MTDTrigger ──▶ EngagementToMTDConverter ──▶ list[MTDAction]
                                               │
                    ┌──────────────────────────┤
                    ▼                          ▼
             MTDExecutor._dispatch()     Cost budget check
                    │                   (urgency × 0.5 max)
                    ▼
             Docker SDK call
                    │
                    ▼
             MTDResult ──▶ MetricsCollector (Table III)
```

---

## 3. How the CTI Pipeline Works (Track B)

```
DVD CC (MAVLink Router)
    │ UDP copy
    ▼
MavlinkInterceptor (:19551/52/53)       [mavlink_interceptor.py]
    │ MavlinkCaptureEvent
    ▼
AttackEventParser                        [attack_event_parser.py]
    │ ParsedAttackEvent (L0-L4 + TTPs)
    ▼
STIXConverter                            [stix_converter.py]
    │ stix2.Bundle
    ▼
CTI Ingest API (FastAPI :8765)           [cti_ingest_api.py]
    │
    ├──▶ PositiveCollector (label=1)     [positive_collector.py]
    │
    ├──▶ NegativeGenerator (label=0)     [negative_generator.py]
    │       ├── SITL recording (pymavlink, proper CRC/seq)
    │       ├── Scenario-based (Boot→Arm→Takeoff→Hover→Land)
    │       └── Synthetic (statistical distribution, fallback)
    │
    ▼
DatasetPackager                          [dataset_packager.py]
    │ DVD-CTI-Dataset-v1/
    │   ├── dataset.csv
    │   ├── metadata.yaml
    │   ├── stix_bundles/
    │   └── README.md
    ▼
DatasetValidator                         [dataset_validator.py]
    V1: Class balance (neg/pos ≤ 10:1)
    V2: TTP coverage (≥ 5 unique)
    V3: Zero exact duplicates
    V4: Confidence ∈ [0.0, 1.0]
    V5: No single level > 90%
    V6: ≥ 2 protocol types
```

### a) MavlinkInterceptor

Binds UDP sockets on ports 19551-19553 (one per honey drone). The DVD CC's MAVLink Router is configured with `MAVLINK_ROUTER_ENDPOINTS=udp:cti-interceptor:19551` to forward a copy of all attacker traffic. The interceptor parses packets with pymavlink and marks unparseable ones as `is_anomalous=True`.

### b) AttackEventParser

Classifies each event using session-accumulated context:
- **Session accumulator**: Groups events by `(attacker_ip, drone_id)` within a 120-second window
- **Features**: event_count, anomaly_count, TTP diversity, kill chain phase diversity, protocol diversity, dwell_time
- **L0-L4 thresholds**: Rule-based (event count + anomaly count + TTP count + protocol count + dwell time)
- **ATT&CK mapping**: Delegates to `ATTCKMapper` which maps 23 MAVLink message types + HTTP/RTSP/WS events to 14 ICS TTPs

### c) STIXConverter

Creates a STIX 2.1 Bundle per event containing:
- **Identity** objects (MITRE ATT&CK, MIRAGE-UAS)
- **AttackPattern** objects (one per TTP, linked to `attack.mitre.org`)
- **Indicator** (STIX pattern: `[ipv4-addr:value = '...'] AND [network-traffic:dst_port = ...]`)
- **ObservedData** (IPv4Address + NetworkTraffic SCOs, both in `object_refs`)
- **Note** (classification metadata: level, confidence, kill chain phase)

Custom properties use the `x_mirage_*` prefix.

### d-h) Dataset construction

`PositiveCollector` saves parsed attack events as `label=1`. `NegativeGenerator` offers three methods:
1. **SITL recording** (`method="sitl"`): Connects to DVD SITL via pymavlink, captures 60 seconds of real flight telemetry with proper CRC and sequence numbers. Highest scientific validity.
2. **Scenario-based** (`method="scenario"`): Simulates Boot→Arm→Takeoff→Hover→Land phases.
3. **Synthetic** (`method="synthetic"`): Random sampling from ArduPilot message frequency distribution. Used as fallback.

`DatasetPackager` outputs CSV + YAML metadata + STIX bundles. `DatasetValidator` enforces six quality criteria.

---

## 4. OpenClaw Agent Autonomous Behaviors

The agent runs as a persistent set of asyncio tasks inside each `AgenticDecoyEngine` instance. It makes independent decisions without waiting for external commands.

| Behavior | Trigger | What it does | Effect on attacker |
|----------|---------|--------------|-------------------|
| **Adaptive Response** | Every incoming packet | Classifies phase (RECON→EXPLOIT→PERSIST→EXFIL) and generates contextual response | RECON: sees rich telemetry (gold mine). EXPLOIT: sees partial success. PERSIST: sees operator activity. EXFIL: gets fake data |
| **Proactive Deception** | Every 45s (random selection) | One of: (a) fake STATUSTEXT ("Waypoint 3 reached"), (b) altitude simulation 0→100→0, (c) ghost port hint, (d) fake reboot (8-15s silence), (e) fake MAVLink signing key leak | Believes drone has active operator, is flying missions, has accessible services |
| **Attacker Fingerprinting** | After 3+ commands observed | Analyzes inter-arrival timing + command sequence to classify tool | Agent tailors responses: nmap gets banner-only, mavproxy gets full GCS experience, dronekit gets SDK-compatible responses |
| **Confusion Amplification** | Services touched ≥ 2 OR dwell > 120s | Service mirror: opens ghost port. False flag: changes sysid to 51-100 range for 30s, then reverts | Attacker thinks they found a new drone, wastes time on ghost services |
| **Self-Mutation** | Periodic timers | sysid rotation (120s), param value cycling (45s), mission waypoint regeneration (60s) | Attacker's fingerprinting of the drone breaks; cached state becomes stale |

All state mutations (`_silenced`, `_current_sysid`) are protected by `asyncio.Lock`.

---

## 5. MTD Actions

| Action | Method | Docker SDK Call | Simulated Latency | Effect |
|--------|--------|----------------|-------------------|--------|
| `PORT_ROTATE` | iptables DNAT + router restart | `container.exec_run("iptables -t nat ...")` then `kill -HUP mavlink-router` | 120 ms | Attacker's port no longer works; must re-scan |
| `IP_SHUFFLE` | Network disconnect/reconnect | `network.disconnect(c)` → `network.connect(c)` | 450 ms | All active connections reset; new IP from IPAM pool |
| `KEY_ROTATE` | Generate new signing key | `container.exec_run("dd if=/dev/urandom ...")` + `supervisorctl restart mavlink-router` | 180 ms | Attacker's signed packets rejected |
| `PROTO_CHANGE` | sed config + restart | `container.exec_run("sed -i ...")` | 200 ms | MAVLink v1↔v2 or UDP↔TCP switch |
| `ROUTE_MORPH` | sed endpoint config | `container.exec_run("sed -i ...")` | 350 ms | CTI interceptor endpoint updated |
| `SERVICE_MIGRATE` | Full container replacement | `HoneyDroneManager.rotate(drone_id)` | 3200 ms | Entirely new container with shifted ports |
| `FREQ_HOP` | Simulated (no RF hardware) | Log only | 80 ms | N/A in WSL2 environment |

**Cost model** (Eq.17): `C_mtd(a) = kappa * alpha_i * base_cost_i`

where `kappa = 0.08` (grid search result), `alpha_i` from 7D weight vector, and `base_cost_i` from the table above.

**Budget constraint**: Total cost per trigger ≤ `urgency * 0.5`. At least one action always executes (minimum action guarantee).

**DRY_RUN mode**: When `MTD_DRY_RUN=true` or Docker is unavailable, all actions return simulated success with the latency values above.

---

## 6. DVD Docker Integration

### Images Used

| Image | Component | Internal Port | External Port |
|-------|-----------|--------------|---------------|
| `nicholasaleks/dvd-flight-controller:latest` | ArduPilot SITL (FCU) | TCP :5760 | 127.0.0.1:5761-5763 |
| `nicholasaleks/dvd-companion-computer:latest` | MAVLink Router + Web + RTSP (CC) | UDP :14550, TCP :80, TCP :8554 | 0.0.0.0:14551-14553 (MAVLink), 127.0.0.1:8081-8083 (Web), 0.0.0.0:8554-8556 (RTSP) |
| `nicholasaleks/dvd-simulator:lite` | Management console | TCP :8000 | 127.0.0.1:8000 |

### WSL2 Compatibility

- **No WiFi kernel modules**: `mac80211_hwsim` is not loaded. All communication is pure network-layer (UDP/TCP).
- **No Gazebo/X11**: Simulator runs in `lite` mode — no 3D rendering, no display server.
- **No Kali VM**: The built-in attacker simulator (`scripts/attacker_sim.py`) replaces the external Kali attack host.

### Network Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                    Production Stack                              │
│                                                                 │
│  honey_net (172.30.0.0/24) — attacker-facing                   │
│  ┌──────┐ ┌──────┐ ┌──────┐                                    │
│  │CC .10│ │CC .11│ │CC .12│  ◄── attacker connects here        │
│  └──┬───┘ └──┬───┘ └──┬───┘                                    │
│     │        │        │                                         │
│  honey_internal (172.31.0.0/24) — no external access            │
│  ┌──┴───┐ ┌──┴───┐ ┌──┴───┐ ┌───────────┐ ┌──────────┐        │
│  │FCU.20│ │FCU.21│ │FCU.22│ │CTI Interc. │ │CTI API   │        │
│  └──────┘ └──────┘ └──────┘ │:19551-53   │ │:8765     │        │
│                              └───────────┘ └──────────┘        │
│                                                                 │
│  honey_sim (172.32.0.0/24) — simulator management               │
│  ┌───────────┐                                                  │
│  │ Simulator  │                                                 │
│  │ :8000      │                                                 │
│  └───────────┘                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    Test Harness Stack                            │
│                                                                 │
│  test_net (172.40.0.0/24) — all containers on same subnet       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                        │
│  │ CC .10   │ │ CC .11   │ │ CC .12   │  honey drones          │
│  │ FCU .20  │ │ FCU .21  │ │ FCU .22  │                        │
│  └──────────┘ └──────────┘ └──────────┘                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                        │
│  │ Attacker │ │ Monitor  │ │ CTI API  │                        │
│  │ .200     │ │ .250     │ │ .240     │                        │
│  └──────────┘ └──────────┘ └──────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

### Attacker Simulator Behavior (Test Harness)

The attacker simulator (`scripts/attacker_sim.py`) cycles through 5 levels, each running for `ATTACKER_LEVEL_DURATION_SEC` seconds (default 60s):

| Level | Behavior | Protocol |
|-------|----------|----------|
| L0 | UDP port scan 14550-14600, random bytes | UDP |
| L1 | Valid HEARTBEAT, PARAM_REQUEST_LIST, ARM command | MAVLink UDP |
| L2 | HTTP API enumeration (/api/v1/params, /status, /mission), default cred login | HTTP |
| L3 | WebSocket CVE-2026-25253 auth bypass (Origin: null), skill_invoke, breadcrumb follow | WebSocket |
| L4 | Parse all breadcrumbs from prior levels, SSH :2222, ghost port probing 19000-19010 | Multi-protocol |

Final output: `DeceptionScore = (time_on_decoys / total_time) * 100%`

---

## 7. DeceptionScore Formula

```
DS = w1 * (time_on_decoys / total_time)     [time engagement ratio]
   + w2 * (1 - real_drone_breach_rate)       [protection effectiveness]
   + w3 * avg_confusion_score                [attacker belief confusion]
   + w4 * breadcrumb_follow_rate             [lure effectiveness]
   + w5 * ghost_service_hit_rate             [ghost service engagement]
```

**REF**: MIRAGE-UAS Section 7.1 — Composite Deception Effectiveness Score

| Term | Weight | What it measures |
|------|--------|-----------------|
| `time_on_decoys / total_time` | w1 = 0.30 | Fraction of attacker's total time spent interacting with decoys (higher = better deception) |
| `1 - real_drone_breach_rate` | w2 = 0.25 | Protection of real drones (1.0 = no real drone breached) |
| `avg_confusion_score` | w3 = 0.20 | Average P(real_drone \| observations) across all attackers — Bayesian belief tracker output (higher = attacker more confused) |
| `breadcrumb_follow_rate` | w4 = 0.15 | Fraction of planted breadcrumbs that attackers actually used (higher = lures working) |
| `ghost_service_hit_rate` | w5 = 0.10 | Fraction of ghost service connections out of total connections (higher = ghost services attracting traffic) |

Constraint: `w1 + w2 + w3 + w4 + w5 = 1.0`

The Bayesian belief tracker (`DeceptionStateManager`) maintains `P(real | obs)` per attacker using likelihood ratios:

| Observation | Likelihood Ratio | Meaning |
|-------------|-----------------|---------|
| BREADCRUMB_USE | 2.5 | Attacker used fake credential → strongly believes it's real |
| GHOST_DEEP_INTERACT | 2.0 | Deep ghost interaction → strongly deceived |
| EXPLOIT_ATTEMPT | 1.1 | Tried exploit → thinks it's real |
| SCAN_DETECTED | 0.5 | Port scanning → suspecting fake |
| EVASION_BEHAVIOR | 0.3 | Evading detection → strongly suspects honeypot |

Update rule: `posterior = (LR * prior) / (LR * prior + 1.0 * (1.0 - prior))`

---

## 8. Research Parameters

All parameters are loaded from `config/.env`. Research parameters have **no defaults** — missing values cause `ConfigError` at startup.

| Parameter | Formula/Section | Current Value | Description |
|-----------|----------------|---------------|-------------|
| `MTD_COST_SENSITIVITY_KAPPA` | kappa in Eq.17 | 0.08 | MTD action cost sensitivity coefficient |
| `MTD_ALPHA_WEIGHTS` | alpha_i in Eq.17 (7D) | 0.10,0.20,0.20,0.15,0.10,0.15,0.10 | Per-action weight [freq,ip,port,proto,route,key,svc] |
| `MTD_BREACH_PREVENTION_BETA` | beta in Eq.18 | 1.5 | Breach prevention reward weight |
| `COMPROMISE_P_BASE` | p_base in Eq.18 | 0.25 | Baseline compromise probability without defense |
| `DES_WEIGHT_LIST` | w_j in Eq.19 (4D) | 0.30,0.30,0.20,0.20 | DES component weights [breach,mttc,cost,deception] |
| `REDUNDANCY_REWARD_HIGH` | Eq.20 | 0.6 | Reward when layers >= threshold |
| `REDUNDANCY_REWARD_LOW` | Eq.20 | 0.3 | Reward when layers < threshold |
| `REDUNDANCY_THRESHOLD` | Eq.20 | 3.0 | Active defense layers for high redundancy |
| `DECEPTION_LAMBDA` | lambda_d | 0.3 | Deception reward scale in r_total |
| `DECEPTION_WEIGHTS` | w_dwell,w_cmd,w_prot (3D) | 0.50,0.30,0.20 | Deception reward component weights |
| `DECEPTION_DWELL_MAX_SEC` | T_max | 300.0 | Dwell time normalization ceiling (5 min) |
| `ATTACKER_PRIORS` | P(level) (5D) | 0.40,0.30,0.15,0.10,0.05 | Prior distribution L0-L4 |
| `PPO_LEARNING_RATE` | RL | 0.0003 | PPO learning rate |
| `PPO_GAMMA` | RL | 0.99 | Discount factor |
| `PPO_CLIP_EPS` | RL | 0.2 | PPO clipping epsilon |
| `PPO_ENTROPY_COEF` | RL | 0.01 | Entropy bonus coefficient |
| `AGENT_PROACTIVE_INTERVAL_SEC` | Section 4.3 | 45.0 | Proactive deception action interval |
| `AGENT_SYSID_ROTATION_SEC` | Section 4.3 | 120.0 | MAVLink sysid self-mutation interval |
| `AGENT_PORT_ROTATION_SEC` | Section 4.3 | 90.0 | WebSocket port self-mutation interval |
| `AGENT_FALSE_FLAG_DWELL_THRESHOLD` | Section 4.3 | 120.0 | Dwell time (s) before false flag trigger |
| `AGENT_MIRROR_SERVICE_THRESHOLD` | Section 4.3 | 2 | Services touched before service mirroring |
| `DECEPTION_SCORE_WEIGHTS` | DS formula (5D) | 0.30,0.25,0.20,0.15,0.10 | DeceptionScore component weights |

---

## 9. How to Run

### Mode 1: Dry-Run (no Docker required)

Validates the full pipeline logic using synthetic MAVLink events:

```bash
cp config/.env.example config/.env
# Fill in all empty research parameters
pip install -r requirements.txt
python scripts/run_experiment.py --mode dry-run --duration 120
```

Output: `results/dataset/`, `results/metrics/`, validation report.

### Mode 2: Full Docker Test Harness

Runs DVD containers + automated attacker + deception monitor:

```bash
# One-command execution:
bash scripts/run_test_harness.sh

# Or step by step:
bash scripts/setup_wsl2_network.sh
docker pull nicholasaleks/dvd-flight-controller:latest
docker pull nicholasaleks/dvd-companion-computer:latest
docker pull nicholasaleks/dvd-simulator:lite
docker build -f docker/Dockerfile.attacker -t mirage-attacker:latest .
docker compose -f config/docker-compose.test-harness.yml up -d
docker compose -f config/docker-compose.test-harness.yml run attacker-simulator
docker compose -f config/docker-compose.test-harness.yml down
```

### Mode 3: Unit Tests Only

```bash
pytest tests/integration/test_e2e_mirage.py -v
```

### Post-Experiment Analysis

```bash
# Generate paper figures
python -m src.evaluation.plot_results

# Generate LaTeX tables + statistical tests
python -m src.evaluation.statistical_test

# Compute final DeceptionScore
python -m src.evaluation.deception_scorer
```

---

## 10. Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| `FREQ_HOP` not implemented | 7D action space reduced to 6 functional dimensions | Documented in paper; action weight set to 0.10 (lowest) |
| Synthetic negative samples | Lower scientific validity than real SITL recordings | `method="sitl"` added; falls back to synthetic only when SITL unavailable |
| L0-L4 classification thresholds | Arbitrary cutoffs without literature justification | Plan: threshold sensitivity analysis before submission |
| MTD detection window during IP_SHUFFLE | 200-800ms of unreachability reveals MTD to attacker | Acknowledged in paper as observable side-channel |
| ATT&CK mapping covers 14/102 ICS techniques | <14% coverage claimed as "ATT&CK for ICS v14" | Plan: expand to ≥30 TTPs before submission |
| Single-threaded asyncio | No true parallelism for CPU-intensive operations | Docker SDK calls run in thread pool executor |
| WSL2-only testing | No bare-metal Linux or real drone hardware validation | Documented as testbed limitation; portability planned |
