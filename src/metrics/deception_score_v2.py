#!/usr/bin/env python3
"""
deception_score_v2.py — Composite Deception Effectiveness Metric

[REPLACES]
    The legacy DeceptionScore with w1/w2 weight bug noted in prior audits.
    v2 uses role-named weights (w_htur, w_belief, w_eng, w_cov, w_pol),
    each component in [0, 1], weights summing to 1.

[FORMULA] — MIRAGE-UAS Eq. DS v2
    DeceptionScore = w_htur · HTUR
                   + w_belief · max(0, avg_p_real - 0.5)
                   + w_eng   · survival_rate
                   + w_cov   · (1 - phase_advance_rate)
                   + w_pol   · H_norm
    where
      HTUR              = unique honey-tokens reused / issued       ∈ [0, 1]
      avg_p_real        = mean Bayesian belief P(real | obs)         ∈ [0, 1]
      survival_rate     = episodes attacker stayed engaged           ∈ [0, 1]
      phase_advance_rate = phase-advance events / total steps        ∈ [0, 1]
      H_norm            = skill_entropy / log2(N_skills)             ∈ [0, 1]

    The `belief` component uses (p_real - 0.5) clipped at 0 because
    P(real) ≤ 0.5 indicates the attacker no longer believes the drone
    is real — no deception credit below that threshold.

[DEFAULT WEIGHTS]
    uniform (0.2 each). Research builds may override per config/.env.
    Sensitivity analysis is expected (grid over {0.1, 0.2, 0.3, 0.4}
    per weight with constraint Σ = 1 — see §6 of paper).

[REF]
    - HTUR / CPR / FSR terminology: this work
    - Belief-based component: Pawlick & Zhu (2021), CDC
    - Survival / phase-advance: Zhuang et al. MTTC (Electronics 2025)
    - Skill entropy: HoneyGPT mode-collapse finding (Song 2024)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional


DEFAULT_WEIGHTS: dict[str, float] = {
    "w_htur": 0.20,
    "w_belief": 0.20,
    "w_eng": 0.20,
    "w_cov": 0.20,
    "w_pol": 0.20,
}

WEIGHT_KEYS = tuple(DEFAULT_WEIGHTS.keys())
N_SKILLS_DEFAULT = 5


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


@dataclass
class DeceptionScoreComponents:
    htur: float
    belief: float          # max(0, avg_p_real - 0.5)
    engagement: float      # survival_rate
    coverage: float        # 1 - phase_advance_rate
    policy: float          # H_norm in [0, 1]

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class DeceptionScoreV2:
    score: float
    components: DeceptionScoreComponents
    weights: dict[str, float]

    def as_dict(self) -> dict:
        return {
            "score": round(float(self.score), 4),
            "components": {
                k: round(float(v), 4) for k, v in self.components.as_dict().items()
            },
            "weights": {k: round(float(v), 4) for k, v in self.weights.items()},
        }


def normalise_entropy(entropy_bits: float, n_skills: int = N_SKILLS_DEFAULT) -> float:
    """Entropy normalised to [0, 1] using uniform distribution as upper bound."""
    if n_skills <= 1:
        return 0.0
    max_h = math.log2(n_skills)
    if max_h <= 0:
        return 0.0
    return _clip01(entropy_bits / max_h)


def validate_weights(weights: dict[str, float]) -> dict[str, float]:
    """Ensure weights contain all keys, non-negative, and sum to 1 (tolerant)."""
    if weights is None:
        return dict(DEFAULT_WEIGHTS)
    missing = [k for k in WEIGHT_KEYS if k not in weights]
    if missing:
        raise ValueError(f"weight dict missing keys: {missing}")
    if any(weights[k] < 0 for k in WEIGHT_KEYS):
        raise ValueError("weights must be non-negative")
    total = sum(weights[k] for k in WEIGHT_KEYS)
    if total <= 0:
        raise ValueError("weights must sum > 0")
    if not math.isclose(total, 1.0, abs_tol=1e-3):
        # Auto-normalise
        weights = {k: weights[k] / total for k in WEIGHT_KEYS}
    return dict(weights)


def compute_deception_score_v2(
    htur: float,
    avg_p_real: float,
    survival_rate: float,
    phase_advance_rate: float,
    skill_entropy_bits: float,
    n_skills: int = N_SKILLS_DEFAULT,
    weights: Optional[dict[str, float]] = None,
) -> DeceptionScoreV2:
    """Compute DeceptionScore v2 from the five L1-L5 inputs.

    All inputs must be in [0, 1] except skill_entropy_bits (raw bits).
    """
    w = validate_weights(weights)
    comp = DeceptionScoreComponents(
        htur=_clip01(htur),
        belief=_clip01(max(0.0, float(avg_p_real) - 0.5)),
        engagement=_clip01(survival_rate),
        coverage=_clip01(1.0 - float(phase_advance_rate)),
        policy=normalise_entropy(skill_entropy_bits, n_skills),
    )
    score = (
        w["w_htur"]   * comp.htur
        + w["w_belief"] * comp.belief
        + w["w_eng"]   * comp.engagement
        + w["w_cov"]   * comp.coverage
        + w["w_pol"]   * comp.policy
    )
    return DeceptionScoreV2(score=_clip01(score), components=comp, weights=w)


# ── Self-tests (run as: python -m src.metrics.deception_score_v2) ──────────


def _run_self_tests() -> None:
    # Test 1: uniform weights should give score = mean of components
    r = compute_deception_score_v2(
        htur=0.60, avg_p_real=0.90, survival_rate=0.80,
        phase_advance_rate=0.05, skill_entropy_bits=2.10,
    )
    expected_mean = (
        0.60
        + max(0, 0.90 - 0.5)
        + 0.80
        + (1 - 0.05)
        + (2.10 / math.log2(5))
    ) / 5
    assert math.isclose(r.score, expected_mean, abs_tol=1e-4), (
        f"uniform mean test failed: got {r.score}, expected {expected_mean}"
    )

    # Test 2: extreme values — must stay in [0, 1]
    for htur, p, surv, adv, h in [
        (0.0, 0.0, 0.0, 1.0, 0.0),
        (1.0, 1.0, 1.0, 0.0, math.log2(5)),
        (0.5, 0.5, 0.5, 0.5, 1.16),
    ]:
        r = compute_deception_score_v2(htur, p, surv, adv, h)
        assert 0.0 <= r.score <= 1.0, f"score {r.score} out of [0,1]"

    # Test 3: belief threshold — p_real <= 0.5 gives 0 belief credit
    r_low = compute_deception_score_v2(0.0, 0.45, 0.0, 1.0, 0.0)
    assert r_low.components.belief == 0.0, (
        f"expected 0 belief at p_real=0.45, got {r_low.components.belief}"
    )

    # Test 4: custom weights auto-normalise when they don't sum to 1
    r_custom = compute_deception_score_v2(
        htur=0.5, avg_p_real=0.8, survival_rate=0.8,
        phase_advance_rate=0.1, skill_entropy_bits=2.0,
        weights={
            "w_htur": 2.0, "w_belief": 1.0, "w_eng": 1.0,
            "w_cov": 1.0, "w_pol": 1.0,  # sums to 6
        },
    )
    total_w = sum(r_custom.weights.values())
    assert math.isclose(total_w, 1.0, abs_tol=1e-4), (
        f"weights not normalised: sum = {total_w}"
    )

    # Test 5: weight validation catches negative
    try:
        validate_weights({
            "w_htur": -0.1, "w_belief": 0.3, "w_eng": 0.3,
            "w_cov": 0.3, "w_pol": 0.2,
        })
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for negative weight")

    print("OK — deception_score_v2 self-tests passed (5/5)")


if __name__ == "__main__":
    _run_self_tests()
