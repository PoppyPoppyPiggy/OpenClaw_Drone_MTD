# §3 System Design

MIRAGE-UAS is organised as three concentric decision loops whose
outputs are reconciled over a lightweight UDP event fabric and
executed against real MAVLink v2 traffic. §3.1 gives the overall
architecture and its mapping onto Docker. §3.2 describes each tier in
detail. §3.3 documents the deterministic host-side OpenClaw agent
that enforces protocol fidelity. §3.4 specifies the UDP event ports
and broadcast cadences. §3.5 lists which components are simulated
versus measured, so evaluation claims can be read without guessing.

## 3.1 Three-tier Architecture

```
                ┌─────────────────────────────────────────────┐
                │ Tier 1 — Strategic GCS                      │
                │ 30 s cadence, LLM, emits typed directives   │
                └───────────────┬─────────────────────────────┘
                                │  UDP :19995  (strategic_directive)
                                ▼
     ┌──────────────────┬──────────────────┬──────────────────┐
     │ Tier 2 cc-honey-01│ Tier 2 cc-honey-02│ Tier 2 cc-honey-03│
     │ 8 s tactical LLM  │ 8 s tactical LLM  │ 8 s tactical LLM  │
     │ 1 s patrol loop   │ 1 s patrol loop   │ 1 s patrol loop   │
     └──────────────────┴──────────────────┴──────────────────┘
                                │  UDP :14551-14553  (MAVLink v2)
                                ▼
                ┌─────────────────────────────────────────────┐
                │ Host: AgenticDecoyEngine × 3                │
                │ OpenClawAgent (5-behaviour state machine)   │
                │ MavlinkResponseGen (pymavlink ardupilotmega)│
                └───────────────┬─────────────────────────────┘
                                │  telemetry + OPS events
                                ▼
                ┌─────────────────────────────────────────────┐
                │ Tier 3 — OpenClaw-SDK honey-token emulator  │
                │ UUID-tagged breadcrumbs, HTUR tracking      │
                └─────────────────────────────────────────────┘
```

The three tiers run at three **distinct cadences** — 1 s patrol, 8 s
tactical, 30 s strategic — so that the strategic plan-of-intent (Tier
1) amortises over many tactical decisions (Tier 2), which themselves
amortise over many protocol-level exchanges (host agent). This is
the temporal abstraction pattern that distinguishes our deployment
from flat LLM-honeypot pipelines (HoneyGPT, LLMHoney) where one LLM
is called per-interaction.

**Host-proxy deployment.** Docker hosts each honeydrone stub
(`cc-honey-01/02/03`) and the strategic GCS. MAVLink UDP is bridged
to the **host** `AgenticDecoyEngine`, which owns the
wire-level response. HTTP / WebSocket / Ghost-port services are
handled directly by the stub. This separation isolates the
protocol-critical path (deterministic, auditable) from the LLM-driven
policy path (higher latency, stochastic). The LLM never touches the
MAVLink encoder.

## 3.2 Per-tier responsibilities

### Tier 1 — Strategic GCS (30 s)

The GCS runs a single Ollama-backed LLM (selected from Llama 3.1 8 B,
Qwen 2.5 14 B, or Gemma 2 9 B) that consumes a fleet-wide state
snapshot — the union of the three honeydrones' phase, attacker-tool
level, belief $\mu_A$, dwell, and recent evasion signals — and emits
a **typed directive** on UDP :19995. A directive is a dataclass of
the form

```json
{
  "action":    "escalate" | "deploy_decoy" | "observe",
  "skill_bias": {skill: probability, ...},   // sums to 1.0
  "urgency":    0.0–1.0,
  "reason":     "free-text one-liner",
  "issued_at":  UNIX_ts,
  "ttl_sec":    30.0
}
```

The effect of the directive on downstream Tier 2 skill distribution
is measured empirically in §5.5 (directive ON/OFF ablation: mean
bias-skill hit-rate 0.00 → 0.77 over 30 calls).

### Tier 2 — Per-drone Tactical LLM (8 s)

Each honeydrone runs a `LLMTacticalAgent` that, every 8 s, selects
one of five proactive skills — `statustext`, `flight_sim`,
`ghost_port`, `reboot_sim`, `credential_leak` — conditioned on
(i) the locally observed attacker trajectory, (ii) the currently
active Tier 1 directive (if within TTL), and (iii) a short
skill-history buffer that discourages repetition. The prompt
template (§4.2) enforces role, context, legal-skill list, and five
HARD RULES that mitigate the mode-collapse pathology documented in
§1.2.

A 1 s **patrol loop** runs below the tactical loop and handles
state-broadcast, telemetry jitter, and heartbeat emission. Patrol is
rule-based (no LLM call) so a stalled Ollama backend never blocks
the wire.

### Tier 3 — OpenClaw-SDK honey-token emulator

Tier 3 plants UUID-tagged credentials, signing keys, and
configuration URLs into the stub's HTTP/WebSocket responses. Each
token is recorded in a local ledger at issuance time and marked
"used" if it reappears in a subsequent authenticated request — a
Honey-Token Uptake Ratio (HTUR) of 1.0 on `cc-honey-01` under the
live attacker simulator (§5.6). Tier 3 is independent of the LLM
path: a silent defender still produces breadcrumbs.

## 3.3 OpenClaw Agent — deterministic protocol fidelity

The host-side agent is a five-behaviour state machine that generates
**MAVLink-valid** responses. The five behaviours are

| Behaviour       | Wire effect                              |
|-----------------|------------------------------------------|
| `sysid_shift`   | rotate `srcSystem` byte (1–254)          |
| `silence`       | drop packets for a short window          |
| `fake_params`   | 17-key PARAM_VALUE lookup table          |
| `fake_waypoints`| 3–12 MISSION items + EXFIL replay        |
| `ghost_hint`    | synthesise banners for ghost TCP ports   |

Phase detection on every inbound packet uses a fixed priority
(first-match wins) — **EXFIL > PERSIST > EXPLOIT > RECON** — matching
the MITRE ATT&CK for ICS tactic ordering. Every decision is
recorded via `_record_decision(...)` for audit. There is no ML in
this hot path: the agent is deliberately deterministic so that
protocol conformance is testable (§5.7).

All encoding uses `pymavlink` with the `ardupilotmega` dialect at
v2 framing; CRCs, sequence numbers, and system IDs are consistent
within a rotation window, and the resulting frames parse under any
stock ArduPilot / QGroundControl / MissionPlanner client.

## 3.4 UDP event fabric

Four fire-and-forget UDP ports bind the three tiers:

| Port   | Direction               | Payload                           | Cadence       |
|--------|-------------------------|-----------------------------------|---------------|
| 19999  | honey → GCS/observer    | state broadcast                   | 1 s           |
| 19998  | honey → observer        | agent decisions, phase/tool Δ     | on-change     |
| 19997  | honey → observer        | sysid/port/param/mission Δ        | on-change     |
| 19996  | honey → observer        | packet events (every MAVLink rsp) | per-packet    |
| 19995  | GCS → honey             | strategic_directive               | 30 s          |

The fabric is observational (no ACK, no retry). This is deliberate:
the evaluation dashboard and the attacker-belief tracker both
consume the same event stream without backpressuring the honey
fleet.

## 3.5 Real vs simulated — explicit label

| Component                           | Real (R) / Simulated (S) |
|-------------------------------------|---------------------------|
| MAVLink v2 frames (CRC, sysid, seq) | **R** (pymavlink)         |
| Tier 2 LLM decisions                | **R** (Ollama 8–14 B)     |
| Tier 1 directive delivery           | **R** (UDP :19995 verified §5.5)|
| Tier 3 honey-token issue/re-use     | **R** (HTUR=1.0 live §5.6)|
| DeceptionEnv `_action_effect` matrix| **S** (heuristic, §3.6, §6)|
| ArduPilot flight dynamics (SITL)    | **S** (`fcu_stub.py`)     |
| Table III MTD latencies (DRY_RUN)   | **S** (labelled "estimated")|
| Attacker-belief $\mu_A$             | **R-LLM** (observer LLM) |

The R/S column is reproduced in every downstream results table so a
reviewer can tell at a glance which numbers come from the live
stack and which from the training simulator.

## 3.6 Training environment (DeceptionEnv)

h-DQN and DQN baselines are trained against `DeceptionEnv`, a
vectorised 10-state / 45-action Gym-style environment whose
action-effect matrix is **heuristic, not empirically fitted**.
Values are seeded from the MITRE ATT&CK phase taxonomy and
honeypot-engagement literature (HoneyDrone NOMS 2018, HoneyGPT
2024) and hand-tuned so the reward surface preserves the expected
qualitative structure:

- **RECON** — low-intensity lures (`statustext`, `flight_sim`)
  raise belief most at lowest evasion risk,
- **EXPLOIT** — PARAM/COMMAND abuse + `credential_leak` is optimal,
- **PERSIST** — `reboot_sim` + `ghost_port` disrupt foothold,
- **EXFIL** — `statustext`/`flight_sim` erode belief
  (phase-negative).

An intensity multiplier `[0.6, 1.0, 1.4]` and a **superlinear**
evasion multiplier `[0.4, 1.0, 2.2]` create a real tradeoff at high
intensities. This testbed is used for offline training only;
every policy is evaluated **out-of-loop** against the LLM defender
on real MAVLink traffic (§5.4), so claims in §5 do not depend on the
fidelity of `_action_effect`. Calibrating the matrix against the
live cross-model data is listed as future work (§6).
