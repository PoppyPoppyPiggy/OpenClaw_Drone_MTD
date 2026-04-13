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
import random
from dataclasses import dataclass, field

import numpy as np


N_ACTIONS = 5
STATE_DIM = 10
ACTION_NAMES = [
    "proactive_statustext",
    "proactive_flight_sim",
    "proactive_ghost_port",
    "proactive_reboot",
    "proactive_fake_key",
]

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
    """

    def __init__(self, max_steps: int = 100) -> None:
        self.max_steps = max_steps
        self.state = AttackerState()
        self._step_duration = 3.0  # seconds per step

        # ══════════════════════════════════════════════════════
        # Action effectiveness matrices
        #
        # Calibrated from real testbed experiment data:
        #   - confusion_honey_*.json: P(real) changes per behavior
        #   - table_vi_agent_decisions.json: dwell impact per behavior
        #   - attacker_log.jsonl: engagement rates per level
        #
        # Each cell = P(real) change when action is taken in that phase
        # Positive = attacker believes more (good deception)
        # Negative = attacker suspects honeypot (bad)
        #
        # The DQN must learn THIS mapping from experience.
        # ══════════════════════════════════════════════════════

        # [phase][action] → Δ P(real)
        self._action_effect = np.array([
            #  statustext  flight_sim  ghost_port  reboot    fake_key
            [  0.04,       0.06,       0.01,       -0.01,    0.02  ],  # RECON: show activity → flight best
            [  0.01,       0.03,       0.03,       0.05,     0.07  ],  # EXPLOIT: bait creds → fake_key best
            [  0.05,       0.02,       0.03,       0.06,     0.04  ],  # PERSIST: confuse → reboot best
            [ -0.02,       -0.01,      0.02,       0.04,     0.06  ],  # EXFIL: leak data → fake_key best
        ])

        # [phase][action] → P(attacker sends more packets after our action)
        self._action_engage = np.array([
            [  0.7,        0.85,       0.3,        0.15,     0.4   ],  # RECON
            [  0.3,        0.4,        0.5,        0.6,      0.85  ],  # EXPLOIT
            [  0.5,        0.3,        0.4,        0.5,      0.55  ],  # PERSIST
            [  0.15,       0.1,        0.25,       0.4,      0.75  ],  # EXFIL
        ])

        # [phase][action] → P(attacker becomes suspicious)
        # reboot is risky (silence = suspicious), ghost in EXFIL = suspicious
        self._action_evasion = np.array([
            [  0.01,       0.01,       0.04,       0.10,     0.02  ],  # RECON
            [  0.03,       0.02,       0.05,       0.12,     0.04  ],  # EXPLOIT
            [  0.02,       0.04,       0.06,       0.15,     0.03  ],  # PERSIST
            [  0.06,       0.08,       0.08,       0.18,     0.04  ],  # EXFIL
        ])

    def reset(self) -> np.ndarray:
        """Reset to new episode (new attacker campaign)."""
        self.state = AttackerState()
        # Randomize starting conditions
        self.state.level = random.randint(0, 2)
        self.state.p_real = 0.7 + random.gauss(0, 0.05)
        return self._observe()

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        """
        Execute one step.

        Args:
            action: int 0-4

        Returns:
            (next_state, reward, done, info)
        """
        s = self.state
        s.step_count += 1
        s.dwell_sec += self._step_duration
        phase = min(s.phase, 3)

        # ── 1. Apply action effect on P(real) ──
        base_effect = self._action_effect[phase, action]
        noise = random.gauss(0, 0.01)
        delta_p = base_effect + noise

        # Bayesian update simulation
        if delta_p > 0:
            lr = 1.0 + delta_p * 10  # positive → LR > 1
        else:
            lr = max(0.1, 1.0 + delta_p * 10)  # negative → LR < 1

        prior = max(0.01, min(0.99, s.p_real))
        s.p_real = (lr * prior) / (lr * prior + 1.0 * (1 - prior))

        # ── 2. Engagement: does attacker respond? ──
        engage_prob = self._action_engage[phase, action]
        engaged = random.random() < engage_prob
        if engaged:
            new_pkts = random.randint(1, 5)
            s.packets += new_pkts
            s.services_touched += 1 if random.random() < 0.3 else 0

        # ── 3. Evasion: does attacker become suspicious? ──
        evasion_prob = self._action_evasion[phase, action]
        # Evasion increases as p_real drops
        if s.p_real < 0.5:
            evasion_prob *= 2.0
        if random.random() < evasion_prob:
            s.evasion_signals += 1
            # Bayesian update with evasion LR=0.3
            s.p_real = (0.3 * s.p_real) / (0.3 * s.p_real + 1.0 * (1 - s.p_real))

        # ── 4. Phase progression (stochastic) ──
        s.time_in_phase += self._step_duration
        phase_advance_prob = 0.02 * (1 + s.time_in_phase / 30.0)
        if random.random() < phase_advance_prob and s.phase < 3:
            s.phase += 1
            s.time_in_phase = 0.0
            # Level may increase with phase
            if s.level < 4 and random.random() < 0.3:
                s.level += 1

        # Exploit attempts increase in EXPLOIT/PERSIST phases
        if s.phase >= 1 and random.random() < 0.1:
            s.exploit_attempts += 1

        # Ghost port tracking
        if action == 2:  # ghost_port
            s.ghost_active = min(s.ghost_active + 1, 5)

        # ── 5. Compute reward ──
        reward = self._compute_reward(delta_p, engaged, s)

        # ── 6. Check termination ──
        done = False
        if s.p_real < 0.2:  # attacker left (detected honeypot)
            done = True
            reward -= 5.0  # severe penalty — deception failed
        elif s.step_count >= self.max_steps:
            done = True
            reward += 3.0 + s.p_real * 2.0  # survived + belief bonus (max +5)
        elif s.evasion_signals >= 5:
            done = True
            reward -= 2.0  # attacker suspicious, left

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

        belief:  Δp_real → deception quality
        engage:  attacker response → interaction maintained
        dwell:   per-step survival bonus → longer = better
        safety:  evasion penalty → suspicion = bad

        Theoretical max per step: ~0.85
        Theoretical max per episode (100 steps): ~85
        """
        r_belief = max(-1.0, min(1.0, delta_p * 8))
        r_engage = 1.0 if engaged else 0.0
        r_dwell = 0.3  # meaningful per-step survival bonus
        r_safety = -0.5 if s.evasion_signals > 0 and s.evasion_signals == (s.step_count > 0) else 0.0
        # Progressive safety penalty
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
