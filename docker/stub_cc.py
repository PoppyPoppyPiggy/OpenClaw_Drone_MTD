#!/usr/bin/env python3
"""Stub CC — MAVLink UDP :14550 + HTTP :80 + RTSP :8554 for testing."""
import asyncio, json, os, random, socket, struct, time
from aiohttp import web

DRONE_ID = os.environ.get('DRONE_ID', 'honey_01')

# --- MAVLink UDP responder ---
async def mavlink_responder():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', 14550))
    sock.setblocking(False)
    loop = asyncio.get_event_loop()
    print(f'Stub CC [{DRONE_ID}] MAVLink UDP :14550')
    while True:
        try:
            data, addr = await loop.sock_recvfrom(sock, 2048)
            # Reply with heartbeat-like bytes
            hb = struct.pack('<BBBIBBx', 2, 3, 81, 0, 3, 3)
            await loop.sock_sendto(sock, hb, addr)
        except Exception:
            await asyncio.sleep(0.1)

# --- HTTP server ---
async def health(request):
    return web.Response(text='OK')

async def api_params(request):
    return web.json_response({'params': {'ARMING_CHECK': 1.0, 'RTL_ALT': 1500.0, 'BATT_CAPACITY': 5200.0}})

async def api_status(request):
    return web.json_response({'drone_id': DRONE_ID, 'armed': False, 'mode': 'STABILIZE', 'battery': random.randint(60,95)})

async def api_mission(request):
    return web.json_response({'mission_count': random.randint(3,8), 'waypoints': []})

async def start_http():
    app = web.Application()
    app.router.add_get('/health', health)
    app.router.add_get('/api/v1/params', api_params)
    app.router.add_get('/api/v1/status', api_status)
    app.router.add_get('/api/v1/mission', api_mission)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 80)
    await site.start()
    print(f'Stub CC [{DRONE_ID}] HTTP :80')

# --- RTSP stub (TCP banner) ---
async def rtsp_handler(reader, writer):
    writer.write(b'RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n')
    await writer.drain()
    writer.close()

async def start_rtsp():
    server = await asyncio.start_server(rtsp_handler, '0.0.0.0', 8554)
    print(f'Stub CC [{DRONE_ID}] RTSP :8554')
    await server.serve_forever()

async def main():
    await start_http()
    await asyncio.gather(mavlink_responder(), start_rtsp())

if __name__ == '__main__':
    asyncio.run(main())
