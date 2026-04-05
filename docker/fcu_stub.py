#!/usr/bin/env python3
"""
fcu_stub.py — Stub DVD Flight Controller (ArduPilot SITL emulation)

[ROLE] TCP :5760 MAVLink responder for testing without real DVD images.
[DATA FLOW] TCP accept → MAVLink parse → HEARTBEAT/ACK/PARAM response → stdout JSON log
"""
import json
import socket
import struct
import sys
import threading
import time

# MAVLink v2 constants
_MAV_TYPE_QUADROTOR = 2
_MAV_AUTOPILOT_ARDUPILOTMEGA = 3
_MAV_STATE_STANDBY = 3
_MAV_MODE_FLAG_CUSTOM = 0x01
_MAV_RESULT_ACCEPTED = 0

# Fake ArduPilot parameters
_PARAMS = [
    ("ARMING_CHECK", 1.0),
    ("RTL_ALT", 1500.0),
    ("BATT_CAPACITY", 5200.0),
    ("WPNAV_SPEED", 500.0),
    ("COMPASS_USE", 1.0),
    ("GPS_TYPE", 1.0),
    ("FENCE_ENABLE", 1.0),
    ("SYSID_MYGCS", 255.0),
]


def _log(event, **kw):
    """[ROLE] JSON structured log to stdout."""
    record = {"timestamp": time.time(), "event": event, **kw}
    print(json.dumps(record), flush=True)


def _pack_heartbeat():
    """[ROLE] MAVLink v2 HEARTBEAT payload (9 bytes)."""
    return struct.pack(
        "<IBBBBB",
        0,                          # custom_mode (STABILIZE)
        _MAV_TYPE_QUADROTOR,
        _MAV_AUTOPILOT_ARDUPILOTMEGA,
        _MAV_MODE_FLAG_CUSTOM,
        _MAV_STATE_STANDBY,
        3,                          # mavlink_version
    )


def _pack_command_ack(cmd_id=0):
    """[ROLE] COMMAND_ACK payload — always ACCEPTED."""
    return struct.pack("<HB", cmd_id, _MAV_RESULT_ACCEPTED)


def _pack_param_value(name, value, index, total):
    """[ROLE] PARAM_VALUE payload."""
    name_bytes = name.encode("ascii")[:16].ljust(16, b"\x00")
    return struct.pack("<f", value) + name_bytes + struct.pack(
        "<BHH", 9, total, index  # param_type=REAL32, count, index
    )


def handle_client(conn, addr):
    """[ROLE] Per-connection MAVLink handler: parse + respond + log."""
    _log("client_connected", addr=f"{addr[0]}:{addr[1]}")
    conn.settimeout(5.0)

    try:
        # Send initial heartbeat burst (3 packets, like real ArduPilot boot)
        for _ in range(3):
            conn.sendall(_pack_heartbeat())
            time.sleep(0.3)

        while True:
            try:
                data = conn.recv(2048)
                if not data:
                    break

                _log("recv", size=len(data), hex=data[:32].hex(), addr=f"{addr[0]}:{addr[1]}")

                # Simple heuristic: if data contains COMMAND_LONG pattern, send ACK
                if len(data) >= 2:
                    msg_hint = struct.unpack_from("<H", data, 0)[0] if len(data) >= 2 else 0
                    if 0 < msg_hint < 600:
                        conn.sendall(_pack_command_ack(msg_hint))

                # Always respond with heartbeat (keeps connection alive)
                conn.sendall(_pack_heartbeat())

                # If it looks like PARAM_REQUEST_LIST, send params
                if len(data) <= 4:
                    for i, (name, val) in enumerate(_PARAMS[:3]):
                        conn.sendall(_pack_param_value(name, val, i, len(_PARAMS)))
                        time.sleep(0.05)

            except socket.timeout:
                # Send periodic heartbeat even on timeout
                conn.sendall(_pack_heartbeat())
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        _log("client_disconnected", addr=f"{addr[0]}:{addr[1]}")
        conn.close()


def main():
    """[ROLE] TCP :5760 server — accepts MAVLink connections."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 5760))
    srv.listen(5)
    _log("fcu_stub_started", port=5760)

    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
