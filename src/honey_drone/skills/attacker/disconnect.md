---
name: disconnect
description: Terminate session — attacker decides to leave (only available after 20+ steps)
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: []
  mirage:
    category: termination
    mitre_attack_ics: []
    kill_chain_phase: null
    risk_to_attacker: none
    intel_gain: 0.0
    observable_by_defender: true
---

# Disconnect

## Attacker Objective
End the current engagement. Only rational when:
- Attacker has gathered sufficient intel
- Attacker has confirmed target is a honeypot
- Cost of continued engagement exceeds expected intel gain

## Precondition
Requires 20+ steps of engagement. Real attackers don't quit instantly —
they need time to assess whether a target is worth attacking.

## Game-Theoretic Role
Disconnect is the attacker's "outside option" — the threat of leaving
keeps the defender honest. If defender's deception is too aggressive
(too many ghost ports, too frequent identity changes), the attacker
detects the honeypot and disconnects early, denying the defender
dwell time and CTI data.

## Reward
- Attacker: keeps 30% of accumulated intel, penalized for early exit
- Defender: gets partial win (dwell time achieved) + bonus for forcing exit
