#!/usr/bin/env python3
"""Stub FCU — TCP :5760 MAVLink heartbeat responder for testing."""
import socket, struct, time, threading

def heartbeat_payload():
    # MAVLink v2 HEARTBEAT: type=2(quadrotor), autopilot=3(ardupilot), base_mode=81, custom=0, status=3(standby), ver=3
    return struct.pack('<BBBIBBx', 2, 3, 81, 0, 3, 3)

def handle_client(conn, addr):
    try:
        while True:
            conn.sendall(heartbeat_payload())
            time.sleep(1.0)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        conn.close()

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', 5760))
    srv.listen(5)
    print('Stub FCU listening on :5760')
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == '__main__':
    main()
