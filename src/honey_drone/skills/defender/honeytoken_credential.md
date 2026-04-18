---
name: honeytoken_credential
description: Plant fake credentials (API tokens, SSH keys, signing keys) as honeytokens to detect and track attacker lateral movement
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins: ["openssl"]
    primaryEnv: DRONE_ID
  mirage:
    category: deception
    mitre_engage: EAC0005
    mitre_d3fend: D3-DUC
    attack_phase: [EXPLOIT, PERSIST, EXFIL]
    effect_on_attacker: "Attacker discovers planted credential and attempts reuse — confirms deception success"
    detectability: 0.4
    risk: low
---

# Honeytoken Credential Planting

## When to Use
Invoke this skill when the attacker has progressed beyond RECON phase
and is actively attempting exploitation or persistence. Planted credentials
serve as tripwires that confirm the attacker believes the honeydrone is real.

## Technique
This skill implements MITRE D3FEND "Decoy User Credential" (D3-DUC) and
MITRE Engage "Lure" (EAC0005):

1. Generate a plausible fake credential matching the drone's context:
   - MAVLink signing key (SHA-256 hash fragment)
   - SSH private key for "companion" user
   - API token prefixed with `sk-drone-`
   - Database connection string to fake fleet C2

2. Embed the credential in a response that appears accidental:
   - STATUSTEXT with truncated key: `"key=a1b2c3d4..."`
   - PARAM_VALUE containing signing key as float
   - WebSocket config_dump with full credential set
   - Terminal `env` output leaking API_TOKEN

3. Track credential usage via DeceptionStateManager:
   - `observe_breadcrumb_access()` when credential is discovered
   - `observe_breadcrumb_use()` when credential is attempted for auth
   - Both trigger Bayesian belief update (LR=1.8 access, LR=2.5 use)

## Expected Attacker Response
- L1-L2: Ignore or store credential for later
- L3: Attempt SSH with leaked password
- L4: Use signing key for MAVLink auth, attempt lateral movement to fleet C2

## Interaction with Other Skills
- Pairs with `ghost_service_decoy`: ghost service validates planted credentials
- Pairs with `adaptive_response`: responses reference planted creds naturally
- Triggers MTD `KEY_ROTATE` after credential is used (invalidate before real damage)

## Reward Signal
- Credential discovered: +0.3 intel for attacker, +0.08 p_real for defender
- Credential used: +0.5 intel for attacker, +0.15 p_real for defender (strong deception)
- Credential verified as fake: -0.2 p_real for defender (deception failed)
