#!/usr/bin/env python3
"""
pcap_writer.py — MAVLink 이벤트 libpcap 파일 기록기

Project  : MIRAGE-UAS
Module   : CTI Pipeline / PCAP Writer
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - MavlinkCaptureEvent list (hex-encoded payloads)

[Outputs]
    - results/logs/capture_{drone_id}_{timestamp}.pcap  (Wireshark compatible)

[Dependencies]
    - struct (stdlib)

[설계 원칙]
    - Standard libpcap format (magic=0xa1b2c3d4)
    - Link-layer type 101 (Raw IP) for MAVLink-over-UDP
    - Minimum 14 byte padding for Wireshark compatibility

[DATA FLOW]
    MavlinkCaptureEvent ──▶ PcapWriter.write_event()
    ──▶ libpcap per-packet record ──▶ .pcap file
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

from shared.logger import get_logger
from shared.models import MavlinkCaptureEvent

logger = get_logger(__name__)

# libpcap constants
_PCAP_MAGIC     = 0xA1B2C3D4
_PCAP_VERSION   = (2, 4)
_PCAP_SNAPLEN   = 65535
_PCAP_LINKTYPE  = 101       # LINKTYPE_RAW (raw IP)
_MIN_PACKET_LEN = 14        # Minimum packet length for Wireshark


class PcapWriter:
    """
    [ROLE] MavlinkCaptureEvent를 libpcap 형식으로 기록.
           Wireshark에서 직접 분석 가능한 .pcap 파일 생성.

    [DATA FLOW]
        __init__(drone_id) ──▶ global header 기록
        write_event(event) ──▶ per-packet record 기록
        close() ──▶ flush + close
    """

    def __init__(self, drone_id: str, output_dir: str = "results/logs") -> None:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        self._path = out_path / f"capture_{drone_id}_{ts}.pcap"
        self._file = open(self._path, "wb")
        self._write_global_header()
        self._count = 0
        logger.info("pcap_writer_opened", path=str(self._path), drone_id=drone_id)

    def _write_global_header(self) -> None:
        """
        [ROLE] libpcap global header 기록 (24 bytes).

        [DATA FLOW]
            magic + version + snaplen + linktype ──▶ file header
        """
        header = struct.pack(
            "<IHHiIII",
            _PCAP_MAGIC,
            _PCAP_VERSION[0],
            _PCAP_VERSION[1],
            0,                  # thiszone (UTC)
            0,                  # sigfigs
            _PCAP_SNAPLEN,
            _PCAP_LINKTYPE,
        )
        self._file.write(header)

    def write_event(self, event: MavlinkCaptureEvent) -> None:
        """
        [ROLE] MavlinkCaptureEvent를 pcap per-packet record로 기록.

        [DATA FLOW]
            event.payload_hex ──▶ bytes 변환
            ──▶ pcap packet header (ts_sec, ts_usec, incl_len, orig_len)
            ──▶ packet data (최소 14 bytes padding)
        """
        try:
            data = bytes.fromhex(event.payload_hex) if event.payload_hex else b""
        except ValueError:
            data = b""

        # Pad to minimum length for Wireshark
        if len(data) < _MIN_PACKET_LEN:
            data = data.ljust(_MIN_PACKET_LEN, b"\x00")

        # Timestamp from event
        ts_sec = event.timestamp_ns // 1_000_000_000
        ts_usec = (event.timestamp_ns % 1_000_000_000) // 1000

        # Per-packet header: ts_sec, ts_usec, incl_len, orig_len
        pkt_header = struct.pack("<IIII", ts_sec, ts_usec, len(data), len(data))
        self._file.write(pkt_header)
        self._file.write(data)
        self._count += 1

    def close(self) -> None:
        """
        [ROLE] 파일 flush + close.

        [DATA FLOW]
            flush() ──▶ close() ──▶ 로그
        """
        self._file.flush()
        self._file.close()
        logger.info(
            "pcap_writer_closed",
            path=str(self._path),
            packets=self._count,
        )

    @property
    def path(self) -> Path:
        """[ROLE] 출력 파일 경로 반환."""
        return self._path

    @property
    def packet_count(self) -> int:
        """[ROLE] 기록된 패킷 수 반환."""
        return self._count
