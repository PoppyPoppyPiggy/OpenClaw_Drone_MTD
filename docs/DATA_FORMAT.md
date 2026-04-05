# MIRAGE-UAS Data Format Specification

## 1. MavlinkCaptureEvent JSON Schema

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `event_id` | string (UUID v4) | unique | Event identifier |
| `timestamp_ns` | int64 | Unix nanoseconds | Capture timestamp |
| `drone_id` | string | `honey_01..03` | Target honey drone |
| `src_ip` | string | IPv4 format | Attacker source IP |
| `src_port` | int | 1-65535 | Attacker source port |
| `protocol` | string | `mavlink\|http\|rtsp\|websocket` | Transport protocol |
| `msg_type` | string | MAVLink msg name | e.g. `COMMAND_LONG` |
| `msg_id` | int | -1 to 65535 | MAVLink message ID |
| `sysid` | int | 0-255 | MAVLink system ID |
| `compid` | int | 0-255 | MAVLink component ID |
| `payload_hex` | string | hex-encoded | Raw payload bytes |
| `http_method` | string | `GET\|POST\|PUT\|DELETE` | HTTP method (if HTTP) |
| `http_path` | string | URL path | HTTP path (if HTTP) |
| `is_anomalous` | bool | | Parser anomaly flag |
| `session_id` | string (UUID) | | Session grouping key |

### MAVLink 2.0 Binary Frame Layout

```
Byte:  0     1      2         3       4     5      6      7-9      10..N    N+1..N+2  (N+3..N+15)
     ┌─────┬──────┬─────────┬───────┬─────┬──────┬──────┬────────┬────────┬──────────┬──────────────┐
     │0xFD │ LEN  │INCOMPAT │COMPAT │ SEQ │SYSID │COMPID│ MSGID  │PAYLOAD │  CRC16   │  SIGNATURE   │
     │ 1B  │ 1B   │  1B     │ 1B    │ 1B  │ 1B   │ 1B   │ 3B LE  │ LEN B  │  2B      │  13B (opt)   │
     └─────┴──────┴─────────┴───────┴─────┴──────┴──────┴────────┴────────┴──────────┴──────────────┘
```

### Example 1: L0 Random Bytes (No Framing)

```python
# L0 script kiddie sends random bytes — no MAVLink framing
payload_hex = "a3f1c29b0e47d82c1f6a3e9d"  # 12 random bytes
# Interceptor marks: is_anomalous=True, msg_type=""
```

### Example 2: L1 HEARTBEAT

```python
import struct
# MAVLink HEARTBEAT payload (msgid=0, 9 bytes)
# struct format: custom_mode(u32) + type(u8) + autopilot(u8) + base_mode(u8) + system_status(u8) + mavlink_version(u8)
payload = struct.pack("<IBBBBB",
    0,    # custom_mode = STABILIZE
    2,    # type = MAV_TYPE_QUADROTOR
    3,    # autopilot = MAV_AUTOPILOT_ARDUPILOTMEGA
    0x51, # base_mode = CUSTOM|SAFETY_ARMED
    4,    # system_status = MAV_STATE_ACTIVE
    3,    # mavlink_version = 3
)
# payload_hex = "000000000203510403"
```

### Example 3: L2 COMMAND_LONG ARM

```python
import struct
# COMMAND_LONG (msgid=76) — ARM command
# struct format: param1..7(7xf32) + command(u16) + target_system(u8) + target_component(u8)
payload = struct.pack("<fffffffHBB",
    1.0,  # param1 = ARM (1.0=arm, 0.0=disarm)
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # param2-7
    400,  # command = MAV_CMD_COMPONENT_ARM_DISARM
    1,    # target_system
    1,    # target_component
)
# payload_hex = "0000803f" + "00"*24 + "9001" + "0101"
```

---

## 2. ParsedAttackEvent Schema

| Field | Type | Description |
|-------|------|-------------|
| `raw_event` | MavlinkCaptureEvent | Original captured event |
| `attacker_level` | L0-L4 | Classified attacker skill level |
| `ttp_ids` | list[string] | ATT&CK for ICS v14 TTP IDs |
| `kill_chain_phase` | string | Kill chain phase |
| `confidence` | float [0.0, 1.0] | Classification confidence |
| `dwell_time_sec` | float | Session dwell time |

### L0-L4 Classification Thresholds

| Level | Events | Anomalies | TTPs | Protocols | Dwell (s) |
|-------|--------|-----------|------|-----------|-----------|
| L4 APT | ≥30 | ≥5 | ≥5 | ≥3 | ≥60 |
| L3 Advanced | ≥15 | ≥2 | ≥3 | ≥2 | any |
| L2 Intermediate | ≥8 | ≥1 or TTPs≥2 | any | any | ≥10 |
| L1 Basic | ≥3 or TTPs≥1 | any | any | any | any |
| L0 Script Kiddie | default | | | | |

### Complete TTP Mapping Table

| MAVLink/Protocol | ATT&CK ID | Technique Name | Phase | Conf |
|------------------|-----------|----------------|-------|------|
| HEARTBEAT | T0842 | Network Sniffing | RECON | 0.70 |
| PARAM_REQUEST_LIST | T0842, T0840 | Network Sniffing + Network Service Discovery | RECON | 0.90 |
| COMMAND_LONG (ARM) | T0855 | Unauthorized Command Message | EXPLOIT | 0.80 |
| PARAM_SET | T0836 | Modify Parameter | EXPLOIT | 0.90 |
| SET_MODE | T0858 | Change Operating Mode | EXPLOIT | 0.90 |
| MISSION_ITEM | T0821 | Modify Controller Tasking | ACTION | 0.85 |
| FILE_TRANSFER_PROTOCOL | T0843, T0839 | Program Upload + Module Firmware | INSTALL | 0.95 |
| LOG_REQUEST_LIST | T0882 | Theft of Operational Information | RECON | 0.85 |
| SET_POSITION_TARGET | T0855, T0831 | Unauthorized Command + Manipulation of Control | ACTION | 0.90 |
| GPS_INJECT_DATA | T0856, T0830 | Spoof Reporting Message + AitM | C2 | 0.95 |
| HTTP GET /api/* | T0842, T0888 | Network Sniffing + Remote System Info Discovery | RECON | 0.75 |
| WS CVE auth bypass | T0820, T0812, T0830 | Exploitation + Default Credentials + AitM | EXPLOIT | 0.98 |

---

## 3. STIX 2.1 Bundle Example

Full bundle for a COMMAND_LONG ARM attack:

```json
{
  "type": "bundle",
  "id": "bundle--a1b2c3d4-...",
  "objects": [
    {
      "type": "identity",
      "id": "identity--mitre-attack",
      "name": "MITRE ATT&CK for ICS",
      "identity_class": "organization"
    },
    {
      "type": "identity",
      "id": "identity--mirage-uas",
      "name": "MIRAGE-UAS Honeydrone System",
      "identity_class": "system"
    },
    {
      "type": "attack-pattern",
      "id": "attack-pattern--...",
      "name": "T0855 — Unauthorized Command Message",
      "external_references": [
        {"source_name": "mitre-attack", "external_id": "T0855",
         "url": "https://attack.mitre.org/techniques/T0855"}
      ],
      "kill_chain_phases": [
        {"kill_chain_name": "mitre-ics-attack", "phase_name": "exploitation"}
      ],
      "x_mitre_id": "T0855",
      "x_mirage_attacker_level": "L2_INTERMEDIATE"
    },
    {
      "type": "indicator",
      "id": "indicator--...",
      "name": "MIRAGE-UAS: mavlink/COMMAND_LONG from 192.168.1.100",
      "pattern": "[ipv4-addr:value = '192.168.1.100'] AND [network-traffic:dst_port = 14551 AND network-traffic:protocols[0] = 'mavlink']",
      "pattern_type": "stix",
      "valid_from": "2026-04-06T12:00:00Z",
      "confidence": 80
    },
    {
      "type": "ipv4-addr",
      "id": "ipv4-addr--...",
      "value": "192.168.1.100"
    },
    {
      "type": "network-traffic",
      "id": "network-traffic--...",
      "src_ref": "ipv4-addr--...",
      "dst_port": 14551,
      "protocols": ["mavlink"],
      "x_mirage_payload_hex": "0000803f...",
      "x_mirage_msg_type": "COMMAND_LONG"
    },
    {
      "type": "observed-data",
      "id": "observed-data--...",
      "first_observed": "2026-04-06T12:00:00Z",
      "last_observed": "2026-04-06T12:00:00Z",
      "number_observed": 1,
      "object_refs": ["ipv4-addr--...", "network-traffic--..."]
    }
  ]
}
```

---

## 4. DVD-CTI-Dataset-v1 CSV Column Specification

| Column | Type | Allowed Values | Example |
|--------|------|----------------|---------|
| `entry_id` | UUID v4 | unique | `52c1cf4b-06bc-...` |
| `timestamp_ns` | int64 | Unix ns | `1775427713358427078` |
| `drone_id` | string | `honey_01\|02\|03\|baseline` | `honey_01` |
| `src_ip` | IPv4 | any valid | `192.168.18.45` |
| `protocol` | string | `mavlink\|http\|rtsp\|websocket` | `mavlink` |
| `msg_type` | string | MAVLink msg / HTTP method | `COMMAND_LONG` |
| `attacker_level` | string | `L0_SCRIPT_KIDDIE..L4_APT` or empty | `L1_BASIC` |
| `ttp_ids` | string | pipe-separated TTP IDs or empty | `T0855\|T0836` |
| `label` | int | `0` (benign) or `1` (attack) | `1` |
| `confidence` | float | [0.0, 1.0] | `0.85` |
| `source` | string | `honeydrone\|synthetic\|sitl_capture` | `honeydrone` |
| `stix_bundle_id` | string | STIX bundle ID or empty | `bundle--a1b2...` |

**Target class distribution**: attack:benign = 1:1.5
**Minimum TTP coverage**: ≥12 unique ATT&CK for ICS TTPs

---

## 5. DeceptionScore Output Format

### deception_timeline.jsonl Record Schema

```json
{
  "timestamp": 1775430610.019,
  "deception_effectiveness": 0.3382,
  "avg_confusion_score": 0.72,
  "ghost_service_hit_rate": 0.0,
  "breadcrumb_follow_rate": 0.0,
  "total_sessions": 136,
  "protected_sessions": 136,
  "total_connections": 136,
  "ghost_connections": 0,
  "breadcrumbs_planted": 0,
  "breadcrumbs_taken": 0
}
```

### DeceptionScore Formula

```
DS = 0.30 * (time_on_decoys / total_time)
   + 0.25 * (1 - breach_rate)
   + 0.20 * avg_confusion_score
   + 0.15 * breadcrumb_follow_rate
   + 0.10 * ghost_service_hit_rate
```

Weights sum to 1.0. Loaded from `DECEPTION_SCORE_WEIGHTS` in `.env`.
