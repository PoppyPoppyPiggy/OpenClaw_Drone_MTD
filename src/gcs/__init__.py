"""
GCS (Ground Control Station) package — Tier 1 strategic layer.

Tier 1 is the operator-facing strategic LLM layer. It observes per-drone
state, issues high-level directives (deploy_decoy, rotate_identity,
escalate), and coordinates the 3-drone fleet. Communicates with Tier 2
(honeydrone tactical agent) via the strategic_directive UDP channel
(port 19995).
"""
