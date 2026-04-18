#!/usr/bin/env python3
"""
train_game.py — Game-Theoretic Training via Alternating Best-Response

[ALGORITHM]
    Fictitious Play / Alternating Best-Response (Brown, 1951)
    Two OpenClaw-style agents (Defender + Attacker) learn in a
    General-Sum Markov Game.

    Round 0: Train Defender DQN vs Random Attacker     → defender_v0
    Round 1: Train Attacker DQN vs Frozen defender_v0  → attacker_v0
    Round 2: Train Defender DQN vs Frozen attacker_v0  → defender_v1
    Round 3: Train Attacker DQN vs Frozen defender_v1  → attacker_v1
    ... until exploitability < threshold or max_rounds reached

[GAME FORMULATION]
    Type: General-Sum Markov Game (Filar & Vrieze, 1997)
    Defender: 5 deception skills (OpenClaw SDK pattern)
    Attacker: 7 attack skills (OpenClaw SDK pattern)
    State: 10-dim per agent (information asymmetry)
    Training: Dueling Double DQN per agent (reuse from train_dqn.py)

[USAGE]
    python3 scripts/train_game.py
    python3 scripts/train_game.py --rounds 4 --episodes 1500

[OUTPUT]
    results/models/game_defender_v{N}.pt
    results/models/game_attacker_v{N}.pt
    results/models/game_defender_final.pt
    results/models/game_training_log.json

[REF]
    Brown (1951), "Iterative Solution of Games by Fictitious Play"
    Lanctot et al. (2017), "A Unified Game-Theoretic Approach to MARL"
    Hou et al. (2025), "Hybrid Defense of MTD and Cyber Deception"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from honey_drone.markov_game_env import (
    MarkovGameEnv,
    SingleAgentWrapper,
    RandomPolicy,
    GreedyDefenderPolicy,
    GreedyAttackerPolicy,
    DQNPolicy,
    DEFENDER_SKILLS,
    ATTACKER_SKILLS,
    N_DEFENDER_ACTIONS,
    N_ATTACKER_ACTIONS,
    DEFENDER_OBS_DIM,
    ATTACKER_OBS_DIM,
)

# Reuse DQN + ReplayBuffer from train_dqn.py
from train_dqn import DQN, ReplayBuffer


# ═══════════════════════════════════════════════════════════════
# Single-Agent DQN Training (one side of the game)
# ═══════════════════════════════════════════════════════════════

def train_one_side(
    role: str,
    opponent_policy,
    episodes: int = 1000,
    batch_size: int = 128,
    gamma: float = 0.99,
    lr: float = 3e-4,
    eps_start: float = 1.0,
    eps_end: float = 0.05,
    eps_decay: int = 400,
    device: torch.device = None,
) -> tuple[DQN, list[float]]:
    """
    Train one agent (defender or attacker) against a frozen opponent.

    Args:
        role: "defender" or "attacker"
        opponent_policy: frozen policy for the other side
        episodes: number of training episodes

    Returns:
        (trained_network, episode_rewards)
    """
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if role == "defender":
        state_dim = DEFENDER_OBS_DIM
        n_actions = N_DEFENDER_ACTIONS
        skill_names = DEFENDER_SKILLS
    else:
        state_dim = ATTACKER_OBS_DIM
        n_actions = N_ATTACKER_ACTIONS
        skill_names = ATTACKER_SKILLS

    # Networks
    policy_net = DQN(state_dim, n_actions).to(device)
    target_net = DQN(state_dim, n_actions).to(device)
    target_net.load_state_dict(policy_net.state_dict())
    target_net.eval()

    optimizer = optim.Adam(policy_net.parameters(), lr=lr)
    replay = ReplayBuffer(50000)
    env = MarkovGameEnv(max_steps=200)
    wrapper = SingleAgentWrapper(env, role, opponent_policy)

    episode_rewards = []
    best_avg = -float("inf")

    for ep in range(episodes):
        state = wrapper.reset()
        ep_reward = 0.0

        for step in range(200):
            # Epsilon-greedy
            eps = eps_end + (eps_start - eps_end) * np.exp(-ep / eps_decay)
            if np.random.random() < eps:
                action = np.random.randint(n_actions)
            else:
                with torch.no_grad():
                    s = torch.FloatTensor(state).unsqueeze(0).to(device)
                    action = policy_net(s).argmax(dim=1).item()

            next_state, reward, done, info = wrapper.step(action)
            replay.push(state, action, reward, next_state, done)
            state = next_state
            ep_reward += reward

            # Train
            if len(replay) >= batch_size:
                states, actions, rewards, next_states, dones = replay.sample(batch_size)
                states = states.to(device)
                actions = actions.to(device)
                rewards = rewards.to(device)
                next_states = next_states.to(device)
                dones = dones.to(device)

                # Double DQN
                q_values = policy_net(states).gather(1, actions.unsqueeze(1)).squeeze(1)
                with torch.no_grad():
                    next_actions = policy_net(next_states).argmax(dim=1)
                    next_q = target_net(next_states).gather(1, next_actions.unsqueeze(1)).squeeze(1)
                    target_q = rewards + gamma * next_q * (~dones)

                loss = nn.functional.huber_loss(q_values, target_q)
                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(policy_net.parameters(), 1.0)
                optimizer.step()

            if done:
                break

        episode_rewards.append(ep_reward)

        # Target network update
        if ep % 5 == 0:
            target_net.load_state_dict(policy_net.state_dict())

        # Progress
        if (ep + 1) % 100 == 0:
            avg = np.mean(episode_rewards[-100:])
            if avg > best_avg:
                best_avg = avg
            print(f"    [{role}] ep {ep+1}/{episodes}  avg_reward={avg:.2f}  "
                  f"best={best_avg:.2f}  eps={eps:.3f}")

    return policy_net, episode_rewards


# ═══════════════════════════════════════════════════════════════
# Evaluation: Cross-Play
# ═══════════════════════════════════════════════════════════════

def evaluate_matchup(
    defender_policy,
    attacker_policy,
    n_episodes: int = 200,
) -> dict:
    """Evaluate a (defender, attacker) pair over N episodes."""
    env = MarkovGameEnv(max_steps=200)
    total_r_def, total_r_atk = 0.0, 0.0
    total_dwell, total_preal = 0.0, 0.0
    outcomes = {"def_win": 0, "atk_win": 0, "timeout": 0, "evasion": 0, "disconnect": 0}

    for _ in range(n_episodes):
        obs_d, obs_a = env.reset()
        ep_r_d, ep_r_a = 0.0, 0.0

        for step in range(200):
            def_act = defender_policy.select(obs_d)
            atk_act = attacker_policy.select(obs_a)
            obs_d, obs_a, r_d, r_a, done, info = env.step(def_act, atk_act)
            ep_r_d += r_d
            ep_r_a += r_a
            if done:
                if env.state.step_count >= 200:
                    outcomes["timeout"] += 1  # defender wins
                elif env.state.evasion_signals >= 5:
                    outcomes["evasion"] += 1
                else:
                    outcomes["disconnect"] += 1
                break

        total_r_def += ep_r_d
        total_r_atk += ep_r_a
        total_dwell += env.state.dwell_sec
        total_preal += env.state.p_real

    return {
        "avg_r_def": round(total_r_def / n_episodes, 3),
        "avg_r_atk": round(total_r_atk / n_episodes, 3),
        "avg_dwell": round(total_dwell / n_episodes, 1),
        "avg_preal": round(total_preal / n_episodes, 4),
        "outcomes": outcomes,
    }


def compute_exploitability(
    def_policy, atk_policy,
    def_net: DQN, atk_net: DQN,
    device: torch.device,
    n_episodes: int = 200,
) -> float:
    """
    Exploitability = how much each side can gain by deviating from current strategy.
    Lower = closer to Nash equilibrium.
    """
    # Current matchup
    base = evaluate_matchup(def_policy, atk_policy, n_episodes)

    # Best-response defender vs current attacker
    # (already trained — that's what we just did)
    # So exploitability is approximated by the improvement from last round

    return base["avg_r_def"] + base["avg_r_atk"]  # proxy: total game value


# ═══════════════════════════════════════════════════════════════
# Main: Alternating Best-Response
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Game-Theoretic Deception Training")
    parser.add_argument("--rounds", type=int, default=3, help="Number of BR rounds")
    parser.add_argument("--episodes", type=int, default=1000, help="Episodes per round")
    parser.add_argument("--eval-episodes", type=int, default=300, help="Eval episodes")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n{'='*60}")
    print(f"  MIRAGE-UAS Game-Theoretic Training")
    print(f"  Algorithm: Alternating Best-Response (Fictitious Play)")
    print(f"  Game: General-Sum Markov Game")
    print(f"  Defender Skills: {N_DEFENDER_ACTIONS} ({', '.join(DEFENDER_SKILLS)})")
    print(f"  Attacker Skills: {N_ATTACKER_ACTIONS} ({', '.join(ATTACKER_SKILLS)})")
    print(f"  Rounds: {args.rounds}")
    print(f"  Episodes/round: {args.episodes}")
    print(f"  Device: {device}")
    print(f"{'='*60}\n")

    model_dir = Path("results/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    training_log = {
        "algorithm": "alternating_best_response",
        "game_type": "general_sum_markov_game",
        "defender_skills": DEFENDER_SKILLS,
        "attacker_skills": ATTACKER_SKILLS,
        "rounds": [],
    }

    # Initial policies
    current_def_policy = RandomPolicy(N_DEFENDER_ACTIONS)
    current_atk_policy = RandomPolicy(N_ATTACKER_ACTIONS)
    def_net = None
    atk_net = None

    t_start = time.time()

    for round_idx in range(args.rounds):
        round_data = {"round": round_idx, "defender": {}, "attacker": {}}
        is_def_turn = (round_idx % 2 == 0)

        if is_def_turn:
            # ── Train Defender vs current Attacker ──
            print(f"\n--- Round {round_idx}: Train DEFENDER vs {'Random' if round_idx == 0 else 'DQN'} Attacker ---")
            def_net, def_rewards = train_one_side(
                role="defender",
                opponent_policy=current_atk_policy,
                episodes=args.episodes,
                device=device,
            )

            # Save checkpoint
            ckpt_path = model_dir / f"game_defender_v{round_idx // 2}.pt"
            torch.save({
                "policy_state_dict": def_net.state_dict(),
                "round": round_idx,
                "role": "defender",
                "state_dim": DEFENDER_OBS_DIM,
                "n_actions": N_DEFENDER_ACTIONS,
                "skills": DEFENDER_SKILLS,
            }, ckpt_path)
            print(f"    Saved: {ckpt_path}")

            # Update current defender policy
            class _DefPolicy:
                def __init__(self, net, dev):
                    self.net = net; self.dev = dev
                def select(self, obs):
                    with torch.no_grad():
                        s = torch.FloatTensor(obs).unsqueeze(0).to(self.dev)
                        return self.net(s).argmax(dim=1).item()
            current_def_policy = _DefPolicy(def_net, device)

            round_data["defender"]["rewards"] = [float(r) for r in def_rewards[-100:]]
            round_data["defender"]["avg_reward"] = float(np.mean(def_rewards[-100:]))

        else:
            # ── Train Attacker vs current Defender ──
            print(f"\n--- Round {round_idx}: Train ATTACKER vs DQN Defender ---")
            atk_net, atk_rewards = train_one_side(
                role="attacker",
                opponent_policy=current_def_policy,
                episodes=args.episodes,
                device=device,
            )

            # Save checkpoint
            ckpt_path = model_dir / f"game_attacker_v{round_idx // 2}.pt"
            torch.save({
                "policy_state_dict": atk_net.state_dict(),
                "round": round_idx,
                "role": "attacker",
                "state_dim": ATTACKER_OBS_DIM,
                "n_actions": N_ATTACKER_ACTIONS,
                "skills": ATTACKER_SKILLS,
            }, ckpt_path)
            print(f"    Saved: {ckpt_path}")

            # Update current attacker policy
            class _AtkPolicy:
                def __init__(self, net, dev):
                    self.net = net; self.dev = dev
                def select(self, obs):
                    with torch.no_grad():
                        s = torch.FloatTensor(obs).unsqueeze(0).to(self.dev)
                        return self.net(s).argmax(dim=1).item()
            current_atk_policy = _AtkPolicy(atk_net, device)

            round_data["attacker"]["rewards"] = [float(r) for r in atk_rewards[-100:]]
            round_data["attacker"]["avg_reward"] = float(np.mean(atk_rewards[-100:]))

        # ── Evaluate current matchup ──
        print(f"\n  Evaluating Round {round_idx} matchup ({args.eval_episodes} episodes)...")
        eval_result = evaluate_matchup(
            current_def_policy, current_atk_policy, args.eval_episodes,
        )
        round_data["evaluation"] = eval_result
        training_log["rounds"].append(round_data)

        print(f"  avg_r_def={eval_result['avg_r_def']:.3f}  "
              f"avg_r_atk={eval_result['avg_r_atk']:.3f}  "
              f"avg_preal={eval_result['avg_preal']:.4f}  "
              f"avg_dwell={eval_result['avg_dwell']:.0f}s")
        print(f"  outcomes: {eval_result['outcomes']}")

    # ── Save final policies ──
    elapsed = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"  Training complete in {elapsed:.1f}s ({elapsed/60:.1f}min)")
    print(f"{'='*60}")

    if def_net is not None:
        final_def = model_dir / "game_defender_final.pt"
        torch.save({
            "policy_state_dict": def_net.state_dict(),
            "role": "defender",
            "state_dim": DEFENDER_OBS_DIM,
            "n_actions": N_DEFENDER_ACTIONS,
            "skills": DEFENDER_SKILLS,
            "training_rounds": args.rounds,
        }, final_def)
        print(f"  Final defender: {final_def}")

    if atk_net is not None:
        final_atk = model_dir / "game_attacker_final.pt"
        torch.save({
            "policy_state_dict": atk_net.state_dict(),
            "role": "attacker",
            "state_dim": ATTACKER_OBS_DIM,
            "n_actions": N_ATTACKER_ACTIONS,
            "skills": ATTACKER_SKILLS,
            "training_rounds": args.rounds,
        }, final_atk)
        print(f"  Final attacker: {final_atk}")

    # ── Cross-play matrix ──
    print(f"\n  Computing cross-play matrix...")
    policies_def = {
        "Random": RandomPolicy(N_DEFENDER_ACTIONS),
        "Greedy": GreedyDefenderPolicy(),
    }
    policies_atk = {
        "Random": RandomPolicy(N_ATTACKER_ACTIONS),
        "Greedy": GreedyAttackerPolicy(),
    }
    if def_net is not None:
        policies_def["Game-EQ"] = current_def_policy
    if atk_net is not None:
        policies_atk["Game-EQ"] = current_atk_policy

    cross_play = {}
    for d_name, d_pol in policies_def.items():
        cross_play[d_name] = {}
        for a_name, a_pol in policies_atk.items():
            result = evaluate_matchup(d_pol, a_pol, 200)
            cross_play[d_name][a_name] = result
            print(f"    {d_name:10s} vs {a_name:10s}: "
                  f"r_def={result['avg_r_def']:+.2f}  "
                  f"r_atk={result['avg_r_atk']:+.2f}  "
                  f"p_real={result['avg_preal']:.3f}")

    training_log["cross_play"] = cross_play
    training_log["elapsed_sec"] = elapsed

    # Save log
    log_path = model_dir / "game_training_log.json"
    log_path.write_text(json.dumps(training_log, indent=2, default=str))
    print(f"\n  Training log: {log_path}")
    print(f"  Done.\n")


if __name__ == "__main__":
    main()
