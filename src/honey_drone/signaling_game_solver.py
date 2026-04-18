#!/usr/bin/env python3
"""
signaling_game_solver.py — Perfect Bayesian Equilibrium for Defender Skill Selection

Project  : MIRAGE-UAS
Module   : Honey Drone / Signaling Game Solver
Author   : DS Lab / 민성
Created  : 2026-04-18

[ROLE]
    OpenClawAgent의 proactive-loop skill selection을 Signaling Game의
    Perfect Bayesian Equilibrium (PBE)로 계산한다. BehaviorLearner (DQN)
    는 long-horizon 학습을 담당하고, 본 솔버는 mu_A (공격자 사후 믿음)
    를 즉시 업데이트하는 one-shot mixed strategy를 반환한다.

[GAME FORMULATION]
    - 유형 집합 Θ = {honeypot, real}  — Nature가 선택
    - 메시지(=defender skill) M = {statustext, flight_sim, ghost_port,
                                    reboot_sim, credential_leak}
    - 공격자 사후 mu_A(θ=real | m) — Bayes rule
    - Defender payoff U_D = Σ_m σ(m|θ) * [mu_A(real|m) - κ·cost(m)]
    - Attacker payoff는 markov_game_env의 belief-update matrix로 근사

[REF]
    Crawford & Sobel (1982), "Strategic Information Transmission",
        Econometrica 50(6), pp. 1431-1451   — Eq.SG-cheaptalk
    Pawlick & Zhu (2021), "Game Theory for Cyber Deception",
        Springer, Eq.10                        — μ_A update
    Fudenberg & Tirole (1991), Game Theory, §8.3 (PBE)

[DATA FLOW]
    OpenClawAgent._proactive_loop
    ──▶ SignalingGameSolver.select_skill(mu_A, phase, context)
    ──▶ (action_idx, action_name, debug_dict)

    매 step 끝에:
    ──▶ SignalingGameSolver.observe_outcome(skill_idx, delta_mu, reward)
    ──▶ exponential-moving-average for per-skill empirical belief shift

[Inputs]
    - mu_A: float            — 현재 attacker 사후 믿음 P(real|obs)
    - phase: int             — attack phase 0=RECON, 1=EXPLOIT, 2=PERSIST, 3=EXFIL
    - context: dict          — OpenClawAgent._build_mab_context() 결과

[Outputs]
    - (action_idx: int, action_name: str, debug: dict)

[Dependencies]
    - numpy
    - honey_drone.markov_game_env (DEFENDER_SKILLS + _def_belief_effect matrix)
"""
from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass, field

import numpy as np

from honey_drone.markov_game_env import DEFENDER_SKILLS, MarkovGameEnv


# ── Skill costs (Eq.17 MTD cost normalization, TIFS T-IFS-25285-2026) ────────
# Not MTD costs per se — these are *deception skill* costs:
# detectability penalty + operating burden, normalized to [0,1].
_SKILL_COST = np.array([
    0.05,   # statustext (cheap, low-bandwidth)
    0.15,   # flight_sim (telemetry emission over 60s)
    0.10,   # ghost_port (socket open)
    0.20,   # reboot_sim (15s silence → risk)
    0.08,   # credential_leak (fake key, one-shot)
], dtype=np.float32)


@dataclass
class SkillStats:
    """Per-skill empirical outcome tracker (EMA)."""
    ema_delta_mu: float = 0.0        # exponential moving average of Δμ_A after this skill
    ema_reward: float = 0.0
    n_invocations: int = 0
    alpha: float = field(default=0.1, repr=False)   # EMA learning rate

    def update(self, delta_mu: float, reward: float) -> None:
        self.n_invocations += 1
        if self.n_invocations == 1:
            self.ema_delta_mu = delta_mu
            self.ema_reward = reward
        else:
            self.ema_delta_mu += self.alpha * (delta_mu - self.ema_delta_mu)
            self.ema_reward += self.alpha * (reward - self.ema_reward)


class SignalingGameSolver:
    """
    Defender side of a two-type Signaling Game (θ ∈ {honeypot, real}).

    Honeypot wants attacker to believe θ=real → select skills that PUSH μ_A
    UP in the current phase while respecting skill cost. The solver combines:

      1. Phase-conditional belief-shift priors from markov_game_env
         (_def_belief_effect matrix — calibrated from simulation sweeps)
      2. Online empirical Δμ_A stats (EMA, per-skill)
      3. Cost-aware softmax over expected utility
      4. ε-exploration to avoid lock-in

    Equilibrium mixing keeps the attacker's best-response uncertain (no
    pooling on a single signal — Crawford-Sobel style information transfer
    without deterministic separation).
    """

    def __init__(
        self,
        *,
        cost_sensitivity_kappa: float = 0.5,
        learning_rate: float = 0.1,
        exploration_epsilon: float = 0.10,
        temperature: float = 0.8,
    ) -> None:
        self._kappa = float(cost_sensitivity_kappa)
        self._epsilon = float(exploration_epsilon)
        self._temperature = max(0.1, float(temperature))

        # Pull the phase-conditional belief-shift prior from the game env.
        # Same matrix the trained DQN was calibrated against → consistent.
        _env = MarkovGameEnv()
        self._belief_prior: np.ndarray = _env._def_belief_effect.copy()

        self._stats: list[SkillStats] = [
            SkillStats(alpha=learning_rate) for _ in DEFENDER_SKILLS
        ]
        self._last_skill_idx: int = -1
        self._last_mu_a: float = 0.7

        # Equilibrium mixing (last computed) — exported for logging
        self._last_mixing: np.ndarray = np.ones(len(DEFENDER_SKILLS)) / len(DEFENDER_SKILLS)
        self._last_eu: np.ndarray = np.zeros(len(DEFENDER_SKILLS))

    # ── Core selection ───────────────────────────────────────────────────────

    def select_skill(
        self,
        *,
        mu_a: float,
        phase: int,
        context: dict | None = None,   # noqa: ARG002 — reserved for future
    ) -> tuple[int, str, dict]:
        """
        Return (skill_idx, skill_name, debug).

        debug includes mixing distribution, expected utility vector, and
        argmax pre-exploration so the caller can log Signaling-Game state.
        """
        phase_idx = max(0, min(3, int(phase)))

        # Expected utility per skill:
        #   EU(m) = prior_shift(phase, m) + empirical_shift(m) - κ·cost(m)
        # When μ_A is already near 1.0, high-risk skills (reboot) have
        # saturated benefit; when μ_A is low, they have higher marginal value.
        prior_shift = self._belief_prior[phase_idx]  # shape (5,)
        empirical = np.array(
            [s.ema_delta_mu for s in self._stats], dtype=np.float32
        )
        # Weight empirical evidence by sample count (↑ as n grows) up to 0.5.
        weights = np.array(
            [min(0.5, s.n_invocations / 20.0) for s in self._stats],
            dtype=np.float32,
        )
        belief_shift = (1.0 - weights) * prior_shift + weights * empirical

        # Marginal-value scaling — larger when μ_A is low (more to gain).
        headroom = max(0.05, 1.0 - mu_a)
        value = belief_shift * headroom

        cost_term = self._kappa * _SKILL_COST
        eu = value - cost_term
        self._last_eu = eu

        # Softmax mixing (Perfect Bayesian Equilibrium approximation via
        # logit-response; temperature → 0 gives pure strategy).
        x = eu / self._temperature
        x = x - x.max()   # numerical stability
        mixing = np.exp(x)
        mixing = mixing / mixing.sum()
        self._last_mixing = mixing

        argmax_idx = int(np.argmax(eu))

        # Sample with ε-exploration.
        if random.random() < self._epsilon:
            idx = random.randint(0, len(DEFENDER_SKILLS) - 1)
            selection_mode = "explore"
        else:
            idx = int(np.random.choice(len(DEFENDER_SKILLS), p=mixing))
            selection_mode = "equilibrium"

        self._last_skill_idx = idx
        self._last_mu_a = mu_a

        debug = {
            "mode": selection_mode,
            "phase": phase_idx,
            "mu_a": round(mu_a, 4),
            "argmax_skill": DEFENDER_SKILLS[argmax_idx],
            "mixing": [round(float(p), 4) for p in mixing],
            "eu": [round(float(v), 4) for v in eu],
            "cost": [round(float(c), 4) for c in cost_term],
        }
        return idx, DEFENDER_SKILLS[idx], debug

    # ── Outcome feedback ─────────────────────────────────────────────────────

    def observe_outcome(
        self,
        *,
        skill_idx: int,
        delta_mu: float,
        reward: float,
    ) -> None:
        """
        Update per-skill EMA with the observed Δμ_A and reward.

        Called by OpenClawAgent._proactive_loop after `_compute_reward()`.
        """
        if not (0 <= skill_idx < len(self._stats)):
            return
        self._stats[skill_idx].update(delta_mu, reward)

    # ── Logging helpers ──────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot for periodic_save."""
        return {
            "last_mu_a": round(self._last_mu_a, 4),
            "last_skill_idx": self._last_skill_idx,
            "last_skill_name": (
                DEFENDER_SKILLS[self._last_skill_idx]
                if self._last_skill_idx >= 0 else None
            ),
            "mixing": [round(float(p), 4) for p in self._last_mixing],
            "expected_utility": [round(float(v), 4) for v in self._last_eu],
            "per_skill_stats": [
                {
                    "skill": DEFENDER_SKILLS[i],
                    "ema_delta_mu": round(s.ema_delta_mu, 4),
                    "ema_reward": round(s.ema_reward, 4),
                    "n": s.n_invocations,
                }
                for i, s in enumerate(self._stats)
            ],
            "kappa": self._kappa,
            "epsilon": self._epsilon,
            "temperature": self._temperature,
        }


def build_from_env() -> SignalingGameSolver:
    """
    Construct a solver from environment variables.

    Reads: SIGNALING_KAPPA, SIGNALING_LEARNING_RATE,
           SIGNALING_EPSILON, SIGNALING_TEMPERATURE
    Defaults chosen for infrastructure sanity (not research params — solver
    is a mechanism, the *weights* it consumes come from constants.py).
    """
    def _f(k: str, d: float) -> float:
        try:
            return float(os.environ.get(k, d))
        except ValueError:
            return d

    return SignalingGameSolver(
        cost_sensitivity_kappa=_f("SIGNALING_KAPPA", 0.5),
        learning_rate=_f("SIGNALING_LEARNING_RATE", 0.1),
        exploration_epsilon=_f("SIGNALING_EPSILON", 0.10),
        temperature=_f("SIGNALING_TEMPERATURE", 0.8),
    )
