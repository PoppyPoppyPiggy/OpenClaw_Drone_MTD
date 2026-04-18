#!/usr/bin/env python3
"""
compare_policies.py — 4-Policy Comparison Experiment

Compares: Random / Greedy / DQN / Full Agent
on identical DeceptionEnv episodes for fair evaluation.

Usage:
    python3 scripts/compare_policies.py
    python3 scripts/compare_policies.py --episodes 1000 --output results/policy_comparison.json

Output:
    Table + JSON with per-policy metrics for paper Table VII
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from honey_drone.deception_env import (
    DeceptionEnv, STATE_DIM, N_ACTIONS, ACTION_NAMES,
)

# ═══════════════════════════════════════════════════════════════
# 4 Policies
# ═══════════════════════════════════════════════════════════════

class RandomPolicy:
    """Uniform random — baseline."""
    name = "Random"
    def select(self, state: np.ndarray) -> int:
        return np.random.randint(N_ACTIONS)


class GreedyPolicy:
    """Rule-based heuristic — best action per phase (expert knowledge)."""
    name = "Greedy"
    # phase → best action index (domain expert handpicked)
    _phase_map = {
        0: 1,  # RECON  → flight_sim (highest base effect 0.05)
        1: 4,  # EXPLOIT → fake_key (highest base effect 0.06)
        2: 3,  # PERSIST → reboot (highest base effect 0.05)
        3: 4,  # EXFIL  → fake_key (highest base effect 0.04)
    }
    def select(self, state: np.ndarray) -> int:
        phase = int(round(state[0] * 3))  # denormalize
        return self._phase_map.get(phase, 1)


class DQNPolicy:
    """DQN-trained policy — loaded from checkpoint."""
    name = "DQN"
    def __init__(self, model_path: str, device: str = "cuda"):
        import importlib.util
        spec = importlib.util.spec_from_file_location("train_dqn", Path(__file__).parent / "train_dqn.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        DQN = mod.DQN
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        ckpt = torch.load(model_path, map_location=self.device, weights_only=False)
        # Auto-detect state_dim from checkpoint
        ckpt_state_dim = ckpt["policy_state_dict"]["feature.0.weight"].shape[1]
        self._state_dim = ckpt_state_dim
        self.net = DQN(ckpt_state_dim, N_ACTIONS).to(self.device)
        self.net.load_state_dict(ckpt["policy_state_dict"])
        self.net.eval()
        self._train_ep = ckpt.get("episode", "?")
        self._train_reward = ckpt.get("avg_reward", "?")

    @property
    def uses_legacy_env(self):
        """DQN was trained on 10-dim state, needs legacy env."""
        return self._state_dim == 10

    def select(self, state: np.ndarray) -> int:
        with torch.no_grad():
            t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            return self.net(t).argmax(dim=1).item()


class GameEQPolicy:
    """Game-theoretic equilibrium defender — trained via alternating best-response."""
    name = "Game-EQ"
    def __init__(self, model_path: str, device: str = "cuda"):
        import importlib.util
        spec = importlib.util.spec_from_file_location("train_dqn", Path(__file__).parent / "train_dqn.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        DQN = mod.DQN
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        ckpt = torch.load(model_path, map_location=self.device, weights_only=False)
        state_dim = ckpt.get("state_dim", 10)
        n_actions = ckpt.get("n_actions", 5)
        hidden = ckpt["policy_state_dict"]["feature.0.weight"].shape[0]
        self.net = DQN(state_dim, n_actions, hidden=hidden).to(self.device)
        self.net.load_state_dict(ckpt["policy_state_dict"])
        self.net.eval()
        self._skills = ckpt.get("skills", [])

    def select(self, state: np.ndarray) -> int:
        with torch.no_grad():
            t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            return self.net(t).argmax(dim=1).item()


class HDQNPolicy:
    """Hierarchical DQN — MetaController + Controller on 45 parameterized actions."""
    name = "h-DQN"
    def __init__(self, model_path: str, device: str = "cuda"):
        from honey_drone.hierarchical_agent import (
            HierarchicalDQN, MetaController, Controller, N_STRATEGIES,
            STATE_DIM, CONTROLLER_INPUT_DIM, N_ACTIONS,
        )
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        ckpt = torch.load(model_path, map_location=self.device, weights_only=False)
        # Auto-detect network size from checkpoint weights
        meta_h = ckpt["meta_state_dict"]["feature.0.weight"].shape[0]
        ctrl_h = ckpt["controller_state_dict"]["feature.0.weight"].shape[0]
        self.hdqn = HierarchicalDQN.__new__(HierarchicalDQN)
        torch.nn.Module.__init__(self.hdqn)
        self.hdqn.meta = MetaController(hidden=meta_h)
        self.hdqn.controller = Controller(hidden=ctrl_h)
        self.hdqn.meta.load_state_dict(ckpt["meta_state_dict"])
        self.hdqn.controller.load_state_dict(ckpt["controller_state_dict"])
        self.hdqn = self.hdqn.to(self.device)
        self.hdqn.eval()
        self._strategy = None
        self._strategy_steps = 0
        self._horizon = 10
        self._train_ep = ckpt.get("episode", "?")

    def select(self, state: np.ndarray) -> int:
        """Returns flat 45-action index. Caller must use param-mode env."""
        self._strategy_steps += 1
        if self._strategy is None or self._strategy_steps >= self._horizon:
            with torch.no_grad():
                s_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                self._strategy = self.hdqn.meta(s_t).argmax(dim=1).item()
            self._strategy_steps = 0
        with torch.no_grad():
            s_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            action_q = self.hdqn.select_action(s_t, self._strategy)
            return action_q.argmax(dim=1).item()

    @property
    def uses_param_env(self):
        return True


# ═══════════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════════

def _evaluate_hdqn_vec(policy, n_episodes: int, seed: int = 42):
    """Evaluate h-DQN using VecDeceptionEnv (64-dim state)."""
    from honey_drone.deception_env import VecDeceptionEnv, N_BASE_ACTIONS, decode_action
    np.random.seed(seed)

    n_envs = min(n_episodes, 512)
    env = VecDeceptionEnv(n_envs=n_envs, max_steps=200)
    states = env.reset_all()

    strats = np.full(n_envs, -1, dtype=np.int32)
    strat_steps = np.zeros(n_envs, dtype=np.int32)
    horizon = 10
    env_rewards = np.zeros(n_envs, dtype=np.float32)
    env_lengths = np.zeros(n_envs, dtype=np.int32)
    done_mask = np.zeros(n_envs, dtype=bool)

    rewards_all = []
    lengths_all = []
    p_reals_all = []
    survivals_all = []
    action_counts = np.zeros(N_ACTIONS)
    phase_actions = {p: np.zeros(N_ACTIONS) for p in range(4)}
    device = policy.device

    while len(rewards_all) < n_episodes:
        # Strategy selection
        need = (strats < 0) | (strat_steps >= horizon)
        idx = np.where(need & ~done_mask)[0]
        if len(idx) > 0:
            with torch.no_grad():
                s_t = torch.from_numpy(states[idx]).to(device)
                strats[idx] = policy.hdqn.meta(s_t).argmax(dim=1).cpu().numpy()
            strat_steps[idx] = 0

        # Action selection
        active = np.where(~done_mask)[0]
        if len(active) == 0:
            # Reset all for next batch
            states = env.reset_all()
            strats[:] = -1
            strat_steps[:] = 0
            env_rewards[:] = 0
            env_lengths[:] = 0
            done_mask[:] = False
            continue

        actions = np.zeros(n_envs, dtype=np.int32)
        with torch.no_grad():
            s_t = torch.from_numpy(states[active]).to(device)
            from honey_drone.hierarchical_agent import N_STRATEGIES
            oh = torch.zeros(len(active), N_STRATEGIES, device=device)
            si = torch.from_numpy(strats[active].clip(0).astype(np.int64)).to(device)
            oh.scatter_(1, si.unsqueeze(1), 1.0)
            ci = torch.cat([s_t, oh], dim=1)
            actions[active] = policy.hdqn.controller(ci).argmax(dim=1).cpu().numpy()

        for i in active:
            base = actions[i] // 9
            action_counts[base] += 1
            phase = int(round(states[i][0] * 3)) if states.shape[1] <= 10 else 0
            # For 64-dim state, phase is one-hot in dims 0-3
            if states.shape[1] > 10:
                phase = int(np.argmax(states[i][:4]))
            phase_actions[phase][base] += 1

        next_states, r, dones, info = env.step(actions)
        env_rewards += r * (~done_mask)
        env_lengths += (~done_mask).astype(np.int32)
        strat_steps += 1
        states = next_states

        newly_done = dones & ~done_mask
        if newly_done.any():
            idx_d = np.where(newly_done)[0]
            for i in idx_d:
                rewards_all.append(env_rewards[i])
                lengths_all.append(env_lengths[i])
                p_reals_all.append(info["p_real"][i])
                survivals_all.append(env_lengths[i] >= 200)
            done_mask |= dones

            if len(rewards_all) >= n_episodes:
                break

        # If all done, reset batch
        if done_mask.all():
            states = env.reset_all()
            strats[:] = -1
            strat_steps[:] = 0
            env_rewards[:] = 0
            env_lengths[:] = 0
            done_mask[:] = False

    rewards_all = rewards_all[:n_episodes]
    lengths_all = lengths_all[:n_episodes]
    p_reals_all = p_reals_all[:n_episodes]
    survivals_all = survivals_all[:n_episodes]

    total_acts = max(action_counts.sum(), 1)
    tp = sum(1 for r in rewards_all if r >= 40)
    fp = sum(1 for r in rewards_all if 10 <= r < 40)
    fn = sum(1 for r in rewards_all if r < 10)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-8)

    return {
        "policy": "h-DQN",
        "episodes": len(rewards_all),
        "avg_reward": round(float(np.mean(rewards_all)), 4),
        "std_reward": round(float(np.std(rewards_all)), 4),
        "avg_length": round(float(np.mean(lengths_all)), 1),
        "avg_p_real": round(float(np.mean(p_reals_all)), 4),
        "survival_rate": round(float(np.mean(survivals_all)), 4),
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "tp": tp, "fp": fp, "fn": fn,
        "action_distribution": {
            ACTION_NAMES[i]: round(action_counts[i] / total_acts * 100, 1)
            for i in range(N_ACTIONS)
        },
        "phase_preference": {
            f"phase_{p}": ACTION_NAMES[int(phase_actions[p].argmax())]
            for p in range(4)
        },
        "rewards_raw": [round(r, 2) for r in rewards_all],
    }

def evaluate_policy(policy, env: DeceptionEnv, n_episodes: int, seed: int = 42):
    """Run n_episodes and collect metrics."""
    np.random.seed(seed)

    use_param = getattr(policy, "uses_param_env", False)
    use_legacy = getattr(policy, "uses_legacy_env", False)

    if use_param and not use_legacy:
        # h-DQN with 64-dim state — use VecDeceptionEnv for correct state dim
        from honey_drone.deception_env import VecDeceptionEnv
        return _evaluate_hdqn_vec(policy, n_episodes, seed)
    elif use_param:
        eval_env = DeceptionEnv(max_steps=env.max_steps, action_mode="param")
    else:
        eval_env = env

    rewards = []
    lengths = []
    p_reals = []
    survivals = []
    action_counts = np.zeros(N_ACTIONS)
    phase_actions = {p: np.zeros(N_ACTIONS) for p in range(4)}

    for ep in range(n_episodes):
        state = eval_env.reset()
        total_r = 0.0
        steps = 0
        while True:
            action = policy.select(state)
            # Map 45-action to base 5 for stats tracking
            base_action = action // 9 if use_param else action
            base_action = min(base_action, N_ACTIONS - 1)
            action_counts[base_action] += 1
            phase = int(round(state[0] * 3))
            phase_actions[phase][base_action] += 1

            state, reward, done, info = eval_env.step(action)
            total_r += reward
            steps += 1
            if done:
                break

        rewards.append(total_r)
        lengths.append(steps)
        p_reals.append(info.get("p_real", 0))
        survivals.append(steps >= eval_env.max_steps)

    total_acts = max(action_counts.sum(), 1)

    # F1: deception effectiveness classification
    tp = sum(1 for r in rewards if r >= 40)
    fp = sum(1 for r in rewards if 10 <= r < 40)
    fn = sum(1 for r in rewards if r < 10)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-8)

    return {
        "policy": policy.name,
        "episodes": n_episodes,
        "avg_reward": round(float(np.mean(rewards)), 4),
        "std_reward": round(float(np.std(rewards)), 4),
        "avg_length": round(float(np.mean(lengths)), 1),
        "avg_p_real": round(float(np.mean(p_reals)), 4),
        "survival_rate": round(float(np.mean(survivals)), 4),
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "tp": tp, "fp": fp, "fn": fn,
        "action_distribution": {
            ACTION_NAMES[i]: round(action_counts[i] / total_acts * 100, 1)
            for i in range(N_ACTIONS)
        },
        "phase_preference": {
            f"phase_{p}": ACTION_NAMES[int(phase_actions[p].argmax())]
            for p in range(4)
        },
        "rewards_raw": [round(r, 2) for r in rewards],
    }


def print_comparison(results: list[dict]):
    """Pretty print comparison table."""
    print()
    print("  ╔════════════════╦══════════╦════════╦═════════╦══════════╦══════════╦════════╦════════╦════════╗")
    print("  ║ Policy         ║ Avg R    ║ Std R  ║ P(real) ║ Survive% ║ Avg Len  ║   P    ║   R    ║  F1    ║")
    print("  ╠════════════════╬══════════╬════════╬═════════╬══════════╬══════════╬════════╬════════╬════════╣")
    for r in results:
        name = r["policy"]
        print(f"  ║ {name:14s} ║ {r['avg_reward']:8.2f} ║ {r['std_reward']:6.2f} ║ "
              f"{r['avg_p_real']:7.4f} ║ {r['survival_rate']*100:7.1f}% ║ {r['avg_length']:8.1f} ║"
              f" {r['precision']:.4f} ║ {r['recall']:.4f} ║ {r['f1']:.4f} ║")
    print("  ╚════════════════╩══════════╩════════╩═════════╩══════════╩══════════╩════════╩════════╩════════╝")

    # Action distribution
    print()
    print("  Action Distribution (%):")
    print("  ┌────────────────┬──────────┬──────────┬──────────┬──────────┬──────────┐")
    print("  │ Policy         │ stxt     │ flight   │ ghost    │ reboot   │ fakekey  │")
    print("  ├────────────────┼──────────┼──────────┼──────────┼──────────┼──────────┤")
    for r in results:
        ad = r["action_distribution"]
        vals = [ad[a] for a in ACTION_NAMES]
        best_idx = np.argmax(vals)
        cells = []
        for i, v in enumerate(vals):
            marker = " ◀" if i == best_idx else "  "
            cells.append(f"{v:5.1f}%{marker}")
        print(f"  │ {r['policy']:14s} │ {cells[0]} │ {cells[1]} │ {cells[2]} │ {cells[3]} │ {cells[4]} │")
    print("  └────────────────┴──────────┴──────────┴──────────┴──────────┴──────────┘")

    # Phase preferences
    print()
    print("  Learned Phase Strategy:")
    print("  ┌────────────────┬──────────────┬──────────────┬──────────────┬──────────────┐")
    print("  │ Policy         │ RECON        │ EXPLOIT      │ PERSIST      │ EXFIL        │")
    print("  ├────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤")
    for r in results:
        pp = r["phase_preference"]
        p0 = pp.get("phase_0", "?")[:12]
        p1 = pp.get("phase_1", "?")[:12]
        p2 = pp.get("phase_2", "?")[:12]
        p3 = pp.get("phase_3", "?")[:12]
        print(f"  │ {r['policy']:14s} │ {p0:12s} │ {p1:12s} │ {p2:12s} │ {p3:12s} │")
    print("  └────────────────┴──────────────┴──────────────┴──────────────┴──────────────┘")

    # Statistical significance
    print()
    if len(results) >= 2:
        from scipy.stats import mannwhitneyu
        baseline = results[0]["rewards_raw"]
        print("  Statistical Significance (Mann-Whitney U vs Random):")
        for r in results[1:]:
            stat, p = mannwhitneyu(r["rewards_raw"], baseline, alternative="greater")
            sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
            cohen_d = (np.mean(r["rewards_raw"]) - np.mean(baseline)) / np.sqrt(
                (np.std(r["rewards_raw"])**2 + np.std(baseline)**2) / 2)
            print(f"    {r['policy']:14s} vs Random: U={stat:.0f}, p={p:.6f} {sig}, Cohen's d={cohen_d:.3f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=500)
    parser.add_argument("--model", type=str, default="results/models/dqn_deception_agent.pt")
    parser.add_argument("--output", type=str, default="results/policy_comparison.json")
    args = parser.parse_args()

    print("╔═══════════════════════════════════════════════════╗")
    print("║  MIRAGE-UAS  4-Policy Comparison                 ║")
    print("╠═══════════════════════════════════════════════════╣")
    print(f"║  Episodes:  {args.episodes:<38}║")
    print("║  Policies:  Random / Greedy / DQN / h-DQN        ║")
    print("╚═══════════════════════════════════════════════════╝")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n  Device: {device}")

    env = DeceptionEnv(max_steps=200)

    # Load policies
    policies = [RandomPolicy()]
    policies.append(GreedyPolicy())

    model_path = Path(args.model)
    if model_path.exists():
        policies.append(DQNPolicy(str(model_path), device))
        print(f"  DQN model: {model_path}")
    else:
        print(f"  DQN model not found: {model_path}")
        print(f"    Run: python3 scripts/train_dqn.py --episodes 3000")

    hdqn_path = Path("results/models/hdqn_deception_agent.pt")
    if hdqn_path.exists():
        policies.append(HDQNPolicy(str(hdqn_path), device))
        print(f"  h-DQN model: {hdqn_path}")
    else:
        print(f"  h-DQN model not found: {hdqn_path}")
        print(f"    Run: python3 scripts/train_hdqn.py --episodes 3000")

    game_path = Path("results/models/game_defender_final.pt")
    if game_path.exists():
        policies.append(GameEQPolicy(str(game_path), device))
        print(f"  Game-EQ model: {game_path}")
    else:
        print(f"  Game-EQ model not found: {game_path}")
        print(f"    Run: python3 scripts/train_game.py --rounds 4")

    # Evaluate each policy
    results = []
    for policy in policies:
        t0 = time.time()
        print(f"\n  Evaluating {policy.name}...", end="", flush=True)
        r = evaluate_policy(policy, env, args.episodes, seed=42)
        elapsed = time.time() - t0
        print(f" done ({elapsed:.1f}s)")
        results.append(r)

    # Print comparison
    print_comparison(results)

    # Save
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove raw rewards for clean JSON (too large)
    save_results = [{k: v for k, v in r.items() if k != "rewards_raw"} for r in results]
    output_path.write_text(json.dumps(save_results, indent=2))
    print(f"\n  Results saved: {output_path}")

    # ── Cross-Play Matrix (Game-Theoretic Evaluation) ─────────────
    game_def_path = Path("results/models/game_defender_final.pt")
    game_atk_path = Path("results/models/game_attacker_final.pt")
    if game_def_path.exists() and game_atk_path.exists():
        print("\n\n  ═══ Cross-Play Matrix (Defender vs Attacker) ═══\n")
        from honey_drone.markov_game_env import (
            MarkovGameEnv,
            RandomPolicy as GameRandom,
            GreedyDefenderPolicy,
            GreedyAttackerPolicy,
            N_DEFENDER_ACTIONS,
            N_ATTACKER_ACTIONS,
            DEFENDER_OBS_DIM,
            ATTACKER_OBS_DIM,
        )
        from train_game import evaluate_matchup

        # Build policy sets
        def _load_game_policy(path, n_actions, state_dim):
            import importlib.util
            spec = importlib.util.spec_from_file_location("train_dqn", Path(__file__).parent / "train_dqn.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            net = mod.DQN(state_dim, n_actions).to(device)
            ckpt = torch.load(path, map_location=device, weights_only=False)
            net.load_state_dict(ckpt["policy_state_dict"])
            net.eval()
            class _P:
                def __init__(self, n, d):
                    self._net = n; self._dev = d
                def select(self, obs):
                    with torch.no_grad():
                        s = torch.FloatTensor(obs).unsqueeze(0).to(self._dev)
                        return self._net(s).argmax(dim=1).item()
            return _P(net, torch.device(device))

        def_pols = {
            "Random": GameRandom(N_DEFENDER_ACTIONS),
            "Greedy": GreedyDefenderPolicy(),
            "Game-EQ": _load_game_policy(game_def_path, N_DEFENDER_ACTIONS, DEFENDER_OBS_DIM),
        }
        atk_pols = {
            "Random": GameRandom(N_ATTACKER_ACTIONS),
            "Greedy": GreedyAttackerPolicy(),
            "Game-EQ": _load_game_policy(game_atk_path, N_ATTACKER_ACTIONS, ATTACKER_OBS_DIM),
        }

        n_eval = min(args.episodes, 300)
        cross_play = {}
        header = f"  {'Def \\\\ Atk':>12s}"
        for a_name in atk_pols:
            header += f" | {a_name:>10s}"
        print(header)
        print("  " + "-" * len(header))

        for d_name, d_pol in def_pols.items():
            row = f"  {d_name:>12s}"
            cross_play[d_name] = {}
            for a_name, a_pol in atk_pols.items():
                result = evaluate_matchup(d_pol, a_pol, n_eval)
                cross_play[d_name][a_name] = result
                row += f" | {result['avg_r_def']:>+10.2f}"
            print(row)

        # Save cross-play
        cp_path = Path("results/cross_play_matrix.json")
        cp_path.write_text(json.dumps(cross_play, indent=2, default=str))
        print(f"\n  Cross-play matrix saved: {cp_path}")


if __name__ == "__main__":
    main()
