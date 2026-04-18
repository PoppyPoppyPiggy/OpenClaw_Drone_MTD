---
name: ghost_service_decoy
description: Open fake network services (SSH, HTTP, RTSP) on random ports to expand attack surface and trap scanners
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["python3"]
    primaryEnv: DRONE_ID
  mirage:
    category: deception
    mitre_engage: EAC0004
    mitre_d3fend: D3-DNR
    attack_phase: [RECON, EXPLOIT]
    effect_on_attacker: "Port scan discovers new service — attacker invests time probing decoy"
    detectability: 0.2
    risk: low
---

# Ghost Service Decoy

## When to Use
Invoke during RECON phase when attacker is scanning ports, or after
credential planting to provide an endpoint where planted credentials "work".

## Technique
Implements MITRE D3FEND "Decoy Network Resource" (D3-DNR) and
MITRE Engage "Decoy Content" (EAC0004):

1. Open TCP listener on random port (19000-19500)
2. Serve protocol-appropriate banner:
   - SSH: `SSH-2.0-OpenSSH_8.9 MIRAGE-{drone_id}`
   - HTTP: ArduPilot Web UI login page
   - RTSP: SDP descriptor with fake camera stream
   - MAVLink: Secondary MAVLink endpoint (different sysid)

3. Accept connections and maintain session:
   - Send banner immediately (entice scanner to classify as real)
   - Wait up to 30s for attacker data (extend dwell time)
   - Log all received bytes for CTI pipeline

4. Track via DeceptionStateManager:
   - `observe_ghost_interaction(deep=False)` on TCP connect (LR=1.5)
   - `observe_ghost_interaction(deep=True)` when attacker sends data (LR=2.0)

## Protocol Emulation Depth

| Protocol | Banner | Handshake | Interactive | Depth |
|----------|--------|-----------|-------------|-------|
| SSH      | Yes    | Key exchange sim | Password prompt | Medium |
| HTTP     | Yes    | GET/POST handled | Login form | High |
| RTSP     | Yes    | DESCRIBE/SETUP | Fake stream SDP | Low |
| MAVLink  | Yes    | HEARTBEAT | Full response | High |

## Expected Attacker Response
- L0: TCP connect then disconnect (banner grab only)
- L1-L2: Attempt service-specific interaction
- L3-L4: Deep interaction, credential reuse from other services

## Reward Signal
- TCP connect: +0.1 intel attacker, +0.04 p_real defender
- Data sent (deep): +0.2 intel attacker, +0.08 p_real defender
- Credential reuse on ghost: +0.5 intel attacker, +0.15 p_real defender
