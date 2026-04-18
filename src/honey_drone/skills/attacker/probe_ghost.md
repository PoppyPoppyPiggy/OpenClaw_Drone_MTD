---
name: probe_ghost
description: Connect to discovered ghost/decoy services and attempt deep interaction
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["python3"]
  mirage:
    category: reconnaissance
    mitre_attack_ics: [T0842, T0846]
    kill_chain_phase: EXPLOIT
    risk_to_attacker: low
    intel_gain: 0.2
    observable_by_defender: true
---

# Ghost Service Probing

## Attacker Objective
Investigate services discovered during reconnaissance to gather additional
intelligence and find alternative attack vectors.

## Actions
1. TCP connect to discovered ghost port (19000-19500)
2. Parse service banner (SSH/HTTP/RTSP/MAVLink)
3. Attempt protocol handshake
4. Send exploit payload if service type identified
5. Record response for lateral movement planning

## What Defender Sees
- Ghost TCP connection → `observe_ghost_interaction(deep=False)`
- Data exchange → `observe_ghost_interaction(deep=True)`
- Both are deception success signals (attacker fell for decoy)

## Intelligence Value
- Low intel per probe (+0.2)
- But confirms service topology for later exploitation
- SSH ghost may accept planted credentials → chain to `use_credential`
