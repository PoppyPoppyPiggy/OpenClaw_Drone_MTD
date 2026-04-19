#!/usr/bin/env python3
"""
run_llm_experiments.py — Tier 2 LLM policy evaluation on DeceptionEnv

[ROLE]
    Drop-in evaluation for DEFENDER_POLICY=llm_agent equivalent. Wraps
    LLMTacticalAgent's select_action() in a synchronous `LLMPolicy.select()`
    that the existing DeceptionEnv loop can call. Supports macro-action (call
    LLM every MACRO steps, reuse the decision in between) to keep call count
    tractable during large sweeps.

[USAGE]
    python scripts/run_llm_experiments.py \\
        --models llama3.1:8b,qwen2.5:14b,gemma2:9b \\
        --seeds 42 \\
        --episodes 50 --max-steps 50 --macro 5

[OUTPUT]
    results/llm_multi_seed/<model>_seed<seed>.json   (per-run metrics)
    results/llm_multi_seed/summary.json              (aggregate + 95% CI)

[NOTES]
    - Denormalizes 10-dim state → context dict matching _build_mab_context
    - LLM call uses httpx sync client (1 thread, avoids asyncio-in-sync mess)
    - Hot model is kept in Ollama GPU memory across calls within a run
    - On any LLM error, falls back to uniform random (same semantics as
      production LLMTacticalAgent)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "config" / ".env")

from honey_drone.deception_env import (  # noqa: E402
    DeceptionEnv,
    N_BASE_ACTIONS,
    BASE_ACTION_NAMES,
)


SKILL_NAMES = (
    "statustext",
    "flight_sim",
    "ghost_port",
    "reboot_sim",
    "credential_leak",
)

SYSTEM_PROMPT = """You are a UAS honeydrone tactical deception agent.
Each step you pick ONE skill to keep the attacker engaged while hiding the
real drone. The attacker adapts — rigid repetition exposes the decoy.

Skills (identify by letter AND index):
  A / 0 = statustext       emit forged MAVLink STATUSTEXT
  B / 1 = flight_sim       simulate benign flight telemetry
  C / 2 = ghost_port       expose a phantom TCP service
  D / 3 = reboot_sim       fake a reboot with stale boot banner
  E / 4 = credential_leak  leak a decoy API key / SSH token

Attacker phases:
  0 RECON    scanning, HEARTBEAT, PARAM_REQUEST
  1 EXPLOIT  COMMAND_LONG, SET_MODE, injection
  2 PERSIST  PARAM_SET, MISSION_ITEM (backdoor install)
  3 EXFIL    LOG_REQUEST, FILE_TRANSFER

Phase -> candidate skills (soft hint; deviate when useful):
  RECON    -> A or B or C   (flood low-value info)
  EXPLOIT  -> C or D or E   (confuse attack surface)
  PERSIST  -> D or A or E   (reset attacker state, feed false creds)
  EXFIL    -> E or D        (plant tracked honey-tokens, break exfil chain)

CRITICAL CONSTRAINTS (obey; do not rationalise around):
  1. Prefer a DIFFERENT skill from last_action unless attacker behaviour
     strongly demands repetition (e.g. the same exploit twice in a row).
  2. If phase_changed_since_last is true, CHANGE your skill class.
  3. Never pick B (flight_sim) in phase 3 (EXFIL) — the effect is negative.
  4. In phase 2 (PERSIST), strongly prefer D (reboot_sim) on first entry.

Respond with ONLY one JSON object, no prose, no markdown, no code fences:
{"skill_idx": <0-4>, "reason": "<one short sentence>"}
(Equivalently you may reply {"skill": "A|B|C|D|E", "reason": "..."}.)"""


def state_to_context(state_vec: np.ndarray) -> dict:
    """Denormalize DeceptionEnv 10-dim state → context dict for the LLM prompt."""
    s = np.asarray(state_vec, dtype=float)
    return {
        "phase_val": int(round(float(s[0]) * 3)),
        "max_level": int(round(float(s[1]) * 4)),
        "avg_p_real": float(s[2]),
        "avg_dwell_sec": float(s[3]) * 600.0,
        "avg_commands": float(s[4]) * 100.0,
        "services_touched": float(s[5]) * 10.0,
        "exploit_attempts": int(round(float(s[6]) * 5)),
        "ghost_active": int(round(float(s[7]) * 5)),
        "time_in_phase": float(s[8]) * 120.0,
        "evasion_signals": int(round(float(s[9]) * 3)),
    }


def format_prompt(context: dict) -> str:
    phase_names = ("RECON", "EXPLOIT", "PERSIST", "EXFIL")
    pv = int(context.get("phase_val", 0))
    pn = phase_names[pv] if 0 <= pv < 4 else f"UNK({pv})"
    last_action = context.get("last_action")
    last_action_s = last_action if last_action is not None else "<none>"
    phase_changed = bool(context.get("phase_changed_since_last", False))
    directive = context.get("strategic_directive")
    directive_line = "(empty)"
    if isinstance(directive, dict):
        bias = directive.get("skill_bias") or {}
        bias_s = ", ".join(f"{k}:{float(v):.2f}" for k, v in bias.items()) or "none"
        directive_line = (
            f"action={directive.get('action', 'observe')} "
            f"urgency={float(directive.get('urgency', 0.5)):.2f} "
            f"skill_bias={{{bias_s}}}"
        )
    return (
        "state:\n"
        f"  attacker_phase       : {pn} ({pv})\n"
        f"  attacker_tool_level  : {int(context.get('max_level', 0))} "
        "(0=nmap..4=custom)\n"
        f"  belief_mu_A          : {float(context.get('avg_p_real', 0.7)):.3f}\n"
        f"  dwell_sec            : {float(context.get('avg_dwell_sec', 0.0)):.1f}\n"
        f"  commands_issued      : {float(context.get('avg_commands', 0.0)):.0f}\n"
        f"  services_touched     : {float(context.get('services_touched', 0.0)):.1f}\n"
        f"  exploit_attempts     : {int(context.get('exploit_attempts', 0))}\n"
        f"  ghost_ports_active   : {int(context.get('ghost_active', 0))}\n"
        f"  time_in_phase_sec    : {float(context.get('time_in_phase', 0.0)):.1f}\n"
        f"  evasion_signals      : {int(context.get('evasion_signals', 0))}\n"
        f"  last_action          : {last_action_s}\n"
        f"  phase_changed_since  : {phase_changed}\n"
        f"directive (from Tier 1 GCS): {directive_line}\n"
        "\nSelect skill now."
    )


class LLMPolicy:
    """Synchronous wrapper mirroring the Policy.select(state) contract."""

    def __init__(
        self,
        model: str,
        ollama_url: str,
        timeout_sec: float = 20.0,
        temperature: float = 0.9,
        macro: int = 5,
        rng_seed: int = 0,
    ) -> None:
        self.model = model
        self.name = f"LLM-{model}"
        self._client = httpx.Client(
            base_url=ollama_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_sec, connect=5.0),
        )
        self._temp = float(temperature)
        self._macro = max(1, int(macro))
        self._step = 0
        self._cached_action: int = 0
        self._rng = random.Random(rng_seed)
        self._calls = 0
        self._fallbacks = 0
        self._latencies: list[float] = []
        self._skill_counts: Counter = Counter()
        self._last_action_idx: int | None = None
        self._last_phase_val: int | None = None
        self._llm_decision_counts: Counter = Counter()  # fresh LLM calls only (no macro replay)
        self._reason_samples: list[dict] = []  # first N + last N reasons for audit
        self._MAX_REASON_SAMPLES = 40

    def reset(self) -> None:
        self._step = 0
        self._cached_action = 0

    def select(self, state: np.ndarray) -> int:
        # Macro-action: only call LLM every `macro` steps, repeat in between.
        if self._step % self._macro == 0:
            self._cached_action = self._call_llm(state)
        self._step += 1
        self._skill_counts[SKILL_NAMES[self._cached_action]] += 1
        return self._cached_action

    def _call_llm(self, state: np.ndarray) -> int:
        context = state_to_context(state)
        if self._last_action_idx is not None:
            context['last_action'] = f"{'ABCDE'[self._last_action_idx]}/{self._last_action_idx} ({SKILL_NAMES[self._last_action_idx]})"
        cur_phase = int(context.get('phase_val', 0))
        context['phase_changed_since_last'] = (
            self._last_phase_val is not None and cur_phase != self._last_phase_val
        )
        self._last_phase_val = cur_phase
        user_prompt = format_prompt(context)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": self._temp,
                "num_predict": 200,
                "top_p": 0.95,
                "top_k": 40,
                "repeat_penalty": 1.2,
            },
            "keep_alive": "1h",
        }
        self._calls += 1
        t0 = time.perf_counter()
        reason_text = ""
        fell_back = False
        try:
            resp = self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("message") or {}).get("content", "") or ""
            parsed = json.loads(content)
            # Accept either {"skill_idx": 0-4 or "A"-"E"} or {"skill": "A"..."E"}.
            # LLMs often put the letter in skill_idx despite schema — be lenient.
            letter_to_idx = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
            val = parsed.get("skill_idx") if parsed.get("skill_idx") is not None else parsed.get("skill")
            if val is None:
                raise ValueError("response missing skill_idx / skill field")
            if isinstance(val, bool):
                raise ValueError(f"bad skill type bool={val}")
            if isinstance(val, (int, float)):
                idx = int(val)
            elif isinstance(val, str):
                s = val.strip()
                if s and s[0].upper() in letter_to_idx:
                    idx = letter_to_idx[s[0].upper()]
                else:
                    # may be a stringified digit like "1"
                    idx = int(s)
            else:
                raise ValueError(f"bad skill type {type(val).__name__}")
            reason_text = str(parsed.get("reason", ""))[:240]
            if idx < 0 or idx >= N_BASE_ACTIONS:
                raise ValueError(f"out_of_range skill_idx={idx}")
        except Exception as _e:
            self._fallbacks += 1
            idx = self._rng.randint(0, N_BASE_ACTIONS - 1)
            fell_back = True
            # Capture the raw LLM content (or error) so we can diagnose
            try:
                raw_content = (data.get("message") or {}).get("content", "")[:300]
            except Exception:
                raw_content = ""
            reason_text = f"fallback:{type(_e).__name__}: {str(_e)[:120]} | raw={raw_content[:160]}"
        self._latencies.append((time.perf_counter() - t0) * 1000.0)
        self._llm_decision_counts[SKILL_NAMES[idx]] += 1
        self._last_action_idx = idx
        # Keep first 20 + last 20 samples (rolling)
        sample = {
            "call_idx": self._calls,
            "phase": int(context.get("phase_val", 0)),
            "mu_A": round(float(context.get("avg_p_real", 0.0)), 3),
            "skill": SKILL_NAMES[idx],
            "skill_idx": idx,
            "reason": reason_text,
            "fallback": fell_back,
        }
        if len(self._reason_samples) < self._MAX_REASON_SAMPLES:
            self._reason_samples.append(sample)
        else:
            self._reason_samples[-1] = sample  # overwrite last with newest
        return idx

    def close(self) -> None:
        self._client.close()

    def summary(self) -> dict:
        lat = self._latencies
        total_decisions = sum(self._llm_decision_counts.values()) or 1
        return {
            "calls": self._calls,
            "fallbacks": self._fallbacks,
            "fallback_rate": round(self._fallbacks / max(1, self._calls), 4),
            "mean_latency_ms": round(sum(lat) / max(1, len(lat)), 1),
            "p50_latency_ms": round(sorted(lat)[len(lat) // 2], 1) if lat else 0.0,
            "p95_latency_ms": round(
                sorted(lat)[int(len(lat) * 0.95)], 1) if lat else 0.0,
            "skill_counts_execution": dict(self._skill_counts),
            "skill_counts_llm_decisions": dict(self._llm_decision_counts),
            "skill_distribution_llm_decisions_pct": {
                k: round(v / total_decisions * 100, 1)
                for k, v in self._llm_decision_counts.items()
            },
            "reason_samples": self._reason_samples,
        }


def _js_divergence_matrix(phase_actions: np.ndarray) -> np.ndarray:
    """Pairwise Jensen-Shannon divergence between per-phase skill distributions."""
    P = phase_actions.astype(np.float64)
    row_sums = P.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    P = P / row_sums
    n = P.shape[0]
    M = np.zeros((n, n), dtype=np.float64)
    eps = 1e-12
    for i in range(n):
        for j in range(n):
            p = P[i] + eps
            q = P[j] + eps
            m = 0.5 * (p + q)
            # KL(p || m)
            kl_pm = float((p * np.log2(p / m)).sum())
            kl_qm = float((q * np.log2(q / m)).sum())
            M[i, j] = 0.5 * (kl_pm + kl_qm)
    return M


def _chi_square_independence(phase_actions: np.ndarray) -> tuple[float, float, int]:
    """Chi-square test of independence between phase and skill.

    Returns (chi2_stat, p_value, dof).
    """
    O = phase_actions.astype(np.float64)
    n = O.sum()
    if n <= 0:
        return (0.0, 1.0, 0)
    row_tot = O.sum(axis=1, keepdims=True)
    col_tot = O.sum(axis=0, keepdims=True)
    E = row_tot @ col_tot / n
    # Avoid div-by-zero; zero E means no cases → skip
    mask = E > 0
    chi2 = float(((O - E)[mask] ** 2 / E[mask]).sum())
    dof = (O.shape[0] - 1) * (O.shape[1] - 1)
    # Survival function of chi2 at given dof
    from math import erf, sqrt
    # Wilson-Hilferty approximation (good enough for paper)
    k = max(1, dof)
    x = chi2
    if x <= 0:
        p = 1.0
    else:
        try:
            import math
            # Regularised incomplete gamma upper, scipy-free approx
            # Use series expansion via scipy if available; else fallback:
            try:
                from scipy.stats import chi2 as _chi2
                p = float(_chi2.sf(x, k))
            except Exception:
                # Wilson-Hilferty normal approximation
                z = (((x / k) ** (1/3)) - (1 - 2/(9*k))) / math.sqrt(2/(9*k))
                p = 0.5 * (1 - erf(z / sqrt(2)))
        except Exception:
            p = 1.0
    return (round(chi2, 4), round(p, 6), int(dof))


def run_single(
    policy: LLMPolicy,
    episodes: int,
    max_steps: int,
    seed: int,
) -> dict:
    """Evaluate a single (model, seed) combination on DeceptionEnv with L1-L5 metrics."""
    rewards = []
    lengths = []
    p_reals_final = []
    survivals = []
    action_counts = np.zeros(N_BASE_ACTIONS)
    phase_actions = np.zeros((4, N_BASE_ACTIONS), dtype=np.int64)

    # L2 belief trajectory
    ep_p_real_mean: list[float] = []
    ep_p_real_std: list[float] = []
    ep_p_real_min: list[float] = []
    ep_p_real_max: list[float] = []
    ep_misbelief_ratio: list[float] = []  # fraction of steps with p_real > 0.7

    # L3 engagement
    ep_mean_step_at_exit: list[int] = []

    # L4 phase coverage
    ep_max_phase: list[int] = []
    ep_phase_advance_rate: list[float] = []  # advances / total steps
    ep_phase_reversal: list[int] = []  # count of phase regressions (rare but possible)
    step_phase_counter = np.zeros(4, dtype=np.int64)  # global phase time-share

    # Reward decomposition (L5)
    rc_belief_sum = 0.0
    rc_engage_sum = 0.0
    rc_dwell_sum = 0.0
    rc_safety_sum = 0.0
    rc_step_count = 0

    t_start = time.perf_counter()
    env = DeceptionEnv(max_steps=max_steps, action_mode="base", seed=seed)

    for ep in range(episodes):
        state = env.reset(seed=seed + ep)
        policy.reset()
        total_r = 0.0
        steps = 0
        ep_info = {}
        p_real_traj = [float(state[2])]  # initial p_real
        phase_traj = [int(round(float(state[0]) * 3))]
        advances = 0
        reversals = 0
        prev_phase = phase_traj[-1]

        while True:
            action = policy.select(state)
            action_counts[action] += 1
            cur_phase = int(round(float(state[0]) * 3))
            cur_phase = max(0, min(3, cur_phase))
            phase_actions[cur_phase, action] += 1
            step_phase_counter[cur_phase] += 1

            state, reward, done, ep_info = env.step(action)
            total_r += float(reward)
            steps += 1

            # Track p_real trajectory
            new_p = float(ep_info.get("p_real", state[2] if len(state) > 2 else 0.0))
            p_real_traj.append(new_p)
            new_phase = int(ep_info.get("phase", cur_phase))
            phase_traj.append(max(0, min(3, new_phase)))
            if new_phase > prev_phase:
                advances += 1
            elif new_phase < prev_phase:
                reversals += 1
            prev_phase = new_phase

            # Reward decomposition
            rc = ep_info.get("reward_components") or {}
            rc_belief_sum += float(rc.get("r_belief", 0.0))
            rc_engage_sum += float(rc.get("r_engage", 0.0))
            rc_dwell_sum += float(rc.get("r_dwell", 0.0))
            rc_safety_sum += float(rc.get("r_safety", 0.0))
            rc_step_count += 1

            if done or steps >= max_steps:
                break

        rewards.append(total_r)
        lengths.append(steps)
        p_reals_final.append(float(ep_info.get("p_real", p_real_traj[-1])))
        survivals.append(steps >= max_steps)
        ep_mean_step_at_exit.append(steps)

        p_arr = np.asarray(p_real_traj, dtype=np.float64)
        ep_p_real_mean.append(float(p_arr.mean()))
        ep_p_real_std.append(float(p_arr.std()))
        ep_p_real_min.append(float(p_arr.min()))
        ep_p_real_max.append(float(p_arr.max()))
        ep_misbelief_ratio.append(float((p_arr > 0.7).mean()))

        ep_max_phase.append(max(phase_traj))
        ep_phase_advance_rate.append(advances / max(1, steps))
        ep_phase_reversal.append(reversals)

        if (ep + 1) % 5 == 0 or ep == episodes - 1:
            pct = 100 * (ep + 1) / episodes
            elapsed = time.perf_counter() - t_start
            eta = elapsed / (ep + 1) * (episodes - ep - 1)
            print(f"    ep {ep + 1}/{episodes} ({pct:>5.1f}%)  "
                  f"last_R={total_r:>6.2f}  elapsed={elapsed:>5.0f}s  "
                  f"eta={eta:>5.0f}s", flush=True)

    total_acts = max(action_counts.sum(), 1.0)
    total_steps = max(int(step_phase_counter.sum()), 1)

    # L5 stats
    chi2_stat, chi2_p, chi2_dof = _chi_square_independence(phase_actions)
    js_mat = _js_divergence_matrix(phase_actions)

    result = {
        "model": policy.model,
        "seed": seed,
        "episodes": episodes,
        "max_steps": max_steps,
        # L1/L3 engagement (primary reward — but env is heuristic, see §5 limitations)
        "avg_reward": round(float(np.mean(rewards)), 4),
        "std_reward": round(float(np.std(rewards)), 4),
        "median_reward": round(float(np.median(rewards)), 4),
        "avg_length": round(float(np.mean(lengths)), 2),
        "avg_p_real": round(float(np.mean(p_reals_final)), 4),
        "survival_rate": round(float(np.mean(survivals)), 4),
        # L2 Belief metrics
        "belief_metrics": {
            "avg_p_real_mean": round(float(np.mean(ep_p_real_mean)), 4),
            "p_real_std_mean": round(float(np.mean(ep_p_real_std)), 4),
            "p_real_min_mean": round(float(np.mean(ep_p_real_min)), 4),
            "p_real_max_mean": round(float(np.mean(ep_p_real_max)), 4),
            "misbelief_duration_ratio_mean": round(float(np.mean(ep_misbelief_ratio)), 4),
        },
        # L3 Engagement
        "engagement_metrics": {
            "mean_step_at_exit": round(float(np.mean(ep_mean_step_at_exit)), 2),
            "survival_rate": round(float(np.mean(survivals)), 4),
        },
        # L4 Phase coverage
        "coverage_metrics": {
            "phase_advance_rate_mean": round(float(np.mean(ep_phase_advance_rate)), 4),
            "phase_reversal_count_mean": round(float(np.mean(ep_phase_reversal)), 4),
            "max_phase_reached_mean": round(float(np.mean(ep_max_phase)), 4),
            "time_share_per_phase": {
                f"phase_{p}": round(
                    float(step_phase_counter[p]) / total_steps, 4
                )
                for p in range(4)
            },
        },
        # L5 Policy-level
        "policy_metrics": {
            "skill_entropy_bits": round(float(_shannon_bits_from_counts(action_counts)), 4),
            "chi_square_stat": chi2_stat,
            "chi_square_pvalue": chi2_p,
            "chi_square_dof": chi2_dof,
            "phase_skill_confusion_matrix": phase_actions.tolist(),
            "phase_skill_confusion_labels": {
                "phases": ["RECON", "EXPLOIT", "PERSIST", "EXFIL"],
                "skills": list(BASE_ACTION_NAMES),
            },
            "js_divergence_phase_pairs": js_mat.round(4).tolist(),
        },
        # Reward decomposition (mean contribution per step per component)
        "reward_components_mean_per_step": {
            "r_belief": round(rc_belief_sum / max(1, rc_step_count), 4),
            "r_engage": round(rc_engage_sum / max(1, rc_step_count), 4),
            "r_dwell": round(rc_dwell_sum / max(1, rc_step_count), 4),
            "r_safety": round(rc_safety_sum / max(1, rc_step_count), 4),
        },
        # Kept for compat (downstream scripts)
        "action_distribution": {
            BASE_ACTION_NAMES[i]: round(action_counts[i] / total_acts * 100, 1)
            for i in range(N_BASE_ACTIONS)
        },
        "phase_preference": {
            f"phase_{p}": BASE_ACTION_NAMES[int(phase_actions[p].argmax())]
            for p in range(4)
        },
        "rewards_raw": [round(r, 3) for r in rewards],
        "wall_sec": round(time.perf_counter() - t_start, 1),
        "llm_summary": policy.summary(),
    }
    return result


def _shannon_bits_from_counts(counts: np.ndarray) -> float:
    total = float(counts.sum())
    if total <= 0:
        return 0.0
    probs = counts / total
    h = 0.0
    for p in probs:
        if p > 0:
            h -= float(p) * math.log2(float(p))
    return h


def aggregate(results_by_model: dict[str, list[dict]]) -> dict:
    """Compute mean + 95% CI across seeds per model."""
    from math import sqrt

    summary = {}
    for model, runs in results_by_model.items():
        rewards_per_seed = [r["avg_reward"] for r in runs]
        p_real_per_seed = [r["avg_p_real"] for r in runs]
        survival_per_seed = [r["survival_rate"] for r in runs]
        n = len(runs)
        if n == 0:
            continue
        def ci95(xs):
            if len(xs) < 2:
                return (float(xs[0]) if xs else 0.0, 0.0)
            m = float(np.mean(xs))
            s = float(np.std(xs, ddof=1))
            se = s / sqrt(len(xs))
            half = 1.96 * se
            return (m, half)
        r_m, r_h = ci95(rewards_per_seed)
        p_m, p_h = ci95(p_real_per_seed)
        s_m, s_h = ci95(survival_per_seed)
        summary[model] = {
            "n_seeds": n,
            "avg_reward_mean": round(r_m, 4),
            "avg_reward_ci95": round(r_h, 4),
            "avg_p_real_mean": round(p_m, 4),
            "avg_p_real_ci95": round(p_h, 4),
            "survival_rate_mean": round(s_m, 4),
            "survival_rate_ci95": round(s_h, 4),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        default="llama3.1:8b,qwen2.5:14b,gemma2:9b",
        help="comma-separated Ollama model tags",
    )
    parser.add_argument(
        "--seeds", default="42",
        help="comma-separated seed integers",
    )
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--macro", type=int, default=5,
                        help="LLM is called once every `macro` env steps")
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("LLM_AGENT_OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument(
        "--output-dir",
        default="results/llm_multi_seed",
    )
    parser.add_argument("--temperature", type=float, default=0.9)
    parser.add_argument("--timeout-sec", type=float, default=20.0)
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Ollama URL : {args.ollama_url}")
    print(f"Models     : {models}")
    print(f"Seeds      : {seeds}")
    print(f"Episodes   : {args.episodes}")
    print(f"Max steps  : {args.max_steps}")
    print(f"Macro      : {args.macro} (LLM call every N env steps)")
    print(f"Output dir : {out_dir}")

    results_by_model: dict[str, list[dict]] = {m: [] for m in models}
    t_global = time.perf_counter()

    for model in models:
        for seed in seeds:
            tag = f"{model.replace(':', '_')}_seed{seed}"
            print(f"\n=== {tag} ===")
            policy = LLMPolicy(
                model=model,
                ollama_url=args.ollama_url,
                timeout_sec=args.timeout_sec,
                temperature=args.temperature,
                macro=args.macro,
                rng_seed=seed,
            )
            try:
                result = run_single(
                    policy, args.episodes, args.max_steps, seed,
                )
            finally:
                policy.close()

            results_by_model[model].append(result)
            out_file = out_dir / f"{tag}.json"
            # Strip rewards_raw on disk to keep file small (keep in memory).
            to_save = {k: v for k, v in result.items() if k != "rewards_raw"}
            out_file.write_text(json.dumps(to_save, indent=2))
            print(f"    → saved {out_file}")
            print(f"    avg_R={result['avg_reward']:.2f}  "
                  f"avg_p_real={result['avg_p_real']:.3f}  "
                  f"survive={result['survival_rate']:.2%}  "
                  f"fallback={result['llm_summary']['fallback_rate']:.2%}")

    # Aggregate
    summary = aggregate(results_by_model)
    summary["_meta"] = {
        "episodes": args.episodes,
        "max_steps": args.max_steps,
        "macro": args.macro,
        "total_wall_sec": round(time.perf_counter() - t_global, 1),
        "seeds": seeds,
        "models": models,
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nSummary written → {summary_path}")
    print(json.dumps({k: v for k, v in summary.items() if not k.startswith("_")},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
