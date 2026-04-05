#!/usr/bin/env python3
"""
inet_config_gen.py — INET 4.5 NED + ini 설정 생성기

Project  : MIRAGE-UAS
Module   : OMNeT++ / INET Config Generator
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - HoneyDroneConfig list (constants.py 기반)
    - MTD latency data (Table III)

[Outputs]
    - omnetpp_trace/UDPDroneNetwork.ned
    - omnetpp_trace/omnetpp.ini

[Dependencies]
    - shared.constants (MAVLINK_PORT_BASE, ATTACKER_PRIORS)

[REF] INET 4.5 UdpBasicBurst, TcpServerHostApp NED modules

[DATA FLOW]
    constants + params ──▶ generate_ned_file() ──▶ .ned
    constants + params ──▶ generate_omnetpp_ini() ──▶ .ini
"""

from __future__ import annotations

from pathlib import Path

from shared.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_OUT = Path("omnetpp_trace")


def generate_ned_file(
    honey_count: int = 3, output_path: Path | None = None
) -> str:
    """
    [ROLE] INET 4.5 NED 네트워크 정의 파일 생성.
           AttackerNode + HoneyDroneNode[N] + CTINode + EtherSwitch.

    [DATA FLOW]
        honey_count ──▶ NED 파일 ──▶ UDPDroneNetwork.ned
    """
    out = output_path or (_DEFAULT_OUT / "UDPDroneNetwork.ned")
    out.parent.mkdir(parents=True, exist_ok=True)

    ned = f"""//
// UDPDroneNetwork.ned — MIRAGE-UAS OMNeT++ Network Definition
// Auto-generated for INET 4.5
//
package mirage;

import inet.node.inet.StandardHost;
import inet.node.ethernet.EtherSwitch;

network UDPDroneNetwork
{{
    parameters:
        int numDrones = default({honey_count});

    submodules:
        attacker: StandardHost {{
            @display("i=device/laptop;p=100,200");
        }}

        drone[numDrones]: StandardHost {{
            @display("i=device/drone;p=300,100+100*index");
        }}

        ctiNode: StandardHost {{
            @display("i=device/server;p=500,200");
        }}

        switch: EtherSwitch {{
            @display("i=device/switch;p=300,300");
            gates:
                ethg[numDrones + 2];
        }}

    connections:
        attacker.ethg++ <--> switch.ethg[0];
        for i=0..numDrones-1 {{
            drone[i].ethg++ <--> switch.ethg[i+1];
        }}
        ctiNode.ethg++ <--> switch.ethg[numDrones+1];
}}
"""
    out.write_text(ned)
    logger.info("ned_file_generated", path=str(out))
    return str(out)


def generate_omnetpp_ini(
    output_path: Path | None = None, params: dict | None = None
) -> str:
    """
    [ROLE] OMNeT++ omnetpp.ini 시뮬레이션 설정 생성.

    [DATA FLOW]
        params (MTD latency, attacker priors) ──▶ omnetpp.ini
    """
    out = output_path or (_DEFAULT_OUT / "omnetpp.ini")
    out.parent.mkdir(parents=True, exist_ok=True)

    p = params or {}
    mavlink_port = p.get("mavlink_port", 14551)
    webclaw_port = p.get("webclaw_port", 18790)
    avg_mtd_latency_ms = p.get("avg_mtd_latency_ms", 250)

    ini = f"""#
# omnetpp.ini — MIRAGE-UAS Simulation Configuration
# Auto-generated for OMNeT++ 6.x + INET 4.5
#

[General]
network = mirage.UDPDroneNetwork
sim-time-limit = 300s
cmdenv-express-mode = true

# Drone configuration
**.drone[*].numApps = 2
**.drone[*].app[0].typename = "UdpEchoApp"
**.drone[*].app[0].localPort = {mavlink_port}
**.drone[*].app[1].typename = "TcpServerHostApp"
**.drone[*].app[1].localPort = {webclaw_port}

# Attacker configuration
**.attacker.numApps = 1
**.attacker.app[0].typename = "UdpBasicBurst"
**.attacker.app[0].destAddresses = "drone[0] drone[1] drone[2]"
**.attacker.app[0].destPort = {mavlink_port}
**.attacker.app[0].messageLength = intuniform(14,263)B
**.attacker.app[0].sendInterval = exponential(100ms)

# CTI passive tap
**.ctiNode.numApps = 1
**.ctiNode.app[0].typename = "UdpSink"
**.ctiNode.app[0].localPort = 19551

# MTD reaction time (from Table III average)
**.drone[*].mtdReactionTime = {avg_mtd_latency_ms}ms
"""
    out.write_text(ini)
    logger.info("omnetpp_ini_generated", path=str(out))
    return str(out)


def map_mtd_to_omnetpp(
    action_type: str, drone_index: int, new_val: str, time_ms: int
) -> str:
    """
    [ROLE] MTD 액션 → OMNeT++ 파라미터 변경 명령 문자열.

    [DATA FLOW]
        action_type + values ──▶ OMNeT++ parameter change string
    """
    mapping = {
        "PORT_ROTATE": f'**.drone[{drone_index}].app[0].localPort = {new_val} at {time_ms}ms',
        "IP_SHUFFLE":  f'**.drone[{drone_index}].networkLayer.ip.address = "{new_val}" at {time_ms}ms',
        "KEY_ROTATE":  f'**.drone[{drone_index}].signingKey = "{new_val}" at {time_ms}ms',
        "SERVICE_MIGRATE": f'**.drone[{drone_index}].restart = true at {time_ms}ms',
    }
    return mapping.get(action_type, f"# Unknown action: {action_type}")
