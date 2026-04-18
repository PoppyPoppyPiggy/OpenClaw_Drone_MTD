#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# train_multi_seed.sh — Parallel 3-seed training for CI computation
#
# [ROLE]
#   Run train_dqn.py across N seeds (default: 42, 1337, 2024) simultaneously
#   on a single GPU via time-slicing. Also trains game-signaling adversary
#   per seed. Used to populate confidence intervals for Table VII.
#
# [OUTPUT]
#   results/models/dqn_deception_agent_seed{42,1337,2024}.pt
#   results/models/game_attacker_vs_signaling_seed{...}.pt
#   results/models/multi_seed_log.json  — aggregated stats
#
# Usage:
#   bash scripts/train_multi_seed.sh                         # defaults
#   SEEDS="11 22 33 44 55" DQN_EPISODES=5000 bash scripts/train_multi_seed.sh
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

SEEDS="${SEEDS:-42 1337 2024}"
DQN_EPISODES="${DQN_EPISODES:-3000}"
GAME_EPISODES="${GAME_EPISODES:-1500}"
EVAL_EPISODES="${EVAL_EPISODES:-300}"
BATCH="${BATCH:-256}"

LOG_ROOT="results/logs/multi_seed_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_ROOT"
mkdir -p results/models

echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  MIRAGE-UAS Multi-Seed Training${NC}"
echo -e "${GREEN}  seeds:      ${SEEDS}${NC}"
echo -e "${GREEN}  DQN eps:    ${DQN_EPISODES}${NC}"
echo -e "${GREEN}  game eps:   ${GAME_EPISODES}${NC}"
echo -e "${GREEN}  eval eps:   ${EVAL_EPISODES}${NC}"
echo -e "${GREEN}  log root:   ${LOG_ROOT}${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"

if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# ── Launch DQN training for every seed concurrently ───────────────────────────
# GPU is shared; CUDA time-slicing will serialize kernels automatically.
# Total wall time ≈ max(single-seed) × K / parallel_efficiency. For DQN on
# small nets this is ~1.5-2× faster than sequential on an RTX 5090.
DQN_PIDS=()
for SEED in $SEEDS; do
    LOG="${LOG_ROOT}/dqn_seed${SEED}.log"
    echo -e "${CYAN}[DQN  seed=${SEED}] launching → ${LOG}${NC}"
    (
        python3 scripts/train_dqn.py \
            --episodes "$DQN_EPISODES" \
            --batch-size "$BATCH" \
            --seed "$SEED" \
            --eval-episodes "$EVAL_EPISODES" \
            --eval-seed $((SEED + 10000)) \
            > "$LOG" 2>&1 || echo "[DQN seed=${SEED}] FAILED" >> "$LOG"
        # Rename canonical output to per-seed checkpoint
        if [[ -f results/models/dqn_deception_agent.pt ]]; then
            mv -f results/models/dqn_deception_agent.pt \
                  "results/models/dqn_deception_agent_seed${SEED}.pt"
        fi
    ) &
    DQN_PIDS+=($!)
done

echo ""
echo -e "${YELLOW}Waiting for ${#DQN_PIDS[@]} parallel DQN runs...${NC}"
for pid in "${DQN_PIDS[@]}"; do
    wait "$pid" || echo -e "${RED}  pid $pid exited non-zero${NC}"
done
echo -e "${GREEN}All DQN seeds done.${NC}"

# ── Frozen-solver attacker (sequential — quick, doesn't need parallel) ────────
for SEED in $SEEDS; do
    LOG="${LOG_ROOT}/game_signaling_seed${SEED}.log"
    echo -e "${CYAN}[GAME seed=${SEED}] frozen-solver attacker training${NC}"
    python3 scripts/train_game.py \
        --defender-policy signaling_eq \
        --episodes "$GAME_EPISODES" \
        --eval-episodes "$EVAL_EPISODES" \
        > "$LOG" 2>&1 || echo "[GAME seed=${SEED}] FAILED"
    if [[ -f results/models/game_attacker_vs_signaling.pt ]]; then
        mv -f results/models/game_attacker_vs_signaling.pt \
              "results/models/game_attacker_vs_signaling_seed${SEED}.pt"
    fi
done

# ── Aggregate eval rewards into CIs ───────────────────────────────────────────
python3 <<PY
import json, glob, re, os, math
from pathlib import Path

log_root = Path("${LOG_ROOT}")
seeds = "${SEEDS}".split()

def parse_dqn(path):
    """Grep the final-eval line from a train_dqn log."""
    avg_r = None; avg_p = None
    try:
        text = Path(path).read_text()
    except Exception:
        return None
    m = re.search(r"Avg reward:\s+([+-]?\d+\.\d+)", text)
    if m: avg_r = float(m.group(1))
    m = re.search(r"Avg P\(real\):\s+([+-]?\d+\.\d+)", text)
    if m: avg_p = float(m.group(1))
    return {"avg_reward": avg_r, "avg_p_real": avg_p}

def parse_game(path):
    """Grep exploitability Δr_atk from game training log."""
    try:
        text = Path(path).read_text()
    except Exception:
        return None
    m = re.search(r"Exploitability:\s+Δr_atk\s*=\s*([+-]?\d+\.\d+)", text)
    return {"exploitability_delta": float(m.group(1))} if m else None

def summarize(rows):
    vals = [r for r in rows if r is not None]
    if not vals:
        return None
    keys = vals[0].keys()
    out = {}
    for k in keys:
        xs = [v[k] for v in vals if v.get(k) is not None]
        if not xs:
            continue
        mean = sum(xs) / len(xs)
        var = sum((x-mean)**2 for x in xs) / max(len(xs)-1, 1)
        sd = math.sqrt(var)
        se = sd / math.sqrt(len(xs))
        out[k] = {
            "mean": round(mean, 4), "sd": round(sd, 4), "se": round(se, 4),
            "n": len(xs), "ci95_low": round(mean - 1.96*se, 4),
            "ci95_high": round(mean + 1.96*se, 4),
            "values": [round(x, 4) for x in xs],
        }
    return out

dqn_rows = [parse_dqn(log_root / f"dqn_seed{s}.log") for s in seeds]
game_rows = [parse_game(log_root / f"game_signaling_seed{s}.log") for s in seeds]

agg = {
    "seeds": seeds,
    "dqn_episodes": ${DQN_EPISODES},
    "game_episodes": ${GAME_EPISODES},
    "eval_episodes": ${EVAL_EPISODES},
    "batch_size": ${BATCH},
    "log_root": str(log_root),
    "dqn": summarize(dqn_rows),
    "game_vs_signaling": summarize(game_rows),
    "per_seed_dqn": dict(zip(seeds, dqn_rows)),
    "per_seed_game": dict(zip(seeds, game_rows)),
}

out_path = Path("results/models/multi_seed_log.json")
out_path.write_text(json.dumps(agg, indent=2))
print(f"aggregated → {out_path}")
if agg["dqn"]:
    for k, v in agg["dqn"].items():
        print(f"  DQN  {k:10s}: mean={v['mean']:8.3f}  sd={v['sd']:.3f}  95% CI=[{v['ci95_low']:.3f}, {v['ci95_high']:.3f}]  n={v['n']}")
if agg["game_vs_signaling"]:
    for k, v in agg["game_vs_signaling"].items():
        print(f"  GAME {k:24s}: mean={v['mean']:8.3f}  sd={v['sd']:.3f}  95% CI=[{v['ci95_low']:.3f}, {v['ci95_high']:.3f}]")
PY

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Multi-seed training complete${NC}"
echo -e "${GREEN}  Aggregated log: results/models/multi_seed_log.json${NC}"
echo -e "${GREEN}  Per-seed logs:  ${LOG_ROOT}/${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
