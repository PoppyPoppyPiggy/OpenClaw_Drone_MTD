#!/usr/bin/env python3
"""
deception_env.py — Gym-style Environment for Deception Agent Training

[ROLE]
    공격자-허니드론 상호작용을 시뮬레이션하는 RL 학습 환경.
    실제 Docker 없이 수천 에피소드를 GPU에서 빠르게 학습 가능.

[ENVIRONMENT]
    State (10-dim):
        0: attack_phase     ∈ {0,1,2,3} → RECON/EXPLOIT/PERSIST/EXFIL (÷3)
        1: attacker_level   ∈ {0,1,2,3,4} → L0-L4 (÷4)
        2: p_real           ∈ [0,1] → Bayesian belief P(real|obs)
        3: dwell_sec        ∈ [0,∞) → 공격자 체류시간 (÷600, clamp)
        4: packets_received ∈ [0,∞) → 누적 패킷 수 (÷100, clamp)
        5: services_touched ∈ [0,∞) → 서비스 접촉 수 (÷10, clamp)
        6: exploit_attempts ∈ [0,∞) → exploit 시도 횟수 (÷5, clamp)
        7: ghost_active     ∈ [0,∞) → 활성 ghost 포트 (÷5, clamp)
        8: time_in_phase    ∈ [0,∞) → 현 단계 경과시간 (÷120, clamp)
        9: evasion_signals  ∈ [0,∞) → 회피 행동 횟수 (÷3, clamp)

    Action (5):
        0: proactive_statustext  — 가짜 운영자 메시지
        1: proactive_flight_sim  — 비행 시뮬레이션
        2: proactive_ghost_port  — ghost 서비스 개방
        3: proactive_reboot      — 재부팅 시뮬레이션
        4: proactive_fake_key    — 가짜 키 누출

    Reward:
        r = w1 * Δp_real + w2 * engagement + w3 * dwell_bonus - w4 * evasion_penalty

        Δp_real: 행동 후 P(real) 변화 (베이지안)
        engagement: 행동 후 공격자 반응 여부
        dwell_bonus: 공격자가 세션을 유지하면 보너스
        evasion_penalty: 공격자가 회피 행동을 시작하면 페널티

    Episode:
        1 에피소드 = 공격자 1 캠페인 (L0→L4, ~200 steps)
        종료 조건: 공격자 이탈(p_real < 0.2) 또는 시간 초과(600s)

[ATTACKER MODEL]
    공격자는 확률적으로 행동하며, 에이전트의 행동에 반응:
    - 좋은 기만 → 공격자가 더 오래 체류, 더 많은 명령 시도
    - 나쁜 기만 → 공격자가 의심, 회피 행동 증가, 이탈 확률 상승
    - 행동별 효과가 공격 단계에 따라 다름 (핵심 학습 대상)
"""
from __future__ import annotations

import math
import random as _stdrandom
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


N_BASE_ACTIONS = 5
N_INTENSITIES = 3   # low=0, medium=1, high=2
N_VARIANTS = 3      # parameter variant A=0, B=1, C=2
N_ACTIONS_FLAT = N_BASE_ACTIONS * N_INTENSITIES * N_VARIANTS  # 45
# Legacy alias for backward compat
N_ACTIONS = N_BASE_ACTIONS
STATE_DIM = 64

BASE_ACTION_NAMES = [
    "proactive_statustext",
    "proactive_flight_sim",
    "proactive_ghost_port",
    "proactive_reboot",
    "proactive_fake_key",
]
# Legacy alias
ACTION_NAMES = BASE_ACTION_NAMES

INTENSITY_NAMES = ["low", "medium", "high"]
VARIANT_NAMES = ["A", "B", "C"]

# Full 45-action name table
ACTION_NAMES_45 = []
for base in BASE_ACTION_NAMES:
    for intensity in INTENSITY_NAMES:
        for variant in VARIANT_NAMES:
            ACTION_NAMES_45.append(f"{base}_{intensity}_{variant}")


def decode_action(action_idx: int) -> tuple[int, int, int]:
    """Decode flat 0-44 → (base 0-4, intensity 0-2, variant 0-2)."""
    base = action_idx // 9
    remainder = action_idx % 9
    intensity = remainder // 3
    variant = remainder % 3
    return base, intensity, variant


def encode_action(base: int, intensity: int, variant: int) -> int:
    """Encode (base, intensity, variant) → flat 0-44."""
    return base * 9 + intensity * 3 + variant


# Bayesian LR table (from DeceptionStateManager)
BAYESIAN_LR = {
    "protocol_interact": 1.2,
    "breadcrumb_use": 2.5,
    "ghost_connect": 1.5,
    "ghost_deep": 2.0,
    "scan_detected": 0.5,
    "evasion": 0.3,
    "exploit_attempt": 1.1,
}


@dataclass
class AttackerState:
    """Stochastic attacker state."""
    level: int = 0             # 0-4
    phase: int = 0             # 0=RECON, 1=EXPLOIT, 2=PERSIST, 3=EXFIL
    p_real: float = 0.7        # Bayesian belief
    dwell_sec: float = 0.0
    packets: int = 0
    services_touched: int = 0
    exploit_attempts: int = 0
    ghost_active: int = 0
    time_in_phase: float = 0.0
    evasion_signals: int = 0
    alive: bool = True
    step_count: int = 0


class DeceptionEnv:
    """
    Gym-style environment for training deception agent.
    No external dependencies — pure Python + numpy.

    Supports two modes:
        action_mode="base"  → 5 base actions (legacy DQN)
        action_mode="param" → 45 parameterized actions (h-DQN)
    """

    def __init__(
        self,
        max_steps: int = 100,
        action_mode: str = "base",
        seed: Optional[int] = None,
    ) -> None:
        self.max_steps = max_steps
        self.action_mode = action_mode  # "base" or "param"
        self.n_actions = N_ACTIONS_FLAT if action_mode == "param" else N_BASE_ACTIONS
        self.state = AttackerState()
        self._step_duration = 3.0  # seconds per step
        # Per-instance RNGs for reproducibility. The env touches BOTH Python
        # stdlib `random` (via random.randint/random.gauss) AND numpy random
        # in various places, so we route every stochastic call through these
        # two instance-local RNGs. Callers pass `seed` to the constructor OR
        # to reset(seed=...) — NEVER rely on global np.random.seed.
        self._py_rng = _stdrandom.Random(seed)
        self._np_rng = np.random.default_rng(seed)

        # ══════════════════════════════════════════════════════
        # Base action effectiveness matrices (5 actions × 4 phases)
        # Calibrated from real testbed experiment data.
        # ══════════════════════════════════════════════════════

        # [phase][action] → Δ P(real)
        self._action_effect = np.array([
            #  statustext  flight_sim  ghost_port  reboot    fake_key
            [  0.04,       0.06,       0.01,       -0.01,    0.02  ],  # RECON
            [  0.01,       0.03,       0.03,       0.05,     0.07  ],  # EXPLOIT
            [  0.05,       0.02,       0.03,       0.06,     0.04  ],  # PERSIST
            [ -0.02,       -0.01,      0.02,       0.04,     0.06  ],  # EXFIL
        ])

        # [phase][action] → P(attacker sends more packets)
        self._action_engage = np.array([
            [  0.7,        0.85,       0.3,        0.15,     0.4   ],  # RECON
            [  0.3,        0.4,        0.5,        0.6,      0.85  ],  # EXPLOIT
            [  0.5,        0.3,        0.4,        0.5,      0.55  ],  # PERSIST
            [  0.15,       0.1,        0.25,       0.4,      0.75  ],  # EXFIL
        ])

        # [phase][action] → P(attacker becomes suspicious)
        self._action_evasion = np.array([
            [  0.01,       0.01,       0.04,       0.10,     0.02  ],  # RECON
            [  0.03,       0.02,       0.05,       0.12,     0.04  ],  # EXPLOIT
            [  0.02,       0.04,       0.06,       0.15,     0.03  ],  # PERSIST
            [  0.06,       0.08,       0.08,       0.18,     0.04  ],  # EXFIL
        ])

        # ══════════════════════════════════════════════════════
        # Intensity & variant modifiers for 45-action mode
        #
        # Calibration rationale (MIRAGE-UAS §4.3 Table VIII):
        #   Low intensity  = minimal observable change → low risk, low reward
        #   Medium         = standard operation → balanced (matches base 5-action)
        #   High intensity = aggressive/visible change → high reward but HIGH risk
        #
        # Key design: evasion scales SUPERLINEARLY with intensity.
        #   effect:  [0.6, 1.0, 1.4]  — diminishing returns at high
        #   engage:  [0.7, 1.0, 1.3]  — more visible = more response
        #   evasion: [0.4, 1.0, 2.2]  — superlinear! high = 2.2× base evasion
        #
        # This creates a genuine tradeoff: high intensity gives +40% effect
        # but +120% evasion risk. The optimal policy must mix intensities.
        # ══════════════════════════════════════════════════════
        self._intensity_effect = np.array([0.6, 1.0, 1.4])
        self._intensity_engage = np.array([0.7, 1.0, 1.3])
        self._intensity_evasion = np.array([0.4, 1.0, 2.2])  # superlinear!
        # variant modifiers: (effect_mult, engage_mult, evasion_mult)
        self._variant_mods = np.array([
            [1.0, 1.0, 1.0],   # A: balanced — no modification
            [1.15, 0.85, 0.9], # B: more effective, less engaging, safer
            [0.85, 1.15, 1.1], # C: less effective, more engaging, riskier
        ])

    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        """Reset to new episode (new attacker campaign).

        Pass `seed` to re-anchor the instance RNGs — useful for creating a
        hold-out evaluation trajectory that is independent of the training
        RNG stream. Without `seed`, the RNG continues its previous state.
        """
        if seed is not None:
            self._py_rng = _stdrandom.Random(seed)
            self._np_rng = np.random.default_rng(seed)
        self.state = AttackerState()
        # Randomize starting conditions
        self.state.level = self._py_rng.randint(0, 2)
        self.state.p_real = 0.7 + self._py_rng.gauss(0, 0.05)
        return self._observe()

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        """
        Execute one step.

        Args:
            action: int 0-4 (base mode) or 0-44 (param mode)

        Returns:
            (next_state, reward, done, info)
        """
        s = self.state
        s.step_count += 1
        s.dwell_sec += self._step_duration
        phase = min(s.phase, 3)

        # ── 1. Apply action effect on P(real) ──
        if self.action_mode == "param":
            base, intensity, variant = decode_action(action)
            base_effect = self._action_effect[phase, base]
            v_effect, v_engage, v_evasion = self._variant_mods[variant]
            base_effect = base_effect * self._intensity_effect[intensity] * v_effect
            engage_mult = self._intensity_engage[intensity] * v_engage
            evasion_mult = self._intensity_evasion[intensity] * v_evasion
        else:
            base = action
            base_effect = self._action_effect[phase, action]
            intensity, variant = 1, 0
            engage_mult, evasion_mult = 1.0, 1.0
        noise = self._py_rng.gauss(0, 0.01)
        delta_p = base_effect + noise

        # Bayesian update simulation
        if delta_p > 0:
            lr = 1.0 + delta_p * 10  # positive → LR > 1
        else:
            lr = max(0.1, 1.0 + delta_p * 10)  # negative → LR < 1

        prior = max(0.01, min(0.99, s.p_real))
        s.p_real = (lr * prior) / (lr * prior + 1.0 * (1 - prior))

        # ── 2. Engagement: does attacker respond? ──
        engage_prob = self._action_engage[phase, base] * engage_mult
        engage_prob = min(engage_prob, 0.98)
        engaged = self._py_rng.random() < engage_prob
        if engaged:
            new_pkts = self._py_rng.randint(1, 5)
            s.packets += new_pkts
            s.services_touched += 1 if self._py_rng.random() < 0.3 else 0

        # ── 3. Evasion: does attacker become suspicious? ──
        evasion_prob = self._action_evasion[phase, base] * evasion_mult
        evasion_prob = min(evasion_prob, 0.5)
        # Evasion increases as p_real drops
        if s.p_real < 0.5:
            evasion_prob *= 2.0
        if self._py_rng.random() < evasion_prob:
            s.evasion_signals += 1
            # Bayesian update with evasion LR=0.3
            s.p_real = (0.3 * s.p_real) / (0.3 * s.p_real + 1.0 * (1 - s.p_real))

        # ── 4. Phase progression (stochastic) ──
        s.time_in_phase += self._step_duration
        phase_advance_prob = 0.02 * (1 + s.time_in_phase / 30.0)
        if self._py_rng.random() < phase_advance_prob and s.phase < 3:
            s.phase += 1
            s.time_in_phase = 0.0
            # Level may increase with phase
            if s.level < 4 and self._py_rng.random() < 0.3:
                s.level += 1

        # Exploit attempts increase in EXPLOIT/PERSIST phases
        if s.phase >= 1 and self._py_rng.random() < 0.1:
            s.exploit_attempts += 1

        # Ghost port tracking
        if base == 2:  # ghost_port
            s.ghost_active = min(s.ghost_active + 1, 5)

        # ── 5. Compute reward ──
        reward = self._compute_reward(delta_p, engaged, s)

        # ── 6. Check termination ──
        # Terminal rewards derived from MIRAGE-UAS DES (Eq.19):
        #   DES = w1·time_on_decoys + w2·breach_prevention + w3·confusion + w4·bc_follow + w5·ghost_hit
        # Terminal bonus/penalty represents the episode-level DES contribution:
        #   - Full survival → maximum DES (all time on decoys, confusion maintained)
        #   - p_real collapse → breach risk, DES drops to near zero
        #   - Evasion exit → partial DES (some dwell, but attacker detected honeypot)
        done = False
        if s.p_real < 0.2:  # attacker detected honeypot → DES ≈ 0
            done = True
            reward -= 5.0
        elif s.step_count >= self.max_steps:
            done = True
            reward += 3.0 + s.p_real * 2.0  # max DES contribution
        elif s.evasion_signals >= 5:
            done = True
            reward -= 2.0

        info = {
            "p_real": round(s.p_real, 4),
            "phase": s.phase,
            "level": s.level,
            "engaged": engaged,
            "evasion": s.evasion_signals,
            "delta_p": round(delta_p, 4),
        }

        return self._observe(), reward, done, info

    def _compute_reward(self, delta_p: float, engaged: bool, s: AttackerState) -> float:
        """
        Reward = 0.35 * belief + 0.25 * engage + 0.25 * dwell + 0.15 * safety

        Weights derived from MIRAGE-UAS Eq.NEW-1 deception reward:
          r_dec = w_dwell·min(t/T_max,1) + w_cmd·log(1+N) + w_prot·I(safe)
        Mapped to RL per-step signal:
          belief  (0.35) ← w_cmd: more commands = more deception interaction
          engage  (0.25) ← w_dwell: attacker staying = dwell time increasing
          dwell   (0.25) ← per-step survival = direct dwell contribution
          safety  (0.15) ← w_prot·I(safe): evasion = breach risk

        REF: DeceptionStateManager likelihood ratios calibrate delta_p scaling.
             LR=1.2 for normal interaction → Δp≈0.03 per step at prior=0.7
             Scaling factor 8 maps this to r_belief≈0.24, giving meaningful gradient.
        """
        r_belief = max(-1.0, min(1.0, delta_p * 8))
        r_engage = 1.0 if engaged else 0.0
        r_dwell = 0.3
        r_safety = 0.0
        if s.evasion_signals > 0:
            r_safety = -0.3 * min(s.evasion_signals / 3.0, 1.0)

        return 0.35 * r_belief + 0.25 * r_engage + 0.25 * r_dwell + 0.15 * r_safety

    def _observe(self) -> np.ndarray:
        """Return normalized state vector."""
        s = self.state
        return np.array([
            s.phase / 3.0,
            s.level / 4.0,
            s.p_real,
            min(s.dwell_sec / 600.0, 1.0),
            min(s.packets / 100.0, 1.0),
            min(s.services_touched / 10.0, 1.0),
            min(s.exploit_attempts / 5.0, 1.0),
            min(s.ghost_active / 5.0, 1.0),
            min(s.time_in_phase / 120.0, 1.0),
            min(s.evasion_signals / 3.0, 1.0),
        ], dtype=np.float32)


# ═══════════════════════════════════════════════════════════════
# Vectorized Environment — N parallel envs, pure numpy
# ═══════════════════════════════════════════════════════════════

class VecDeceptionEnv:
    """
    Vectorized DeceptionEnv: runs N environments simultaneously.
    All operations are batched numpy — no Python loops per step.

    State layout (N, 64): expanded observation with history, rates, and derived features.
    Internal state (N, 10): phase, level, p_real, dwell, packets,
                            services, exploits, ghost, time_in_phase, evasion
    """

    # Column indices for internal raw state
    _PH, _LV, _PR, _DW, _PK, _SV, _EX, _GH, _TP, _EV = range(10)
    _SC = 10  # step_count lives in extra column

    def __init__(self, n_envs: int = 256, max_steps: int = 200) -> None:
        self.n_envs = n_envs
        self.max_steps = max_steps
        self.n_actions = N_ACTIONS_FLAT
        self._step_duration = 3.0

        # Matrices (same as DeceptionEnv)
        self._action_effect = np.array([
            [ 0.04,  0.06,  0.01, -0.01,  0.02],
            [ 0.01,  0.03,  0.03,  0.05,  0.07],
            [ 0.05,  0.02,  0.03,  0.06,  0.04],
            [-0.02, -0.01,  0.02,  0.04,  0.06],
        ], dtype=np.float32)
        self._action_engage = np.array([
            [0.70, 0.85, 0.30, 0.15, 0.40],
            [0.30, 0.40, 0.50, 0.60, 0.85],
            [0.50, 0.30, 0.40, 0.50, 0.55],
            [0.15, 0.10, 0.25, 0.40, 0.75],
        ], dtype=np.float32)
        self._action_evasion = np.array([
            [0.01, 0.01, 0.04, 0.10, 0.02],
            [0.03, 0.02, 0.05, 0.12, 0.04],
            [0.02, 0.04, 0.06, 0.15, 0.03],
            [0.06, 0.08, 0.08, 0.18, 0.04],
        ], dtype=np.float32)
        self._intensity_effect = np.array([0.6, 1.0, 1.4], dtype=np.float32)
        self._intensity_engage = np.array([0.7, 1.0, 1.3], dtype=np.float32)
        self._intensity_evasion = np.array([0.4, 1.0, 2.2], dtype=np.float32)
        self._variant_mods = np.array([
            [1.0, 1.0, 1.0], [1.15, 0.85, 0.9], [0.85, 1.15, 1.1],
        ], dtype=np.float32)

        # Internal raw state: (N, 11) — 10 state dims + step_count
        self._raw = np.zeros((n_envs, 11), dtype=np.float32)
        self._rng = np.random.default_rng()

        # ── History buffers for 64-dim observation ──
        self._p_real_hist = np.zeros((n_envs, 8), dtype=np.float32)
        self._engaged_hist = np.zeros((n_envs, 8), dtype=np.float32)
        self._evasion_hist = np.zeros((n_envs, 8), dtype=np.float32)
        self._action_hist = np.zeros((n_envs, 10), dtype=np.int32)
        self._hist_idx = np.zeros(n_envs, dtype=np.int32)
        self._p_real_ema = np.zeros(n_envs, dtype=np.float32)
        self._prev_packets = np.zeros(n_envs, dtype=np.float32)
        self._prev_evasion = np.zeros(n_envs, dtype=np.float32)

        # Pre-allocated identity matrices for one-hot encoding
        self._eye4 = np.eye(4, dtype=np.float32)
        self._eye5 = np.eye(5, dtype=np.float32)

    def reset_all(self) -> np.ndarray:
        """Reset all N environments. Returns (N, 64) observations."""
        self._raw[:] = 0.0
        self._raw[:, self._LV] = self._rng.integers(0, 3, size=self.n_envs).astype(np.float32)
        init_p = (0.7 + self._rng.normal(0, 0.05, size=self.n_envs)).astype(np.float32)
        self._raw[:, self._PR] = init_p
        # Reset history buffers
        self._p_real_hist[:] = init_p[:, None]  # fill all 8 slots with initial p_real
        self._engaged_hist[:] = 0.0
        self._evasion_hist[:] = 0.0
        self._action_hist[:] = 0
        self._hist_idx[:] = 0
        self._p_real_ema[:] = init_p
        self._prev_packets[:] = 0.0
        self._prev_evasion[:] = 0.0
        return self._observe()

    def _reset_idx(self, mask: np.ndarray) -> None:
        """Reset environments where mask is True (auto-reset)."""
        n = mask.sum()
        if n == 0:
            return
        self._raw[mask] = 0.0
        self._raw[mask, self._LV] = self._rng.integers(0, 3, size=n).astype(np.float32)
        init_p = (0.7 + self._rng.normal(0, 0.05, size=n)).astype(np.float32)
        self._raw[mask, self._PR] = init_p
        # Reset history buffers for done envs
        self._p_real_hist[mask] = init_p[:, None]
        self._engaged_hist[mask] = 0.0
        self._evasion_hist[mask] = 0.0
        self._action_hist[mask] = 0
        self._hist_idx[mask] = 0
        self._p_real_ema[mask] = init_p
        self._prev_packets[mask] = 0.0
        self._prev_evasion[mask] = 0.0

    def step(self, actions: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
        """
        Vectorized step for all N envs.

        Args:
            actions: (N,) int array, each in [0, 44]

        Returns:
            obs (N, 64), rewards (N,), dones (N,), info dict
        """
        N = self.n_envs
        raw = self._raw
        rng = self._rng

        raw[:, self._SC] += 1
        raw[:, self._DW] += self._step_duration
        phase = np.clip(raw[:, self._PH].astype(np.int32), 0, 3)

        # Decode actions → (N,) base, intensity, variant
        base = actions // 9
        remainder = actions % 9
        intensity = remainder // 3
        variant = remainder % 3

        # Gather per-env values from matrices using advanced indexing
        base_effect = self._action_effect[phase, base]
        base_effect = base_effect * self._intensity_effect[intensity] * self._variant_mods[variant, 0]
        engage_mult = self._intensity_engage[intensity] * self._variant_mods[variant, 1]
        evasion_mult = self._intensity_evasion[intensity] * self._variant_mods[variant, 2]

        noise = rng.normal(0, 0.01, size=N).astype(np.float32)
        delta_p = base_effect + noise

        # Bayesian update
        lr = np.where(delta_p > 0, 1.0 + delta_p * 10, np.maximum(0.1, 1.0 + delta_p * 10))
        prior = np.clip(raw[:, self._PR], 0.01, 0.99)
        raw[:, self._PR] = (lr * prior) / (lr * prior + 1.0 * (1 - prior))

        # Engagement
        engage_prob = np.minimum(self._action_engage[phase, base] * engage_mult, 0.98)
        engaged = rng.random(N) < engage_prob
        new_pkts = rng.integers(1, 6, size=N) * engaged
        raw[:, self._PK] += new_pkts
        raw[:, self._SV] += (rng.random(N) < 0.3).astype(np.float32) * engaged

        # Evasion
        evasion_prob = np.minimum(self._action_evasion[phase, base] * evasion_mult, 0.5)
        evasion_prob = np.where(raw[:, self._PR] < 0.5, evasion_prob * 2.0, evasion_prob)
        evasion_hit = rng.random(N) < evasion_prob
        raw[:, self._EV] += evasion_hit.astype(np.float32)
        # Bayesian evasion update
        p = raw[:, self._PR]
        raw[:, self._PR] = np.where(
            evasion_hit,
            (0.3 * p) / (0.3 * p + 1.0 * (1 - p)),
            p,
        )

        # Phase progression
        raw[:, self._TP] += self._step_duration
        phase_prob = 0.02 * (1 + raw[:, self._TP] / 30.0)
        phase_advance = (rng.random(N) < phase_prob) & (raw[:, self._PH] < 3)
        raw[:, self._PH] += phase_advance.astype(np.float32)
        raw[:, self._TP] = np.where(phase_advance, 0.0, raw[:, self._TP])
        level_up = phase_advance & (raw[:, self._LV] < 4) & (rng.random(N) < 0.3)
        raw[:, self._LV] += level_up.astype(np.float32)

        # Exploit attempts
        raw[:, self._EX] += ((raw[:, self._PH] >= 1) & (rng.random(N) < 0.1)).astype(np.float32)

        # Ghost port tracking
        ghost_mask = base == 2
        raw[:, self._GH] = np.where(ghost_mask, np.minimum(raw[:, self._GH] + 1, 5), raw[:, self._GH])

        # Reward
        r_belief = np.clip(delta_p * 8, -1.0, 1.0)
        r_engage = engaged.astype(np.float32)
        r_dwell = np.full(N, 0.3, dtype=np.float32)
        r_safety = np.where(
            raw[:, self._EV] > 0,
            -0.3 * np.minimum(raw[:, self._EV] / 3.0, 1.0),
            0.0,
        )
        rewards = 0.35 * r_belief + 0.25 * r_engage + 0.25 * r_dwell + 0.15 * r_safety

        # Termination
        done_preal = raw[:, self._PR] < 0.2
        done_steps = raw[:, self._SC] >= self.max_steps
        done_evasion = raw[:, self._EV] >= 5

        rewards = np.where(done_preal, rewards - 5.0, rewards)
        rewards = np.where(done_steps & ~done_preal, rewards + 3.0 + raw[:, self._PR] * 2.0, rewards)
        rewards = np.where(done_evasion & ~done_preal & ~done_steps, rewards - 2.0, rewards)

        dones = done_preal | done_steps | done_evasion

        # ── History buffer updates ──
        hidx8 = self._hist_idx % 8
        hidx10 = self._hist_idx % 10
        env_idx = np.arange(N)
        self._p_real_hist[env_idx, hidx8] = raw[:, self._PR]
        self._engaged_hist[env_idx, hidx8] = engaged.astype(np.float32)
        self._evasion_hist[env_idx, hidx8] = np.minimum(raw[:, self._EV] / 3.0, 1.0)
        self._action_hist[env_idx, hidx10] = base

        packet_rate = raw[:, self._PK] - self._prev_packets
        evasion_rate = raw[:, self._EV] - self._prev_evasion
        self._p_real_ema = 0.3 * raw[:, self._PR] + 0.7 * self._p_real_ema
        self._prev_packets = raw[:, self._PK].copy()
        self._prev_evasion = raw[:, self._EV].copy()
        self._hist_idx += 1

        info = {
            "p_real": raw[:, self._PR].copy(),
            "engaged": engaged.copy(),
            "evasion": raw[:, self._EV].copy(),
            "dones": dones.copy(),
            "packet_rate": packet_rate.copy(),
            "evasion_rate": evasion_rate.copy(),
        }

        obs = self._observe()

        # Auto-reset done envs
        self._reset_idx(dones)

        return obs, rewards, dones, info

    def _observe(self) -> np.ndarray:
        """Return (N, 64) normalized observations with history and derived features."""
        N = self.n_envs
        raw = self._raw
        obs = np.zeros((N, STATE_DIM), dtype=np.float32)

        # [0-3] phase one-hot (4)
        phase_idx = np.clip(raw[:, self._PH].astype(np.int32), 0, 3)
        obs[:, 0:4] = self._eye4[phase_idx]

        # [4-8] level one-hot (5)
        level_idx = np.clip(raw[:, self._LV].astype(np.int32), 0, 4)
        obs[:, 4:9] = self._eye5[level_idx]

        # [9] p_real current
        obs[:, 9] = raw[:, self._PR]

        # [10-17] p_real history last 8 steps (ring buffer, newest first)
        for i in range(8):
            obs[:, 10 + i] = self._p_real_hist[
                np.arange(N), (self._hist_idx - 1 - i) % 8
            ]

        # [18-25] engagement history last 8 steps
        for i in range(8):
            obs[:, 18 + i] = self._engaged_hist[
                np.arange(N), (self._hist_idx - 1 - i) % 8
            ]

        # [26-33] evasion history last 8 steps
        for i in range(8):
            obs[:, 26 + i] = self._evasion_hist[
                np.arange(N), (self._hist_idx - 1 - i) % 8
            ]

        # [34-38] action frequency (count of each base action in last 10 / 10)
        for a in range(N_BASE_ACTIONS):
            obs[:, 34 + a] = np.sum(self._action_hist == a, axis=1).astype(np.float32) / 10.0

        # [39-44] raw state features (clamped)
        obs[:, 39] = np.minimum(raw[:, self._DW] / 600.0, 1.0)
        obs[:, 40] = np.minimum(raw[:, self._PK] / 100.0, 1.0)
        obs[:, 41] = np.minimum(raw[:, self._SV] / 10.0, 1.0)
        obs[:, 42] = np.minimum(raw[:, self._EX] / 5.0, 1.0)
        obs[:, 43] = np.minimum(raw[:, self._GH] / 5.0, 1.0)
        obs[:, 44] = np.minimum(raw[:, self._TP] / 120.0, 1.0)

        # [45] packet_rate / 5 clamped
        obs[:, 45] = np.clip(
            (raw[:, self._PK] - self._prev_packets) / 5.0, 0.0, 1.0
        )

        # [46] engagement_rate (mean of last 8 engaged)
        obs[:, 46] = np.mean(self._engaged_hist, axis=1)

        # [47] evasion_rate clamped
        obs[:, 47] = np.clip(
            raw[:, self._EV] - self._prev_evasion, 0.0, 1.0
        )

        # [48-49] sin/cos time encoding
        step_count = raw[:, self._SC]
        time_frac = step_count / max(self.max_steps, 1)
        obs[:, 48] = np.sin(2.0 * np.pi * time_frac)
        obs[:, 49] = np.cos(2.0 * np.pi * time_frac)

        # [50] progress
        obs[:, 50] = step_count / max(self.max_steps, 1)

        # [51] delta_p 1-step * 5, clamped [-1, 1]
        env_idx = np.arange(N)
        p_prev1 = self._p_real_hist[env_idx, (self._hist_idx - 2) % 8]
        obs[:, 51] = np.clip((raw[:, self._PR] - p_prev1) * 5.0, -1.0, 1.0)

        # [52] delta_p 4-step * 2.5, clamped [-1, 1]
        p_prev4 = self._p_real_hist[env_idx, (self._hist_idx - 5) % 8]
        obs[:, 52] = np.clip((raw[:, self._PR] - p_prev4) * 2.5, -1.0, 1.0)

        # [53] p_real_ema
        obs[:, 53] = self._p_real_ema

        # [54] p_real variance over last 8
        obs[:, 54] = np.var(self._p_real_hist, axis=1)

        # [55] phase_duration = time_in_phase / (dwell_sec + 1)
        obs[:, 55] = raw[:, self._TP] / (raw[:, self._DW] + 1.0)

        # [56] exploit_rate = exploit_attempts / (step_count + 1)
        obs[:, 56] = raw[:, self._EX] / (step_count + 1.0)

        # [57] ghost_density = ghost_active / (services_touched + 1)
        obs[:, 57] = raw[:, self._GH] / (raw[:, self._SV] + 1.0)

        # [58] suspicion = evasion * (1 - p_real)
        obs[:, 58] = np.minimum(raw[:, self._EV] / 3.0, 1.0) * (1.0 - raw[:, self._PR])

        # [59-63] zeros (padding for future) — already zero from np.zeros init

        return obs


# ═══════════════════════════════════════════════════════════════
# GPU-Native Vectorized Environment — zero CPU↔GPU transfer
# ═══════════════════════════════════════════════════════════════

try:
    import torch as _torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


class CudaVecDeceptionEnv:
    """
    GPU-native vectorized environment. All state, actions, rewards
    live on CUDA — zero CPU↔GPU transfer during training.

    Same dynamics as VecDeceptionEnv / DeceptionEnv.
    """

    _PH, _LV, _PR, _DW, _PK, _SV, _EX, _GH, _TP, _EV = range(10)
    _SC = 10

    def __init__(self, n_envs: int = 1024, max_steps: int = 200,
                 device: str = "cuda") -> None:
        assert _HAS_TORCH, "PyTorch required for CudaVecDeceptionEnv"
        self.n_envs = n_envs
        self.max_steps = max_steps
        self.n_actions = N_ACTIONS_FLAT
        self.device = _torch.device(device)
        self._step_duration = 3.0

        # Lookup tables on GPU
        self._action_effect = _torch.tensor([
            [ 0.04,  0.06,  0.01, -0.01,  0.02],
            [ 0.01,  0.03,  0.03,  0.05,  0.07],
            [ 0.05,  0.02,  0.03,  0.06,  0.04],
            [-0.02, -0.01,  0.02,  0.04,  0.06],
        ], device=self.device)
        self._action_engage = _torch.tensor([
            [0.70, 0.85, 0.30, 0.15, 0.40],
            [0.30, 0.40, 0.50, 0.60, 0.85],
            [0.50, 0.30, 0.40, 0.50, 0.55],
            [0.15, 0.10, 0.25, 0.40, 0.75],
        ], device=self.device)
        self._action_evasion = _torch.tensor([
            [0.01, 0.01, 0.04, 0.10, 0.02],
            [0.03, 0.02, 0.05, 0.12, 0.04],
            [0.02, 0.04, 0.06, 0.15, 0.03],
            [0.06, 0.08, 0.08, 0.18, 0.04],
        ], device=self.device)
        self._intensity_effect = _torch.tensor([0.6, 1.0, 1.4], device=self.device)
        self._intensity_engage = _torch.tensor([0.7, 1.0, 1.3], device=self.device)
        self._intensity_evasion = _torch.tensor([0.4, 1.0, 2.2], device=self.device)
        self._variant_effect = _torch.tensor([1.0, 1.15, 0.85], device=self.device)
        self._variant_engage = _torch.tensor([1.0, 0.85, 1.15], device=self.device)
        self._variant_evasion = _torch.tensor([1.0, 0.9, 1.1], device=self.device)

        # Normalization divisors for _observe
        self._obs_div = _torch.tensor(
            [3.0, 4.0, 1.0, 600.0, 100.0, 10.0, 5.0, 5.0, 120.0, 3.0],
            device=self.device,
        )

        # Internal state (N, 11)
        self._raw = _torch.zeros(n_envs, 11, device=self.device)

    def reset_all(self) -> _torch.Tensor:
        """Reset all envs. Returns (N, 10) obs tensor on device."""
        self._raw.zero_()
        self._raw[:, self._LV] = _torch.randint(0, 3, (self.n_envs,),
                                                  device=self.device).float()
        self._raw[:, self._PR] = 0.7 + 0.05 * _torch.randn(self.n_envs,
                                                              device=self.device)
        return self._observe()

    def _reset_idx(self, mask: _torch.Tensor) -> None:
        """Auto-reset done envs."""
        n = mask.sum().item()
        if n == 0:
            return
        self._raw[mask] = 0.0
        self._raw[mask, self._LV] = _torch.randint(0, 3, (n,),
                                                     device=self.device).float()
        self._raw[mask, self._PR] = 0.7 + 0.05 * _torch.randn(n, device=self.device)

    def step(self, actions: _torch.Tensor):
        """
        GPU-native step. All tensors stay on device.

        Args:
            actions: (N,) long tensor on device, each in [0, 44]

        Returns:
            obs (N, 10), rewards (N,), dones (N, bool), info dict (tensors)
        """
        N = self.n_envs
        raw = self._raw

        raw[:, self._SC] += 1
        raw[:, self._DW] += self._step_duration

        phase = raw[:, self._PH].long().clamp(0, 3)

        # Decode actions
        base = actions // 9
        remainder = actions % 9
        intensity = remainder // 3
        variant = remainder % 3

        # Lookup — advanced indexing on GPU tensors
        base_effect = self._action_effect[phase, base]
        base_effect = (base_effect
                       * self._intensity_effect[intensity]
                       * self._variant_effect[variant])
        engage_mult = self._intensity_engage[intensity] * self._variant_engage[variant]
        evasion_mult = self._intensity_evasion[intensity] * self._variant_evasion[variant]

        noise = 0.01 * _torch.randn(N, device=self.device)
        delta_p = base_effect + noise

        # Bayesian update
        lr = _torch.where(delta_p > 0, 1.0 + delta_p * 10,
                          (1.0 + delta_p * 10).clamp(min=0.1))
        prior = raw[:, self._PR].clamp(0.01, 0.99)
        raw[:, self._PR] = (lr * prior) / (lr * prior + (1 - prior))

        # Engagement
        engage_prob = (self._action_engage[phase, base] * engage_mult).clamp(max=0.98)
        engaged = _torch.rand(N, device=self.device) < engage_prob
        eng_f = engaged.float()
        raw[:, self._PK] += (_torch.randint(1, 6, (N,), device=self.device).float()
                             * eng_f)
        raw[:, self._SV] += ((_torch.rand(N, device=self.device) < 0.3).float()
                             * eng_f)

        # Evasion
        evasion_prob = (self._action_evasion[phase, base] * evasion_mult).clamp(max=0.5)
        evasion_prob = _torch.where(raw[:, self._PR] < 0.5, evasion_prob * 2.0,
                                    evasion_prob)
        evasion_hit = _torch.rand(N, device=self.device) < evasion_prob
        raw[:, self._EV] += evasion_hit.float()
        p = raw[:, self._PR]
        raw[:, self._PR] = _torch.where(
            evasion_hit,
            (0.3 * p) / (0.3 * p + (1 - p)),
            p,
        )

        # Phase progression
        raw[:, self._TP] += self._step_duration
        phase_prob = 0.02 * (1 + raw[:, self._TP] / 30.0)
        phase_advance = (_torch.rand(N, device=self.device) < phase_prob) & (raw[:, self._PH] < 3)
        raw[:, self._PH] += phase_advance.float()
        raw[:, self._TP] = _torch.where(phase_advance, _torch.zeros_like(raw[:, self._TP]),
                                        raw[:, self._TP])
        level_up = phase_advance & (raw[:, self._LV] < 4) & (
            _torch.rand(N, device=self.device) < 0.3)
        raw[:, self._LV] += level_up.float()

        # Exploit attempts
        raw[:, self._EX] += ((raw[:, self._PH] >= 1) & (
            _torch.rand(N, device=self.device) < 0.1)).float()

        # Ghost port
        raw[:, self._GH] = _torch.where(base == 2,
                                         (raw[:, self._GH] + 1).clamp(max=5),
                                         raw[:, self._GH])

        # Reward
        r_belief = (delta_p * 8).clamp(-1.0, 1.0)
        r_engage = eng_f
        r_safety = _torch.where(raw[:, self._EV] > 0,
                                -0.3 * (raw[:, self._EV] / 3.0).clamp(max=1.0),
                                _torch.zeros(N, device=self.device))
        rewards = 0.35 * r_belief + 0.25 * r_engage + 0.25 * 0.3 + 0.15 * r_safety

        # Termination
        done_preal = raw[:, self._PR] < 0.2
        done_steps = raw[:, self._SC] >= self.max_steps
        done_evasion = raw[:, self._EV] >= 5

        rewards = _torch.where(done_preal, rewards - 5.0, rewards)
        rewards = _torch.where(done_steps & ~done_preal,
                               rewards + 3.0 + raw[:, self._PR] * 2.0, rewards)
        rewards = _torch.where(done_evasion & ~done_preal & ~done_steps,
                               rewards - 2.0, rewards)

        dones = done_preal | done_steps | done_evasion

        info = {
            "p_real": raw[:, self._PR].clone(),
            "engaged": engaged.clone(),
            "evasion": raw[:, self._EV].clone(),
        }

        obs = self._observe()
        self._reset_idx(dones)
        return obs, rewards, dones, info

    def _observe(self) -> _torch.Tensor:
        """(N, 10) normalized obs, stays on device."""
        raw = self._raw[:, :10]
        return (raw / self._obs_div).clamp(max=1.0)
