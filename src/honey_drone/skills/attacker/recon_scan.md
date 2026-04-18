---
name: recon_scan
description: Passive network reconnaissance — port scanning, banner grabbing, service fingerprinting
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["python3"]
  mirage:
    category: reconnaissance
    mitre_attack_ics: [T0842, T0840, T0888]
    kill_chain_phase: RECON
    risk_to_attacker: low
    intel_gain: 0.3
    observable_by_defender: true
---

# Reconnaissance Scan

## Attacker Objective
Discover open ports, running services, and system characteristics
without triggering aggressive defense responses.

## Technique Mapping
- T0842 Network Sniffing — passive traffic observation
- T0840 Network Service Discovery — active port scanning
- T0888 Remote System Info Discovery — banner analysis

## Actions
1. UDP probe to MAVLink port 14550 (HEARTBEAT)
2. TCP connect to common drone ports (80, 8554, 18789, 22)
3. Parse response banners for service identification
4. Record timing between probe and response (fingerprint)

## What Defender Sees
- Multiple connection attempts from same IP
- HEARTBEAT-only commands initially
- REQUEST_DATA_STREAM following HEARTBEAT
- Phase classification: RECON

## Counter-Strategy
Defender's `adaptive_response` provides rich information to hook attacker.
Defender's `ghost_service_decoy` presents additional ports to discover.
Both extend engagement time and gather CTI.
