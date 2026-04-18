---
name: operator_simulation
description: Simulate realistic drone operator activity via STATUSTEXT messages and telemetry patterns
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["python3"]
    primaryEnv: DRONE_ID
  mirage:
    category: deception
    mitre_engage: EAC0006
    mitre_d3fend: D3-SDP
    attack_phase: [RECON, PERSIST]
    effect_on_attacker: "Drone appears actively operated — attacker believes human operator present"
    detectability: 0.3
    risk: none
---

# Operator Activity Simulation

## When to Use
Invoke during RECON (to make drone appear active) and PERSIST (to make
attacker believe an operator is monitoring — increasing caution).

## Technique
Implements "Pocket Litter" — making the honeydrone appear lived-in:

### STATUSTEXT Injection
Unsolicited MAVLink STATUSTEXT messages simulating operator actions:

**Operator Messages** (PERSIST phase):
- "Waypoint 3 reached"
- "Camera started"
- "RTL initiated"
- "Battery low — switching to RTL"
- "Mission 2 uploaded successfully"
- "Payload released at WP5"
- "Mode change: GUIDED"

**System Messages** (RECON phase):
- "ArduCopter V4.3.7 (fmuv3)"
- "GPS 1: detected u-blox at 115200 baud"
- "EKF2 IMU0 is using GPS"
- "PreArm: Compass not healthy"

### Flight Simulation
12-step altitude profile simulating real flight:
```
Alt(m): 0 → 20 → 50 → 80 → 100 → 100 → 90 → 70 → 50 → 30 → 10 → 0
```
Each step broadcasts HEARTBEAT + STATUSTEXT with altitude.
Attacker monitoring telemetry sees realistic flight pattern.

### Parameter Drift
Gaussian noise (sigma=0.5%) applied to all 17 fake parameters every 45s.
Simulates real sensor noise — parameter values subtly change over time,
exactly as a real ArduPilot does during operation.

## Why This Works
Real drones continuously emit STATUSTEXT during operation. A honeypot
that only responds to queries (never initiates) is detectable by
advanced attackers checking for "liveness" indicators.
