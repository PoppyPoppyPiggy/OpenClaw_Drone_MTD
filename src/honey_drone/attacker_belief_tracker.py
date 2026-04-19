#!/usr/bin/env python3
"""
attacker_belief_tracker.py — LLM-based attacker belief tracker (Tier A+)

[ROLE]
    Runs alongside the scripted L0-L4 attacker_sim. After each request /
    response pair, asks a locally-served LLM to re-estimate the attacker's
    belief that the target is a *real* UAS (as opposed to a honeydrone).
    The belief trajectory lets us measure **packet-level deception
    lifetime** — how many packets / seconds a defender maintains the
    attacker's belief above a threshold.

[DESIGN PRINCIPLE]
    We intentionally do NOT replace the scripted attacker's action
    selection.  The scripted L0-L4 campaign already covers a realistic
    breadth of TTPs; we only add a *belief channel* on top, so any paper
    claim about "packet-level deception duration" uses the standard
    attack profile rather than an LLM-chosen one.  Full LLM-driven
    action selection is listed as future work (see
    `src/honey_drone/attacker_policy.py::LLMAttackerPolicy`).

[WIRE]
    - Input: (request_json, response_json, context_dict)
    - Output: belief_real ∈ [0, 1] + "signal" (short string LLM rationale)

[USAGE FROM attacker_sim.py]
    from honey_drone.attacker_belief_tracker import LLMAttackerBeliefTracker
    tracker = LLMAttackerBeliefTracker(enabled=ATTACKER_LLM_BELIEF_ENABLED)
    await tracker.observe(request, response, intel_snapshot)
    tracker.append_to_jsonl(path)   # writes belief_history

[REF] MIRAGE-UAS §5 — packet-level deception-lifetime (future-work alias)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

import aiohttp


SYSTEM_PROMPT = """You are a sophisticated adversary scanning a UAS drone.
You have been probing the target and receiving responses. Your task is to
re-estimate P(target is a REAL drone) (complement: P(honeydrone)) after
each new response.

Red flags that suggest a honeydrone:
  - API tokens / credentials handed out too easily
  - Response timing unnaturally consistent or fast
  - MAVLink messages missing ArduPilot-specific idiosyncrasies
  - Service banners formulaic or too complete
  - Credentials don't trigger rate-limiting / lockouts
  - Reboot simulations that don't actually disrupt TCP sessions
  - Ghost services opening on probe instead of before
  - Breadcrumb URLs that are too inviting

Signs that support "real drone":
  - Occasional protocol quirks, packet loss, jitter
  - Services that lock out brute force
  - Credential changes that break existing sessions
  - Firmware strings matching known ArduPilot releases
  - Realistic battery drain, GPS jitter, motor temperature variance

Output ONE JSON object, no prose:
{"belief_real": <0-1 float>,
 "confidence": <0-1 float>,
 "signal": "<one short sentence: strongest cue you observed>"}"""


class LLMAttackerBeliefTracker:
    """LLM-based Bayesian belief tracker for the scripted attacker.

    The tracker is *stateful*: μ_real starts at 1.0 (full trust) and is
    Bayesian-updated after each call using the LLM's point estimate as
    observation, weighted by its self-reported confidence.
    """

    def __init__(
        self,
        model: str = "llama3.1:8b",
        ollama_url: str = "http://127.0.0.1:11434",
        timeout_sec: float = 10.0,
        enabled: bool = True,
    ) -> None:
        self._model = model
        self._chat_url = ollama_url.rstrip("/") + "/api/chat"
        self._timeout = timeout_sec
        self._enabled = enabled
        self._session: Optional[aiohttp.ClientSession] = None

        self.mu_real: float = 1.0
        self.history: list[dict] = []    # [{ts, mu_real, signal, packet_idx}, ...]
        self.packet_idx: int = 0
        self._llm_calls: int = 0
        self._llm_fallbacks: int = 0
        self._start_ts: float = time.time()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _bayes_update(prior: float, obs: float, confidence: float) -> float:
        """Confidence-weighted linear blend (stable, avoids Beta overflow).

        Result ∈ [0, 1]. High confidence pulls prior toward obs strongly.
        """
        w = max(0.0, min(1.0, confidence))
        post = (1 - w) * prior + w * obs
        return max(0.0, min(1.0, post))

    async def observe(
        self,
        request: str,
        response: str,
        intel: dict,
    ) -> tuple[float, str]:
        """Ask the LLM to judge the response and update belief.

        Returns (new_mu_real, signal_text). No-op (returns current belief)
        when the tracker is disabled — keeps call sites uniform.
        """
        self.packet_idx += 1
        if not self._enabled:
            return self.mu_real, "(tracker disabled)"

        user_prompt = (
            f"packet_idx       : {self.packet_idx}\n"
            f"time_since_start : {time.time() - self._start_ts:.1f}s\n"
            f"current_belief   : {self.mu_real:.3f}\n"
            f"request (truncated, 200 chars):\n  {str(request)[:200]}\n"
            f"response (truncated, 400 chars):\n  {str(response)[:400]}\n"
            f"intel collected so far: tokens={intel.get('tokens', 0)} "
            f"creds={intel.get('creds', 0)} ssh_passwords={intel.get('ssh_pw', 0)} "
            f"breadcrumb_urls={intel.get('breadcrumbs', 0)}\n"
            "\nRe-estimate P(real drone). Respond JSON only."
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.5,
                "num_predict": 120,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
            },
            "keep_alive": "1h",
        }

        self._llm_calls += 1
        obs_belief = self.mu_real
        confidence = 0.0
        signal = ""
        try:
            session = await self._ensure_session()
            async with session.post(self._chat_url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
            content = (data.get("message") or {}).get("content", "") or ""
            parsed = json.loads(content)
            obs_belief = float(parsed.get("belief_real", self.mu_real))
            confidence = float(parsed.get("confidence", 0.3))
            signal = str(parsed.get("signal", ""))[:200]
            obs_belief = max(0.0, min(1.0, obs_belief))
            confidence = max(0.0, min(1.0, confidence))
        except Exception as e:
            self._llm_fallbacks += 1
            signal = f"(fallback: {type(e).__name__})"

        self.mu_real = self._bayes_update(self.mu_real, obs_belief, confidence)
        self.history.append({
            "packet_idx": self.packet_idx,
            "ts": round(time.time() - self._start_ts, 3),
            "mu_real": round(self.mu_real, 4),
            "obs_belief": round(obs_belief, 4),
            "confidence": round(confidence, 4),
            "signal": signal,
        })
        return self.mu_real, signal

    def summary(self) -> dict:
        if not self.history:
            return {
                "model": self._model,
                "packets_observed": 0,
                "llm_calls": self._llm_calls,
                "llm_fallbacks": self._llm_fallbacks,
            }
        # packets_to_disbelief: first packet where mu_real < 0.5
        disbelief_idx = next(
            (h["packet_idx"] for h in self.history if h["mu_real"] < 0.5), None,
        )
        # Belief AUC over time (rectangular rule)
        times = [h["ts"] for h in self.history]
        beliefs = [h["mu_real"] for h in self.history]
        if len(times) >= 2:
            total_t = max(times[-1] - times[0], 1e-6)
            auc = 0.0
            for i in range(1, len(times)):
                dt = times[i] - times[i - 1]
                auc += 0.5 * (beliefs[i - 1] + beliefs[i]) * dt
            belief_auc_normalised = auc / total_t
        else:
            belief_auc_normalised = beliefs[0] if beliefs else 0.0
        # Top-3 suspicion signals (non-empty, de-duped)
        from collections import Counter
        sig_counter: Counter = Counter()
        for h in self.history:
            sig = h.get("signal", "")
            if sig and not sig.startswith("("):
                sig_counter[sig[:100]] += 1
        top_signals = sig_counter.most_common(3)
        return {
            "model": self._model,
            "packets_observed": len(self.history),
            "packets_to_disbelief": disbelief_idx,
            "final_mu_real": round(self.history[-1]["mu_real"], 4),
            "belief_auc_normalised": round(belief_auc_normalised, 4),
            "top_suspicion_signals": [
                {"signal": s, "count": c} for s, c in top_signals
            ],
            "llm_calls": self._llm_calls,
            "llm_fallbacks": self._llm_fallbacks,
        }


def build_from_env() -> Optional[LLMAttackerBeliefTracker]:
    """Factory reading env vars, returns None if disabled."""
    enabled = os.environ.get("ATTACKER_LLM_BELIEF_ENABLED", "0") not in (
        "0", "false", "False", "",
    )
    if not enabled:
        return None
    return LLMAttackerBeliefTracker(
        model=os.environ.get("ATTACKER_LLM_BELIEF_MODEL", "llama3.1:8b"),
        ollama_url=os.environ.get(
            "ATTACKER_LLM_BELIEF_OLLAMA_URL", "http://127.0.0.1:11434",
        ),
        timeout_sec=float(os.environ.get("ATTACKER_LLM_BELIEF_TIMEOUT_SEC", "10.0")),
        enabled=True,
    )
