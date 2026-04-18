#!/usr/bin/env python3
"""
analyze_game.py — Game-Theoretic Training Analysis & Paper Figures

Reads game_training_log.json from train_game.py and generates:
  1. Nash convergence plot (both agents' rewards per round)
  2. Strategy profile heatmap (action freq by phase at equilibrium)
  3. Cross-play matrix heatmap
  4. LaTeX table for paper

Usage:
    python3 scripts/analyze_game.py
    python3 scripts/analyze_game.py --log results/models/game_training_log.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from honey_drone.markov_game_env import (
    MarkovGameEnv,
    DEFENDER_SKILLS,
    ATTACKER_SKILLS,
    N_DEFENDER_ACTIONS,
    N_ATTACKER_ACTIONS,
    RandomPolicy,
    GreedyDefenderPolicy,
    GreedyAttackerPolicy,
)


def load_log(path: str) -> dict:
    return json.loads(Path(path).read_text())


def plot_convergence(log: dict, out_dir: Path) -> None:
    """Plot Nash convergence: both agents' rewards per training round."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rounds = log["rounds"]
    r_defs = [r["evaluation"]["avg_r_def"] for r in rounds]
    r_atks = [r["evaluation"]["avg_r_atk"] for r in rounds]
    p_reals = [r["evaluation"]["avg_preal"] for r in rounds]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

    # Rewards
    x = list(range(len(rounds)))
    ax1.plot(x, r_defs, "b-o", label="Defender", linewidth=2, markersize=8)
    ax1.plot(x, r_atks, "r-s", label="Attacker", linewidth=2, markersize=8)
    ax1.set_xlabel("Training Round", fontsize=12)
    ax1.set_ylabel("Average Reward", fontsize=12)
    ax1.set_title("Nash Convergence", fontsize=13, fontweight="bold")
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3)

    # P(real) convergence
    ax2.plot(x, p_reals, "-^", linewidth=2, markersize=8, color="#2ca02c")
    ax2.set_xlabel("Training Round", fontsize=12)
    ax2.set_ylabel("Avg P(real)", fontsize=12)
    ax2.set_title("Belief Convergence", fontsize=13, fontweight="bold")
    ax2.set_ylim(0, 1)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    path = out_dir / "game_convergence.pdf"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.savefig(out_dir / "game_convergence.png", dpi=150, bbox_inches="tight")
    print(f"  Convergence plot: {path}")
    plt.close()


def plot_cross_play(log: dict, out_dir: Path) -> None:
    """Plot cross-play matrix as heatmap."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cp = log.get("cross_play", {})
    if not cp:
        print("  No cross-play data in log.")
        return

    def_names = list(cp.keys())
    atk_names = list(cp[def_names[0]].keys()) if def_names else []
    if not atk_names:
        return

    # Build matrix
    matrix = np.zeros((len(def_names), len(atk_names)))
    for i, d in enumerate(def_names):
        for j, a in enumerate(atk_names):
            matrix[i, j] = cp[d][a]["avg_r_def"]

    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(matrix, cmap="RdYlGn", aspect="auto")

    ax.set_xticks(range(len(atk_names)))
    ax.set_xticklabels([f"Atk:{n}" for n in atk_names], fontsize=10)
    ax.set_yticks(range(len(def_names)))
    ax.set_yticklabels([f"Def:{n}" for n in def_names], fontsize=10)

    # Annotate
    for i in range(len(def_names)):
        for j in range(len(atk_names)):
            val = matrix[i, j]
            color = "white" if abs(val) > matrix.max() * 0.6 else "black"
            ax.text(j, i, f"{val:+.1f}", ha="center", va="center",
                    fontsize=11, fontweight="bold", color=color)

    ax.set_title("Cross-Play Matrix (Defender Reward)", fontsize=13, fontweight="bold")
    plt.colorbar(im, ax=ax, label="Defender Avg Reward")
    plt.tight_layout()

    path = out_dir / "game_cross_play.pdf"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.savefig(out_dir / "game_cross_play.png", dpi=150, bbox_inches="tight")
    print(f"  Cross-play heatmap: {path}")
    plt.close()


def plot_strategy_profile(out_dir: Path, n_episodes: int = 500) -> None:
    """Run equilibrium policies and plot action frequency by phase."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import torch

    game_def_path = Path("results/models/game_defender_final.pt")
    game_atk_path = Path("results/models/game_attacker_final.pt")

    if not game_def_path.exists() or not game_atk_path.exists():
        print("  Game-EQ checkpoints not found, skipping strategy profile.")
        return

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Load policies — auto-detect hidden size from checkpoint
    from train_dqn import DQN
    def _load(path, n_act, s_dim):
        ckpt = torch.load(path, map_location=device, weights_only=False)
        hidden = ckpt["policy_state_dict"]["feature.0.weight"].shape[0]
        net = DQN(s_dim, n_act, hidden=hidden).to(device)
        net.load_state_dict(ckpt["policy_state_dict"])
        net.eval()
        return net

    def_net = _load(game_def_path, N_DEFENDER_ACTIONS, 10)
    atk_net = _load(game_atk_path, N_ATTACKER_ACTIONS, 10)

    # Collect action counts by phase
    def_phase_actions = np.zeros((4, N_DEFENDER_ACTIONS))
    atk_phase_actions = np.zeros((4, N_ATTACKER_ACTIONS))

    env = MarkovGameEnv(max_steps=200)
    for _ in range(n_episodes):
        obs_d, obs_a = env.reset()
        for _ in range(200):
            with torch.no_grad():
                d_act = def_net(torch.FloatTensor(obs_d).unsqueeze(0).to(device)).argmax(1).item()
                a_act = atk_net(torch.FloatTensor(obs_a).unsqueeze(0).to(device)).argmax(1).item()

            phase = min(env.state.phase, 3)
            def_phase_actions[phase, d_act] += 1
            atk_phase_actions[phase, a_act] += 1

            obs_d, obs_a, _, _, done, _ = env.step(d_act, a_act)
            if done:
                break

    # Normalize to percentages
    for p in range(4):
        s = def_phase_actions[p].sum()
        if s > 0:
            def_phase_actions[p] /= s
        s = atk_phase_actions[p].sum()
        if s > 0:
            atk_phase_actions[p] /= s

    phases = ["RECON", "EXPLOIT", "PERSIST", "EXFIL"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Defender
    im1 = ax1.imshow(def_phase_actions, cmap="Blues", aspect="auto", vmin=0, vmax=1)
    ax1.set_xticks(range(N_DEFENDER_ACTIONS))
    ax1.set_xticklabels([s.replace("deception_", "") for s in DEFENDER_SKILLS],
                         rotation=45, ha="right", fontsize=9)
    ax1.set_yticks(range(4))
    ax1.set_yticklabels(phases, fontsize=10)
    ax1.set_title("Defender Strategy Profile", fontsize=13, fontweight="bold")
    for i in range(4):
        for j in range(N_DEFENDER_ACTIONS):
            ax1.text(j, i, f"{def_phase_actions[i,j]:.0%}", ha="center", va="center", fontsize=9)
    plt.colorbar(im1, ax=ax1)

    # Attacker
    im2 = ax2.imshow(atk_phase_actions, cmap="Reds", aspect="auto", vmin=0, vmax=1)
    ax2.set_xticks(range(N_ATTACKER_ACTIONS))
    ax2.set_xticklabels(ATTACKER_SKILLS, rotation=45, ha="right", fontsize=9)
    ax2.set_yticks(range(4))
    ax2.set_yticklabels(phases, fontsize=10)
    ax2.set_title("Attacker Strategy Profile", fontsize=13, fontweight="bold")
    for i in range(4):
        for j in range(N_ATTACKER_ACTIONS):
            ax2.text(j, i, f"{atk_phase_actions[i,j]:.0%}", ha="center", va="center", fontsize=9)
    plt.colorbar(im2, ax=ax2)

    plt.tight_layout()
    path = out_dir / "game_strategy_profile.pdf"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.savefig(out_dir / "game_strategy_profile.png", dpi=150, bbox_inches="tight")
    print(f"  Strategy profile: {path}")
    plt.close()


def generate_latex(log: dict, out_dir: Path) -> None:
    """Generate LaTeX table for paper."""
    cp = log.get("cross_play", {})
    if not cp:
        return

    def_names = list(cp.keys())
    atk_names = list(cp[def_names[0]].keys()) if def_names else []

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Cross-Play Defender Reward Matrix}",
        r"\label{tab:cross_play}",
        r"\begin{tabular}{l" + "c" * len(atk_names) + "}",
        r"\toprule",
        r"Defender $\backslash$ Attacker & " + " & ".join(atk_names) + r" \\",
        r"\midrule",
    ]
    for d in def_names:
        vals = []
        row_vals = [cp[d][a]["avg_r_def"] for a in atk_names]
        best = max(row_vals)
        for a in atk_names:
            v = cp[d][a]["avg_r_def"]
            s = f"{v:+.1f}"
            if v == best:
                s = r"\textbf{" + s + "}"
            vals.append(s)
        lines.append(f"  {d} & " + " & ".join(vals) + r" \\")

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]

    path = out_dir / "table_cross_play.tex"
    path.write_text("\n".join(lines))
    print(f"  LaTeX table: {path}")


def compute_signaling_exploitability(
    n_episodes: int = 300,
    kappa: float = 0.5,
    temperature: float = 0.8,
    epsilon: float = 0.10,
    seed: int = 2024,
) -> dict:
    """
    [ROLE] Measure exploitability of the (frozen) Signaling Game equilibrium.
           Exploitability = r_atk(best-response) - r_atk(random)
           Lower values ⇒ solver is closer to a robust Nash-like fixed point.

    [INPUTS]
        results/models/game_attacker_vs_signaling.pt  (if present)
        — produced by `train_game.py --defender-policy signaling_eq`.
        If missing, only random- and greedy-attacker payoffs are reported.

    [REF] Lanctot et al. (2017) — Unified game-theoretic approach to MARL
          Fudenberg & Tirole (1991), Game Theory, §8.3 (PBE exploitability)
    """
    import torch

    from honey_drone.markov_game_env import (
        MarkovGameEnv, RandomPolicy, GreedyAttackerPolicy,
        N_ATTACKER_ACTIONS, ATTACKER_OBS_DIM,
    )
    from honey_drone.signaling_game_solver import SignalingGameSolver

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Seed the Python + numpy global RNGs so MarkovGameEnv (which uses them
    # throughout its step() logic) is deterministic across evaluations.
    import random as _pyrnd
    _pyrnd.seed(seed)
    np.random.seed(seed)

    def _eval(defender, attacker, n_eps, eval_seed_offset: int = 0):
        # Reseed so every _eval call starts from the same RNG anchor,
        # but different (defender, attacker) matchups use distinct offsets
        # so their trajectories are independent.
        _pyrnd.seed(seed + eval_seed_offset)
        np.random.seed(seed + eval_seed_offset)
        env = MarkovGameEnv(max_steps=200)
        r_def_sum, r_atk_sum = 0.0, 0.0
        for _ in range(n_eps):
            obs_d, obs_a = env.reset()
            ep_d, ep_a = 0.0, 0.0
            for _ in range(200):
                phase = int(round(float(obs_d[0]) * 3))
                mu = float(obs_d[2])
                d_act, _, _ = defender.select_skill(
                    mu_a=mu, phase=max(0, min(3, phase)),
                )
                a_act = attacker.select(obs_a)
                obs_d, obs_a, r_d, r_a, done, _ = env.step(d_act, a_act)
                ep_d += r_d
                ep_a += r_a
                if done:
                    break
            r_def_sum += ep_d
            r_atk_sum += ep_a
        return {
            "avg_r_def": round(r_def_sum / n_eps, 4),
            "avg_r_atk": round(r_atk_sum / n_eps, 4),
        }

    defender = SignalingGameSolver(
        cost_sensitivity_kappa=kappa,
        temperature=temperature,
        exploration_epsilon=epsilon,
        learning_rate=0.0,  # frozen equilibrium
    )

    out = {
        "kappa": kappa,
        "temperature": temperature,
        "epsilon": epsilon,
        "episodes": n_episodes,
    }

    out["random_attacker"] = _eval(defender, RandomPolicy(N_ATTACKER_ACTIONS), n_episodes, eval_seed_offset=0)
    out["greedy_attacker"] = _eval(defender, GreedyAttackerPolicy(), n_episodes, eval_seed_offset=1)

    # Best-response attacker (trained vs frozen solver)
    br_path = Path("results/models/game_attacker_vs_signaling.pt")
    if br_path.exists():
        from train_dqn import DQN
        ckpt = torch.load(br_path, map_location=device, weights_only=False)
        hidden = ckpt["policy_state_dict"]["feature.0.weight"].shape[0]
        net = DQN(ATTACKER_OBS_DIM, N_ATTACKER_ACTIONS, hidden=hidden).to(device)
        net.load_state_dict(ckpt["policy_state_dict"])
        net.eval()

        class _BR:
            def select(self, obs):
                with torch.no_grad():
                    s = torch.FloatTensor(obs).unsqueeze(0).to(device)
                    return net(s).argmax(dim=1).item()

        out["best_response_attacker"] = _eval(defender, _BR(), n_episodes, eval_seed_offset=2)
        # Proper exploitability (Lanctot 2017): gain of BR over the
        # current strategy σ_A that the solver expected. We use the
        # greedy attacker as σ_A because it's the phase-optimal
        # deterministic baseline; "Δ vs random" is reported separately
        # as a sanity check on BR training effectiveness.
        out["exploitability_br_vs_greedy"] = round(
            out["best_response_attacker"]["avg_r_atk"] - out["greedy_attacker"]["avg_r_atk"], 4,
        )
        out["br_gain_vs_random"] = round(
            out["best_response_attacker"]["avg_r_atk"] - out["random_attacker"]["avg_r_atk"], 4,
        )
        # Back-compat alias — older code paths read `exploitability_vs_random`.
        out["exploitability_vs_random"] = out["br_gain_vs_random"]
        out["exploitability_vs_greedy"] = out["exploitability_br_vs_greedy"]
    else:
        out["best_response_attacker"] = None
        out["exploitability_vs_random"] = None
        out["exploitability_vs_greedy"] = None
        out["note"] = ("Best-response checkpoint not found. Run: "
                       "python3 scripts/train_game.py --defender-policy signaling_eq")

    return out


def print_exploitability(expl: dict) -> None:
    print("\n  ── Signaling-Eq Solver Exploitability ──")
    print(f"  config: κ={expl['kappa']}  τ={expl['temperature']}  ε={expl['epsilon']}  "
          f"({expl['episodes']} episodes each)\n")
    rows = [
        ("Random attacker",  expl["random_attacker"]),
        ("Greedy attacker",  expl["greedy_attacker"]),
        ("Best-response attacker", expl["best_response_attacker"]),
    ]
    for name, r in rows:
        if r is None:
            print(f"    {name:24s} — not run")
            continue
        print(f"    {name:24s}  r_def={r['avg_r_def']:+8.3f}   r_atk={r['avg_r_atk']:+8.3f}")
    if expl["exploitability_vs_random"] is not None:
        print(f"\n    Exploitability Δr_atk (BR − Random): {expl['exploitability_vs_random']:+.3f}")
        print(f"    Exploitability Δr_atk (BR − Greedy): {expl['exploitability_vs_greedy']:+.3f}")
        print(f"    (lower absolute Δ = solver closer to equilibrium)\n")
    else:
        print(f"\n    {expl.get('note', '')}\n")


def main():
    parser = argparse.ArgumentParser(description="Analyze game-theoretic training")
    parser.add_argument("--log", type=str, default="results/models/game_training_log.json")
    parser.add_argument("--skip-exploitability", action="store_true",
                        help="Skip signaling-eq exploitability computation")
    parser.add_argument("--expl-episodes", type=int, default=300)
    parser.add_argument("--sig-kappa", type=float, default=0.5)
    parser.add_argument("--sig-temperature", type=float, default=0.8)
    parser.add_argument("--sig-epsilon", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=2024,
                        help="Seed for exploitability evaluation RNGs (stdlib + numpy)")
    args = parser.parse_args()

    fig_dir = Path("results/figures")
    fig_dir.mkdir(parents=True, exist_ok=True)
    latex_dir = Path("results/latex")
    latex_dir.mkdir(parents=True, exist_ok=True)

    log_path = Path(args.log)
    if log_path.exists():
        log = load_log(str(log_path))
        print(f"\n  MIRAGE-UAS Game-Theoretic Analysis")
        print(f"  Log: {log_path}")
        print(f"  Rounds: {len(log['rounds'])}")
        print(f"  Elapsed: {log.get('elapsed_sec', 0):.1f}s\n")

        plot_convergence(log, fig_dir)
        plot_cross_play(log, fig_dir)
        plot_strategy_profile(fig_dir)
        generate_latex(log, latex_dir)
    else:
        print(f"\n  Training log not found: {log_path} — skipping DQN-game plots")
        print(f"  (will still compute signaling-eq exploitability if requested)\n")

    # ── Signaling-Eq Exploitability (NEW) ──
    if not args.skip_exploitability:
        expl = compute_signaling_exploitability(
            n_episodes=args.expl_episodes,
            kappa=args.sig_kappa,
            temperature=args.sig_temperature,
            epsilon=args.sig_epsilon,
            seed=args.seed,
        )
        print_exploitability(expl)
        expl_path = Path("results/signaling_exploitability.json")
        expl_path.write_text(json.dumps(expl, indent=2))
        print(f"  Saved: {expl_path}")

    print(f"\n  Done.\n")


if __name__ == "__main__":
    main()
