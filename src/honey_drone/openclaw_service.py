#!/usr/bin/env python3
"""
openclaw_service.py — OpenClaw SDK-style WebSocket Response Service

[ROLE]
    Emulates OpenClaw's agentic AI interface for L3-L4 attacker engagement.
    Provides realistic skill_invoke / agent.run / terminal responses
    that mirror real OpenClaw SDK behavior (v2026.1.x).

[DATA FLOW]
    AgenticDecoyEngine._websocket_handler()
    ──▶ OpenClawService.handle(raw_msg, attacker_ip)
    ──▶ JSON response (skill result / agent output / terminal echo)

[EMULATED ENDPOINTS]
    - skill_invoke:   MAVLink telemetry, camera, mission, system info
    - agent.run:      Multi-step task execution with progress updates
    - terminal:       Fake shell with breadcrumb-laden output
    - auth:           CVE-2026-25253 (always accepts, leaks permissions)
    - config:         Drone configuration dump with planted credentials

[REF] MIRAGE-UAS §4.3 — OpenClaw Deception Interface
"""
from __future__ import annotations

import hashlib
import json
import random
import time
import uuid
from typing import Optional

from shared.logger import get_logger

logger = get_logger(__name__)


class OpenClawService:
    """
    Stateful OpenClaw SDK emulation service.
    Maintains per-attacker session state for realistic multi-turn interactions.
    """

    def __init__(self, drone_id: str, index: int = 1) -> None:
        self._drone_id = drone_id
        self._index = index
        self._sessions: dict[str, dict] = {}
        self._version = "2026.1.28"  # Pre-CVE-patch version

        # Breadcrumb material
        self._signing_key = hashlib.md5(drone_id.encode()).hexdigest()
        self._api_token = f"sk-drone-{hashlib.sha256(drone_id.encode()).hexdigest()[:16]}"
        self._ssh_password = "companion_root_2026"

        # Fake skill registry (mirrors real OpenClaw)
        self._skills = {
            "mavlink_telemetry": self._skill_telemetry,
            "camera_stream": self._skill_camera,
            "mission_manager": self._skill_mission,
            "system_info": self._skill_system,
            "param_dump": self._skill_params,
            "log_download": self._skill_logs,
            "firmware_info": self._skill_firmware,
        }

    def _get_session(self, attacker_ip: str) -> dict:
        if attacker_ip not in self._sessions:
            self._sessions[attacker_ip] = {
                "session_id": str(uuid.uuid4())[:8],
                "authenticated": False,
                "permissions": [],
                "commands_run": 0,
                "started_at": time.time(),
            }
        return self._sessions[attacker_ip]

    def handle(self, raw_msg: str | bytes, attacker_ip: str) -> Optional[dict]:
        """
        Handle incoming WebSocket message, return OpenClaw-style response.
        Returns None if message format is unrecognized.
        """
        if isinstance(raw_msg, bytes):
            raw_msg = raw_msg.decode("utf-8", errors="ignore")

        try:
            msg = json.loads(raw_msg)
        except (json.JSONDecodeError, ValueError):
            return None

        session = self._get_session(attacker_ip)
        msg_type = msg.get("type", "")
        session["commands_run"] += 1

        base = {
            "version": self._version,
            "timestamp": time.time(),
            "session": session["session_id"],
        }

        if msg_type == "ping":
            return {**base, "type": "pong", "status": "ok"}

        if msg_type == "auth":
            return self._handle_auth(msg, session, base)

        if msg_type == "skill_invoke":
            return self._handle_skill(msg, session, base)

        if msg_type == "agent.run":
            return self._handle_agent_run(msg, session, base)

        if msg_type == "terminal":
            return self._handle_terminal(msg, session, base)

        if msg_type == "config":
            return self._handle_config(session, base)

        if msg_type == "list_skills":
            return {
                **base,
                "type": "skill_list",
                "skills": list(self._skills.keys()),
                "count": len(self._skills),
            }

        # Default ack
        return {**base, "type": "ack", "message": "Connected to OpenClaw gateway"}

    def _handle_auth(self, msg: dict, session: dict, base: dict) -> dict:
        """CVE-2026-25253: auth bypass — always succeeds, leaks permissions."""
        session["authenticated"] = True
        session["permissions"] = [
            "skill_invoke", "config_read", "config_write",
            "mission_upload", "firmware_update", "terminal",
        ]
        logger.info(
            "openclaw_auth_bypass",
            drone_id=self._drone_id,
            permissions=session["permissions"],
        )
        return {
            **base,
            "type": "auth_result",
            "authenticated": True,
            "permissions": session["permissions"],
            "token": self._api_token,
        }

    def _handle_skill(self, msg: dict, session: dict, base: dict) -> dict:
        """Skill invocation — run registered skill handler."""
        skill_name = msg.get("skill", "mavlink_telemetry")
        params = msg.get("params", {})

        handler = self._skills.get(skill_name)
        if handler is None:
            return {
                **base,
                "type": "skill_error",
                "skill": skill_name,
                "error": f"Unknown skill: {skill_name}",
                "available": list(self._skills.keys()),
            }

        result = handler(params)
        return {
            **base,
            "type": "skill_result",
            "skill": skill_name,
            "status": "success",
            "result": result,
        }

    def _handle_agent_run(self, msg: dict, session: dict, base: dict) -> dict:
        """agent.run — multi-step task with progress reporting."""
        task = msg.get("task", "status check")
        return {
            **base,
            "type": "agent_result",
            "task": task,
            "steps": [
                {"step": 1, "action": "connecting to flight controller", "status": "done"},
                {"step": 2, "action": "reading telemetry data", "status": "done"},
                {"step": 3, "action": "analyzing system state", "status": "done"},
            ],
            "result": {
                "drone_id": self._drone_id,
                "armed": False,
                "mode": random.choice(["STABILIZE", "GUIDED", "AUTO"]),
                "battery_pct": random.randint(45, 95),
                "gps_fix": 3,
                "satellites": random.randint(8, 14),
                "altitude_m": round(random.uniform(0, 150), 1),
            },
            "signing_key_hint": self._signing_key[:8] + "...",
        }

    def _handle_terminal(self, msg: dict, session: dict, base: dict) -> dict:
        """Terminal emulation — fake shell with breadcrumbs."""
        command = msg.get("command", "whoami")
        output = self._fake_terminal(command)
        return {
            **base,
            "type": "terminal_result",
            "command": command,
            "exit_code": 0,
            "output": output,
        }

    def _handle_config(self, session: dict, base: dict) -> dict:
        """Configuration dump with planted credentials."""
        return {
            **base,
            "type": "config_dump",
            "config": {
                "drone_id": self._drone_id,
                "signing_key": self._signing_key,
                "api_token": self._api_token,
                "ssh_password": self._ssh_password,
                "mavlink_port": 14550,
                "webclaw_port": 18789,
                "backup_gcs": f"172.40.0.{10 + self._index}:14560",
                "fleet_c2": "172.40.0.100:4444",
            },
        }

    # ── Skill handlers ──────────────────────────────────────────

    def _skill_telemetry(self, params: dict) -> dict:
        return {
            "drone_id": self._drone_id,
            "altitude": round(random.uniform(0, 150), 1),
            "speed_ms": round(random.uniform(0, 15), 1),
            "battery": random.randint(45, 95),
            "gps_fix": 3,
            "satellites": random.randint(8, 14),
            "mode": random.choice(["STABILIZE", "GUIDED", "AUTO", "RTL"]),
            "armed": random.choice([True, False]),
        }

    def _skill_camera(self, params: dict) -> dict:
        return {
            "stream_url": f"rtsp://172.40.0.{10 + self._index}:8554/camera",
            "resolution": "1280x720",
            "fps": 30,
            "codec": "H264",
            "recording": random.choice([True, False]),
        }

    def _skill_mission(self, params: dict) -> dict:
        return {
            "mission_count": random.randint(3, 12),
            "current_wp": random.randint(0, 5),
            "waypoints": [
                {"seq": i, "lat": round(37.5665 + random.gauss(0, 0.005), 6),
                 "lon": round(126.978 + random.gauss(0, 0.005), 6),
                 "alt": round(random.uniform(30, 150), 1)}
                for i in range(random.randint(3, 8))
            ],
        }

    def _skill_system(self, params: dict) -> dict:
        return {
            "drone_id": self._drone_id,
            "firmware": "ArduCopter V4.3.7 (fmuv3)",
            "frame": "QUAD/X",
            "os": "ChibiOS",
            "uptime_sec": random.randint(3600, 86400),
            "cpu_pct": random.randint(10, 45),
            "mem_free_kb": random.randint(50000, 200000),
        }

    def _skill_params(self, params: dict) -> dict:
        return {
            "param_count": 17,
            "params": {
                "ARMING_CHECK": 1.0, "RTL_ALT": 1500.0,
                "BATT_CAPACITY": 5200.0, "WPNAV_SPEED": 500.0,
                "FENCE_ENABLE": 1.0, "GPS_TYPE": 1.0,
                "SIGNING_KEY": self._signing_key,
            },
        }

    def _skill_logs(self, params: dict) -> dict:
        return {
            "log_count": 5,
            "logs": [
                {"id": i + 1, "date": f"2026-04-0{i + 1}",
                 "size_kb": random.randint(100, 5000),
                 "duration_min": random.randint(5, 45)}
                for i in range(5)
            ],
        }

    def _skill_firmware(self, params: dict) -> dict:
        return {
            "current": "ArduCopter V4.3.7",
            "available": "ArduCopter V4.4.0",
            "update_url": f"http://172.40.0.{10 + self._index}:8765/firmware/update",
            "signing_required": True,
            "signing_key_path": "/etc/mavlink/signing.key",
        }

    def _fake_terminal(self, command: str) -> str:
        """Generate fake terminal output with breadcrumbs."""
        cmd = command.strip().lower()
        if cmd == "whoami":
            return "companion"
        if cmd == "id":
            return "uid=1000(companion) gid=1000(companion) groups=1000(companion),27(sudo),44(video)"
        if "cat" in cmd and "signing" in cmd:
            return self._signing_key
        if "ls" in cmd:
            return "config.yaml  firmware/  logs/  mavlink-router  signing.key  startup.sh"
        if "cat" in cmd and "config" in cmd:
            return json.dumps({
                "drone_id": self._drone_id,
                "signing_key": self._signing_key,
                "ssh_password": self._ssh_password,
                "api_token": self._api_token,
            }, indent=2)
        if "uname" in cmd:
            return "Linux companion 5.15.0-91-generic #101-Ubuntu SMP aarch64 GNU/Linux"
        if "ps" in cmd:
            return (
                "PID TTY      TIME CMD\n"
                "  1 ?    00:00:02 systemd\n"
                " 42 ?    00:01:15 mavlink-router\n"
                " 78 ?    00:00:45 openclaw-agent\n"
                "112 ?    00:00:03 camera-streamer\n"
            )
        if "env" in cmd or "printenv" in cmd:
            return (
                f"DRONE_ID={self._drone_id}\n"
                f"MAVLINK_PORT=14550\n"
                f"API_TOKEN={self._api_token}\n"
                f"SIGNING_KEY={self._signing_key[:16]}...\n"
            )
        return f"bash: {command}: command executed"
