---
name: mirage-uas-skills
description: OpenClaw skill registry for MIRAGE-UAS game-theoretic deception training
version: 1.0.0
---

# MIRAGE-UAS Skill Registry

Two OpenClaw-style agents with opposing skill sets compete in a
General-Sum Markov Game. Each skill maps to a game action.

## Defender Skills (Honeydrone Agent)

| # | Skill | Category | MITRE D3FEND | Game Action |
|---|-------|----------|-------------|-------------|
| 0 | [operator_simulation](defender/operator_simulation.md) | deception | D3-SDP | deception_statustext |
| 1 | [adaptive_response](defender/adaptive_response.md) | deception | D3-SDP | deception_flight_sim |
| 2 | [ghost_service_decoy](defender/ghost_service_decoy.md) | deception | D3-DNR | deception_ghost_port |
| 3 | [identity_mutation](defender/identity_mutation.md) | mtd | D3-NHD | deception_reboot_sim |
| 4 | [honeytoken_credential](defender/honeytoken_credential.md) | deception | D3-DUC | deception_credential_leak |

## Attacker Skills (Adversary Agent)

| # | Skill | Category | MITRE ATT&CK ICS | Game Action |
|---|-------|----------|------------------|-------------|
| 0 | [recon_scan](attacker/recon_scan.md) | reconnaissance | T0842, T0840 | recon_scan |
| 1 | [exploit_mavlink](attacker/exploit_mavlink.md) | exploitation | T0855, T0858 | exploit_mavlink |
| 2 | [use_credential](attacker/use_credential.md) | lateral_movement | T0812, T0859 | use_credential |
| 3 | [probe_ghost](attacker/probe_ghost.md) | reconnaissance | T0842 | probe_ghost |
| 4 | [verify_honeypot](attacker/verify_honeypot.md) | counter_deception | T0842 | verify_honeypot |
| 5 | [lateral_pivot](attacker/lateral_pivot.md) | lateral_movement | T0867 | lateral_pivot |
| 6 | [disconnect](attacker/disconnect.md) | termination | — | disconnect |

## Honeypot Deception Taxonomy Mapping

Standard honeypot techniques and how they map to our skills:

| Deception Technique | Reference | Our Implementation |
|---|---|---|
| **Honeytoken / Decoy Credential** | MITRE D3FEND D3-DUC | `honeytoken_credential` — fake signing keys, API tokens, SSH passwords |
| **Decoy Network Resource** | MITRE D3FEND D3-DNR | `ghost_service_decoy` — fake SSH/HTTP/RTSP on random ports |
| **Pocket Litter** | MITRE Engage EAC0006 | `operator_simulation` + `adaptive_response` — realistic telemetry, operator messages |
| **Network Diversity** | MITRE D3FEND D3-NHD | `identity_mutation` — sysid rotation, GPS shift, reboot simulation |
| **Lure / Breadcrumb** | MITRE Engage EAC0005 | `honeytoken_credential` — credentials that lead to ghost services |
| **Decoy Content** | MITRE Engage EAC0004 | `ghost_service_decoy` — fake services with interactive emulation |
| **Connected Honeynet** | MITRE D3FEND D3-CHN | Entire MIRAGE-UAS system — Docker containers forming honeynet |
| **Bayesian Belief Tracking** | Aradi et al. (2025) | `DeceptionStateManager` — per-attacker P(real\|obs) tracking |

## What's Different from Traditional Honeypots

| Traditional Honeypot | MIRAGE-UAS |
|---|---|
| Static responses | **Adaptive** — phase-specific, fingerprint-aware |
| Passive (wait for attacker) | **Proactive** — agent initiates ghost ports, leaks creds |
| Single service | **Multi-service** — MAVLink + HTTP + WS + RTSP + SSH |
| No attacker modeling | **Bayesian belief tracking** per attacker |
| Fixed configuration | **Self-mutating** — sysid, ports, params change autonomously |
| Manual deployment | **Game-theoretic optimization** of skill timing |
