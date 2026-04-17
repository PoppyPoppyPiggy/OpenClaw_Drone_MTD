#!/usr/bin/env python3
"""
behavior_learner.py — Learned Policy Selector for Proactive Behaviors

Project  : MIRAGE-UAS
Module   : Honey Drone / Behavior Learner

[ROLE]
    3가지 모드로 동작:
    1. DQN 정책 (dqn_deception_agent.pt 존재 시) — GPU/CPU 추론
    2. Greedy 정책 (DQN 없을 때 fallback) — phase별 최적 행동
    3. Random 정책 (POLICY_MODE=random 설정 시) — 실험 baseline

[PIPELINE]
    train_dqn.py (GPU 학습, 시뮬레이터)
    → results/models/dqn_deception_agent.pt
    → BehaviorLearner (컨테이너 내 추론)
    → OpenClawAgent._proactive_loop()
    → 실제 허니드론에서 행동 실행

[CONTEXT → ACTION]
    state[0]: phase/3       → RECON/EXPLOIT/PERSIST/EXFIL
    state[1]: level/4       → L0-L4
    state[2]: p_real        → Bayesian belief
    state[3]: dwell/600     → 체류시간
    state[4]: packets/100   → 패킷 수
    state[5]: services/10   → 서비스 접촉
    state[6]: exploits/5    → exploit 시도
    state[7]: ghost/5       → ghost 포트
    state[8]: time_phase/120 → 단계 경과시간
    state[9]: evasion/3     → 회피 신호
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

from shared.logger import get_logger

logger = get_logger(__name__)

ACTIONS = [
    "proactive_statustext",
    "proactive_flight_sim",
    "proactive_ghost_port",
    "proactive_reboot",
    "proactive_fake_key",
]
N_ACTIONS = len(ACTIONS)
STATE_DIM = 10


class BehaviorLearner:
    """
    Selects proactive deception behavior using learned DQN policy.
    Falls back to greedy heuristic if no trained model available.
    """

    def __init__(
        self,
        drone_id: str = "",
        model_dir: str = "results/models",
        policy_mode: str = "auto",  # "auto" / "hdqn" / "dqn" / "greedy" / "random"
        **kwargs,  # backward compat (alpha etc)
    ) -> None:
        self._drone_id = drone_id
        self._model_dir = Path(model_dir)
        self._policy_mode = policy_mode
        self._dqn_net = None
        self._hdqn = None
        self._device = None
        self._game_eq_loaded = False
        self._step_count = 0
        self._current_strategy = None
        self._strategy_steps = 0
        self._strategy_horizon = 10

        # Stats tracking
        self._total_selections = [0] * N_ACTIONS
        self._total_reward = [0.0] * N_ACTIONS

        # Greedy phase→action map (from compare_policies.py results)
        self._greedy_map = {
            0: 1,  # RECON  → flight_sim
            1: 4,  # EXPLOIT → fake_key
            2: 0,  # PERSIST → statustext
            3: 4,  # EXFIL  → fake_key
        }

        # Try loading: h-DQN → game-eq → DQN (priority order)
        if policy_mode in ("auto", "hdqn"):
            self._try_load_hdqn()
        if self._hdqn is None and policy_mode in ("auto", "game", "dqn"):
            self._try_load_game_eq()
        if self._hdqn is None and self._dqn_net is None and policy_mode in ("auto", "dqn"):
            self._try_load_dqn()

        if self._hdqn:
            mode_str = "h-DQN"
        elif self._dqn_net:
            mode_str = "Game-EQ" if self._game_eq_loaded else "DQN"
        elif policy_mode == "random":
            mode_str = "random"
        else:
            mode_str = "greedy"
        logger.info(
            "behavior_learner_initialized",
            drone_id=drone_id,
            policy_mode=mode_str,
            model_dir=str(self._model_dir),
            hdqn_loaded=self._hdqn is not None,
            dqn_loaded=self._dqn_net is not None,
        )

    def _try_load_hdqn(self) -> None:
        """Load h-DQN policy from checkpoint if available."""
        model_path = self._model_dir / "hdqn_deception_agent.pt"
        if not model_path.exists():
            logger.info("hdqn_model_not_found", path=str(model_path))
            return

        try:
            import torch
            from honey_drone.hierarchical_agent import HierarchicalDQN

            from honey_drone.hierarchical_agent import MetaController, Controller

            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            ckpt = torch.load(str(model_path), map_location=self._device, weights_only=False)
            # Auto-detect network size from checkpoint
            meta_h = ckpt["meta_state_dict"]["feature.0.weight"].shape[0]
            ctrl_h = ckpt["controller_state_dict"]["feature.0.weight"].shape[0]
            self._hdqn = HierarchicalDQN.__new__(HierarchicalDQN)
            torch.nn.Module.__init__(self._hdqn)
            self._hdqn.meta = MetaController(hidden=meta_h)
            self._hdqn.controller = Controller(hidden=ctrl_h)
            self._hdqn.meta.load_state_dict(ckpt["meta_state_dict"])
            self._hdqn.controller.load_state_dict(ckpt["controller_state_dict"])
            self._hdqn = self._hdqn.to(self._device)
            self._hdqn.eval()

            train_ep = ckpt.get("episode", "?")
            train_reward = ckpt.get("avg_reward", "?")
            logger.info(
                "hdqn_policy_loaded",
                drone_id=self._drone_id,
                path=str(model_path),
                trained_episodes=train_ep,
                trained_avg_reward=train_reward,
                n_strategies=ckpt.get("n_strategies", 6),
                n_actions=ckpt.get("n_actions", 45),
                device=str(self._device),
            )
        except Exception as e:
            logger.warning("hdqn_load_failed", error=str(e))
            self._hdqn = None

    def _try_load_game_eq(self) -> None:
        """Load game-theoretic equilibrium defender policy if available."""
        model_path = self._model_dir / "game_defender_final.pt"
        if not model_path.exists():
            return

        try:
            import torch
            import torch.nn as nn

            class DQN(nn.Module):
                def __init__(self, state_dim, n_actions, hidden=128):
                    super().__init__()
                    self.feature = nn.Sequential(
                        nn.Linear(state_dim, hidden), nn.ReLU(),
                        nn.Linear(hidden, hidden), nn.ReLU(),
                    )
                    self.value = nn.Sequential(
                        nn.Linear(hidden, hidden // 2), nn.ReLU(),
                        nn.Linear(hidden // 2, 1),
                    )
                    self.advantage = nn.Sequential(
                        nn.Linear(hidden, hidden // 2), nn.ReLU(),
                        nn.Linear(hidden // 2, n_actions),
                    )
                def forward(self, x):
                    feat = self.feature(x)
                    val = self.value(feat)
                    adv = self.advantage(feat)
                    return val + adv - adv.mean(dim=-1, keepdim=True)

            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            ckpt = torch.load(str(model_path), map_location=self._device, weights_only=False)
            state_dim = ckpt.get("state_dim", STATE_DIM)
            n_actions = ckpt.get("n_actions", N_ACTIONS)
            self._dqn_net = DQN(state_dim, n_actions).to(self._device)
            self._dqn_net.load_state_dict(ckpt["policy_state_dict"])
            self._dqn_net.eval()
            self._game_eq_loaded = True

            logger.info(
                "game_eq_policy_loaded",
                drone_id=self._drone_id,
                path=str(model_path),
                training_rounds=ckpt.get("training_rounds", "?"),
                skills=ckpt.get("skills", []),
                device=str(self._device),
            )
        except Exception as e:
            logger.warning("game_eq_load_failed", error=str(e))

    def _try_load_dqn(self) -> None:
        """Load DQN policy from checkpoint if available."""
        model_path = self._model_dir / "dqn_deception_agent.pt"
        if not model_path.exists():
            logger.info("dqn_model_not_found", path=str(model_path), fallback="greedy")
            return

        try:
            import torch
            import torch.nn as nn

            # Rebuild network architecture (must match train_dqn.py)
            class DQN(nn.Module):
                def __init__(self, state_dim, n_actions, hidden=128):
                    super().__init__()
                    self.feature = nn.Sequential(
                        nn.Linear(state_dim, hidden), nn.ReLU(),
                        nn.Linear(hidden, hidden), nn.ReLU(),
                    )
                    self.value = nn.Sequential(
                        nn.Linear(hidden, hidden // 2), nn.ReLU(),
                        nn.Linear(hidden // 2, 1),
                    )
                    self.advantage = nn.Sequential(
                        nn.Linear(hidden, hidden // 2), nn.ReLU(),
                        nn.Linear(hidden // 2, n_actions),
                    )
                def forward(self, x):
                    feat = self.feature(x)
                    val = self.value(feat)
                    adv = self.advantage(feat)
                    return val + adv - adv.mean(dim=-1, keepdim=True)

            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self._dqn_net = DQN(STATE_DIM, N_ACTIONS).to(self._device)
            ckpt = torch.load(str(model_path), map_location=self._device, weights_only=False)
            self._dqn_net.load_state_dict(ckpt["policy_state_dict"])
            self._dqn_net.eval()

            train_ep = ckpt.get("episode", "?")
            train_reward = ckpt.get("avg_reward", "?")
            logger.info(
                "dqn_policy_loaded",
                drone_id=self._drone_id,
                path=str(model_path),
                trained_episodes=train_ep,
                trained_avg_reward=train_reward,
                device=str(self._device),
            )
        except Exception as e:
            logger.warning("dqn_load_failed", error=str(e), fallback="greedy")
            self._dqn_net = None

    def select_action(self, context: dict) -> tuple[int, str, dict]:
        """
        Select action based on current policy.

        Args:
            context: dict with attack state features

        Returns:
            (action_index, action_name, debug_info)
        """
        self._step_count += 1
        state = self._context_to_state(context)

        if self._hdqn is not None:
            action_idx, debug = self._select_hdqn(state, context)
        elif self._dqn_net is not None:
            action_idx, debug = self._select_dqn(state, context)
        elif self._policy_mode == "random":
            action_idx = np.random.randint(N_ACTIONS)
            debug = {"mode": "random"}
        else:
            action_idx, debug = self._select_greedy(state, context)

        action_name = ACTIONS[action_idx]
        self._total_selections[action_idx] += 1

        logger.info(
            "mab_action_selected",
            drone_id=self._drone_id,
            action=action_name,
            mode=debug.get("mode", "?"),
            phase=context.get("phase_name", "?"),
            step=self._step_count,
            selections=self._total_selections.copy(),
            **{k: v for k, v in debug.items() if k != "mode"},
        )

        return action_idx, action_name, debug

    def _select_hdqn(self, state: np.ndarray, context: dict) -> tuple[int, dict]:
        """h-DQN: MetaController selects strategy, Controller selects action.
        Maps 45-action back to base 5 for the proactive loop."""
        import torch
        from honey_drone.hierarchical_agent import STRATEGY_NAMES, N_STRATEGIES

        self._strategy_steps += 1
        # Select strategy every K steps
        if self._current_strategy is None or self._strategy_steps >= self._strategy_horizon:
            with torch.no_grad():
                s_t = torch.FloatTensor(state).unsqueeze(0).to(self._device)
                strategy_q = self._hdqn.meta(s_t).squeeze(0).cpu().numpy()
            self._current_strategy = int(np.argmax(strategy_q))
            self._strategy_steps = 0

        # Select parameterized action
        with torch.no_grad():
            s_t = torch.FloatTensor(state).unsqueeze(0).to(self._device)
            action_q = self._hdqn.select_action(s_t, self._current_strategy)
            flat_action = action_q.argmax(dim=1).item()

        # Decode to base action (0-4) for the proactive loop
        base_action = flat_action // 9

        strategy_name = STRATEGY_NAMES[self._current_strategy]
        return base_action, {
            "mode": "h-DQN",
            "strategy": strategy_name,
            "strategy_idx": self._current_strategy,
            "flat_action": flat_action,
            "base_action": base_action,
        }

    def _select_dqn(self, state: np.ndarray, context: dict) -> tuple[int, dict]:
        """DQN forward pass → action with highest Q-value."""
        import torch
        with torch.no_grad():
            t = torch.FloatTensor(state).unsqueeze(0).to(self._device)
            q_values = self._dqn_net(t).squeeze(0).cpu().numpy()

        action_idx = int(np.argmax(q_values))
        return action_idx, {
            "mode": "DQN",
            "q_values": {ACTIONS[i]: round(float(q_values[i]), 4) for i in range(N_ACTIONS)},
            "q_best": round(float(q_values[action_idx]), 4),
        }

    def _select_greedy(self, state: np.ndarray, context: dict) -> tuple[int, dict]:
        """Phase-based greedy heuristic."""
        phase = int(round(state[0] * 3))
        action_idx = self._greedy_map.get(phase, 1)
        return action_idx, {
            "mode": "greedy",
            "phase": phase,
        }

    def update(self, action_idx: int, reward: float, context: dict) -> None:
        """Record reward (for logging, DQN doesn't online-update)."""
        self._total_reward[action_idx] += reward
        logger.info(
            "mab_reward_update",
            drone_id=self._drone_id,
            action=ACTIONS[action_idx],
            reward=round(reward, 4),
            avg_reward=round(
                self._total_reward[action_idx] / max(self._total_selections[action_idx], 1), 4
            ),
            total_steps=self._step_count,
        )

    def get_stats(self) -> dict:
        """Return policy statistics."""
        total = sum(self._total_selections)
        if self._hdqn:
            mode = "h-DQN"
        elif self._dqn_net:
            mode = "DQN"
        elif self._policy_mode == "random":
            mode = "random"
        else:
            mode = "greedy"
        return {
            "mode": mode,
            "total_steps": total,
            "per_arm": {
                ACTIONS[i]: {
                    "selections": self._total_selections[i],
                    "avg_reward": round(self._total_reward[i] / max(self._total_selections[i], 1), 4),
                    "pct": round(self._total_selections[i] / max(total, 1) * 100, 1),
                }
                for i in range(N_ACTIONS)
            },
        }

    def _context_to_state(self, ctx: dict) -> np.ndarray:
        """Convert context dict to DQN-compatible 10-dim state vector.

        Uses actual phase from OpenClawAgent fingerprint (phase_val key)
        instead of inferring from max_level.
        """
        # Use actual phase from agent's phase detection (0-3 int)
        phase_val = ctx.get("phase_val", 0)
        phase_names = {0: "recon", 1: "exploit", 2: "persist", 3: "exfil"}
        ctx["phase_name"] = phase_names.get(phase_val, "recon")

        return np.array([
            phase_val / 3.0,
            min(ctx.get("max_level", 0) / 4.0, 1.0),
            ctx.get("avg_p_real", 0.7),
            min(ctx.get("avg_dwell_sec", 0) / 600.0, 1.0),
            min(ctx.get("avg_commands", 0) / 100.0, 1.0),
            min(ctx.get("services_touched", 0) / 10.0, 1.0),
            min(ctx.get("exploit_attempts", 0) / 5.0, 1.0),
            min(ctx.get("ghost_active", 0) / 5.0, 1.0),
            min(ctx.get("time_in_phase", 0) / 120.0, 1.0),
            min(ctx.get("evasion_signals", 0) / 3.0, 1.0),
        ], dtype=np.float32)

    def _save_model(self) -> None:
        """Save runtime stats (DQN model is saved by train_dqn.py)."""
        self._model_dir.mkdir(parents=True, exist_ok=True)
        path = self._model_dir / f"policy_stats_{self._drone_id}.json"
        path.write_text(json.dumps(self.get_stats(), indent=2))
