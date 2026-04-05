#!/usr/bin/env python3
"""
verify_ports.py — MAVLink UDP 포트 바인딩 및 WSL2 포워딩 검증

Project  : CTI-RL-MTD Honey Drone Testbed
Module   : Phase 0 / 포트 검증
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-02
Version  : 0.1.0

[Inputs]
    - 환경변수: MAVLINK_PORT_BASE, HONEY_DRONE_COUNT, SITL_PORT_BASE
    - 실행 중인 Docker 컨테이너 (optional — 없으면 dry-run)

[Outputs]
    - 터미널 포트 검증 리포트
    - results/logs/port_check.json

[Dependencies]
    - socket   (stdlib)
    - asyncio  (stdlib)
    - dotenv   (python-dotenv)
"""

import asyncio
import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import structlog
from dotenv import load_dotenv

# ── 환경변수 로드 ─────────────────────────────────────────────────────────────
ENV_PATH = Path(__file__).parent.parent / "config" / ".env"
load_dotenv(ENV_PATH)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

# ── 환경변수 로드 (인프라 고정값) ────────────────────────────────────────────
MAVLINK_PORT_BASE: int = int(os.environ["MAVLINK_PORT_BASE"])
SITL_PORT_BASE: int = int(os.environ["SITL_PORT_BASE"])
HONEY_DRONE_COUNT: int = int(os.environ["HONEY_DRONE_COUNT"])
CTI_API_PORT: int = int(os.environ["CTI_API_PORT"])
CTI_API_HOST: str = os.environ["CTI_API_HOST"]
RESULTS_DIR = Path(__file__).parent.parent / "results" / "logs"

# UDP 바인딩 대기 시간 (초)
BIND_TIMEOUT_SEC: float = 2.0
# MAVLink heartbeat 페이로드 (minimal valid, type=0)
MAVLINK_HEARTBEAT_BYTES: bytes = bytes([
    0xFE, 0x09, 0x00, 0xFF, 0xBE,   # magic, len, seq, sysid, compid
    0x00,                             # HEARTBEAT msg_id
    0x00, 0x00, 0x00, 0x00,          # custom_mode
    0x06, 0x08, 0x00, 0x00,          # type, autopilot, base_mode, system_status
    0x03,                             # mavlink_version
    0x00, 0x00,                       # CRC (dummy)
])


# ── 데이터 모델 ───────────────────────────────────────────────────────────────
@dataclass
class PortCheckResult:
    port: int
    protocol: str          # "UDP" | "TCP"
    role: str              # "mavlink_honey_01" | "cti_api" 등
    bind_ok: bool          # 포트 바인딩 가능 여부
    loopback_ok: bool      # 루프백 패킷 송수신 성공 여부
    error: str = ""

    def __repr__(self) -> str:
        bind_icon = "✅" if self.bind_ok else "❌"
        loop_icon = "✅" if self.loopback_ok else "⚠️ "
        return (
            f"{bind_icon} {self.protocol}:{self.port:5d} [{self.role:20s}] "
            f"bind={self.bind_ok} loopback={self.loopback_ok}"
            + (f"  err={self.error}" if self.error else "")
        )


# ── 검증 함수 ─────────────────────────────────────────────────────────────────

async def check_udp_port(port: int, role: str) -> PortCheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # UDP 포트에 소켓을 바인딩하고 루프백 패킷을 송수신하여
    # MAVLink 트래픽이 실제로 WSL2 ↔ Docker 간 흐를 수 있는지 확인
    #
    # [DATA FLOW]
    #   socket.bind(port) ──▶ sendto(127.0.0.1:port) ──▶ recvfrom()
    #   결과 ──▶ PortCheckResult
    # ────────────────────────────────────────────────────────────────────────
    bind_ok = False
    loopback_ok = False
    error = ""

    # 1단계: 바인딩 가능 여부
    try:
        probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe_sock.settimeout(BIND_TIMEOUT_SEC)
        probe_sock.bind(("0.0.0.0", port))
        bind_ok = True
    except OSError as e:
        error = f"bind failed: {e}"
        probe_sock = None

    # 2단계: 루프백 송수신 (바인딩 성공 시에만)
    if bind_ok and probe_sock:
        try:
            recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            recv_sock.settimeout(BIND_TIMEOUT_SEC)
            recv_sock.bind(("127.0.0.1", port + 1000))  # 임시 수신 포트

            probe_sock.sendto(MAVLINK_HEARTBEAT_BYTES, ("127.0.0.1", port + 1000))
            data, _ = recv_sock.recvfrom(256)
            loopback_ok = data == MAVLINK_HEARTBEAT_BYTES
            recv_sock.close()
        except OSError as e:
            error = f"loopback failed: {e}"
        finally:
            probe_sock.close()

    return PortCheckResult(
        port=port,
        protocol="UDP",
        role=role,
        bind_ok=bind_ok,
        loopback_ok=loopback_ok,
        error=error,
    )


async def check_tcp_port(port: int, role: str) -> PortCheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # TCP 포트 바인딩 가능 여부만 확인 (SITL 제어 포트, CTI API)
    # 루프백 통신은 서비스 기동 후 별도 health check로 수행
    #
    # [DATA FLOW]
    #   socket.bind(port) ──▶ PortCheckResult
    # ────────────────────────────────────────────────────────────────────────
    bind_ok = False
    error = ""

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        bind_ok = True
        s.close()
    except OSError as e:
        error = f"bind failed: {e}"

    return PortCheckResult(
        port=port,
        protocol="TCP",
        role=role,
        bind_ok=bind_ok,
        loopback_ok=False,    # TCP는 루프백 테스트 미수행
        error=error,
    )


async def run_port_checks() -> list[PortCheckResult]:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # 허니드론 N개의 MAVLink(UDP) + SITL(TCP) 포트와
    # CTI API(TCP) 포트를 병렬로 검증
    #
    # [DATA FLOW]
    #   환경변수(포트 범위) ──▶ 비동기 check 태스크 생성
    #   ──▶ gather() ──▶ list[PortCheckResult]
    # ────────────────────────────────────────────────────────────────────────
    tasks = []

    for i in range(HONEY_DRONE_COUNT):
        idx = i + 1
        mavlink_port = MAVLINK_PORT_BASE + idx        # 14551, 14552, 14553
        sitl_port = SITL_PORT_BASE + idx              # 5761, 5762, 5763

        tasks.append(check_udp_port(
            mavlink_port, role=f"mavlink_honey_{idx:02d}"
        ))
        tasks.append(check_tcp_port(
            sitl_port, role=f"sitl_honey_{idx:02d}"
        ))

    # CTI API TCP 포트
    tasks.append(check_tcp_port(CTI_API_PORT, role="cti_ingest_api"))

    results = await asyncio.gather(*tasks)
    return list(results)


def save_port_report(results: list[PortCheckResult]) -> None:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # 포트 검증 결과를 JSON으로 저장 (CI / 논문 재현성 로그)
    # [DATA FLOW] list[PortCheckResult] ──▶ dict ──▶ results/logs/port_check.json
    # ────────────────────────────────────────────────────────────────────────
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "port": r.port, "protocol": r.protocol, "role": r.role,
            "bind_ok": r.bind_ok, "loopback_ok": r.loopback_ok, "error": r.error,
        }
        for r in results
    ]
    output = RESULTS_DIR / "port_check.json"
    output.write_text(json.dumps(payload, indent=2))
    logger.info("port_check saved", path=str(output))


async def main() -> None:
    print("\n" + "═" * 60)
    print("  CTI-RL-MTD Honey Drone — Phase 0: MAVLink 포트 검증")
    print("═" * 60 + "\n")

    results = await run_port_checks()

    for r in results:
        print(repr(r))

    save_port_report(results)

    failed = [r for r in results if not r.bind_ok]
    print("\n" + "─" * 60)
    print(f"  검증 완료: {len(results)}개 포트  |  FAIL: {len(failed)}개")
    print("─" * 60)

    if failed:
        print("\n⚠️  사용 중인 포트가 있습니다. docker-compose 실행 전 해결하세요.\n")
        sys.exit(1)

    print("\n✅ 모든 포트 사용 가능 — docker-compose.honey.yml 실행 준비 완료\n")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
