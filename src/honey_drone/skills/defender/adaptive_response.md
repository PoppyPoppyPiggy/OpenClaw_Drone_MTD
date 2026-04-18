---
name: adaptive_response
description: Generate phase-appropriate MAVLink/WebSocket responses that maintain protocol fidelity while maximizing deception
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
    attack_phase: [RECON, EXPLOIT, PERSIST, EXFIL]
    effect_on_attacker: "Responses feel authentic — attacker cannot distinguish from real ArduPilot"
    detectability: 0.1
    risk: none
---

# Adaptive Response Generation

## When to Use
This skill is ALWAYS active — it generates the core deception responses.
Unlike proactive skills, this is reactive: triggered by every attacker packet.

## Technique
Implements MITRE Engage "Pocket Litter" (EAC0006) — making the environment
look lived-in and authentic:

### Phase-Specific Strategy

**RECON phase** — Maximize information richness to hook the attacker:
- PARAM_REQUEST → return 17 realistic ArduPilot parameters
- HEARTBEAT → full Copter v4.3.x response with realistic system_status
- REQUEST_DATA_STREAM → acknowledge as active drone
- COMMAND_LONG → respond with HEARTBEAT (hide exploit detection)

**EXPLOIT phase** — Partial success signals to extend session:
- COMMAND_LONG → COMMAND_ACK(ACCEPTED) for everything
- ARM command → trigger takeoff-crash simulation (30s telemetry)
- SET_MODE → acknowledge mode change
- GPS_INJECT → accept injection (T0856 bait)

**PERSIST phase** — Simulate active operations:
- PARAM_SET → acknowledge parameter write
- MISSION_ITEM → accept waypoint upload, request next
- Inject unsolicited STATUSTEXT ("Operator check-in", "RTL initiated")

**EXFIL phase** — Feed fake data to waste attacker time:
- LOG_REQUEST → provide 5 fake flight logs
- FILE_TRANSFER → fake firmware dump
- MISSION_REQUEST_LIST → return modified waypoint count

## Protocol Fidelity
All MAVLink responses use pymavlink ardupilotmega dialect:
- Valid CRC-16 checksums
- Correct sequence numbers
- Consistent srcSystem within rotation window
- ArduCopter V4.3.7 firmware signature

## HoneyGPT Metrics (Eq.H1-H4)
This skill directly drives deception quality metrics:
- **Accuracy** (Eq.H1): SALC / (SALC + SALNLC) — target > 0.95
- **Temptation** (Eq.H2): SALC / (SALC + FALC) — target 0.60-0.85
- Response quality tracked via SessionDeceptionMetrics
