#!/usr/bin/env python3
"""
llm_agent.py — Tier 2 Tactical LLM Deception Policy

[ROLE]
    Drop-in replacement for BehaviorLearner.select_action / SignalingGameSolver.select_skill
    when DEFENDER_POLICY=llm_agent. Calls a local Ollama-served LLM to pick a deception
    skill given the 10-dim attacker-context observed by OpenClawAgent.

[DATA FLOW]
    OpenClawAgent._proactive_loop()  (every AGENT_PROACTIVE_INTERVAL_SEC)
    ──▶ _build_mab_context() → 10-dim context dict
    ──▶ LLMTacticalAgent.select_action(context)   (async, ~0.5-3s)
    ──▶ Ollama /api/chat  (127.0.0.1:11434)
    ──▶ JSON {"skill_idx": 0-4, "reason": "..."}
    ──▶ (idx, name, debug) back to OpenClawAgent

[SKILLS] — must match action_funcs ordering in openclaw_agent.py:474
    0 statustext       1 flight_sim       2 ghost_port
    3 reboot_sim       4 credential_leak

[FALLBACK] — on any parse/network/timeout error, returns a uniformly random skill
           and logs `llm_agent_fallback` so ablations can quantify reliability.

[REF] MIRAGE-UAS §4.4 — LLM-Based Tactical Defender Policy
"""
from __future__ import annotations

import json
import random
import time
from typing import Optional

import aiohttp

from shared.logger import get_logger

logger = get_logger(__name__)

SKILL_NAMES: tuple[str, ...] = (
    "statustext",
    "flight_sim",
    "ghost_port",
    "reboot_sim",
    "credential_leak",
)

SYSTEM_PROMPT = """You are a UAS honeydrone tactical deception agent.
Each step you pick ONE skill to keep the attacker engaged while hiding the
real drone. The attacker adapts — rigid repetition exposes the decoy.

Skills (identify by letter AND index):
  A / 0 = statustext       emit forged MAVLink STATUSTEXT
  B / 1 = flight_sim       simulate benign flight telemetry
  C / 2 = ghost_port       expose a phantom TCP service
  D / 3 = reboot_sim       fake a reboot with stale boot banner
  E / 4 = credential_leak  leak a decoy API key / SSH token

Attacker phases:
  0 RECON    scanning, HEARTBEAT, PARAM_REQUEST
  1 EXPLOIT  COMMAND_LONG, SET_MODE, injection
  2 PERSIST  PARAM_SET, MISSION_ITEM (backdoor install)
  3 EXFIL    LOG_REQUEST, FILE_TRANSFER

Phase -> candidate skills (soft hint; deviate when useful):
  RECON    -> A or B or C   (flood low-value info)
  EXPLOIT  -> C or D or E   (confuse attack surface)
  PERSIST  -> D or A or E   (reset attacker state, feed false creds)
  EXFIL    -> E or D        (plant tracked honey-tokens, break exfil chain)

CRITICAL CONSTRAINTS (obey; do not rationalise around):
  1. Prefer a DIFFERENT skill from last_action unless attacker behaviour
     strongly demands repetition (e.g. the same exploit twice in a row).
  2. If phase_changed_since_last is true, CHANGE your skill class.
  3. Never pick B (flight_sim) in phase 3 (EXFIL) — the effect is negative.
  4. In phase 2 (PERSIST), strongly prefer D (reboot_sim) on first entry.

Respond with ONLY one JSON object, no prose, no markdown, no code fences:
{"skill_idx": <0-4>, "reason": "<one short sentence>"}
(Equivalently you may reply {"skill": "A|B|C|D|E", "reason": "..."}.)"""


class LLMTacticalAgent:
    """
    Thin async Ollama client exposing the same call signature as BehaviorLearner.
    One instance per drone, one shared aiohttp.ClientSession reused across calls.
    """

    def __init__(
        self,
        drone_id: str,
        model_name: str,
        ollama_base_url: str = "http://127.0.0.1:11434",
        timeout_sec: float = 6.0,
        temperature: float = 0.9,
    ) -> None:
        self._drone_id = drone_id
        self._model = model_name
        self._chat_url = ollama_base_url.rstrip("/") + "/api/chat"
        self._timeout_sec = timeout_sec
        self._temperature = temperature
        self._session: Optional[aiohttp.ClientSession] = None
        self._rng = random.Random(
            (hash(drone_id) ^ hash(model_name)) & 0xFFFFFFFF
        )
        self._call_count = 0
        self._fallback_count = 0
        self._total_latency_ms = 0.0
        self._last_action_idx: Optional[int] = None
        self._last_phase_val: Optional[int] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout_sec)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def select_action(self, context: dict) -> tuple[int, str, dict]:
        """
        Select a tactical deception skill via LLM.

        Matches signature of BehaviorLearner.select_action(context) -> (idx, name, debug).
        Returns a uniform random fallback on any error.
        """
        self._call_count += 1
        t0 = time.perf_counter()

        # Enrich context with feedback from previous call (breaks mode
        # collapse — see docs/prompt_v2_draft.md for rationale).
        if self._last_action_idx is not None:
            context = dict(context)
            letter = "ABCDE"[self._last_action_idx]
            context.setdefault(
                "last_action",
                f"{letter}/{self._last_action_idx} ({SKILL_NAMES[self._last_action_idx]})",
            )
        cur_phase = int(context.get("phase_val", 0))
        if self._last_phase_val is not None:
            context.setdefault(
                "phase_changed_since_last", cur_phase != self._last_phase_val,
            )
        self._last_phase_val = cur_phase

        user_prompt = self._format_context(context)
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": self._temperature,
                "num_predict": 200,
                "top_p": 0.95,
                "top_k": 40,
                "repeat_penalty": 1.2,
            },
            "keep_alive": "1h",
        }

        reason = ""
        error_kind: Optional[str] = None
        idx = -1
        try:
            session = await self._ensure_session()
            async with session.post(self._chat_url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
            content = (data.get("message") or {}).get("content", "") or ""
            parsed = json.loads(content)
            # Accept letter (A-E) or number (0-4) in either skill_idx / skill
            letter_to_idx = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
            val = parsed.get("skill_idx")
            if val is None:
                val = parsed.get("skill")
            if val is None:
                raise ValueError("missing skill_idx / skill field")
            if isinstance(val, bool):
                raise ValueError(f"bad skill type bool={val}")
            if isinstance(val, (int, float)):
                idx = int(val)
            elif isinstance(val, str):
                s = val.strip()
                if s and s[0].upper() in letter_to_idx:
                    idx = letter_to_idx[s[0].upper()]
                else:
                    idx = int(s)
            else:
                raise ValueError(f"bad skill type {type(val).__name__}")
            reason = str(parsed.get("reason", ""))[:240]
            if idx < 0 or idx >= len(SKILL_NAMES):
                raise ValueError(f"out_of_range skill_idx={idx}")
        except Exception as e:
            error_kind = type(e).__name__
            self._fallback_count += 1
            idx = self._rng.randint(0, len(SKILL_NAMES) - 1)
            reason = f"fallback_random ({error_kind})"
            logger.warning(
                "llm_agent_fallback",
                drone_id=self._drone_id,
                model=self._model,
                error=str(e)[:200],
                error_kind=error_kind,
            )

        latency_ms = (time.perf_counter() - t0) * 1000.0
        self._total_latency_ms += latency_ms
        self._last_action_idx = idx
        name = SKILL_NAMES[idx]
        debug = {
            "policy": "llm_agent",
            "model": self._model,
            "reasoning": reason,
            "latency_ms": round(latency_ms, 1),
            "calls_total": self._call_count,
            "fallbacks_total": self._fallback_count,
            "fallback_rate": round(
                self._fallback_count / max(1, self._call_count), 3
            ),
        }
        if error_kind:
            debug["error_kind"] = error_kind

        logger.info(
            "llm_agent_decision",
            drone_id=self._drone_id,
            skill_idx=idx,
            skill=name,
            latency_ms=debug["latency_ms"],
            fallback=(error_kind is not None),
        )
        return idx, name, debug

    def update(self, *_args, **_kwargs) -> None:
        """
        No-op — LLMTacticalAgent is inference-only. Kept for API parity with
        BehaviorLearner.update() so openclaw_agent can call it unconditionally.
        """
        return None

    def snapshot(self) -> dict:
        """Audit-trail snapshot for run summaries."""
        return {
            "policy": "llm_agent",
            "model": self._model,
            "calls_total": self._call_count,
            "fallbacks_total": self._fallback_count,
            "fallback_rate": round(
                self._fallback_count / max(1, self._call_count), 3
            ),
            "avg_latency_ms": round(
                self._total_latency_ms / max(1, self._call_count), 1
            ),
        }

    @staticmethod
    def _format_context(context: dict) -> str:
        phase_names = ("RECON", "EXPLOIT", "PERSIST", "EXFIL")
        phase_val = int(context.get("phase_val", 0))
        phase_name = (
            phase_names[phase_val] if 0 <= phase_val < len(phase_names) else f"UNK({phase_val})"
        )
        base = (
            "Current attacker context (one decision, reply JSON only):\n"
            f"  phase                : {phase_name} ({phase_val})\n"
            f"  attacker_tool_level  : {int(context.get('max_level', 0))} "
            "(0=nmap, 1=mavproxy, 2=dronekit, 3=metasploit, 4=custom_exploit)\n"
            f"  belief_mu_A          : {float(context.get('avg_p_real', 0.7)):.3f} "
            "(lower = attacker suspects honeypot)\n"
            f"  dwell_sec            : {float(context.get('avg_dwell_sec', 0.0)):.1f}\n"
            f"  commands_issued      : {float(context.get('avg_commands', 0.0)):.0f}\n"
            f"  services_touched     : {float(context.get('services_touched', 0.0)):.1f}\n"
            f"  exploit_attempts     : {int(context.get('exploit_attempts', 0))}\n"
            f"  ghost_ports_active   : {int(context.get('ghost_active', 0))}\n"
            f"  time_in_phase_sec    : {float(context.get('time_in_phase', 0.0)):.1f}\n"
            f"  evasion_signals      : {int(context.get('evasion_signals', 0))}\n"
        )
        directive = context.get("strategic_directive")
        if isinstance(directive, dict):
            bias = directive.get("skill_bias") or {}
            bias_str = ", ".join(f"{k}:{float(v):.2f}" for k, v in bias.items()) or "none"
            base += (
                "\nSTRATEGIC DIRECTIVE from GCS (Tier 1):\n"
                f"  action     : {directive.get('action', 'observe')}\n"
                f"  urgency    : {float(directive.get('urgency', 0.5)):.2f}\n"
                f"  skill_bias : {bias_str}\n"
                f"  reason     : {str(directive.get('reason', ''))[:200]}\n"
                "Treat the directive as strong guidance, not a hard constraint.\n"
            )
        return base


def build_from_env(drone_id: str) -> LLMTacticalAgent:
    """
    Factory reading env vars:
      LLM_AGENT_MODEL        (default: llama3.1:8b)
      LLM_AGENT_OLLAMA_URL   (default: http://127.0.0.1:11434)
      LLM_AGENT_TIMEOUT_SEC  (default: 6.0)
      LLM_AGENT_TEMPERATURE  (default: 0.4)
    """
    import os

    return LLMTacticalAgent(
        drone_id=drone_id,
        model_name=os.environ.get("LLM_AGENT_MODEL", "llama3.1:8b").strip(),
        ollama_base_url=os.environ.get(
            "LLM_AGENT_OLLAMA_URL", "http://127.0.0.1:11434"
        ).strip(),
        timeout_sec=float(os.environ.get("LLM_AGENT_TIMEOUT_SEC", "6.0")),
        temperature=float(os.environ.get("LLM_AGENT_TEMPERATURE", "0.9")),
    )
