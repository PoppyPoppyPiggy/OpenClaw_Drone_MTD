---
name: identity_mutation
description: Rotate sysid, simulate reboot, execute false-flag identity pivot to confuse attacker tracking
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["python3"]
    primaryEnv: DRONE_ID
  mirage:
    category: mtd
    mitre_engage: EAC0003
    mitre_d3fend: D3-NHD
    attack_phase: [EXPLOIT, PERSIST]
    effect_on_attacker: "Drone appears as different system — attacker tracking invalidated"
    detectability: 0.7
    risk: medium
---

# Identity Mutation (Self-Morphing)

## When to Use
Invoke when the attacker has been engaged for extended periods (dwell > 120s)
or has identified specific system characteristics. This skill breaks attacker
fingerprinting by changing the drone's observable identity.

## Technique
Implements MITRE Engage "Introduced Vulnerabilities" (EAC0003) — intentionally
changing system identity to study attacker adaptation behavior:

### Sub-Behaviors

**Sysid Rotation** (every 120s):
- Change MAVLink srcSystem byte (1-254)
- Reconstruct pymavlink encoder with new identity
- All subsequent packets carry new sysid
- Attacker's nmap/mavproxy fingerprint becomes stale

**Reboot Simulation** (on demand):
- Set `_silenced = True` for 8-15 seconds
- Complete radio silence — no responses to any protocol
- Resume with NEW sysid (different system after "reboot")
- Attacker sees: silence → new identity = believable reboot

**False Flag** (dwell > 120s):
- Swap sysid to different range (51-100 vs normal 1-50)
- Shift GPS coordinates by ~12km
- Hold for 30 seconds, then revert
- Attacker believes they connected to a DIFFERENT drone

## Risk Assessment
High detectability (0.7) — timing analysis can detect simulated reboot
if silence duration doesn't match real ArduPilot boot time (18-25s).
Use sparingly against L3-L4 attackers who may run consistency checks.

## Interaction with verify_honeypot
If attacker uses `verify_honeypot` skill immediately after identity_mutation,
the timing inconsistency has high probability of detection. This creates
the core game-theoretic tradeoff:
- Defender: identity_mutation is powerful but detectable
- Attacker: verify_honeypot catches it but costs time
