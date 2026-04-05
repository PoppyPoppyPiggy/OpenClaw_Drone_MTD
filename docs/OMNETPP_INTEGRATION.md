# OMNeT++ Integration Guide

## 1. Why OMNeT++

MIRAGE-UAS includes OMNeT++ 6.x + INET 4.5 integration for two purposes:

1. **Deterministic replay**: Re-run the exact attack scenario in a controlled simulation to verify timing-dependent behaviors (MTD reaction latency, false flag duration, sysid rotation windows)
2. **ACM CCS Artifact Evaluation**: Meet the "Reproduced" badge criteria by providing a simulation-based reproduction path that doesn't require Docker or real network infrastructure

Reference: ACM Artifact Review and Badging v1.1

## 2. Setup on WSL2 Ubuntu

```bash
# Install OMNeT++ 6.x
wget https://github.com/omnetpp/omnetpp/releases/download/omnetpp-6.0.3/omnetpp-6.0.3-linux-x86_64.tgz
tar xzf omnetpp-6.0.3-linux-x86_64.tgz
cd omnetpp-6.0.3
source setenv
./configure && make -j$(nproc)

# Install INET 4.5
cd ../
git clone --branch v4.5.2 https://github.com/inet-framework/inet.git
cd inet
make makefiles && make -j$(nproc)
```

## 3. Replay Workflow

```
Step 1: Export traces from experiment results
  python3 -m src.omnetpp.trace_exporter

Step 2: Copy generated files to OMNeT++ project
  cp omnetpp_trace/UDPDroneNetwork.ned   inet/examples/mirage/
  cp omnetpp_trace/omnetpp.ini           inet/examples/mirage/
  cp omnetpp_trace/attack_scenario.xml   inet/examples/mirage/

Step 3: Run simulation
  cd inet/examples/mirage
  opp_run -m -n ../../src -l ../../src/INET omnetpp.ini

Step 4: Compare results
  python3 -c "
  from src.omnetpp.trace_exporter import export_traffic_trace
  # Compare simulated vs real packet counts
  "
```

## 4. Component Mapping

| MIRAGE-UAS Component | OMNeT++ INET Module | Role |
|---------------------|---------------------|------|
| DVD CC (MAVLink UDP :14550) | `StandardHost` + `UdpEchoApp` | Honey drone MAVLink responder |
| DVD CC (HTTP :80) | `StandardHost` + `TcpServerHostApp` | Web UI endpoint |
| OpenClaw WS (:18789) | `StandardHost` + `TcpServerHostApp` | WebSocket emulation |
| Attacker L0-L4 | `StandardHost` + `UdpBasicBurst` | Attack traffic generator |
| CTI Interceptor | `StandardHost` + `UdpSink` | Passive traffic tap |
| Docker network | `EtherSwitch` | Layer-2 switching |
| MTD PORT_ROTATE | Parameter change at runtime | `**.localPort` modification |
| MTD IP_SHUFFLE | Node address reassignment | `**.ip.address` modification |

## 5. Validation Criteria

After simulation replay, verify:

| Metric | Tolerance | Method |
|--------|-----------|--------|
| Total packet count | ±5% | Compare CSV row count vs simulation event count |
| Timing distribution | ±10% | K-S test on inter-arrival time distributions |
| DeceptionScore | ±0.05 | Recompute DS from simulated engagement metrics |
| MTD action count | exact match | Count parameter changes in simulation log |
| Attack phase transitions | exact match | Verify RECON→EXPLOIT→PERSIST→EXFIL in order |
