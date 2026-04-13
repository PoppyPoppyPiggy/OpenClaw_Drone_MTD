"""
MIRAGE-UAS 하이브리드 방어 통합 평가

Reference:
  Hou et al. (2025), "Effectiveness Evaluation Method for Hybrid Defense
  of MTD and Cyber Deception," Computers 14(12):513
  DOI: 10.3390/computers14120513

  논문 핵심: 하이브리드(MTD+기만)가 MTD 단독 대비 서비스율 79.66% vs 55.67%
"""
from __future__ import annotations

import math
import statistics


# ── Eq.13: Hybrid attack success probability ────────────────────────────────

def hybrid_attack_success_static(
    t: float,
    P0: float,
    r: int,
    gamma_t: float,
    k0: float,
    f_m_eff: float,
) -> float:
    """
    Eq.13a -- Static attacker attack success probability P_s(t).

    P_s(t) = P0 + (1 - r*gamma(t)) * (k0/f'_m) * t

    Args:
        t:       elapsed time
        P0:      initial attack success probability
        r:       number of deployed honeydrones
        gamma_t: decoy access probability at time t
        k0:      static attack success rate increase parameter
        f_m_eff: effective MTD shuffle frequency

    Reference: Hou et al. (2025), Computers 14(12):513, Eq.13
    """
    return P0 + (1 - r * gamma_t) * (k0 / f_m_eff) * t


def hybrid_attack_success_adaptive(
    t: float,
    P0: float,
    r: int,
    gamma_t: float,
    a: float,
    f_m_eff: float,
) -> float:
    """
    Eq.13b -- Adaptive attacker attack success probability P_c(t).

    P_c(t) = P0 + (1 - r*gamma(t)) * (1 - e^{-a*t}) / f'_m

    Reference: Hou et al. (2025), Computers 14(12):513, Eq.13
    """
    return P0 + (1 - r * gamma_t) * (1 - math.exp(-a * t)) / f_m_eff


# ── Eq.15: Defender payoff function ──────────────────────────────────────────

def defender_payoff(
    p_attack: float,
    w_l: float,
    w_0: float,
    r_d: float,
    r_0: float,
    f_m_eff: float,
    F_m: float,
    f_d_eff: float,
    F_d: float,
) -> float:
    """
    Eq.15 -- Defender Payoff (Security + Reliability - Cost).

    U* = [1 - P_attack(t)]              (Security)
       + [1 - W_l/W_0 - R_d/R_0]       (Reliability)
       - [f'_m/F_m + f'_d/F_d]          (Defense Cost)

    Args:
        p_attack:       attack success probability
        w_l, w_0:       actual / max tolerable latency
        r_d, r_0:       actual / max tolerable packet drop rate
        f_m_eff, F_m:   effective / max MTD shuffle frequency
        f_d_eff, F_d:   effective / max decoy update frequency

    Reference: Hou et al. (2025), Computers 14(12):513, Eq.15
    """
    security = 1 - p_attack
    reliability = 1 - w_l / w_0 - r_d / r_0
    cost = f_m_eff / F_m + f_d_eff / F_d
    return security + reliability - cost


# ── Experiment-log-based approximation ───────────────────────────────────────

def compute_from_experiment_log(sessions: list[dict]) -> dict:
    """
    Convert attacker_log.jsonl measured values into Hou 2025 metrics.
    Uses log data only -- no simulation parameters needed.
    """
    if not sessions:
        return {
            "p_attack": 1.0,
            "availability": 0.0,
            "defense_cost": 0.0,
            "payoff": 0.0,
            "n_sessions": 0,
        }

    n = len(sessions)
    breached = sum(1 for s in sessions if s.get("breached", False))
    disrupted = sum(1 for s in sessions if s.get("service_disrupted", False))
    avg_mtd_cost = statistics.mean(
        [s.get("mtd_cost", 0.0) for s in sessions]
    )

    p_attack = breached / n
    availability = 1 - disrupted / n
    defense_cost = avg_mtd_cost

    # Payoff approximation (W_l/W_0, R_d/R_0 estimated from availability)
    payoff = (1 - p_attack) + availability - defense_cost

    return {
        "p_attack": round(p_attack, 4),
        "availability": round(availability, 4),
        "defense_cost": round(defense_cost, 4),
        "payoff": round(payoff, 4),
        "n_sessions": n,
    }


def build_hybrid_defense_table(
    sessions_no_defense: list[dict],
    sessions_mtd_only: list[dict],
    sessions_deception_only: list[dict],
    sessions_mirage: list[dict],
) -> dict:
    """
    Build Hou 2025 comparison table: 4 defense conditions.

    Reference: Hou et al. (2025), Computers 14(12):513, Tables 3-4
    Expected: MIRAGE (hybrid) achieves lowest P_attack and highest Payoff.
    """
    rows = []
    for label, sessions in [
        ("No defense", sessions_no_defense),
        ("MTD only", sessions_mtd_only),
        ("Deception only", sessions_deception_only),
        ("MIRAGE-UAS (Hybrid)", sessions_mirage),
    ]:
        metrics = compute_from_experiment_log(sessions)
        metrics["condition"] = label
        rows.append(metrics)

    return {
        "table": rows,
        "citation": "Hou et al. (2025), Computers 14(12):513, DOI:10.3390/computers14120513",
        "interpretation": (
            "MIRAGE-UAS should show: lowest P_attack, "
            "highest availability, highest payoff"
        ),
    }
