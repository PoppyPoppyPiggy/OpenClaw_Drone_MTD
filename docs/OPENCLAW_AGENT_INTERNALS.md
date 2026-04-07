# OpenClaw Agent Internals

This document describes exactly what the `OpenClawAgent` class does at the code level: what is real, what is simulated, and what each behavior actually changes in internal state and on the wire.

Source files:
- `src/honey_drone/openclaw_agent.py` -- the autonomous agent
- `src/honey_drone/agentic_decoy_engine.py` -- the engine that hosts the agent
- `src/honey_drone/mavlink_response_gen.py` -- fallback MAVLink packet generation
- `src/honey_drone/deception_state_manager.py` -- Bayesian belief tracking

---

## 1. Architecture Overview

### What OpenClawAgent is

OpenClawAgent is a **rule-based autonomous deception agent**. It is not an LLM, not a neural network, and not a reinforcement learning policy. It is a deterministic state machine with randomized parameters that makes decisions based on:

- The sequence of MAVLink commands received from the attacker
- Inter-arrival timing of those commands
- How many distinct services the attacker has touched
- How long the attacker has been connected (dwell time)
- Timer-based periodic loops

There is no learning, no gradient descent, and no prompt engineering. Every decision is a hard-coded rule with randomized values drawn from fixed ranges.

### Data Flow

```
AttackerPacket (UDP MAVLink or TCP WebSocket)
    |
    v
AgenticDecoyEngine._receive_loop() / _websocket_handler()
    |
    v
OpenClawAgent.observe(event)          -- updates fingerprint, phase, confusion triggers
    |
    v
EngagementTracker.update_session()    -- updates dwell time, command count
    |
    v
OpenClawAgent.generate_response(event) -- returns adaptive MAVLink bytes (or None)
    |                                      |
    | (if None)                            | (if bytes)
    v                                      v
MavlinkResponseGenerator.generate()    UDP sendto(attacker)
    |
    v
UDP sendto(attacker)
    |
    v
_assess_and_signal()
    |---> MTDTrigger queue   (Track A -- MTD Controller)
    |---> CTI event queue    (Track B -- CTI Pipeline)
```

### The OODA Loop

| Phase   | Method                        | What happens                                         |
|---------|-------------------------------|------------------------------------------------------|
| Observe | `observe()` / `observe_ws()`  | Record command in conversation history, update services_touched set |
| Orient  | `_update_fingerprint()`       | Classify attacker tool (nmap/mavproxy/dronekit/metasploit/custom) via timing + sequence analysis |
| Decide  | `_detect_attack_phase()`      | Transition the phase state machine (RECON/EXPLOIT/PERSIST/EXFIL) based on command types seen |
| Act     | `generate_response()`         | Return phase-appropriate MAVLink bytes; also, `_proactive_loop()` fires unsolicited actions on a timer |

---

## 2. The 5 Behaviors -- What Each Actually Does

### Behavior 1: Phase-Adaptive Response

**What triggers it:** Every call to `generate_response()` or `generate_ws_response()` dispatches based on the current `fp.attack_phase`.

**Phase detection logic** (`_detect_attack_phase()`):

The phase is determined by scanning the set of all MAVLink `msg_type` values seen from the attacker. The check order matters -- the first matching rule wins:

| Phase   | Trigger command types                                                        | Meaning                                       |
|---------|-----------------------------------------------------------------------------|-----------------------------------------------|
| EXFIL   | `LOG_REQUEST_LIST`, `LOG_REQUEST_DATA`, `FILE_TRANSFER_PROTOCOL`            | Attacker is extracting data                   |
| PERSIST | `PARAM_SET`, `MISSION_ITEM`, `MISSION_ITEM_INT`                             | Attacker is writing to drone storage           |
| EXPLOIT | `COMMAND_LONG`, `SET_MODE`, `SET_POSITION_TARGET_LOCAL_NED`, `SET_ACTUATOR_CONTROL_TARGET` | Attacker is executing commands       |
| RECON   | Everything else (HEARTBEAT, PARAM_REQUEST_*, REQUEST_DATA_STREAM)           | Attacker is only reading/observing             |

Phase transitions emit a `phase_transition` event on UDP 19998. The transition timestamp is stored in `fp.phase_changed_at_ns`.

**Response strategy per phase:**

| Phase   | MAVLink response                                                                        | WebSocket response                              |
|---------|----------------------------------------------------------------------------------------|------------------------------------------------|
| RECON   | Rich PARAM_VALUE (exposes fake param pool), HEARTBEAT, MISSION_COUNT. COMMAND_LONG gets HEARTBEAT (hides exploit detection). | System info JSON with service list, param count, uptime |
| EXPLOIT | COMMAND_ACK(ACCEPTED) for everything. ARM command triggers takeoff-crash simulation. GPS_INJECT_DATA gets ACK. | Fake auth token, full permissions list, signing key fragment |
| PERSIST | COMMAND_ACK for MISSION_ITEM/PARAM_SET. Unsolicited STATUSTEXT with operator messages. | Telemetry stream JSON with altitude, battery, mode, operator notes |
| EXFIL   | PARAM_VALUE for log requests, MISSION_COUNT for mission requests. HEARTBEAT fallback. | Full data dump: 5 fake flight logs, config dict, waypoints, signing key |

**What internal state changes:** `fp.attack_phase` (AttackPhase enum), `fp.phase_changed_at_ns` (int, nanoseconds).

**What the attacker sees:** Different MAVLink message types in response to the same commands depending on what phase the agent thinks the attacker is in. In RECON, COMMAND_LONG gets a HEARTBEAT instead of a COMMAND_ACK -- this hides the fact that the system detected the command.

**What the attacker does NOT see:** The phase classification itself, the fingerprint record, the decision audit log.

---

### Behavior 2: Proactive Luring (5 sub-actions)

The proactive loop runs every `AGENT_PROACTIVE_INTERVAL_SEC` seconds (default: 45s). Each cycle, it randomly picks one of 5 actions with equal probability.

#### 2a: STATUSTEXT Injection (`_proactive_statustext`)

- **Trigger:** Timer (proactive loop)
- **State change:** None. Only a decision record is appended to `_decisions`.
- **What the attacker sees:** A STATUSTEXT MAVLink packet with a message like `"Waypoint 3 reached"`, `"Camera started"`, `"RTL initiated"`, etc. Selected randomly from `_STATUS_MESSAGES_OPERATOR` (13 messages) or `_STATUS_MESSAGES_RECON` (7 messages).
- **What the attacker does NOT see:** That this message was generated by the agent, not by a real operator or autopilot.
- **Note:** The current implementation logs the decision but does not actually send the STATUSTEXT packet over UDP to the attacker. The packet build capability exists (`_build_statustext()`), but the proactive loop only records the decision. The STATUSTEXT is sent as a response only during PERSIST phase responses.

#### 2b: Flight Simulation (`_proactive_flight_sim`)

- **Trigger:** Timer (proactive loop)
- **State change:** `_fake_params["SIMULATED_ALT"]` is set through the sequence `[0, 20, 50, 80, 100, 100, 90, 70, 50, 30, 10, 0]` over 60 seconds (5s per step). After completion, `SIMULATED_ALT` is removed from `_fake_params`.
- **What the attacker sees:** If the attacker requests PARAM_VALUE during the simulation, they may receive `SIMULATED_ALT` as one of the random parameters. During PERSIST phase WebSocket responses, the altitude field reflects `SIMULATED_ALT` if present.
- **What the attacker does NOT see:** The altitude profile is not streamed as `GLOBAL_POSITION_INT` telemetry. There is no real telemetry altitude change in the `MavlinkResponseGenerator.FlightState`.

#### 2c: Ghost Port Opening (`_proactive_ghost_port`)

- **Trigger:** Timer (proactive loop), or confusion amplification when services touched >= `AGENT_MIRROR_SERVICE_THRESHOLD` (default: 2)
- **State change:** A random port in range 19000-19500 is appended to `_active_ghost_ports` (list of int). A decision is recorded. A STATUSTEXT hint referencing `_current_ws_port` is logged.
- **What the attacker sees:** The agent logs the intent to open a ghost TCP port and emits state diff on UDP 19997. The actual TCP listener creation is delegated to the CC stub / FakeServiceFactory infrastructure -- the agent itself does not call `socket.bind()` for the ghost port.
- **What the attacker does NOT see:** The `_active_ghost_ports` list, the decision rationale.

#### 2d: Reboot Simulation (`_proactive_reboot`)

- **Trigger:** Timer (proactive loop)
- **State change:**
  - `_silenced` set to `True` for 8-15 seconds (uniform random from `_REBOOT_SILENCE_MIN_SEC` to `_REBOOT_SILENCE_MAX_SEC`)
  - After silence, `_silenced` set back to `False`
  - `_current_sysid` changed to a new random value in 1-254
  - `_mav` (pymavlink MAVLink encoder) reconstructed with new srcSystem
- **What the attacker sees:** Complete silence (no MAVLink responses, no WebSocket responses) for 8-15 seconds. When communication resumes, the `srcSystem` field in all MAVLink packets is a different value. This looks like the drone rebooted and came back as a different system.
- **What the attacker does NOT see:** That `_silenced` is a boolean flag, not a real reboot.

#### 2e: Fake Signing Key Leak (`_proactive_fake_key`)

- **Trigger:** Timer (proactive loop)
- **State change:** A SHA-256 hash (first 32 chars) is generated from `"{drone_id}:{timestamp}"` and stored in `_planted_credentials` with key `"signing_key_{timestamp}"`.
- **What the attacker sees:** The key is not directly pushed to the attacker via this action. It becomes available when the attacker reaches EXPLOIT phase (WebSocket response includes `signing_key_fragment`) or EXFIL phase (full `signing_key` in data dump).
- **What the attacker does NOT see:** The `_planted_credentials` dict, the generation algorithm.

---

### Behavior 3: Attacker Fingerprinting

**What triggers it:** Every call to `observe()` calls `_update_fingerprint()`, which calls `_detect_tool()`.

**Detection method** (`_detect_tool()`):

The classifier uses two inputs:
1. **Inter-arrival timing:** Mean interval between consecutive packets from the same attacker IP, computed from `_conversation_history` nanosecond timestamps.
2. **Command sequence patterns:** The set and order of `msg_type` values in `fp.command_sequence`.

| Tool               | Detection rule                                                                                      |
|--------------------|------------------------------------------------------------------------------------------------------|
| `NMAP_SCANNER`     | avg_interval < 0.1s AND unique command types > 5                                                    |
| `MAVPROXY_GCS`     | First 3 commands are `["HEARTBEAT", "HEARTBEAT", "REQUEST_DATA_STREAM"]` OR (`REQUEST_DATA_STREAM` in commands AND avg_interval > 0.8s) |
| `DRONEKIT_SCRIPT`  | `SET_MODE` in commands AND `REQUEST_DATA_STREAM` in commands                                        |
| `METASPLOIT_MODULE`| 2+ of {`FILE_TRANSFER_PROTOCOL`, `LOG_REQUEST_DATA`, `SET_ACTUATOR_CONTROL_TARGET`, `GPS_INJECT_DATA`} AND avg_interval < 2.0s |
| `CUSTOM_EXPLOIT`   | Any of {`PARAM_SET`, `FILE_TRANSFER_PROTOCOL`, `LOG_REQUEST_LIST`, `SET_ACTUATOR_CONTROL_TARGET`, `MISSION_ITEM`} present |

Rules are evaluated top-to-bottom; first match wins. Requires at least 3 commands before classification begins.

**How fingerprint affects response strategy:**
- `NMAP_SCANNER`: Only HEARTBEAT responses are returned, regardless of phase. All other message types get `None` (no response).
- All other tools: Normal phase-based response strategy applies.

**What changes:** `fp.tool` (AttackerTool enum). On tool change, a `level_reclassified` event is emitted on UDP 19998 with the last 3 commands as evidence.

---

### Behavior 4: Confusion Amplification

Checked on every `observe()` call via `_check_confusion_triggers()`.

#### Service Mirroring

- **Trigger:** `len(services_touched[attacker_ip]) >= AGENT_MIRROR_SERVICE_THRESHOLD` (default: 2) AND `_mirror_active` is `False`
- **What happens:** Sets `_mirror_active = True`, schedules `_proactive_ghost_port()` as an async task
- **State change:** `_mirror_active` (bool, set once, never reset), `_active_ghost_ports` (appended)
- **What the attacker sees:** A new ghost port appears (same as Behavior 2c)

#### False Flag

- **Trigger:** Attacker dwell time > `AGENT_FALSE_FLAG_DWELL_THRESHOLD` (default: 120s) AND `_false_flag_active` is `False`
- **What happens:** `_execute_false_flag()` runs for 30 seconds:
  1. Saves original sysid and GPS
  2. Sets `_current_sysid` to random value in 51-100 (normal range is 1-50)
  3. Shifts GPS: `lat += 0.1`, `lon += 0.1` (approximately 12km shift)
  4. Reconstructs pymavlink encoder with new srcSystem
  5. Waits 30 seconds
  6. Restores original sysid, GPS, and clears `_false_flag_active`
- **State change:** `_false_flag_active` (bool), `_current_sysid` (int, temporarily 51-100), `_current_gps` (lat/lon shifted +0.1 deg each)
- **What the attacker sees:** For 30 seconds, all MAVLink responses have a different srcSystem byte and (if GLOBAL_POSITION_INT is generated) a GPS position ~12km away. Then the original identity returns.

#### ARM-Takeoff-Crash Simulation

- **Trigger:** COMMAND_LONG with `MAV_CMD_COMPONENT_ARM_DISARM` during EXPLOIT phase
- **What happens:** `handle_arm_command()` runs:
  1. 30 iterations, 1 second each: `_fake_params["SIMULATED_ALT"]` ramps from 0 to 120 (capped)
  2. `_silenced = True`, `SIMULATED_ALT` removed
  3. 20 seconds of silence (`_ARM_CRASH_SILENCE_SEC`)
  4. `_silenced = False`
- **State change:** `_fake_params["SIMULATED_ALT"]` (float, 0-120), `_silenced` (bool)
- **What the attacker sees:** A COMMAND_ACK(ACCEPTED) for the ARM command (sent before the simulation starts). Then if requesting parameters during the 30s flight, they may see `SIMULATED_ALT` values. Then 20 seconds of complete silence (no responses). Then normal operation resumes.

---

### Behavior 5: Self-Mutation (4 rotation loops)

All loops run as independent `asyncio.Task` instances started in `start()`.

#### sysid Rotation

- **Timer:** `AGENT_SYSID_ROTATION_SEC` (default: 120s)
- **State change:** `_current_sysid` = `random.randint(1, 254)`. `_mav` (pymavlink MAVLink encoder) reconstructed with new srcSystem.
- **Wire-level change:** The `srcSystem` byte in all future MAVLink v2 packets changes.
- **Emits:** State diff on UDP 19997, decision on UDP 19998.

#### WebSocket Port Rotation

- **Timer:** `AGENT_PORT_ROTATION_SEC` (default: 90s)
- **State change:** `_current_ws_port` = `random.randint(18700, 18900)`.
- **Wire-level change:** The value is recorded internally, but the actual WebSocket server (`AgenticDecoyEngine._ws_server`) is NOT restarted on the new port. The rotation updates the internal variable used in STATUSTEXT hints and ghost port references.
- **Emits:** State diff on UDP 19997, decision on UDP 19998.

#### Parameter Drift

- **Timer:** `_PARAM_CYCLE_SEC` = 45 seconds
- **State change:** Every value in `_fake_params` (dict, 17 keys) is multiplied by `1.0 + random.gauss(0, 0.005)`. This is Gaussian noise with sigma = 0.5%.
- **Wire-level change:** Future PARAM_VALUE responses contain slightly different values each cycle. Over time, parameter values drift from their initial values.
- **Emits:** State diff on UDP 19997 (reports first 3 changed params), decision on UDP 19998.

#### Mission Refresh

- **Timer:** `_MISSION_REFRESH_SEC` = 60 seconds
- **State change:** `_fake_waypoints` is regenerated: 3-12 random waypoints around Seoul (37.5665, 126.9780) with Gaussian scatter (sigma = 0.005 degrees, ~550m), altitudes 30-150m, commands randomly chosen from WAYPOINT(16)/LAND(21)/TAKEOFF(22).
- **Wire-level change:** Future MISSION_COUNT responses return a different count. Future EXFIL-phase WebSocket data dumps return different waypoint data.
- **Emits:** State diff on UDP 19997, decision on UDP 19998.

---

## 3. What is Real vs Simulated

| Aspect | Status | Detail |
|--------|--------|--------|
| MAVLink packet generation | REAL | pymavlink `ardupilotmega` dialect encodes actual MAVLink v2 binary frames with correct CRC, sequence numbers, and system IDs. These are valid MAVLink packets that any MAVLink parser will decode. |
| UDP socket communication | REAL | `AgenticDecoyEngine` binds a real UDP socket on `config.mavlink_port`, receives real packets via `recvfrom()`, and sends real responses via `sendto()`. Actual bytes on the wire. |
| WebSocket server | REAL | `websockets.serve()` creates a real TCP WebSocket server on `config.webclaw_port`. Attackers can connect and exchange JSON messages. |
| Attacker fingerprinting | REAL | Timing analysis computed from actual `time.time_ns()` deltas between received packets. Command sequence is the actual ordered list of `msg_type` values from real packets. |
| Phase detection | REAL | Based on the actual set of MAVLink command types observed in received packets. No simulation -- if an attacker sends `COMMAND_LONG`, the phase transitions to EXPLOIT. |
| Bayesian belief tracking | REAL | `DeceptionStateManager` maintains per-attacker `AttackerBeliefState` with actual posterior probability updates using likelihood ratios. Prior is 0.7, clamped to [0.01, 0.99]. Formula: `posterior = (lr * prior) / (lr * prior + 1.0 * (1 - prior))`. |
| Decision audit log | REAL | Every agent decision creates an `AgentDecision` record with timestamp, behavior name, target IP, and rationale. Stored in `_decisions` list and emitted via UDP. |
| Ghost TCP services | SIMULATED | The agent logs the decision and appends to `_active_ghost_ports`, but does not itself call `socket.bind()`. The actual TCP listener (if any) is the responsibility of the CC stub / FakeServiceFactory. |
| Flight simulation altitude | SIMULATED | Only changes `_fake_params["SIMULATED_ALT"]` (a dict entry). Does not modify `MavlinkResponseGenerator.FlightState`. The telemetry broadcast loop in the engine uses `FlightState`, not `_fake_params`, so periodic telemetry altitude is unaffected. |
| GPS coordinate shift | SIMULATED | Changes `_current_gps` dict values. This affects future WebSocket JSON responses that reference `_current_gps`, but does NOT affect the `MavlinkResponseGenerator.FlightState.lat_deg/lon_deg` used in `GLOBAL_POSITION_INT` telemetry broadcasts. |
| WebSocket port rotation | SIMULATED | `_current_ws_port` is updated, but the actual `websockets.serve()` instance in `AgenticDecoyEngine` is not restarted. The old port remains active; the new port is never bound. The value is used only in log messages and STATUSTEXT hints. |
| Reboot silence | REAL (effect) | `_silenced = True` causes `generate_response()` and `generate_ws_response()` to return `None`, which means no response bytes are sent. The attacker genuinely sees silence on the wire. |
| RTSP camera | SIMULATED | Listed in WebSocket RECON responses as a service (`"rtsp"` in the services list), but no RTSP server exists. |
| Signing key leak | SIMULATED | SHA-256 hashes are generated and stored in `_planted_credentials`, but they do not correspond to any real MAVLink signing key. They are plausible-looking hex strings. |

---

## 4. Internal Variable Reference

### Per-agent state (on `OpenClawAgent` instance)

| Variable | Type | Range/Values | Changed by | Wire-level effect |
|----------|------|-------------|------------|-------------------|
| `_current_sysid` | int | 1-254 | `_sysid_rotation_loop()` (timer, 120s), `_proactive_reboot()` (proactive loop), `_execute_false_flag()` (dwell threshold) | `srcSystem` byte in all MAVLink packets |
| `_current_ws_port` | int | 18700-18900 | `_port_rotation_loop()` (timer, 90s) | Internal only -- actual WS server port unchanged |
| `_fake_params` | dict[str, float] | 17 ArduPilot params, values drift via Gaussian noise | `_param_cycle_loop()` (timer, 45s), `_proactive_flight_sim()` adds/removes `SIMULATED_ALT` | PARAM_VALUE response payloads |
| `_fake_waypoints` | list[dict] | 3-12 waypoints, lat/lon near Seoul, alt 30-150m | `_mission_refresh_loop()` (timer, 60s) | MISSION_COUNT response, EXFIL data dumps |
| `_silenced` | bool | True/False | `_proactive_reboot()` (8-15s True), `handle_arm_command()` (20s True) | When True, all responses return None (complete silence on wire) |
| `_mirror_active` | bool | True/False | `_check_confusion_triggers()` when services_touched >= threshold | One-shot flag; triggers ghost port opening |
| `_false_flag_active` | bool | True/False | `_execute_false_flag()` (True for 30s then False) | Guards against concurrent false flag executions |
| `_current_gps` | dict | `{"lat": float, "lon": float, "alt": float}` | `_execute_false_flag()` shifts lat/lon by +0.1 for 30s | Affects WebSocket JSON responses only |
| `_active_ghost_ports` | list[int] | Ports in 19000-19500 range, grows monotonically | `_proactive_ghost_port()` (proactive loop or confusion trigger) | Logged intent; actual TCP binding is external |
| `_planted_credentials` | dict[str, str] | `"signing_key_{timestamp}": "{hex32}"` | `_proactive_fake_key()` (proactive loop) | Available in EXPLOIT/EXFIL WebSocket responses |
| `_fingerprints` | dict[str, AttackerFingerprint] | Per attacker IP | `_update_fingerprint()` on every `observe()` | Determines phase + tool classification |
| `_conversation_history` | dict[str, list[tuple]] | Per attacker IP, list of (msg_type, payload_hex, timestamp_ns) | `observe()` on every packet | Used for timing analysis and phase detection |
| `_services_touched` | dict[str, set[str]] | Per attacker IP, set of `"{protocol}:{msg_type}"` strings | `observe()` and `observe_ws()` | Drives confusion amplification threshold |
| `_decisions` | list[AgentDecision] | Grows monotonically | Every `_record_decision()` call | Audit log; not visible to attacker |

### Per-attacker state (on `AttackerFingerprint`)

| Field | Type | Set by |
|-------|------|--------|
| `attacker_ip` | str | Creation |
| `command_sequence` | list[str] | `observe()` appends msg_type |
| `tool` | AttackerTool enum | `_detect_tool()` |
| `attack_phase` | AttackPhase enum | `_detect_attack_phase()` |
| `first_seen_ns` | int | Creation (time.time_ns()) |
| `phase_changed_at_ns` | int | `_detect_attack_phase()` on transition |
| `unique_services_touched` | int | `_update_fingerprint()` / `observe_ws()` |

### Per-attacker belief state (on `DeceptionStateManager`)

| Field | Type | Range | Updated by |
|-------|------|-------|------------|
| `p_believes_real` | float | [0.01, 0.99] (clamped) | `_bayesian_update()` on every observation |
| `total_observations` | int | 0+ | Incremented per Bayesian update |
| `breadcrumbs_seen` | int | 0+ | `observe_breadcrumb_access()` |
| `breadcrumbs_used` | int | 0+ | `observe_breadcrumb_use()` |
| `ghost_interactions` | int | 0+ | `observe_ghost_interaction()` |
| `scan_events` | int | 0+ | `observe_scan()` |
| `evasion_events` | int | 0+ | `observe_evasion()` |

Bayesian update formula:
```
posterior = (likelihood_ratio * prior) / (likelihood_ratio * prior + 1.0 * (1 - prior))
```

Likelihood ratios by observation type:

| ObservationType | Ratio | Effect on P(real) |
|----------------|-------|-------------------|
| PROTOCOL_INTERACT | 1.2 | Slight increase (attacker more convinced it is real) |
| BREADCRUMB_ACCESS | 1.8 | Moderate increase |
| BREADCRUMB_USE | 2.5 | Strong increase (attacker is using planted creds) |
| GHOST_CONNECT | 1.5 | Moderate increase |
| GHOST_DEEP_INTERACT | 2.0 | Strong increase |
| RECONNECT_SAME | 1.3 | Slight increase |
| RECONNECT_DIFFERENT | 0.7 | Slight decrease (attacker exploring = mild suspicion) |
| SCAN_DETECTED | 0.5 | Moderate decrease (attacker scanning = suspicion) |
| EXPLOIT_ATTEMPT | 1.1 | Slight increase (attacker committed enough to exploit) |
| EVASION_BEHAVIOR | 0.3 | Strong decrease (attacker suspects honeypot) |

---

## 5. UDP Event Ports

All events are fire-and-forget UDP packets sent to `127.0.0.1` via `_udp_emit()`. No acknowledgment, no retry. If nothing is listening, the packet is silently dropped.

| Port  | Source | Content | Frequency |
|-------|--------|---------|-----------|
| 19999 | `AgenticDecoyEngine._state_broadcast_loop()` | JSON snapshot: drone_id, attacker_ip, current_phase, attacker_level, belief_score, active_behaviors, last_action, dwell_seconds, commands_received, mtd_triggers_sent, confusion_delta, session_id | Every 1 second |
| 19998 | `OpenClawAgent._record_decision()` and phase/tool change events | JSON decision event: event type (`agent_decision`, `phase_transition`, `level_reclassified`), drone_id, trigger, behavior, input_state, decision rationale, expected_effect, confidence | On every agent decision or state transition |
| 19997 | `OpenClawAgent._emit_state_diff()` | JSON state diff: behavior name, trigger, list of changes (variable name, before value, after value, wire_level_change description, effect_on_attacker description) | On sysid rotation, port rotation, param cycle, mission refresh, ghost port, reboot, false flag |
| 19996 | `MavlinkResponseGenerator._emit_packet_event()` | JSON packet event: request msg_type, response bytes (first 20 bytes hex), srcSystem, system_status, deception notes, position jitter measurement | On every MAVLink response packet generated (including telemetry broadcasts) |

---

## 6. Configuration Constants

These values are loaded from `config/.env` via `shared/constants.py`:

| Constant | Default | Used by |
|----------|---------|---------|
| `AGENT_PROACTIVE_INTERVAL_SEC` | 45.0 | Proactive loop sleep interval |
| `AGENT_SYSID_ROTATION_SEC` | 120.0 | sysid rotation loop interval |
| `AGENT_PORT_ROTATION_SEC` | 90.0 | WebSocket port rotation loop interval |
| `AGENT_FALSE_FLAG_DWELL_THRESHOLD` | 120.0 | Dwell time (seconds) before false flag triggers |
| `AGENT_MIRROR_SERVICE_THRESHOLD` | 2 | Number of distinct services touched before ghost port opens |

Hardcoded constants in `openclaw_agent.py`:

| Constant | Value | Used by |
|----------|-------|---------|
| `_REBOOT_SILENCE_MIN_SEC` | 8.0 | Minimum reboot silence duration |
| `_REBOOT_SILENCE_MAX_SEC` | 15.0 | Maximum reboot silence duration |
| `_ARM_TAKEOFF_SIM_SEC` | 30.0 | Duration of ARM-takeoff simulation (but actual loop uses `range(30)` with 1s sleep) |
| `_ARM_CRASH_SILENCE_SEC` | 20.0 | Silence after simulated crash |
| `_MISSION_REFRESH_SEC` | 60.0 | Mission waypoint regeneration interval |
| `_PARAM_CYCLE_SEC` | 45.0 | Parameter drift interval |
| `_FLIGHT_SIM_DURATION_SEC` | 60.0 | Total flight simulation duration |
| `_FLIGHT_SIM_STEPS` | 12 | Number of altitude steps in flight simulation |
