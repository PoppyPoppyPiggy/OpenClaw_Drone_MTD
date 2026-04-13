"""
MIRAGE-UAS CTI 데이터셋 품질 지표

References:
  TTP Coverage    -- MITRE ATT&CK for ICS v14 (2023)
  STIX Complete.  -- Schlette et al. (2021) Int.J.InfoSec 20:21-38
                     DOI: 10.1007/s10207-020-00490-y
  Dataset Balance -- Garcia et al. (2019) Pattern Recognition 91:216-231
                     DOI: 10.1016/j.patcog.2019.02.023
  Novel TTP Rate  -- Wang et al. (2024) HoneyGPT arXiv:2406.01882
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

# ATT&CK for ICS v14 total technique count (2023)
ATTCK_ICS_TOTAL = 83

# TTPs actually mapped in MIRAGE-UAS attck_mapper.py (21 techniques)
MIRAGE_MAPPED_TTPS: set[str] = {
    "T0807", "T0809", "T0812", "T0813", "T0815",
    "T0820", "T0821", "T0830", "T0831", "T0836",
    "T0839", "T0840", "T0842", "T0843", "T0849",
    "T0855", "T0856", "T0858", "T0882", "T0886",
    "T0888",
}

# STIX 2.1 Attack Pattern optional fields for completeness scoring
STIX_OPTIONAL_FIELDS = [
    "aliases",
    "external_references",
    "kill_chain_phases",
    "x_mitre_detection",
    "x_mitre_platforms",
    "x_mitre_data_sources",
]


# ── Eq.17: TTP Coverage Rate ────────────────────────────────────────────────

def ttp_coverage_rate(
    detected_ttps: set[str],
    attck_ics_total: int = ATTCK_ICS_TOTAL,
) -> float:
    """
    Eq.17 -- TTP Coverage Rate.

    Coverage = |TTPs_detected intersect ATT&CK_ICS| / |ATT&CK_ICS_total|

    Reference: MITRE ATT&CK for ICS v14 (2023), 83 techniques
    """
    ics_ttps = {t for t in detected_ttps if t.startswith("T0")}
    return len(ics_ttps) / attck_ics_total


def ttp_coverage_detail(detected_ttps: set[str]) -> dict:
    """TTP coverage breakdown with mapped vs total analysis."""
    ics_detected = {t for t in detected_ttps if t.startswith("T0")}
    coverage = len(ics_detected) / ATTCK_ICS_TOTAL

    mapped_detected = ics_detected & MIRAGE_MAPPED_TTPS
    coverage_of_mapped = (
        len(mapped_detected) / len(MIRAGE_MAPPED_TTPS)
        if MIRAGE_MAPPED_TTPS
        else 0.0
    )

    return {
        "detected_ttp_count": len(ics_detected),
        "attck_ics_total": ATTCK_ICS_TOTAL,
        "coverage_rate": round(coverage, 4),
        "coverage_of_mapped": round(coverage_of_mapped, 4),
        "mapped_in_mirage": len(MIRAGE_MAPPED_TTPS),
        "detected_list": sorted(ics_detected),
        "citation": "MITRE ATT&CK for ICS v14 (2023), https://attack.mitre.org/matrices/ics/",
    }


# ── Eq.18: STIX Bundle Completeness ─────────────────────────────────────────

def stix_object_completeness(
    stix_obj: dict,
    optional_fields: list[str] | None = None,
) -> float:
    """
    Eq.18 -- STIX Object Completeness.

    SC(o) = sum(v(a_o)) / |A_optional(o)|

    Reference: Schlette et al. (2021), Int.J.InfoSec 20:21-38
    """
    if optional_fields is None:
        optional_fields = STIX_OPTIONAL_FIELDS
    if not optional_fields:
        return 0.0
    filled = sum(
        1
        for f in optional_fields
        if stix_obj.get(f) not in (None, [], {}, "")
    )
    return filled / len(optional_fields)


def bundle_completeness(bundle_path: str) -> dict:
    """Compute average completeness of attack-pattern objects in a STIX bundle."""
    path = Path(bundle_path)
    if not path.exists():
        return {"error": f"file not found: {bundle_path}"}

    with open(path) as f:
        bundle = json.load(f)

    objects = bundle.get("objects", [])
    attack_patterns = [o for o in objects if o.get("type") == "attack-pattern"]

    if not attack_patterns:
        return {"attack_pattern_count": 0, "avg_completeness": 0.0}

    scores = [stix_object_completeness(o) for o in attack_patterns]
    return {
        "attack_pattern_count": len(attack_patterns),
        "avg_completeness": round(statistics.mean(scores), 4),
        "min_completeness": round(min(scores), 4),
        "max_completeness": round(max(scores), 4),
        "citation": "Schlette et al. (2021), DOI:10.1007/s10207-020-00490-y",
    }


# ── Eq.19: Dataset Balance ───────────────────────────────────────────────────

def dataset_balance(label_counts: dict[str, int]) -> dict:
    """
    Eq.19 -- Dataset Balance via Normalized Shannon Entropy.

    H_norm = -sum(p_i * log2(p_i)) / log2(k)

    H_norm=1.0: perfectly balanced, H_norm->0: extremely imbalanced.

    Reference: Garcia et al. (2019), Pattern Recognition 91:216-231
    """
    total = sum(label_counts.values())
    if total == 0:
        return {"h_norm": 0.0, "imbalance_ratio": None}

    k = len(label_counts)
    probs = [c / total for c in label_counts.values()]

    h = -sum(p * math.log2(p) for p in probs if p > 0)
    h_norm = h / math.log2(k) if k > 1 else 0.0

    counts = list(label_counts.values())
    ir = max(counts) / min(counts) if min(counts) > 0 else float("inf")

    return {
        "label_counts": label_counts,
        "total_samples": total,
        "h_normalized": round(h_norm, 4),
        "imbalance_ratio": round(ir, 2),
        "is_balanced": h_norm > 0.8,
        "citation": "Garcia et al. (2019), Pattern Recognition 91:216-231",
    }


# ── Eq.20: Novel TTP Discovery Rate ─────────────────────────────────────────

def novel_ttp_rate(
    session_ttps: list[str],
    known_baseline_ttps: set[str] | None = None,
) -> float:
    """
    Eq.20 -- Novel TTP Discovery Rate.

    NoveltyRate = |TTPs_discovered \\ TTPs_ATT&CK_existing| / |TTPs_discovered|

    Reference: Wang et al. (2024), HoneyGPT arXiv:2406.01882
    Field deployment captured ~45% novel ATT&CK techniques.
    """
    if known_baseline_ttps is None:
        known_baseline_ttps = MIRAGE_MAPPED_TTPS
    discovered = set(session_ttps)
    if not discovered:
        return 0.0
    novel = discovered - known_baseline_ttps
    return len(novel) / len(discovered)


# ── Composite CTI quality computation ────────────────────────────────────────

def compute_cti_quality(
    attack_events_path: str = "results/dataset/attack_events.jsonl",
    stix_bundles_dir: str = "results/dataset/stix",
) -> dict:
    """
    Compute all CTI quality metrics from attack_events.jsonl + STIX bundles.
    """
    all_ttps: list[str] = []
    level_counts: dict[str, int] = {}

    events_path = Path(attack_events_path)
    if events_path.exists():
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                ttps = entry.get("ttp_ids", entry.get("ttps", []))
                level = entry.get("attacker_level", "unknown")
                all_ttps.extend(ttps)
                level_counts[level] = level_counts.get(level, 0) + 1

    unique_ttps = set(all_ttps)

    # STIX bundle completeness
    bundle_scores: list[float] = []
    stix_path = Path(stix_bundles_dir)
    if stix_path.exists():
        for bundle_file in stix_path.glob("*.json"):
            score = bundle_completeness(str(bundle_file))
            if "avg_completeness" in score:
                bundle_scores.append(score["avg_completeness"])

    # Dataset balance by attacker level
    balance = dataset_balance(level_counts) if level_counts else {}

    return {
        "ttp_coverage": ttp_coverage_detail(unique_ttps),
        "stix_completeness": {
            "avg": round(statistics.mean(bundle_scores), 4) if bundle_scores else None,
            "bundle_count": len(bundle_scores),
        },
        "dataset_balance": balance,
        "novel_ttp_rate": round(novel_ttp_rate(all_ttps), 4),
        "total_events": len(all_ttps),
        "unique_ttps_count": len(unique_ttps),
    }
