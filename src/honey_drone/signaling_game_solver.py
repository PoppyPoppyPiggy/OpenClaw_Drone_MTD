#!/usr/bin/env python3
"""
signaling_game_solver.py — Logit-Response Behavioural Model for Defender Skill Selection

Project  : MIRAGE-UAS
Module   : Honey Drone / Signaling Game Behavioural Solver
Author   : DS Lab / 민성
Created  : 2026-04-18

[ROLE]
    OpenClawAgent 의 proactive-loop skill selection 을 Signaling Game
    공식에 기초한 *behavioural approximation* 으로 계산한다.
    BehaviorLearner (DQN) 는 long-horizon 학습을 맡고, 본 솔버는
    공격자 사후 mu_A 와 defender cost 를 반영한 one-shot mixed
    strategy 를 반환한다.

    [IMPORTANT] 본 솔버는 **엄밀한 Perfect Bayesian Equilibrium solver
    가 아니다**. Crawford–Sobel (1982) 의 PBE 는 type-conditional
    strategies 에 대한 fixed-point 방정식을 풀어야 구한다.
    여기서는 다음의 **Quantal Response Equilibrium** 계열 근사(McKelvey
    & Palfrey, 1995)를 사용한다:

        σ(m) ∝ exp( EU(m) / τ )
        EU(m) = belief_shift(phase, m; μ_A) − κ · cost(m)

    즉 expected-utility 에 대한 **logit choice (softmax)** 이며,
    온도 τ → 0 일 때 best-response 에 수렴한다. ε-greedy 로
    exploration 을 보강해 online EMA 가 per-skill Δμ 를 추정하도록
    한다.

[WHY NOT FULL PBE]
    - 본 시스템은 attacker type 추정을 DeceptionStateManager 로
      분리했으므로 solver 에서는 μ_A 를 '외부 입력'으로 다룬다.
    - 한 ep 내 fixed-point 수렴은 온라인 RL 루프 지연 요건과 충돌
      (< 100ms 결정 시간 예산).
    - 대신 markov_game_env 의 belief-shift prior 를 고정값으로 쓰고
      empirical EMA 로 자체 보정 → 연구자-해석가능한 mixed strategy.

[GAME FORMULATION]
    - 유형 집합 Θ = {honeypot, real}  — Nature가 선택
    - 메시지(=defender skill) M = {statustext, flight_sim, ghost_port,
                                    reboot_sim, credential_leak}
    - 공격자 사후 mu_A(θ=real | m) — Bayes rule (DeceptionStateManager 담당)
    - Defender payoff U_D ≈ Σ_m σ(m) · [Δμ_A(m|phase) − κ·cost(m)]
    - Mixed strategy σ = softmax(U_D / τ)  — logit-response approximation

[REF]
    McKelvey & Palfrey (1995), "Quantal Response Equilibria for Normal
        Form Games", Games and Economic Behavior, 10(1):6-38.
        — 본 솔버가 구현하는 behavioural equilibrium 의 모집단
    Crawford & Sobel (1982), "Strategic Information Transmission"
        — PBE 정의 (본 솔버는 이를 *완전 구현하지 않음*)
    Pawlick & Zhu (2021), "Game Theory for Cyber Deception", Eq.10
        — μ_A Bayesian update (솔버의 외부 입력)
    Fudenberg & Tirole (1991), Game Theory, §8.3 (PBE background)

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
      3. Cost-aware logit-response softmax over expected utility  (QRE)
      4. ε-exploration to avoid lock-in

    Mixed-strategy σ = softmax(EU / τ) is a **behavioural Quantal Response
    Equilibrium (QRE) approximation** of mixed-strategy play, NOT a
    complete Perfect Bayesian Equilibrium solver. As τ → 0 the policy
    approaches pure best-response; large τ approaches uniform. τ is a
    researcher-set behavioural parameter — see
    `scripts/tune_signaling.py` for the grid search that anchors our
    default value, and `sensitivity_sweep()` in this module for a
    standalone τ×κ sweep used in the paper appendix.
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


def sensitivity_sweep(
    *,
    kappas: list[float] | None = None,
    temperatures: list[float] | None = None,
    phases: tuple[int, ...] = (0, 1, 2, 3),
    mu_a_grid: tuple[float, ...] = (0.3, 0.5, 0.7, 0.9),
    seed: int = 42,
) -> dict:
    """
    Compute the mixed-strategy distribution induced by this logit-response
    model across a τ×κ grid, as a function of phase × μ_A. Output is used
    for the paper-appendix sensitivity table that shows how the behavioural
    equilibrium shifts with the two hyperparameters. This is a *closed-form*
    sweep — no env rollouts — so it is fast and deterministic.

    Returns
    -------
    dict with keys:
        "grid": { (κ, τ): np.ndarray of shape (phases, mu_grid, n_skills) }
        "argmax_skill_shift": count of argmax skill changes vs baseline
        "mean_entropy": average shannon entropy of the mixing distribution
                         (higher = more stochastic, lower = near-pure)
    """
    import random as _pyrnd
    _pyrnd.seed(seed)
    np.random.seed(seed)

    kappas = list(kappas or [0.1, 0.3, 0.5, 0.7, 1.0])
    temperatures = list(temperatures or [0.3, 0.5, 0.8, 1.2, 2.0])

    out: dict = {"grid": {}}
    baseline_argmax: dict | None = None

    for k in kappas:
        for t in temperatures:
            s = SignalingGameSolver(
                cost_sensitivity_kappa=k,
                temperature=t,
                exploration_epsilon=0.0,   # deterministic for the sweep
                learning_rate=0.0,
            )
            cell = np.zeros((len(phases), len(mu_a_grid), len(DEFENDER_SKILLS)))
            for pi, phase in enumerate(phases):
                for mi, mu in enumerate(mu_a_grid):
                    _idx, _name, dbg = s.select_skill(mu_a=mu, phase=phase)
                    cell[pi, mi, :] = np.array(dbg["mixing"])
            out["grid"][(k, t)] = cell

    # Baseline: the (κ=0.5, τ=0.8) cell argmax per (phase, μ)
    if (0.5, 0.8) in out["grid"]:
        base = out["grid"][(0.5, 0.8)].argmax(axis=-1)
    else:
        base = out["grid"][(kappas[0], temperatures[0])].argmax(axis=-1)

    shifts = {}
    entropies = []
    for (k, t), cell in out["grid"].items():
        argmx = cell.argmax(axis=-1)
        shifts[(k, t)] = int(np.sum(argmx != base))
        # Shannon entropy of every (phase, μ) distribution, averaged
        p = np.clip(cell, 1e-12, 1.0)
        entropies.append(float((-p * np.log(p)).sum(axis=-1).mean()))
    out["argmax_skill_shift_vs_baseline"] = shifts
    out["mean_entropy_per_cell"] = entropies
    out["kappas"] = kappas
    out["temperatures"] = temperatures
    out["phases"] = list(phases)
    out["mu_a_grid"] = list(mu_a_grid)
    return out
