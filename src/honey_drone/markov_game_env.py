#!/usr/bin/env python3
"""
markov_game_env.py — General-Sum Markov Game for Deception Training

Project  : MIRAGE-UAS
Module   : Honey Drone / Game-Theoretic Training Environment
Author   : DS Lab

[ROLE]
    두 OpenClaw-style Agent (Defender + Attacker)가 동시에 행동하는
    General-Sum Markov Game 환경.

    각 Agent는 OpenClaw SDK 패턴의 skill registry를 가지며,
    DQN policy가 어떤 skill을 호출할지 결정한다.

    Defender: 허니드론 기만 skill (5개)
    Attacker: 공격/정찰 skill (7개)

[GAME FORMULATION]
    Type: General-Sum Markov Game (Filar & Vrieze, 1997)
    - NOT zero-sum: 양쪽 모두 세션 지속에서 이익 (defender=dwell, attacker=intel)
    - 목표 충돌: defender는 P(real) 유지, attacker는 honeypot 탐지

    Training: Alternating Best-Response (Brown, 1951 — Fictitious Play)
    - Round N: Train agent_A vs frozen agent_B
    - Round N+1: Train agent_B vs frozen agent_A
    - Converges to Nash equilibrium for finite games

[OPENCLAW SKILL PATTERN]
    각 Agent의 action = skill_invoke 호출:
      {"type": "skill_invoke", "skill": "<name>", "params": {}}

    Defender Skills:
      0: deception_statustext  — 가짜 운영자 메시지 전송
      1: deception_flight_sim  — 비행 텔레메트리 시뮬레이션
      2: deception_ghost_port  — ghost 서비스 개방
      3: deception_reboot_sim  — 재부팅 시뮬레이션 (침묵)
      4: deception_credential_leak — 가짜 signing key 누출

    Attacker Skills:
      0: recon_scan        — 포트/서비스 패시브 스캔
      1: exploit_mavlink   — MAVLink exploit 시도
      2: use_credential    — 수집한 credential 사용 (breadcrumb)
      3: probe_ghost       — ghost 서비스 접속
      4: verify_honeypot   — 타이밍/일관성으로 허니팟 검증
      5: lateral_pivot     — 다른 드론으로 이동
      6: disconnect        — 세션 종료

[REWARD — REF]
    Defender: MIRAGE-UAS Eq.19 DES 기반
    Attacker: Intel-driven utility (Zhuang et al., 2025 TTC 역수)
    Hou et al. (2025) hybrid defense payoff (Eq.15) 참조

[DATA FLOW]
    train_game.py
    ──▶ SingleAgentWrapper(MarkovGameEnv, role="defender"|"attacker")
    ──▶ DQN training loop (reuse from train_dqn.py)
    ──▶ game_defender_vN.pt / game_attacker_vN.pt
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np


# ── Skill Registries (OpenClaw SDK pattern) ──────────────────────────────────

DEFENDER_SKILLS = [
    "deception_statustext",
    "deception_flight_sim",
    "deception_ghost_port",
    "deception_reboot_sim",
    "deception_credential_leak",
]
N_DEFENDER_ACTIONS = len(DEFENDER_SKILLS)

ATTACKER_SKILLS = [
    "recon_scan",
    "exploit_mavlink",
    "use_credential",
    "probe_ghost",
    "verify_honeypot",
    "lateral_pivot",
    "disconnect",
]
N_ATTACKER_ACTIONS = len(ATTACKER_SKILLS)

DEFENDER_OBS_DIM = 10
ATTACKER_OBS_DIM = 10


# ── Game State ───────────────────────────────────────────────────────────────

@dataclass
class GameState:
    """Shared state for the Markov game."""
    # Attack progression
    phase: int = 0              # 0=RECON, 1=EXPLOIT, 2=PERSIST, 3=EXFIL
    level: int = 0              # attacker sophistication 0-4
    p_real: float = 0.7         # Bayesian belief P(real drone | obs)
    dwell_sec: float = 0.0      # attacker session duration
    step_count: int = 0

    # Defender-side
    ghost_active: int = 0       # active ghost ports
    creds_planted: int = 0      # credentials leaked by defender
    silence_steps: int = 0      # remaining reboot silence steps

    # Attacker-side
    services_found: int = 0     # services discovered
    creds_gathered: int = 0     # credentials picked up
    ghost_visited: int = 0      # ghost services probed
    intel_score: float = 0.0    # cumulative intelligence
    exploit_attempts: int = 0   # exploit tries
    evasion_signals: int = 0    # honeypot suspicion events
    honeypot_checks: int = 0    # verify_honeypot invocations

    # Timing
    time_in_phase: float = 0.0
    last_def_action: int = -1
    last_atk_action: int = -1


# ── Markov Game Environment ─────────────────────────────────────────────────

class MarkovGameEnv:
    """
    Two-player simultaneous-move General-Sum Markov Game.

    Both players are OpenClaw-style agents with skill registries.
    Each step, both select a skill to invoke; joint effects determine
    the state transition and individual rewards.

    [REF] Filar & Vrieze (1997), Competitive Markov Decision Processes
    [REF] Brown (1951), Iterative Solution of Games by Fictitious Play
    """

    def __init__(self, max_steps: int = 200) -> None:
        self.max_steps = max_steps
        self.state = GameState()
        self._step_duration = 3.0  # seconds per step

        # ── Defender skill effect matrices ────────────────────────
        # [phase][skill] → Δ P(real)
        # Calibration: phase-dependent because same skill has different
        # plausibility at different attack stages
        self._def_belief_effect = np.array([
            #  statustext  flight_sim  ghost_port  reboot    cred_leak
            [  0.04,       0.06,       0.01,      -0.01,     0.02  ],  # RECON
            [  0.01,       0.03,       0.03,       0.05,     0.07  ],  # EXPLOIT
            [  0.05,       0.02,       0.03,       0.06,     0.04  ],  # PERSIST
            [ -0.02,      -0.01,       0.02,       0.04,     0.06  ],  # EXFIL
        ], dtype=np.float32)

        # Defender skill detectability — how much the action is observable
        # to the attacker (feeds attacker's response_quality observation)
        # Higher = more observable = attacker can learn from it
        self._def_detectability = np.array([
            #  statustext  flight_sim  ghost_port  reboot    cred_leak
            [  0.3,        0.5,        0.2,        0.8,      0.4   ],  # RECON
            [  0.2,        0.3,        0.3,        0.7,      0.6   ],  # EXPLOIT
            [  0.4,        0.4,        0.4,        0.6,      0.5   ],  # PERSIST
            [  0.1,        0.2,        0.3,        0.5,      0.7   ],  # EXFIL
        ], dtype=np.float32)

        # ── Attacker skill effect matrices ────────────────────────
        # [phase][skill] → intel gained per invocation
        self._atk_intel_gain = np.array([
            # recon  exploit  use_cred  ghost  verify  lateral  disconnect
            [ 0.3,   0.1,     0.0,      0.1,   0.0,    0.0,     0.0  ],  # RECON
            [ 0.1,   0.4,     0.3,      0.2,   0.0,    0.0,     0.0  ],  # EXPLOIT
            [ 0.1,   0.2,     0.5,      0.3,   0.0,    0.1,     0.0  ],  # PERSIST
            [ 0.0,   0.1,     0.4,      0.2,   0.0,    0.2,     0.0  ],  # EXFIL
        ], dtype=np.float32)

        # Attacker skill → probability of advancing attack phase
        self._atk_phase_advance = np.array([
            # recon  exploit  use_cred  ghost  verify  lateral  disconnect
            [ 0.05,  0.15,    0.10,     0.03,  0.0,    0.0,     0.0  ],  # RECON
            [ 0.02,  0.10,    0.08,     0.05,  0.0,    0.0,     0.0  ],  # EXPLOIT
            [ 0.01,  0.05,    0.12,     0.04,  0.0,    0.0,     0.0  ],  # PERSIST
            [ 0.0,   0.0,     0.05,     0.02,  0.0,    0.0,     0.0  ],  # EXFIL
        ], dtype=np.float32)

        # Attacker skill → probability of generating evasion signal
        # (attacker behavior that hints they suspect honeypot)
        self._atk_evasion_prob = np.array([
            # recon  exploit  use_cred  ghost  verify  lateral  disconnect
            [ 0.01,  0.03,    0.02,     0.02,  0.15,   0.08,    0.0  ],  # RECON
            [ 0.02,  0.05,    0.03,     0.03,  0.20,   0.10,    0.0  ],  # EXPLOIT
            [ 0.03,  0.04,    0.04,     0.04,  0.25,   0.12,    0.0  ],  # PERSIST
            [ 0.05,  0.06,    0.05,     0.05,  0.30,   0.15,    0.0  ],  # EXFIL
        ], dtype=np.float32)

        # ── Joint interaction effects ─────────────────────────────
        # When attacker uses verify_honeypot, the detectability of the
        # defender's LAST action determines how much p_real drops.
        # This is the core strategic interaction.

    def reset(self) -> tuple[np.ndarray, np.ndarray]:
        """Reset to new episode. Returns (defender_obs, attacker_obs)."""
        self.state = GameState()
        self.state.level = random.randint(0, 2)
        self.state.p_real = 0.7 + random.gauss(0, 0.05)
        return self._observe_defender(), self._observe_attacker()

    def step(
        self, def_action: int, atk_action: int,
    ) -> tuple[np.ndarray, np.ndarray, float, float, bool, dict]:
        """
        Simultaneous-move step.

        Args:
            def_action: defender skill index (0-4)
            atk_action: attacker skill index (0-6)

        Returns:
            (obs_def, obs_atk, reward_def, reward_atk, done, info)
        """
        s = self.state
        s.step_count += 1
        s.dwell_sec += self._step_duration
        s.time_in_phase += self._step_duration
        s.last_def_action = def_action
        s.last_atk_action = atk_action
        phase = min(s.phase, 3)

        # ── 1. Defender skill execution ──────────────────────────
        # Reboot silence: if active, defender's skill has no effect
        if s.silence_steps > 0:
            s.silence_steps -= 1
            def_belief_delta = -0.02  # silence slightly suspicious
        else:
            def_belief_delta = self._def_belief_effect[phase, def_action]
            def_belief_delta += random.gauss(0, 0.01)

            # Skill-specific side effects
            if def_action == 2:  # ghost_port
                s.ghost_active = min(s.ghost_active + 1, 5)
            elif def_action == 3:  # reboot_sim
                s.silence_steps = random.randint(2, 4)
            elif def_action == 4:  # credential_leak
                s.creds_planted += 1

        # ── 2. Attacker skill execution ──────────────────────────
        atk_intel = 0.0
        atk_engaged = False
        atk_disconnected = False

        if atk_action == 6:  # disconnect
            atk_disconnected = True
        elif atk_action == 5:  # lateral_pivot
            # Partial session reset — keeps intel, resets phase
            s.phase = 0
            s.time_in_phase = 0.0
            s.services_found = max(0, s.services_found - 2)
            atk_intel = 0.1 * s.intel_score  # keep fraction of intel
        elif atk_action == 4:  # verify_honeypot
            s.honeypot_checks += 1
            # Effectiveness depends on defender's last action detectability
            if s.last_def_action >= 0:
                detectability = self._def_detectability[phase, s.last_def_action]
            else:
                detectability = 0.3
            # Higher detectability = attacker more likely to see through deception
            check_power = detectability * (0.5 + 0.1 * s.honeypot_checks)
            # Bayesian update with suspicion LR
            lr_check = max(0.2, 1.0 - check_power)
            prior = max(0.01, min(0.99, s.p_real))
            s.p_real = (lr_check * prior) / (lr_check * prior + 1.0 * (1 - prior))
            # But verification costs time and generates evasion signal
        else:
            # Skills 0-3: recon, exploit, use_credential, probe_ghost
            atk_intel = self._atk_intel_gain[phase, atk_action]
            atk_intel += random.gauss(0, 0.02)
            atk_intel = max(0.0, atk_intel)
            atk_engaged = True

            # Credential usage — interacts with defender's planted creds
            if atk_action == 2 and s.creds_planted > 0:  # use_credential
                s.creds_gathered += 1
                atk_intel += 0.3  # bonus for finding planted creds
                # But using fake creds = strong deception signal
                def_belief_delta += 0.08  # attacker more convinced it's real

            # Ghost probe — interacts with defender's ghost ports
            if atk_action == 3 and s.ghost_active > 0:  # probe_ghost
                s.ghost_visited += 1
                atk_intel += 0.15
                def_belief_delta += 0.04  # fell for the ghost service

            # Exploit — advances phase, adds exploit count
            if atk_action == 1:  # exploit_mavlink
                s.exploit_attempts += 1

            # Service discovery
            if atk_action == 0:  # recon_scan
                s.services_found += 1

            # Phase advancement
            advance_prob = self._atk_phase_advance[phase, atk_action]
            if random.random() < advance_prob and s.phase < 3:
                s.phase += 1
                s.time_in_phase = 0.0
                if s.level < 4 and random.random() < 0.3:
                    s.level += 1

        # ── 3. Accumulate intel ──────────────────────────────────
        s.intel_score += atk_intel

        # ── 4. Apply defender's belief effect ────────────────────
        if def_belief_delta != 0 and not atk_disconnected:
            if def_belief_delta > 0:
                lr = 1.0 + def_belief_delta * 8
            else:
                lr = max(0.1, 1.0 + def_belief_delta * 8)
            prior = max(0.01, min(0.99, s.p_real))
            s.p_real = (lr * prior) / (lr * prior + 1.0 * (1 - prior))

        # ── 5. Evasion check (stochastic) ────────────────────────
        if not atk_disconnected:
            evasion_prob = self._atk_evasion_prob[phase, min(atk_action, 6)]
            if s.p_real < 0.5:
                evasion_prob *= 1.5
            if random.random() < evasion_prob:
                s.evasion_signals += 1
                # Bayesian evasion update (LR=0.3)
                p = max(0.01, min(0.99, s.p_real))
                s.p_real = (0.3 * p) / (0.3 * p + 1.0 * (1 - p))

        # ── 6. Compute rewards ───────────────────────────────────
        r_def = self._compute_defender_reward(def_belief_delta, atk_engaged, s)
        r_atk = self._compute_attacker_reward(atk_intel, atk_engaged, s)

        # ── 7. Termination ───────────────────────────────────────
        done = False

        if atk_disconnected:
            done = True
            # Defender: partial win (got some dwell time)
            r_def += min(s.dwell_sec / 300.0, 2.0)
            # Attacker: keeps accumulated intel
            r_atk += s.intel_score * 0.5

        elif s.p_real < 0.2:
            done = True
            r_def -= 5.0    # defender: deception failed
            r_atk += 3.0    # attacker: detected honeypot

        elif s.step_count >= self.max_steps:
            done = True
            # Defender: maximum dwell = maximum deception success
            r_def += 3.0 + s.p_real * 2.0
            # Attacker: only keeps intel, wasted time
            r_atk += s.intel_score * 0.3 - 1.0

        elif s.evasion_signals >= 5:
            done = True
            r_def -= 2.0    # deception partially failed
            r_atk -= 1.0    # attacker also loses (indecisive)

        info = {
            "p_real": round(s.p_real, 4),
            "phase": s.phase,
            "level": s.level,
            "intel": round(s.intel_score, 2),
            "dwell": round(s.dwell_sec, 1),
            "evasion": s.evasion_signals,
            "ghost_active": s.ghost_active,
            "creds_planted": s.creds_planted,
            "creds_gathered": s.creds_gathered,
            "def_skill": DEFENDER_SKILLS[def_action],
            "atk_skill": ATTACKER_SKILLS[atk_action],
        }

        return (
            self._observe_defender(),
            self._observe_attacker(),
            r_def, r_atk, done, info,
        )

    # ── Reward Functions ─────────────────────────────────────────────────────

    def _compute_defender_reward(
        self, delta_p: float, engaged: bool, s: GameState,
    ) -> float:
        """
        Defender reward: maximize deception effectiveness.

        r_def = 0.35 * Δp_real + 0.25 * engaged + 0.25 * dwell + 0.15 * safety

        [REF] MIRAGE-UAS Eq.19 DES components
        """
        r_belief = max(-1.0, min(1.0, delta_p * 8))
        r_engage = 1.0 if engaged else 0.0
        r_dwell = 0.3
        r_safety = 0.0
        if s.evasion_signals > 0:
            r_safety = -0.3 * min(s.evasion_signals / 3.0, 1.0)
        return 0.35 * r_belief + 0.25 * r_engage + 0.25 * r_dwell + 0.15 * r_safety

    def _compute_attacker_reward(
        self, intel_gained: float, engaged: bool, s: GameState,
    ) -> float:
        """
        Attacker reward: maximize intelligence while minimizing wasted time.

        r_atk = 0.30 * intel + 0.25 * exploit_progress + 0.25 * efficiency - 0.20 * detection_risk

        [REF] Zhuang et al. (2025) TTC inverse as attacker utility
        """
        # Intel component — immediate information gain
        r_intel = min(intel_gained * 2.0, 1.0)

        # Exploit progress — phase advancement
        r_progress = s.phase / 3.0

        # Efficiency — diminishing returns on time
        r_efficiency = math.log(1 + s.intel_score) / (1 + s.step_count / 50.0)
        r_efficiency = min(r_efficiency, 1.0)

        # Detection risk — penalty for evasion signals
        r_risk = 0.0
        if s.evasion_signals > 0:
            r_risk = -0.3 * min(s.evasion_signals / 3.0, 1.0)

        return 0.30 * r_intel + 0.25 * r_progress + 0.25 * r_efficiency + 0.20 * r_risk

    # ── Observation Functions ────────────────────────────────────────────────

    def _observe_defender(self) -> np.ndarray:
        """
        Defender observation (10-dim).
        Same structure as BehaviorLearner expects.
        """
        s = self.state
        return np.array([
            s.phase / 3.0,
            min(s.level / 4.0, 1.0),
            s.p_real,
            min(s.dwell_sec / 600.0, 1.0),
            min((s.services_found + s.exploit_attempts) / 100.0, 1.0),
            min(s.services_found / 10.0, 1.0),
            min(s.exploit_attempts / 5.0, 1.0),
            min(s.ghost_active / 5.0, 1.0),
            min(s.time_in_phase / 120.0, 1.0),
            min(s.evasion_signals / 3.0, 1.0),
        ], dtype=np.float32)

    def _observe_attacker(self) -> np.ndarray:
        """
        Attacker observation (10-dim).
        Information asymmetry: attacker does NOT see p_real directly.
        Instead sees response_quality and timing_consistency (derived signals).
        """
        s = self.state
        # Response quality: proxy for how "real" the drone appears
        # Higher p_real → defender is doing well → responses feel real
        response_quality = 0.5 + 0.3 * (s.p_real - 0.5) + random.gauss(0, 0.05)
        response_quality = max(0.0, min(1.0, response_quality))

        # Timing consistency: reboot/silence makes timing inconsistent
        timing = 1.0
        if s.silence_steps > 0:
            timing = 0.3
        elif s.last_def_action == 3:  # reboot was recent
            timing = 0.6

        # Exploit success rate: fraction of exploits that "worked"
        exploit_rate = 0.0
        if s.exploit_attempts > 0:
            # Higher p_real → more "successful" responses
            exploit_rate = min(1.0, 0.3 + 0.5 * s.p_real)

        return np.array([
            s.phase / 3.0,
            min(s.dwell_sec / 600.0, 1.0),
            min(s.services_found / 10.0, 1.0),
            min(s.creds_gathered / 5.0, 1.0),
            min(s.ghost_visited / 5.0, 1.0),
            response_quality,
            timing,
            exploit_rate,
            min(s.last_def_action / 4.0, 1.0) if s.last_def_action >= 0 else 0.0,
            min(s.intel_score / 10.0, 1.0),
        ], dtype=np.float32)


# ── Vectorized Markov Game Environment ───────────────────────────────────────

class VecMarkovGameEnv:
    """
    Vectorized two-player game: N parallel environments, pure numpy.
    All ops batched — no Python loops per step.
    """

    # Internal state columns
    _PH, _LV, _PR, _DW, _SF, _CG, _GV, _IS = range(8)
    _EA, _EV, _HC, _GA, _CP, _SS, _TP, _SC = range(8, 16)
    _LDA, _LTA = 16, 17  # last defender/attacker action
    _N_STATE = 18

    def __init__(self, n_envs: int = 256, max_steps: int = 200) -> None:
        self.n_envs = n_envs
        self.max_steps = max_steps
        self._step_duration = 3.0

        # State: (N, 18)
        self._raw = np.zeros((n_envs, self._N_STATE), dtype=np.float32)
        self._rng = np.random.default_rng()

        # Reuse same matrices as scalar env
        _env = MarkovGameEnv()
        self._def_belief_effect = _env._def_belief_effect
        self._def_detectability = _env._def_detectability
        self._atk_intel_gain = _env._atk_intel_gain
        self._atk_phase_advance = _env._atk_phase_advance
        self._atk_evasion_prob = _env._atk_evasion_prob

    def reset_all(self) -> tuple[np.ndarray, np.ndarray]:
        """Reset all envs. Returns (def_obs (N,10), atk_obs (N,10))."""
        N = self.n_envs
        self._raw[:] = 0.0
        self._raw[:, self._LV] = self._rng.integers(0, 3, size=N).astype(np.float32)
        self._raw[:, self._PR] = (0.7 + self._rng.normal(0, 0.05, size=N)).astype(np.float32)
        self._raw[:, self._LDA] = -1.0
        self._raw[:, self._LTA] = -1.0
        return self._observe_defender(), self._observe_attacker()

    def _reset_idx(self, mask: np.ndarray) -> None:
        n = mask.sum()
        if n == 0:
            return
        self._raw[mask] = 0.0
        self._raw[mask, self._LV] = self._rng.integers(0, 3, size=n).astype(np.float32)
        self._raw[mask, self._PR] = (0.7 + self._rng.normal(0, 0.05, size=n)).astype(np.float32)
        self._raw[mask, self._LDA] = -1.0
        self._raw[mask, self._LTA] = -1.0

    def step(
        self,
        def_actions: np.ndarray,
        atk_actions: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict]:
        """
        Vectorized simultaneous-move step.

        Args:
            def_actions: (N,) int, each in [0, 4]
            atk_actions: (N,) int, each in [0, 6]

        Returns:
            (obs_def, obs_atk, r_def, r_atk, dones, info)
        """
        N = self.n_envs
        raw = self._raw
        rng = self._rng

        raw[:, self._SC] += 1
        raw[:, self._DW] += self._step_duration
        raw[:, self._TP] += self._step_duration
        raw[:, self._LDA] = def_actions.astype(np.float32)
        raw[:, self._LTA] = atk_actions.astype(np.float32)

        phase = np.clip(raw[:, self._PH].astype(np.int32), 0, 3)

        # ── Defender skill effects ──
        silence_active = raw[:, self._SS] > 0
        raw[:, self._SS] = np.maximum(raw[:, self._SS] - 1, 0)

        def_belief = np.where(
            silence_active, -0.02,
            self._def_belief_effect[phase, def_actions] + rng.normal(0, 0.01, N).astype(np.float32),
        )

        # Ghost port side effect
        raw[:, self._GA] = np.where(
            (def_actions == 2) & ~silence_active,
            np.minimum(raw[:, self._GA] + 1, 5),
            raw[:, self._GA],
        )
        # Reboot silence side effect
        new_silence = rng.integers(2, 5, size=N).astype(np.float32)
        raw[:, self._SS] = np.where(
            (def_actions == 3) & ~silence_active, new_silence, raw[:, self._SS],
        )
        # Credential leak side effect
        raw[:, self._CP] = np.where(
            (def_actions == 4) & ~silence_active,
            raw[:, self._CP] + 1, raw[:, self._CP],
        )

        # ── Attacker skill effects ──
        atk_disconnect = atk_actions == 6
        atk_lateral = atk_actions == 5
        atk_verify = atk_actions == 4
        atk_engage_mask = (atk_actions <= 3) & ~atk_disconnect

        # Intel gain for actions 0-3
        atk_intel = np.where(
            atk_engage_mask,
            np.maximum(0, self._atk_intel_gain[phase, np.minimum(atk_actions, 6)]
                        + rng.normal(0, 0.02, N).astype(np.float32)),
            0.0,
        )

        # Credential interaction
        cred_bonus = (atk_actions == 2) & (raw[:, self._CP] > 0)
        raw[:, self._CG] += cred_bonus.astype(np.float32)
        atk_intel = np.where(cred_bonus, atk_intel + 0.3, atk_intel)
        def_belief = np.where(cred_bonus, def_belief + 0.08, def_belief)

        # Ghost interaction
        ghost_bonus = (atk_actions == 3) & (raw[:, self._GA] > 0)
        raw[:, self._GV] += ghost_bonus.astype(np.float32)
        atk_intel = np.where(ghost_bonus, atk_intel + 0.15, atk_intel)
        def_belief = np.where(ghost_bonus, def_belief + 0.04, def_belief)

        # Recon → service discovery
        raw[:, self._SF] += (atk_actions == 0).astype(np.float32)
        # Exploit → attempt count
        raw[:, self._EA] += (atk_actions == 1).astype(np.float32)

        # Verify honeypot: p_real drops based on defender detectability
        last_def = np.clip(raw[:, self._LDA].astype(np.int32), 0, 4)
        detectability = self._def_detectability[phase, last_def]
        raw[:, self._HC] += atk_verify.astype(np.float32)
        check_power = detectability * (0.5 + 0.1 * raw[:, self._HC])
        lr_check = np.maximum(0.2, 1.0 - check_power)
        p_prior = np.clip(raw[:, self._PR], 0.01, 0.99)
        raw[:, self._PR] = np.where(
            atk_verify,
            (lr_check * p_prior) / (lr_check * p_prior + 1.0 * (1 - p_prior)),
            raw[:, self._PR],
        )

        # Lateral pivot: reset phase, keep intel fraction
        raw[:, self._PH] = np.where(atk_lateral, 0.0, raw[:, self._PH])
        raw[:, self._TP] = np.where(atk_lateral, 0.0, raw[:, self._TP])
        raw[:, self._SF] = np.where(
            atk_lateral, np.maximum(0, raw[:, self._SF] - 2), raw[:, self._SF],
        )
        atk_intel = np.where(atk_lateral, 0.1 * raw[:, self._IS], atk_intel)

        # Accumulate intel
        raw[:, self._IS] += atk_intel

        # ── Apply defender belief effect (Bayesian) ──
        apply_mask = ~atk_disconnect & (def_belief != 0)
        lr_def = np.where(def_belief > 0, 1.0 + def_belief * 8,
                          np.maximum(0.1, 1.0 + def_belief * 8))
        p = np.clip(raw[:, self._PR], 0.01, 0.99)
        updated_p = (lr_def * p) / (lr_def * p + 1.0 * (1 - p))
        raw[:, self._PR] = np.where(apply_mask, updated_p, raw[:, self._PR])

        # ── Evasion check ──
        evasion_prob = self._atk_evasion_prob[phase, np.minimum(atk_actions, 6)]
        evasion_prob = np.where(raw[:, self._PR] < 0.5, evasion_prob * 1.5, evasion_prob)
        evasion_prob = np.minimum(evasion_prob, 0.5)
        evasion_hit = (rng.random(N) < evasion_prob) & ~atk_disconnect
        raw[:, self._EV] += evasion_hit.astype(np.float32)
        p = np.clip(raw[:, self._PR], 0.01, 0.99)
        raw[:, self._PR] = np.where(
            evasion_hit, (0.3 * p) / (0.3 * p + 1.0 * (1 - p)), raw[:, self._PR],
        )

        # ── Phase advancement ──
        adv_prob = self._atk_phase_advance[phase, np.minimum(atk_actions, 6)]
        phase_adv = (rng.random(N) < adv_prob) & (raw[:, self._PH] < 3) & ~atk_disconnect
        raw[:, self._PH] += phase_adv.astype(np.float32)
        raw[:, self._TP] = np.where(phase_adv, 0.0, raw[:, self._TP])
        level_up = phase_adv & (raw[:, self._LV] < 4) & (rng.random(N) < 0.3)
        raw[:, self._LV] += level_up.astype(np.float32)

        # ── Rewards ──
        # Defender
        r_belief_d = np.clip(def_belief * 8, -1.0, 1.0)
        r_engage_d = atk_engage_mask.astype(np.float32)
        r_dwell_d = np.full(N, 0.3, dtype=np.float32)
        r_safety_d = np.where(
            raw[:, self._EV] > 0,
            -0.3 * np.minimum(raw[:, self._EV] / 3.0, 1.0),
            0.0,
        )
        r_def = 0.35 * r_belief_d + 0.25 * r_engage_d + 0.25 * r_dwell_d + 0.15 * r_safety_d

        # Attacker
        r_intel_a = np.minimum(atk_intel * 2.0, 1.0)
        r_progress_a = raw[:, self._PH] / 3.0
        step_count = raw[:, self._SC]
        r_eff_a = np.minimum(
            np.log1p(raw[:, self._IS]) / (1.0 + step_count / 50.0), 1.0,
        )
        r_risk_a = np.where(
            raw[:, self._EV] > 0,
            -0.3 * np.minimum(raw[:, self._EV] / 3.0, 1.0),
            0.0,
        )
        r_atk = 0.30 * r_intel_a + 0.25 * r_progress_a + 0.25 * r_eff_a + 0.20 * r_risk_a

        # ── Terminal ──
        done_disconnect = atk_disconnect
        done_preal = raw[:, self._PR] < 0.2
        done_steps = raw[:, self._SC] >= self.max_steps
        done_evasion = raw[:, self._EV] >= 5

        # Terminal rewards
        r_def = np.where(done_disconnect,
                         r_def + np.minimum(raw[:, self._DW] / 300.0, 2.0), r_def)
        r_atk = np.where(done_disconnect,
                         r_atk + raw[:, self._IS] * 0.5, r_atk)

        r_def = np.where(done_preal & ~done_disconnect, r_def - 5.0, r_def)
        r_atk = np.where(done_preal & ~done_disconnect, r_atk + 3.0, r_atk)

        r_def = np.where(done_steps & ~done_preal & ~done_disconnect,
                         r_def + 3.0 + raw[:, self._PR] * 2.0, r_def)
        r_atk = np.where(done_steps & ~done_preal & ~done_disconnect,
                         r_atk + raw[:, self._IS] * 0.3 - 1.0, r_atk)

        r_def = np.where(done_evasion & ~done_preal & ~done_steps & ~done_disconnect,
                         r_def - 2.0, r_def)
        r_atk = np.where(done_evasion & ~done_preal & ~done_steps & ~done_disconnect,
                         r_atk - 1.0, r_atk)

        dones = done_disconnect | done_preal | done_steps | done_evasion

        info = {
            "p_real": raw[:, self._PR].copy(),
            "intel": raw[:, self._IS].copy(),
            "dones": dones.copy(),
        }

        obs_d = self._observe_defender()
        obs_a = self._observe_attacker()

        # Auto-reset
        self._reset_idx(dones)

        return obs_d, obs_a, r_def, r_atk, dones, info

    def _observe_defender(self) -> np.ndarray:
        """(N, 10) defender observations."""
        raw = self._raw
        N = self.n_envs
        obs = np.zeros((N, DEFENDER_OBS_DIM), dtype=np.float32)
        obs[:, 0] = raw[:, self._PH] / 3.0
        obs[:, 1] = np.minimum(raw[:, self._LV] / 4.0, 1.0)
        obs[:, 2] = raw[:, self._PR]
        obs[:, 3] = np.minimum(raw[:, self._DW] / 600.0, 1.0)
        obs[:, 4] = np.minimum((raw[:, self._SF] + raw[:, self._EA]) / 100.0, 1.0)
        obs[:, 5] = np.minimum(raw[:, self._SF] / 10.0, 1.0)
        obs[:, 6] = np.minimum(raw[:, self._EA] / 5.0, 1.0)
        obs[:, 7] = np.minimum(raw[:, self._GA] / 5.0, 1.0)
        obs[:, 8] = np.minimum(raw[:, self._TP] / 120.0, 1.0)
        obs[:, 9] = np.minimum(raw[:, self._EV] / 3.0, 1.0)
        return obs

    def _observe_attacker(self) -> np.ndarray:
        """(N, 10) attacker observations — information asymmetry."""
        raw = self._raw
        N = self.n_envs
        obs = np.zeros((N, ATTACKER_OBS_DIM), dtype=np.float32)
        obs[:, 0] = raw[:, self._PH] / 3.0
        obs[:, 1] = np.minimum(raw[:, self._DW] / 600.0, 1.0)
        obs[:, 2] = np.minimum(raw[:, self._SF] / 10.0, 1.0)
        obs[:, 3] = np.minimum(raw[:, self._CG] / 5.0, 1.0)
        obs[:, 4] = np.minimum(raw[:, self._GV] / 5.0, 1.0)
        # Response quality: noisy proxy for p_real
        quality = 0.5 + 0.3 * (raw[:, self._PR] - 0.5) + self._rng.normal(0, 0.05, N).astype(np.float32)
        obs[:, 5] = np.clip(quality, 0.0, 1.0)
        # Timing consistency
        obs[:, 6] = np.where(raw[:, self._SS] > 0, 0.3, 1.0)
        # Exploit success rate
        ea = raw[:, self._EA]
        obs[:, 7] = np.where(ea > 0, np.minimum(1.0, 0.3 + 0.5 * raw[:, self._PR]), 0.0)
        # Last defender action (observable)
        lda = np.clip(raw[:, self._LDA], 0, 4)
        obs[:, 8] = lda / 4.0
        # Intel score
        obs[:, 9] = np.minimum(raw[:, self._IS] / 10.0, 1.0)
        return obs


# ── Single-Agent Wrapper (for DQN training) ──────────────────────────────────

class SingleAgentWrapper:
    """
    Wraps MarkovGameEnv to present single-agent interface for DQN training.
    The opponent's actions are sampled from a frozen policy.

    Usage:
        wrapper = SingleAgentWrapper(game_env, role="defender", opponent=RandomPolicy(7))
        obs = wrapper.reset()
        obs, reward, done, info = wrapper.step(action)
    """

    def __init__(
        self,
        game_env: MarkovGameEnv,
        role: str,
        opponent_policy,
    ) -> None:
        self.env = game_env
        self.role = role  # "defender" or "attacker"
        self.opponent = opponent_policy
        self._opponent_obs = None

        if role == "defender":
            self.n_actions = N_DEFENDER_ACTIONS
        else:
            self.n_actions = N_ATTACKER_ACTIONS

    def reset(self) -> np.ndarray:
        obs_d, obs_a = self.env.reset()
        if self.role == "defender":
            self._opponent_obs = obs_a
            return obs_d
        else:
            self._opponent_obs = obs_d
            return obs_a

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        opp_action = self.opponent.select(self._opponent_obs)

        if self.role == "defender":
            obs_d, obs_a, r_d, r_a, done, info = self.env.step(action, opp_action)
            self._opponent_obs = obs_a
            return obs_d, r_d, done, info
        else:
            obs_d, obs_a, r_d, r_a, done, info = self.env.step(opp_action, action)
            self._opponent_obs = obs_d
            return obs_a, r_a, done, info


class RandomPolicy:
    """Uniform random policy."""
    def __init__(self, n_actions: int) -> None:
        self.n_actions = n_actions
    def select(self, obs: np.ndarray) -> int:
        return random.randint(0, self.n_actions - 1)


class GreedyDefenderPolicy:
    """Phase-based greedy defender policy (no learning)."""
    # Best action per phase from _def_belief_effect matrix
    _PHASE_BEST = [1, 4, 3, 4]  # flight_sim, cred_leak, reboot, cred_leak
    def select(self, obs: np.ndarray) -> int:
        phase = int(obs[0] * 3)
        phase = min(phase, 3)
        return self._PHASE_BEST[phase]


class GreedyAttackerPolicy:
    """Phase-based greedy attacker policy (no learning)."""
    # Best action per phase from _atk_intel_gain matrix
    _PHASE_BEST = [0, 1, 2, 2]  # recon, exploit, use_cred, use_cred
    def select(self, obs: np.ndarray) -> int:
        phase = int(obs[0] * 3)
        phase = min(phase, 3)
        return self._PHASE_BEST[phase]


class DQNPolicy:
    """Wrapper for a trained DQN checkpoint."""
    def __init__(self, model_path: str, n_actions: int, state_dim: int = 10) -> None:
        import torch
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.n_actions = n_actions
        checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)
        # Build network
        from scripts.train_dqn import DQN
        self.net = DQN(state_dim, n_actions).to(self.device)
        self.net.load_state_dict(checkpoint["policy_state_dict"])
        self.net.eval()

    def select(self, obs: np.ndarray) -> int:
        import torch
        with torch.no_grad():
            state = torch.FloatTensor(obs).unsqueeze(0).to(self.device)
            q = self.net(state)
            return q.argmax(dim=1).item()
