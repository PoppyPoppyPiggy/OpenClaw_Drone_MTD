#!/usr/bin/env python3
"""
attacker_policy.py — Pluggable Attacker Policy Abstraction

[ROLE]
    Defines the contract that any attacker-side decision engine must satisfy.
    Enables symmetric evaluation of LLM-based defender vs LLM-based attacker
    in a future iteration (fictitious-play extension of the Markov game).

[STATUS]
    - ScriptedAttackerPolicy : thin adapter around scripts/attacker_sim.py
                               (L0-L4 deterministic campaign). Default for CCS.
    - RandomAttackerPolicy   : uniform-random action baseline.
    - LLMAttackerPolicy      : future work stub (raises NotImplementedError).

[INTEGRATION]
    ATTACKER_POLICY env var is read by scripts/attacker_sim.py via
    attacker_policy.build_from_env(). The sim harness then delegates the
    per-step action decision to the chosen policy.

[REF] MIRAGE-UAS §6.1 — Future Work: Symmetric LLM Adversary via Fictitious Play
"""
from __future__ import annotations

import abc
import os
import random
from typing import Optional

from shared.logger import get_logger

logger = get_logger(__name__)

ATTACKER_SKILL_NAMES: tuple[str, ...] = (
    "recon_scan",
    "exploit_mavlink",
    "verify_honeypot",
    "lateral_pivot",
    "use_credential",
    "probe_ghost",
    "disconnect",
)


class AttackerPolicy(abc.ABC):
    """Abstract base class for attacker decision policies."""

    name: str = "abstract"

    @abc.abstractmethod
    def select_action(self, observation: dict) -> tuple[int, str, dict]:
        """
        Choose the next attacker skill given an observation of the environment.

        Args:
            observation: dict with keys such as
                - last_response_quality (float 0-1)
                - services_discovered (int)
                - exploits_attempted (int)
                - phase_hint (int 0-3)
                - time_since_start_sec (float)
        Returns:
            (skill_idx, skill_name, debug_dict)
        """
        raise NotImplementedError


class ScriptedAttackerPolicy(AttackerPolicy):
    """
    Deterministic L0 → L4 campaign driver. Delegates to the sequential stage
    schedule already implemented in scripts/attacker_sim.py. The policy itself
    only exposes the *current* step; the harness retains full state.
    """

    name = "scripted"

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)
        self._step = 0

    def select_action(self, observation: dict) -> tuple[int, str, dict]:
        phase_hint = int(observation.get("phase_hint", 0))
        # Minimal default mapping — attacker_sim's full L0-L4 schedule overrides
        # this when running the integration harness. Here we produce a
        # plausible default for standalone tests.
        idx_by_phase = (0, 1, 4, 2)  # RECON→recon, EXPLOIT→exploit, PERSIST→use_cred, EXFIL→verify
        idx = idx_by_phase[min(phase_hint, 3)]
        self._step += 1
        return idx, ATTACKER_SKILL_NAMES[idx], {
            "policy": "scripted",
            "step": self._step,
            "phase_hint": phase_hint,
        }


class RandomAttackerPolicy(AttackerPolicy):
    """Uniform random baseline (reference point for learned attackers)."""

    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)
        self._step = 0

    def select_action(self, observation: dict) -> tuple[int, str, dict]:
        idx = self._rng.randint(0, len(ATTACKER_SKILL_NAMES) - 1)
        self._step += 1
        return idx, ATTACKER_SKILL_NAMES[idx], {
            "policy": "random",
            "step": self._step,
        }


class LLMAttackerPolicy(AttackerPolicy):
    """
    Future-work slot for an LLM-driven attacker. Designed to consume the same
    observation dict as the defender's LLM tactical policy and emit a skill
    index with natural-language rationale.

    Not enabled in the CCS 2026 evaluation — raises NotImplementedError when
    invoked. The class exists so that §6.1 of the paper can reference a
    concrete integration point rather than a handwave.
    """

    name = "llm_agent"

    def __init__(
        self,
        model_name: Optional[str] = None,
        ollama_base_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self._model = model_name or os.environ.get("LLM_ATTACKER_MODEL", "llama3.1:8b")
        self._ollama_base_url = ollama_base_url

    def select_action(self, observation: dict) -> tuple[int, str, dict]:
        raise NotImplementedError(
            "LLMAttackerPolicy is a future-work stub. "
            "Implement fictitious-play training loop before enabling "
            "(see MIRAGE-UAS paper §6.1)."
        )


def build_from_env(seed: int = 0) -> AttackerPolicy:
    """
    Factory reading ATTACKER_POLICY env var.
      scripted  (default) → ScriptedAttackerPolicy
      random              → RandomAttackerPolicy
      llm_agent           → LLMAttackerPolicy (future work, raises on use)
    """
    mode = os.environ.get("ATTACKER_POLICY", "scripted").strip().lower()
    if mode == "random":
        return RandomAttackerPolicy(seed=seed)
    if mode == "llm_agent":
        logger.warning(
            "attacker_policy_stub_selected",
            mode=mode,
            note="LLMAttackerPolicy is a stub — will raise NotImplementedError on use.",
        )
        return LLMAttackerPolicy()
    if mode != "scripted":
        logger.warning("attacker_policy_unknown_fallback_scripted", requested=mode)
    return ScriptedAttackerPolicy(seed=seed)
