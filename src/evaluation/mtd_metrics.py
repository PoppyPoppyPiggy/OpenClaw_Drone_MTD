"""
MIRAGE-UAS MTD 효과 실측 지표

References:
  TTC/SRRP -- Sharma, D.P. (2025), Electronics 14(11):2205
              DOI: 10.3390/electronics14112205
  Entropy  -- Zhuang, DeLoach, Ou (2014), ACM MTD Workshop
              DOI: 10.1145/2663474.2663479
           -- Janani, K. (2025), arXiv:2504.11661
"""
from __future__ import annotations

import math
import statistics


# ── Attacker skill mapping (L0-L4 -> s value, Sharma 2025) ──────────────────

ATTACKER_SKILL: dict[str, float] = {
    "L0_SCRIPT_KIDDIE": 0.20,
    "L1_BASIC":         0.35,
    "L2_INTERMEDIATE":  0.55,
    "L3_ADVANCED":      0.75,
    "L4_APT":           1.00,
}

# NVD total vulnerability count (Sharma 2025 baseline K=122,774)
NVD_TOTAL_K = 122_774


# ── Eq.08: TTC single host ──────────────────────────────────────────────────

def ttc_single_host(
    v_i: int,
    m_i: int,
    theta_i: float,
    attacker_level: str,
    K: int = NVD_TOTAL_K,
) -> float:
    """
    Eq.08 -- TTC for a single host under MTD shuffling.

    ttc(h_i) = t1*p + t2*(1-p)*(1-u) + t3*u*(1-p)

    Args:
        v_i:            known vulnerabilities on this host (NVD CVE count)
        m_i:            available exploits (ExploitDB count)
        theta_i:        MTD shuffling rate = 1/T_i (T_i: reconfiguration period in days)
        attacker_level: "L0_SCRIPT_KIDDIE" .. "L4_APT"
        K:              total NVD vulnerabilities

    Returns:
        Expected TTC in days.

    Reference: Sharma (2025), Electronics 14(11):2205, Eq.7-9
    """
    s = ATTACKER_SKILL.get(attacker_level, 0.55)

    # p: prob of known vuln + known exploit
    p = 1 - math.exp(-(1 - theta_i) * v_i * m_i / K)

    # u: prob of unknown vuln + unknown exploit
    u = (1 - s) ** ((1 - theta_i) * v_i) if v_i > 0 else 0.0

    # Time parameters (days)
    t1 = 1.0

    # Known vuln + unknown exploit: 5.8 * E_T days
    v_E = max(v_i - m_i, 0)
    E_T = s * (1.0 + v_E * 0.5) if v_E > 0 else s
    t2 = 5.8 * E_T

    # Unknown vuln + unknown exploit: McQueen-based
    t3 = (30.42 * (1 / s - 0.5) + 5.8) if s > 0 else 365.0

    return t1 * p + t2 * (1 - p) * (1 - u) + t3 * u * (1 - p)


# ── Eq.09: MTTC network ─────────────────────────────────────────────────────

def mttc_network(host_ttcs: list[float]) -> float:
    """
    Eq.09 -- MTTC for a network (average over all attack paths).

    Reference: Sharma (2025), Eq.1-2
    """
    return statistics.mean(host_ttcs) if host_ttcs else 0.0


# ── Eq.10: SRRP ─────────────────────────────────────────────────────────────

def srrp(ttc_no_mtd: float, ttc_mtd: float) -> float:
    """
    Eq.10 -- Security Risk Reduction Percentage.

    SRRP = (1 - TTC_no_mtd / TTC_mtd) * 100%

    Reference: Sharma (2025), Electronics 14(11):2205, Eq.17-18
    Daily shuffling achieves ~90% SRRP (paper result).
    """
    if ttc_mtd <= 0:
        return 0.0
    return (1.0 - ttc_no_mtd / ttc_mtd) * 100.0


# ── Eq.11a: Attack Surface Entropy ──────────────────────────────────────────

def attack_surface_entropy(config_probs: list[float]) -> float:
    """
    Eq.11a -- Shannon entropy of MTD configuration distribution.

    H(X) = -sum(p_i * log2(p_i))

    Uniform distribution maximizes entropy -> maximizes attacker uncertainty.

    Reference: Janani, K. (2025), arXiv:2504.11661
    """
    probs = [p for p in config_probs if p > 0]
    if not probs:
        return 0.0
    return -sum(p * math.log2(p) for p in probs)


# ── Eq.11b: Max config entropy ──────────────────────────────────────────────

def max_config_entropy(domain_sizes: list[int]) -> float:
    """
    Eq.11b -- Maximum achievable entropy (Zhuang 2014, Theorem 4.2).

    H_C(Sigma) <= sum(log2(|Pi_i|))

    28 bits ~ 2^28 ~ 268M possible configs -> exhaustive search infeasible.

    Reference: Zhuang, DeLoach, Ou (2014), ACM MTD Workshop
               DOI: 10.1145/2663474.2663479
    """
    return sum(math.log2(d) for d in domain_sizes if d > 1)


def entropy_utilization(
    config_probs: list[float],
    domain_sizes: list[int],
) -> float:
    """Actual entropy / max entropy = MTD diversity utilization ratio."""
    h_actual = attack_surface_entropy(config_probs)
    h_max = max_config_entropy(domain_sizes)
    return h_actual / h_max if h_max > 0 else 0.0


# ── Composite computation ────────────────────────────────────────────────────

def compute_mtd_effectiveness(
    vuln_counts: dict[str, int] | None = None,
    exploit_counts: dict[str, int] | None = None,
    mtd_period_days: float = 1.0,
    port_pool_size: int = 100,
    ip_pool_size: int = 256,
    protocol_variants: int = 3,
) -> dict:
    """
    MIRAGE-UAS MTD effectiveness composite computation.

    Returns dict with:
        ttc_by_level_no_mtd / _with_mtd: per-level TTC (days)
        mttc_no_mtd_days / mttc_with_mtd_days: network MTTC
        srrp_pct: Security Risk Reduction Percentage
        entropy_bits: attack surface entropy (bits)
        entropy_max_bits: theoretical max entropy
        entropy_utilization: utilization ratio
    """
    if vuln_counts is None:
        vuln_counts = {
            "L0_SCRIPT_KIDDIE": 3, "L1_BASIC": 5,
            "L2_INTERMEDIATE": 8, "L3_ADVANCED": 10, "L4_APT": 10,
        }
    if exploit_counts is None:
        exploit_counts = {
            "L0_SCRIPT_KIDDIE": 1, "L1_BASIC": 2,
            "L2_INTERMEDIATE": 4, "L3_ADVANCED": 7, "L4_APT": 9,
        }

    theta = 1.0 / mtd_period_days

    # Per-level TTC
    ttc_no_mtd: dict[str, float] = {}
    ttc_with_mtd: dict[str, float] = {}
    for level in ATTACKER_SKILL:
        v = vuln_counts.get(level, 5)
        m = exploit_counts.get(level, 2)
        ttc_no_mtd[level] = ttc_single_host(v, m, 0.0, level)
        ttc_with_mtd[level] = ttc_single_host(v, m, theta, level)

    # Network MTTC
    mttc_no = mttc_network(list(ttc_no_mtd.values()))
    mttc_yes = mttc_network(list(ttc_with_mtd.values()))

    # Entropy: uniform distribution assumption per dimension
    domain_sizes = [port_pool_size, ip_pool_size, protocol_variants]
    h_max = max_config_entropy(domain_sizes)

    # Per-dimension uniform entropy summed (= max entropy under uniform)
    h_actual = sum(
        attack_surface_entropy([1.0 / d] * d)
        for d in domain_sizes if d > 1
    )

    h_util = h_actual / h_max if h_max > 0 else 0.0

    return {
        "ttc_by_level_no_mtd": {k: round(v, 2) for k, v in ttc_no_mtd.items()},
        "ttc_by_level_with_mtd": {k: round(v, 2) for k, v in ttc_with_mtd.items()},
        "mttc_no_mtd_days": round(mttc_no, 2),
        "mttc_with_mtd_days": round(mttc_yes, 2),
        "srrp_pct": round(srrp(mttc_no, mttc_yes), 1),
        "entropy_bits": round(h_actual, 2),
        "entropy_max_bits": round(h_max, 2),
        "entropy_utilization": round(h_util, 4),
        "mtd_period_days": mtd_period_days,
        "config_space": {
            "port_pool": port_pool_size,
            "ip_pool": ip_pool_size,
            "protocol_variants": protocol_variants,
        },
        "citations": {
            "ttc_srrp": "Sharma (2025), Electronics 14(11):2205",
            "entropy": "Zhuang et al. (2014), ACM MTD + Janani (2025), arXiv:2504.11661",
        },
    }
