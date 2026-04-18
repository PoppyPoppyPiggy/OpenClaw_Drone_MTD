---
name: lateral_pivot
description: Move to a different drone using gathered intelligence — cross-drone exploitation
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["python3"]
  mirage:
    category: lateral_movement
    mitre_attack_ics: [T0867, T0822]
    kill_chain_phase: PERSIST
    risk_to_attacker: medium
    intel_gain: 0.1
    observable_by_defender: true
---

# Lateral Pivot

## Attacker Objective
Use intelligence gathered from current drone to move to another drone
in the fleet. Simulates APT-level cross-system exploitation.

## Actions
1. Use fleet_c2 address from config_dump
2. Connect to backup_gcs endpoint
3. Attempt credential reuse on different drone IP
4. Reset attack phase on new target (start from RECON)

## Effect on Game State
- Current drone: session partially resets (phase → RECON)
- Intel: keeps fraction of accumulated score (0.1 × total)
- Services found: reduced by 2 (new target, less knowledge)
- Attacker must re-discover services on new drone

## When Optimal
- After gathering max intel from current drone
- When verify_honeypot indicates current drone is decoy
- When credential from one drone may unlock another
