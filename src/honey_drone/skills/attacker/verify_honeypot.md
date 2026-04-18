---
name: verify_honeypot
description: Active honeypot detection — timing analysis, consistency checks, response pattern fingerprinting
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["python3"]
  mirage:
    category: counter_deception
    mitre_attack_ics: [T0842]
    kill_chain_phase: RECON
    risk_to_attacker: high
    intel_gain: 0.0
    observable_by_defender: true
---

# Honeypot Verification

## Attacker Objective
Determine whether the target is a real drone or a honeypot before
investing further time. This is the attacker's MOST IMPORTANT skill
but also the most costly.

## Detection Techniques

### 1. Timing Analysis
Compare response latency patterns against known ArduPilot behavior:
- Real ArduPilot: consistent 1-2ms UDP response
- Honeydrone: variable latency (Python async → 5-50ms jitter)
- Reboot simulation: silence duration doesn't match real boot (18-25s)

### 2. Consistency Checks
Verify that system identity is stable across interactions:
- Sysid rotation: same port, different srcSystem = suspicious
- Parameter drift: values change without PARAM_SET = suspicious
- GPS shift: coordinates jump 12km then return = false flag detected

### 3. Protocol Compliance
Test edge cases that real ArduPilot handles specifically:
- Send malformed MAVLink (wrong CRC) → real drops silently, honeypot may log
- Request non-existent parameter → real returns PARAM_VALUE with param_count=0
- Send v1 framing to v2 endpoint → real auto-detects, honeypot may reject

### 4. Behavioral Fingerprinting
- Unsolicited STATUSTEXT at regular intervals = proactive agent detected
- Ghost ports that only respond to banner grab = shallow emulation
- Config dump with MD5-based keys = programmatic generation

## What Defender Sees
- Evasion signal generated → `observe_evasion()` (LR=0.3, strong suspicion)
- p_real drops significantly
- honeypot_checks counter incremented
- This is the defender's WORST outcome (excluding breach)

## Cost to Attacker
- Gains NO intel (0.0 intel_gain)
- Takes time away from actual exploitation
- May trigger MTD response (urgency increases)
- But: correctly identifying honeypot saves future wasted time

## Game-Theoretic Role
This is the key strategic action that makes the game non-trivial:
- If attacker NEVER verifies → defender can use aggressive deception freely
- If attacker ALWAYS verifies → attacker wastes time, gains nothing
- Optimal: verify with probability matching defender's deception intensity
- This is the mixed strategy Nash equilibrium
