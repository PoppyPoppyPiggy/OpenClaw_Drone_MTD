# MIRAGE-UAS Code Rules

You are working on MIRAGE-UAS — a UAS honeydrone deception framework for ACM CCS 2026.
36 Python modules, 12,380 lines in src/, async-first architecture.

Follow these rules strictly when upgrading code for production readiness.

## RULE 1: ARCHITECTURE BOUNDARY — Host-Proxy Pattern

The system uses a host-proxy pattern:
- Host: AgenticDecoyEngine -> OpenClawAgent (UDP 14551-53)
- Docker: cc_stub containers forward MAVLink to host engine
- cc_stub handles HTTP/WS/Ghost directly (NOT routed to host)

MUST: Keep this boundary. Host modules (openclaw_agent, agentic_decoy_engine,
engagement_tracker, mavlink_response_gen) are the REAL execution path.
MUST NOT: Wire DeceptionOrchestrator/FakeServiceFactory/BreadcrumbPlanter
into host path without explicit approval — cc_stub reimplements their logic.

## RULE 2: ASYNC DISCIPLINE — 15 Concurrent Tasks

Each AgenticDecoyEngine runs 5 async tasks (x3 drones = 15 total).
Tasks: recv_loop, ws_server, telemetry_loop, broadcast_loop, mtd_consumer

MUST: Use asyncio.wait_for(coro, timeout=X) on all external I/O
MUST: Use return_exceptions=True in asyncio.gather() and check results
MUST: Log task crashes with full traceback before restarting
MUST NOT: Use raw socket.recvfrom() without timeout in async context
MUST NOT: Create unbounded queues — cap asyncio.Queue(maxsize=1000)

## RULE 3: DATA FLOW — Dataclass Only at Boundaries

Cross-module data uses @dataclass from shared/models.py:
MavlinkCaptureEvent, ParsedAttackEvent, MTDTrigger, MTDResult,
AttackerFingerprint, AgentDecision, EngagementMetrics

MUST: Pass dataclass objects between modules, never raw dicts
MUST: Add new cross-module types to shared/models.py
MUST NOT: Return Optional[dict] from public APIs — use typed result objects

## RULE 4: OPENCLAW AGENT — 5 Behaviors, Rule-Based

OpenClawAgent is NOT an LLM/NN/RL policy. It is a deterministic state
machine with randomized parameters. Every decision is a hard-coded rule.

State variables that affect wire-level output:
- _current_sysid (1-254) -> srcSystem byte in MAVLink packets
- _silenced (bool) -> complete silence on wire when True
- _fake_params (17 keys) -> PARAM_VALUE response payloads
- _fake_waypoints (3-12) -> MISSION_COUNT + EXFIL data dumps

MUST: Preserve OODA loop: observe() -> _update_fingerprint() ->
      _detect_attack_phase() -> generate_response()
MUST: Record every decision via _record_decision() for audit trail
MUST NOT: Add ML inference in the hot path (per-packet processing)

## RULE 5: PHASE DETECTION — Order Matters

Phase detection checks in THIS order (first match wins):
1. EXFIL   <- LOG_REQUEST_LIST, LOG_REQUEST_DATA, FILE_TRANSFER_PROTOCOL
2. PERSIST <- PARAM_SET, MISSION_ITEM, MISSION_ITEM_INT
3. EXPLOIT <- COMMAND_LONG, SET_MODE, SET_POSITION_TARGET
4. RECON   <- everything else (HEARTBEAT, PARAM_REQUEST_*, etc.)

MUST: Maintain this priority order — EXFIL > PERSIST > EXPLOIT > RECON
MUST NOT: Add new phases without updating response strategy per phase

## RULE 6: MAVLINK PROTOCOL FIDELITY

MAVLink responses must be valid ArduPilot Copter v4.3.x packets.
pymavlink ardupilotmega dialect encodes actual MAVLink v2 binary frames
with correct CRC, sequence numbers, and system IDs.

MUST: Use pymavlink for all MAVLink encoding (never hand-craft bytes)
MUST: Maintain srcSystem consistency within a rotation window
MUST: Generate responses that pass any MAVLink parser validation
MUST NOT: Mix MAVLink v1 and v2 framing in the same session

## RULE 7: CONFIGURATION — Research vs Infrastructure

All config goes through shared/constants.py -> config/.env (63 params).

Research params (22): NO defaults — ConfigError if missing
  MTD_COST_SENSITIVITY_KAPPA, DECEPTION_WEIGHTS, ATTACKER_PRIORS, etc.
Infrastructure params: sensible defaults OK
  LOG_LEVEL=INFO, RESULTS_DIR=results, HONEY_DRONE_COUNT=3

MUST: Add new research params to config/.env.example with formula reference
MUST: Validate weight arrays sum to 1.0 (use _require_weights())
MUST NOT: Hardcode research parameters in source code

## RULE 8: LOGGING — structlog JSON, 4 UDP Event Ports

Use structlog from shared/logger.py. No print() anywhere.

```
log.info("event_name", key=value)       # state transitions
log.warning("event_name", key=value)    # fallback/degraded mode
log.error("event_name", error=str(e))   # caught exceptions
```

4 UDP event ports (fire-and-forget, no ACK):
- 19999: state broadcast (1s interval)
- 19998: agent decisions + phase/tool transitions
- 19997: state diffs (sysid, port, param, mission changes)
- 19996: packet events (every MAVLink response)

MUST: Emit events on correct UDP port for observatory compatibility
MUST NOT: Add blocking I/O in event emission (_udp_emit is fire-and-forget)

## RULE 9: METRICS — Real vs Simulated, Label Honestly

Known simulated values that MUST be labeled:
- Table III MTD latencies (DRY_RUN lookup table, not Docker API)
- confusion_score = 0.72 (hardcoded in DeceptionStateManager)
- WS port rotation (internal var only, server not restarted)
- Ghost TCP ports (agent logs intent, cc_stub creates actual listener)
- GPS shift (affects WS JSON only, not GLOBAL_POSITION_INT telemetry)

MUST: Mark simulated values with warning in output and "estimated" in paper
MUST: Use real data from experiment logs, never hardcode metric values
MUST NOT: Present DRY_RUN results as measured values

## RULE 10: FORMULAS — Paper Reference Required

Every formula in code must reference its paper source:
- Eq.17 — MTD Cost (TIFS T-IFS-25285-2026)
- Eq.H1 — Accuracy (HoneyGPT, USENIX 2025)
- Eq.M1 — MTTC (Zhuang et al., MDPI Electronics 2025)
- Eq.DS — DeceptionScore v2 (MIRAGE-UAS composite)

DeceptionScore v2 components (from METRICS_FORMULAS.md section 6.2):
- DR = Diversion Ratio        [w=0.20, source: arXiv:2603.17272]
- BP = Breach Prevention       [w=0.25]
- IQ = Interaction Quality     [w=0.25, source: HoneyGPT]
- TC = Temporal Cost           [w=0.20, source: Zhuang MTTC]
- ME = MTD Effectiveness       [w=0.10, source: arXiv:2504.11661]

MUST: Comment formula source in code (Eq.XX — Paper Name)
MUST: Use weights from config/.env, not hardcoded

## RULE 11: ERROR HANDLING — Per Module Pattern

- agentic_decoy_engine.py: try/except per packet (never crash recv_loop)
  -> log.error + continue
- openclaw_agent.py: try/except per behavior loop (never crash agent)
  -> log.error + sleep + retry
- mtd_executor.py: typed exceptions (DockerException -> MTDResult)
  -> return MTDResult(success=False, error_msg=str(e))
- openclaw_service.py: validate JSON input before processing
  -> return error response, never crash

MUST: Return result objects on error path (not None, not raise)
MUST: Use typed except (DockerException, json.JSONDecodeError, etc.)
MUST NOT: Use bare except Exception: pass

## RULE 12: TESTING — Integration First

Existing: tests/integration/test_e2e_mirage.py (8 scenarios, dry-run)
Uses: pytest + pytest-asyncio + pytest-cov

MUST: Run pytest before any PR (python -m pytest tests/)
MUST: New modules need at least happy-path + one error-path test
MUST: Use DRY_RUN mode in tests (no Docker dependency)
MUST NOT: Mock OpenClawAgent business logic — mock only I/O

## RULE 13: DOCSTRING PATTERN — Paper Reference

Module-level: [ROLE], [DATA FLOW], [Inputs], [Outputs], [Dependencies], [REF section X.Y]
Class-level: [ROLE] one-liner
Method-level: only if non-obvious

MUST: Include [REF] paper section citation for evaluation/metric modules
MUST: Keep Korean for presentation doc, English for code docstrings

## RULE 14: GIT — Conventional Commits

Prefix: feat: / fix: / perf: / test: / docs:
Never commit: .env, .venv/, *.pt checkpoints, engines.log (850MB+)

## KNOWN ISSUES TO FIX (Priority Order)

- P0: confusion_score=0.72 hardcoded -> replace with IQ from HoneyGPT Eq.H1-H4
- P0: Table III simulated -> label "estimated" or measure real Docker API
- P0: openclaw_service.py — no input validation on JSON payloads
- P1: Async tasks lack supervisor (crash = silent death)
- P1: Ghost port opening is intent-only (agent logs, cc_stub creates)
- P2: WS port rotation updates var but doesn't restart server
- P2: GPS shift affects WS JSON only, not MAVLink telemetry
