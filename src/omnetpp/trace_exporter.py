#!/usr/bin/env python3
"""
trace_exporter.py — OMNeT++ 6.x + INET 4.5 트레이스 내보내기

Project  : MIRAGE-UAS
Module   : OMNeT++ / Trace Exporter
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - results/dataset/DVD-CTI-Dataset-v1/dataset.csv
    - results/metrics/*.json

[Outputs]
    - omnetpp_trace/attack_scenario.xml
    - omnetpp_trace/traffic_trace.csv
    - omnetpp_trace/mtd_events.csv
    - omnetpp_trace/replay.ini

[Dependencies]
    - csv, json, xml.etree (stdlib)

[REF] OMNeT++ 6.x ScenarioManager, INET 4.5 UdpBasicBurst

[DATA FLOW]
    dataset.csv + metrics JSON ──▶ export_*() ──▶ omnetpp_trace/
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from shared.constants import RESULTS_DIR
from shared.logger import get_logger

logger = get_logger(__name__)

_DATASET_CSV  = Path(RESULTS_DIR) / "dataset" / "DVD-CTI-Dataset-v1" / "dataset.csv"
_METRICS_DIR  = Path(RESULTS_DIR) / "metrics"
_DEFAULT_OUT  = Path("omnetpp_trace")


def export_attack_scenario(
    csv_path: Path | None = None, output_dir: Path | None = None
) -> str:
    """
    [ROLE] dataset.csv → OMNeT++ ScenarioManager XML.

    [DATA FLOW]
        dataset.csv attack rows ──▶ <scenario> XML ──▶ attack_scenario.xml
    """
    src = csv_path or _DATASET_CSV
    out = output_dir or _DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)

    root = Element("scenarios")
    root.set("xmlns", "omnetpp.org/scenario/1.0")

    max_ts = 0
    if src.exists():
        with open(src) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("label", "0") != "1":
                    continue
                ts_ns = int(row.get("timestamp_ns", 0))
                ts_ms = ts_ns // 1_000_000
                max_ts = max(max_ts, ts_ms)

                sc = SubElement(root, "scenario")
                sc.set("t", f"{ts_ms}ms")
                pkt = SubElement(sc, "sendPacket")
                pkt.set("src", row.get("src_ip", "0.0.0.0"))
                pkt.set("dst", "172.40.0.10")
                pkt.set("port", "14550")
                pkt.set("protocol", row.get("protocol", "mavlink"))
                pkt.set("size", "64")
                pkt.set("msg_type", row.get("msg_type", ""))
                pkt.set("label", "1")

    root.set("simTimeLimit", f"{max_ts + 1000}ms")

    xml_path = out / "attack_scenario.xml"
    xml_bytes = tostring(root, encoding="unicode")
    xml_path.write_text('<?xml version="1.0"?>\n' + xml_bytes)
    logger.info("omnetpp_scenario_exported", path=str(xml_path))
    return str(xml_path)


def export_traffic_trace(
    metrics_dir: Path | None = None, output_dir: Path | None = None
) -> str:
    """
    [ROLE] 메트릭 기반 합성 트래픽 트레이스 CSV 생성.

    [DATA FLOW]
        table_iv_dataset.json ──▶ Poisson 타이밍 ──▶ traffic_trace.csv
    """
    m_dir = metrics_dir or _METRICS_DIR
    out = output_dir or _DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)

    csv_path = out / "traffic_trace.csv"
    t4_path = m_dir / "table_iv_dataset.json"
    total = 100
    if t4_path.exists():
        t4 = json.loads(t4_path.read_text())
        total = t4.get("total_samples", 100)

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ms", "src_ip", "dst_ip", "dst_port",
                         "protocol", "size_bytes", "msg_type", "label"])
        t = 0
        for i in range(min(total, 500)):
            t += max(1, int(random.expovariate(0.1)))
            proto = random.choice(["mavlink", "http", "websocket"])
            label = 1 if random.random() < 0.4 else 0
            writer.writerow([
                t,
                f"172.40.0.{random.randint(100,200)}",
                f"172.40.0.{random.choice([10,11,12])}",
                14550 if proto == "mavlink" else 80,
                proto,
                random.randint(14, 263),
                random.choice(["HEARTBEAT", "COMMAND_LONG", "PARAM_SET", "SET_MODE"]),
                label,
            ])

    logger.info("omnetpp_traffic_trace_exported", path=str(csv_path), rows=min(total, 500))
    return str(csv_path)


def export_mtd_events(
    metrics_dir: Path | None = None, output_dir: Path | None = None
) -> str:
    """
    [ROLE] MTD 이벤트 CSV 생성 (Table III 기반).

    [DATA FLOW]
        table_iii_mtd_latency.json ──▶ mtd_events.csv
    """
    m_dir = metrics_dir or _METRICS_DIR
    out = output_dir or _DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)

    csv_path = out / "mtd_events.csv"
    t3_path = m_dir / "table_iii_mtd_latency.json"

    actions = []
    if t3_path.exists():
        actions = json.loads(t3_path.read_text())

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ms", "drone_id", "action_type",
                         "old_port", "new_port", "old_ip", "new_ip", "latency_ms"])
        t = 5000
        for act in actions:
            for i in range(act.get("count", 1)):
                t += random.randint(10000, 30000)
                drone = f"honey_0{random.randint(1,3)}"
                writer.writerow([
                    t, drone, act["action_type"],
                    14551, 14551 + random.randint(1, 100),
                    "172.40.0.10", f"172.40.0.{random.randint(10,50)}",
                    round(act["avg_ms"] + random.gauss(0, act["avg_ms"] * 0.1), 1),
                ])

    logger.info("omnetpp_mtd_events_exported", path=str(csv_path))
    return str(csv_path)


def generate_replay_ini(
    output_dir: Path | None = None, honey_count: int = 3
) -> str:
    """
    [ROLE] OMNeT++ .ini 시뮬레이션 설정 파일 생성.

    [DATA FLOW]
        honey_count + defaults ──▶ replay.ini
    """
    out = output_dir or _DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)

    ini_content = f"""[General]
network = UDPDroneNetwork
sim-time-limit = 300s
**.drone[*].numApps = 2
**.drone[*].app[0].typename = "UdpEchoApp"
**.drone[*].app[0].localPort = 14551
**.drone[*].app[1].typename = "TcpServerHostApp"
**.drone[*].app[1].localPort = 18789
**.attacker.numApps = 1
**.attacker.app[0].typename = "UdpBasicBurst"
**.attacker.app[0].destAddresses = "{' '.join(f'drone[{i}]' for i in range(honey_count))}"
**.attacker.app[0].messageLength = intuniform(14,263)B
**.attacker.app[0].sendInterval = exponential(100ms)
"""

    ini_path = out / "replay.ini"
    ini_path.write_text(ini_content)
    logger.info("omnetpp_replay_ini_generated", path=str(ini_path))
    return str(ini_path)


def main() -> None:
    """
    [ROLE] 모든 OMNeT++ 트레이스 내보내기 실행.

    [DATA FLOW]
        export_*() ──▶ omnetpp_trace/ ──▶ 요약 출력
    """
    xml = export_attack_scenario()
    csv_path = export_traffic_trace()
    mtd = export_mtd_events()
    ini = generate_replay_ini()
    print(f"OMNeT++ traces exported:")
    print(f"  Scenario: {xml}")
    print(f"  Traffic:  {csv_path}")
    print(f"  MTD:      {mtd}")
    print(f"  Config:   {ini}")


if __name__ == "__main__":
    main()
