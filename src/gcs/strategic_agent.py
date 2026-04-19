#!/usr/bin/env python3
"""
strategic_agent.py — Tier 1 GCS Strategic LLM Agent (MIRAGE-UAS §4.3)

[ROLE]
    Runs at the Ground Control Station. Observes fleet-wide state (per-drone
    attacker fingerprints, belief mu_A, phase, engagement metrics) and emits
    StrategicDirective messages that bias Tier 2 tactical decisions.

    The agent is "OpenClaw-inspired" — it borrows OpenClaw's gateway-tool
    abstraction (strategic decisions routed through an LLM with a bounded
    tool set) but runs locally with Ollama rather than depending on the
    upstream OpenClaw Node.js binary. This keeps the evaluation fully local,
    reproducible, and free.

[TOOLS]  (LLM-selected, one per cycle)
    deploy_decoy      - bias skill toward ghost_port / flight_sim
    rotate_identity   - bias skill toward reboot_sim + request sysid rotation
    escalate          - raise urgency, bias toward credential_leak
    de_escalate       - reduce urgency, bias toward statustext
    alert_operator    - emit high-urgency log for human
    observe           - default no-op directive (low urgency)

[WIRE]  UDP 127.0.0.1:19995 (one directive per drone per cycle)

[CLI]
    python -m gcs.strategic_agent \\
        --drone-ids honey_ctnr_1,honey_ctnr_2,honey_ctnr_3 \\
        --interval 30 --model qwen2.5:14b

[REF] MIRAGE-UAS §4.3 — Hierarchical Deception Control
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from gcs.strategic_directive import STRATEGIC_DIRECTIVE_PORT, StrategicDirective
from shared.logger import get_logger

logger = get_logger(__name__)


STRATEGIC_SYSTEM_PROMPT = """You are the MIRAGE-UAS GCS Strategic Commander.
You oversee 3 honeydrones under active probing by an unknown adversary.
Every cycle you pick ONE strategic action per drone to bias its tactical
deception engine.

Strategic actions (choose exactly one per drone):
  deploy_decoy      - expand decoy surface (ghost_port / flight_sim)
  rotate_identity   - force sysid/reboot to break persistence
  escalate          - raise urgency; plant credentials (credential_leak)
  de_escalate       - reduce noise; minor statustext only
  alert_operator    - flag to human operator; high urgency
  observe           - passive; low urgency

Tactical skill indices you can hint via skill_bias:
  0 statustext  1 flight_sim  2 ghost_port  3 reboot_sim  4 credential_leak

Heuristics:
  - RECON + mu_A high     → observe / deploy_decoy
  - EXPLOIT + high level  → escalate + rotate_identity
  - PERSIST               → rotate_identity (breaks backdoor install)
  - EXFIL                 → escalate + alert_operator
  - Evasion rising        → de_escalate (attacker may be about to leave)

Respond ONLY with a single JSON object, no prose, no markdown:
{"action":"<one of above>", "skill_bias":{"<idx>":<weight>,...},
 "urgency":<0-1>, "reason":"<one short sentence>"}"""


@dataclass
class DroneSnapshot:
    """Lightweight per-drone state pulled from results/runs/ or held defaults."""

    drone_id: str
    phase_val: int = 0
    max_level: int = 0
    mu_A: float = 0.7
    dwell_sec: float = 0.0
    exploit_attempts: int = 0
    evasion_signals: int = 0
    time_in_phase: float = 0.0


class StrategicAgent:
    def __init__(
        self,
        drone_ids: list[str],
        model_name: str,
        ollama_base_url: str = "http://127.0.0.1:11434",
        interval_sec: float = 30.0,
        timeout_sec: float = 10.0,
    ) -> None:
        self._drone_ids = list(drone_ids)
        self._model = model_name
        self._chat_url = ollama_base_url.rstrip("/") + "/api/chat"
        self._interval = float(interval_sec)
        self._timeout = float(timeout_sec)
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        # Per-drone latest state_broadcast snapshot (populated by UDP 19999 listener).
        # Key: drone_id, Value: {"raw": dict, "received_at": epoch_seconds}
        self._snapshots: dict[str, dict] = {}
        self._state_transport: Optional[asyncio.DatagramTransport] = None

    async def start(self) -> None:
        self._running = True
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout)
        )
        # Bring up the state-broadcast listener (UDP 19999) so that per-drone
        # snapshots feed _load_snapshot() instead of hitting the file-based
        # fallback (which causes degenerate identical directives).
        await self._start_state_listener()
        logger.info(
            "gcs_strategic_agent_started",
            model=self._model,
            drones=self._drone_ids,
            interval=self._interval,
        )
        try:
            while self._running:
                await self._cycle()
                await asyncio.sleep(self._interval)
        finally:
            if self._session:
                await self._session.close()
            if self._state_transport is not None:
                try:
                    self._state_transport.close()
                except Exception:
                    pass
                self._state_transport = None

    async def _start_state_listener(self) -> None:
        """Bind UDP 19999 for honey→GCS state_broadcast snapshots.

        Non-fatal on bind failure (logs warning). Populates self._snapshots
        keyed by drone_id with each fresh JSON snapshot from honey drones.
        """
        agent = self

        class _StateProtocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
                try:
                    snap = json.loads(data.decode("utf-8", errors="replace"))
                except Exception:
                    return
                did = snap.get("drone_id")
                if not did:
                    return
                agent._snapshots[did] = {
                    "raw": snap,
                    "received_at": time.time(),
                }

        try:
            loop = asyncio.get_running_loop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.bind(("0.0.0.0", 19999))
            sock.setblocking(False)
            transport, _ = await loop.create_datagram_endpoint(
                _StateProtocol, sock=sock,
            )
            self._state_transport = transport
            logger.info("gcs_state_listener_started", port=19999)
        except Exception as e:
            logger.warning("gcs_state_listener_bind_failed", error=str(e))

    async def stop(self) -> None:
        self._running = False

    async def _cycle(self) -> None:
        # Pull latest per-drone snapshots (best-effort; defaults if unavailable)
        snapshots = [self._load_snapshot(did) for did in self._drone_ids]
        # One strategic call per drone. Could batch in future.
        for snap in snapshots:
            try:
                directive = await self._decide(snap)
            except Exception as e:
                logger.warning(
                    "gcs_decide_failed",
                    drone_id=snap.drone_id,
                    error=str(e)[:200],
                )
                continue
            self._emit_directive(directive)

    async def _decide(self, snap: DroneSnapshot) -> StrategicDirective:
        phase_names = ("RECON", "EXPLOIT", "PERSIST", "EXFIL")
        phase_name = phase_names[snap.phase_val] if 0 <= snap.phase_val < 4 else f"UNK({snap.phase_val})"
        user_prompt = (
            f"Drone: {snap.drone_id}\n"
            f"Phase: {phase_name} ({snap.phase_val})\n"
            f"Attacker tool level: {snap.max_level} "
            "(0=nmap..4=custom)\n"
            f"Belief mu_A: {snap.mu_A:.3f}\n"
            f"Dwell sec: {snap.dwell_sec:.1f}\n"
            f"Exploit attempts: {snap.exploit_attempts}\n"
            f"Evasion signals: {snap.evasion_signals}\n"
            f"Time in phase: {snap.time_in_phase:.1f}s\n"
            "Decide one strategic action. JSON only."
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": STRATEGIC_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 180},
        }
        assert self._session is not None
        t0 = time.perf_counter()
        async with self._session.post(self._chat_url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
        latency_ms = (time.perf_counter() - t0) * 1000.0

        content = (data.get("message") or {}).get("content", "") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(
                "gcs_parse_failed",
                drone_id=snap.drone_id,
                content=content[:200],
            )
            parsed = {}

        action = str(parsed.get("action", "observe"))
        if action not in {
            "deploy_decoy", "rotate_identity", "escalate",
            "de_escalate", "alert_operator", "observe",
        }:
            action = "observe"

        bias_raw = parsed.get("skill_bias") or {}
        bias: dict[int, float] = {}
        if isinstance(bias_raw, dict):
            for k, v in bias_raw.items():
                try:
                    bias[int(k)] = float(v)
                except (ValueError, TypeError):
                    continue
        urgency = float(parsed.get("urgency", 0.5))
        urgency = max(0.0, min(1.0, urgency))
        reason = str(parsed.get("reason", ""))[:240]

        logger.info(
            "gcs_directive_issued",
            drone_id=snap.drone_id,
            action=action,
            urgency=urgency,
            latency_ms=round(latency_ms, 1),
        )

        return StrategicDirective(
            target_drone_id=snap.drone_id,
            issued_at=time.time(),
            action=action,
            skill_bias=bias,
            urgency=urgency,
            reason=reason,
            ttl_sec=max(self._interval * 1.5, 30.0),
        )

    def _emit_directive(self, directive: StrategicDirective) -> None:
        """Send directive to every configured drone target.

        Targets come from GCS_DRONE_TARGETS (comma-separated host:port). If
        not set, we try service-DNS names derived from drone_id ("cc_honey_01"
        etc.) and fall back to 127.0.0.1 for single-process dev mode.
        """
        import os
        raw = directive.to_json_bytes()
        targets_env = os.environ.get("GCS_DRONE_TARGETS", "").strip()
        if targets_env:
            targets = [t.strip() for t in targets_env.split(",") if t.strip()]
        else:
            # Derive service DNS names from drone_ids (compose sets them).
            # drone_id "honey_01" → service "cc_honey_01"
            did = directive.target_drone_id
            dns_name = f"cc_{did}" if did and did != "*" else "127.0.0.1"
            targets = [f"{dns_name}:{STRATEGIC_DIRECTIVE_PORT}",
                       f"127.0.0.1:{STRATEGIC_DIRECTIVE_PORT}"]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for t in targets:
                try:
                    host, _, port = t.rpartition(":")
                    port = int(port) if port else STRATEGIC_DIRECTIVE_PORT
                    sock.sendto(raw, (host or "127.0.0.1", port))
                except Exception as e:
                    logger.warning("gcs_emit_failed",
                                   target=t, error=str(e)[:100])
        finally:
            sock.close()

    def _load_snapshot(self, drone_id: str) -> DroneSnapshot:
        """Per-drone snapshot — prefers the UDP 19999 live feed.

        Resolution order:
          1. In-memory cache populated by the state_broadcast listener
             (fresh, from honey containers every 1 s)
          2. File fallback at results/runs/<drone_id>.json (legacy)
          3. Zero-init DroneSnapshot (initial-cycle warm-up only)
        """
        # ── 1. Live UDP snapshot cache ──
        PHASE_MAP = {"RECON": 0, "EXPLOIT": 1, "PERSIST": 2, "EXFIL": 3, "IDLE": 0}
        LEVEL_MAP = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4, "---": 0}
        entry = self._snapshots.get(drone_id)
        if entry is not None:
            snap = entry["raw"]
            age = time.time() - entry["received_at"]
            # Only trust snapshots younger than 5 seconds
            if age < 5.0:
                phase_raw = str(snap.get("current_phase", "IDLE")).upper()
                lvl_raw = str(snap.get("attacker_level", "---")).upper()
                return DroneSnapshot(
                    drone_id=drone_id,
                    phase_val=PHASE_MAP.get(phase_raw, 0),
                    max_level=LEVEL_MAP.get(lvl_raw, 0),
                    mu_A=float(snap.get("belief_score", 0.7)),
                    dwell_sec=float(snap.get("dwell_seconds", 0.0)),
                    exploit_attempts=int(snap.get("commands_received", 0)),
                    evasion_signals=0,   # not in state_broadcast schema
                    time_in_phase=0.0,   # not in state_broadcast schema
                )

        # ── 2. File fallback (legacy) ──
        results_dir = os.environ.get("RESULTS_DIR", "results")
        path = os.path.join(results_dir, "runs", f"{drone_id}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return DroneSnapshot(
                drone_id=drone_id,
                phase_val=int(data.get("phase_val", 0)),
                max_level=int(data.get("max_level", 0)),
                mu_A=float(data.get("mu_A", 0.7)),
                dwell_sec=float(data.get("dwell_sec", 0.0)),
                exploit_attempts=int(data.get("exploit_attempts", 0)),
                evasion_signals=int(data.get("evasion_signals", 0)),
                time_in_phase=float(data.get("time_in_phase", 0.0)),
            )
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            pass

        # ── 3. Zero-init fallback ──
        return DroneSnapshot(drone_id=drone_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MIRAGE-UAS GCS Strategic LLM Agent (Tier 1)"
    )
    parser.add_argument(
        "--drone-ids",
        default=os.environ.get("GCS_DRONE_IDS", "honey_ctnr_1,honey_ctnr_2,honey_ctnr_3"),
        help="comma-separated drone IDs",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GCS_STRATEGIC_MODEL", "qwen2.5:14b"),
        help="Ollama model tag",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("GCS_OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument(
        "--interval", type=float,
        default=float(os.environ.get("GCS_INTERVAL_SEC", "30")),
        help="seconds between strategic cycles",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="run exactly one cycle then exit (for smoke tests)",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = _parse_args()
    drone_ids = [x.strip() for x in args.drone_ids.split(",") if x.strip()]
    agent = StrategicAgent(
        drone_ids=drone_ids,
        model_name=args.model,
        ollama_base_url=args.ollama_url,
        interval_sec=args.interval,
    )
    if args.once:
        agent._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=agent._timeout)
        )
        try:
            await agent._cycle()
        finally:
            await agent._session.close()
        return 0
    try:
        await agent.start()
    except KeyboardInterrupt:
        await agent.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
