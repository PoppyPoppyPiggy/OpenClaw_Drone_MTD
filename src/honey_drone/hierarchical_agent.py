#!/usr/bin/env python3
"""
hierarchical_agent.py — Hierarchical DQN for Agentic Deception

[ARCHITECTURE]
    h-DQN (Kulkarni et al., 2016, NIPS) adapted for deception:

    MetaController (high-level):
        Input:  state (10-dim)
        Output: strategy (6 options)
        Runs every K steps (strategy horizon)

    Controller (low-level):
        Input:  state (10-dim) + strategy one-hot (6-dim) = 16-dim
        Output: parameterized action (45 = 5 base × 3 intensity × 3 variant)

[6 STRATEGIES]
    0: aggressive_engage   — maximize attacker interaction
    1: passive_monitor     — observe without proactive action
    2: identity_shift      — change drone identity (sysid, GPS)
    3: service_expansion   — open ghost ports, breadcrumbs
    4: credential_leak     — plant fake keys/tokens
    5: adaptive_response   — phase-matched optimal response

[45 ACTIONS]
    5 base behaviors × 3 intensity levels × 3 parameter variants
    See deception_env.py for full action table.

[REF] MIRAGE-UAS §4.3 — Hierarchical Deception Policy
"""
from __future__ import annotations

import torch
import torch.nn as nn
import numpy as np

N_STRATEGIES = 6
N_ACTIONS = 45
STATE_DIM = 10
CONTROLLER_INPUT_DIM = STATE_DIM + N_STRATEGIES  # 16

STRATEGY_NAMES = [
    "aggressive_engage",
    "passive_monitor",
    "identity_shift",
    "service_expansion",
    "credential_leak",
    "adaptive_response",
]


class MetaController(nn.Module):
    """
    High-level: selects strategy from state.
    Dueling architecture for stable Q-value estimation.
    """

    def __init__(self, state_dim: int = STATE_DIM, n_strategies: int = N_STRATEGIES, hidden: int = 128):
        super().__init__()
        self.feature = nn.Sequential(
            nn.Linear(state_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        self.value = nn.Sequential(
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, 1),
        )
        self.advantage = nn.Sequential(
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, n_strategies),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feat = self.feature(x)
        val = self.value(feat)
        adv = self.advantage(feat)
        return val + adv - adv.mean(dim=-1, keepdim=True)


class Controller(nn.Module):
    """
    Low-level: selects parameterized action from state + strategy.
    Input: state (10) + strategy one-hot (6) = 16-dim.
    """

    def __init__(self, input_dim: int = CONTROLLER_INPUT_DIM, n_actions: int = N_ACTIONS, hidden: int = 256):
        super().__init__()
        self.feature = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
        )
        self.value = nn.Sequential(
            nn.Linear(hidden // 2, hidden // 4),
            nn.ReLU(),
            nn.Linear(hidden // 4, 1),
        )
        self.advantage = nn.Sequential(
            nn.Linear(hidden // 2, hidden // 4),
            nn.ReLU(),
            nn.Linear(hidden // 4, n_actions),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feat = self.feature(x)
        val = self.value(feat)
        adv = self.advantage(feat)
        return val + adv - adv.mean(dim=-1, keepdim=True)


class HierarchicalDQN(nn.Module):
    """
    Combined h-DQN: MetaController + Controller.

    Usage:
        hdqn = HierarchicalDQN()
        strategy = hdqn.select_strategy(state)
        action = hdqn.select_action(state, strategy)
    """

    def __init__(self):
        super().__init__()
        self.meta = MetaController()
        self.controller = Controller()

    def select_strategy(self, state: torch.Tensor) -> torch.Tensor:
        """Select strategy given state. Returns Q-values for strategies."""
        return self.meta(state)

    def select_action(self, state: torch.Tensor, strategy: int) -> torch.Tensor:
        """Select action given state + strategy. Returns Q-values for actions."""
        batch_size = state.shape[0] if state.dim() > 1 else 1
        if state.dim() == 1:
            state = state.unsqueeze(0)

        # One-hot encode strategy
        strategy_onehot = torch.zeros(batch_size, N_STRATEGIES, device=state.device)
        strategy_onehot[:, strategy] = 1.0

        # Concatenate state + strategy
        controller_input = torch.cat([state, strategy_onehot], dim=-1)
        return self.controller(controller_input)

    def forward(self, state: torch.Tensor, strategy: int = None):
        """Full forward: meta selects strategy, controller selects action."""
        if strategy is None:
            strategy_q = self.meta(state)
            strategy = strategy_q.argmax(dim=-1).item()
        action_q = self.select_action(state, strategy)
        return strategy, action_q


def decode_action(action_idx: int) -> tuple[int, int, int]:
    """
    Decode flat action index (0-44) → (base, intensity, variant).

    base:      0-4 (statustext, flight_sim, ghost_port, reboot, fake_key)
    intensity: 0-2 (low, medium, high)
    variant:   0-2 (parameter variant A, B, C)
    """
    base = action_idx // 9           # 0-4
    remainder = action_idx % 9
    intensity = remainder // 3       # 0-2
    variant = remainder % 3          # 0-2
    return base, intensity, variant


def encode_action(base: int, intensity: int, variant: int) -> int:
    """Encode (base, intensity, variant) → flat action index."""
    return base * 9 + intensity * 3 + variant
