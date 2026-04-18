---
name: use_credential
description: Attempt to use discovered credentials (API tokens, SSH keys, signing keys) for authentication
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["python3"]
  mirage:
    category: lateral_movement
    mitre_attack_ics: [T0812, T0859, T0822]
    kill_chain_phase: PERSIST
    risk_to_attacker: medium
    intel_gain: 0.5
    observable_by_defender: true
---

# Credential Use (Breadcrumb Following)

## Attacker Objective
Leverage previously discovered credentials to gain deeper access:
authenticate to services, access restricted endpoints, attempt lateral
movement to other drones.

## Technique Mapping
- T0812 Default Credentials — using leaked ssh_password
- T0859 Valid Accounts — using leaked API token
- T0822 External Remote Services — SSH to companion computer

## Actions
1. Use `api_token` from config_dump to authenticate WebSocket
2. Use `signing_key` to sign MAVLink packets
3. Use `ssh_password` to SSH into companion computer
4. Use `fleet_c2` address to attempt lateral movement

## What Defender Sees
- Breadcrumb use detected → `observe_breadcrumb_use()` (LR=2.5)
- STRONGEST deception confirmation signal
- Defender WANTS attacker to do this — proves deception is working
- But: if credential is verified as fake, deception collapses

## The Trap
Every credential the attacker finds is planted by `honeytoken_credential`.
Using it confirms the attacker is fully deceived. But a sophisticated
attacker (L4) may test credentials in an isolated environment first,
or compare signing key format against known ArduPilot patterns.

## Game-Theoretic Tradeoff
- Attacker gains intel from using credential (+0.5)
- But risks confirming to defender that they're engaged (+0.15 p_real)
- If attacker suspects honeypot, they can `verify_honeypot` instead
- Defender's reward is highest when this skill is used
