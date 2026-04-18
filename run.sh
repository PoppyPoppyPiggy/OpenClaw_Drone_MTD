#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# MIRAGE-UAS — End-to-End Experiment Pipeline (v0.3.0)
#
# Author   : DS Lab / 민성
# Project  : MIRAGE-UAS (CCS 2026)
#
# Phases (each can be independently skipped via flags):
#   0. Environment + prerequisites
#   1. Host-level verification (Python agent + container entrypoint)
#   2. Offline training on GPU  (tune → DQN → h-DQN → Game-EQ → frozen-solver BR)
#   3. Docker stack run         (build image → compose up → attacker L0-L4 → down)
#   4. Analysis                 (compare_policies, analyze_game, figures, LaTeX)
#   5. Policy sweep (optional)  (dqn / signaling_eq / hybrid, archived)
#
# Flags:
#   --fast                 short durations (≈2–3 min total, for iteration)
#   --skip-install         skip apt/pip prereq install
#   --skip-verify          skip host-level verification (phase 1)
#   --skip-train           skip offline training (phase 2)
#   --skip-docker          skip docker experiments (phase 3)
#   --skip-analysis        skip analysis (phase 4)
#   --skip-sweep           skip multi-policy sweep (phase 5)
#   --only-sweep           run only the sweep (phase 5), skip 1-4
#   --policy <p>           when running phase 3, use this defender policy
#                          (dqn | signaling_eq | hybrid; default: signaling_eq)
#   --duration SEC         attacker level duration (default 600 / --fast=60)
#   --verbose              LOG_LEVEL=DEBUG + set -x
#   --no-color             disable ANSI colour codes
#   -h | --help            show this text
#
# Everything is teed to results/logs/run_<TS>/phase_<NN>_<name>.log.
# Training checkpoints live in results/models/ and are NEVER deleted by this
# script (preserved across --fast iterations and sweeps).
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

# ──────────── CLI parsing ───────────────────────────────────────────────────────
FAST=0
SKIP_INSTALL=0
SKIP_VERIFY=0
SKIP_TRAIN=0
SKIP_DOCKER=0
SKIP_ANALYSIS=0
SKIP_SWEEP=1        # sweep is opt-in by default (adds 3× runtime)
ONLY_SWEEP=0
VERBOSE=0
POLICY="${DEFENDER_POLICY:-signaling_eq}"
DURATION="${ATTACKER_LEVEL_DURATION_SEC:-}"
NO_COLOR=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fast)           FAST=1; shift ;;
        --skip-install)   SKIP_INSTALL=1; shift ;;
        --skip-verify)    SKIP_VERIFY=1; shift ;;
        --skip-train)     SKIP_TRAIN=1; shift ;;
        --skip-docker)    SKIP_DOCKER=1; shift ;;
        --skip-analysis)  SKIP_ANALYSIS=1; shift ;;
        --skip-sweep)     SKIP_SWEEP=1; shift ;;
        --only-sweep)     ONLY_SWEEP=1; SKIP_SWEEP=0; shift ;;
        --run-sweep)      SKIP_SWEEP=0; shift ;;
        --policy)         POLICY="$2"; shift 2 ;;
        --duration)       DURATION="$2"; shift 2 ;;
        --verbose)        VERBOSE=1; shift ;;
        --no-color)       NO_COLOR=1; shift ;;
        -h|--help)        sed -n '2,45p' "$0"; exit 0 ;;
        *)                echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

[[ $FAST -eq 1 && -z "${DURATION}" ]] && DURATION=60
[[ -z "${DURATION}" ]] && DURATION=600

# ──────────── Colours + logging ────────────────────────────────────────────────
if [[ $NO_COLOR -eq 1 || ! -t 1 ]]; then
    RED='' GREEN='' CYAN='' YELLOW='' MAGENTA='' BOLD='' NC=''
else
    RED=$'\033[0;31m' GREEN=$'\033[0;32m' CYAN=$'\033[0;36m'
    YELLOW=$'\033[1;33m' MAGENTA=$'\033[0;35m' BOLD=$'\033[1m' NC=$'\033[0m'
fi

TS="$(date +%Y%m%d_%H%M%S)"
LOG_ROOT="${ROOT}/results/logs/run_${TS}"
mkdir -p "${LOG_ROOT}"
MAIN_LOG="${LOG_ROOT}/run.log"

# Mirror everything to main log (keep stdout for terminal display)
exec > >(tee -a "${MAIN_LOG}") 2>&1

[[ $VERBOSE -eq 1 ]] && set -x
[[ $VERBOSE -eq 1 ]] && export LOG_LEVEL=DEBUG || export LOG_LEVEL=${LOG_LEVEL:-INFO}

banner() {
    echo ""
    echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${GREEN}  $1${NC}"
    echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════════════${NC}"
}
phase() {   echo -e "\n${BOLD}${CYAN}▶ PHASE $1 — $2${NC}"; }
step()  {   echo -e "${CYAN}  ·${NC} $1"; }
ok()    {   echo -e "  ${GREEN}✓${NC} $1"; }
warn()  {   echo -e "  ${YELLOW}!${NC} $1"; }
err()   {   echo -e "  ${RED}✗${NC} $1" >&2; }
die()   {   err "$1"; exit 1; }

# Run a command, tee output to a per-phase log, display live.
run_phase() {
    local phase_id="$1"; shift
    local name="$1"; shift
    local log="${LOG_ROOT}/phase_${phase_id}_${name}.log"
    step "Logging → ${log#${ROOT}/}"
    if "$@" 2>&1 | tee -a "${log}"; then
        return 0
    else
        local rc=${PIPESTATUS[0]}
        err "phase ${phase_id} (${name}) exited with code ${rc}"
        return "${rc}"
    fi
}

# ──────────── Banner ───────────────────────────────────────────────────────────
banner "MIRAGE-UAS End-to-End Pipeline   [run_${TS}]"
echo "  ROOT:        ${ROOT}"
echo "  Mode:        $([[ $FAST -eq 1 ]] && echo 'FAST (short)' || echo 'full')"
echo "  Policy:      ${POLICY}"
echo "  Duration:    ${DURATION}s × 5 levels"
echo "  Verbose:     $([[ $VERBOSE -eq 1 ]] && echo yes || echo no)"
echo "  Log root:    ${LOG_ROOT#${ROOT}/}"
echo "  Skip map:    install=$SKIP_INSTALL verify=$SKIP_VERIFY train=$SKIP_TRAIN docker=$SKIP_DOCKER analysis=$SKIP_ANALYSIS sweep=$SKIP_SWEEP"
[[ $ONLY_SWEEP -eq 1 ]] && echo "  ONLY-SWEEP mode: skipping phases 1-4"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

# ──────────── PHASE 0: Prerequisites + config ─────────────────────────────────
phase 00 "Environment + prerequisites"

if [[ $SKIP_INSTALL -eq 0 ]]; then
    step "Python 3 version"
    python3 --version || die "python3 not installed"

    if [[ -d .venv ]]; then
        step "Activating .venv"
        # shellcheck disable=SC1091
        source .venv/bin/activate
    else
        warn ".venv not found — using system Python (consider: python3 -m venv .venv)"
    fi

    step "Python dependencies"
    python3 -m pip install -q \
        pymavlink websockets aiohttp python-dotenv structlog stix2 numpy \
        matplotlib scipy statsmodels fastapi uvicorn pyyaml aiofiles \
        2>&1 | tail -1 || warn "pip install reported warnings"
    if command -v nvidia-smi >/dev/null 2>&1; then
        step "CUDA GPU detected"
        nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | sed 's/^/    /'
    else
        warn "No NVIDIA GPU detected — training will run on CPU (slow)"
    fi
else
    step "skipping pip install (--skip-install)"
    [[ -d .venv ]] && source .venv/bin/activate
fi

step "Bootstrapping config/.env"
if [[ ! -f config/.env ]]; then
    cp config/.env.example config/.env
    ok "Copied .env.example → .env"
fi

# Fill research-parameter defaults if blank. These mirror scripts/verify_honeydrone.py
# defaults — sensible values, NOT tuned; tune_signaling will overwrite some later.
python3 <<'PY'
from pathlib import Path
env = Path("config/.env")
lines = env.read_text().splitlines()
DEFAULTS = {
    "MTD_COST_SENSITIVITY_KAPPA": "0.5",
    "MTD_ALPHA_WEIGHTS": "0.1,0.15,0.1,0.15,0.2,0.1,0.2",
    "MTD_BREACH_PREVENTION_BETA": "0.5",
    "COMPROMISE_P_BASE": "0.3",
    "DES_WEIGHT_LIST": "0.25,0.25,0.25,0.25",
    "REDUNDANCY_REWARD_HIGH": "0.5", "REDUNDANCY_REWARD_LOW": "0.1",
    "REDUNDANCY_THRESHOLD": "0.5",
    "DECEPTION_LAMBDA": "0.5", "DECEPTION_WEIGHTS": "0.4,0.3,0.3",
    "DECEPTION_DWELL_MAX_SEC": "300",
    "ATTACKER_PRIORS": "0.2,0.2,0.2,0.2,0.2",
    "PPO_LEARNING_RATE": "3e-4", "PPO_GAMMA": "0.99",
    "PPO_CLIP_EPS": "0.2", "PPO_ENTROPY_COEF": "0.01",
    "AGENT_PROACTIVE_INTERVAL_SEC": "8.0",
    "AGENT_SYSID_ROTATION_SEC": "60.0",
    "AGENT_PORT_ROTATION_SEC": "90.0",
    "AGENT_FALSE_FLAG_DWELL_THRESHOLD": "60.0",
    "AGENT_MIRROR_SERVICE_THRESHOLD": "3",
    "DECEPTION_SCORE_WEIGHTS": "0.25,0.2,0.2,0.15,0.2",
    "DEFENDER_POLICY": "signaling_eq",
    "SIGNALING_KAPPA": "0.5", "SIGNALING_TEMPERATURE": "0.8",
    "SIGNALING_EPSILON": "0.10", "SIGNALING_LEARNING_RATE": "0.1",
}
changed = 0
seen = set()
new = []
for ln in lines:
    s = ln.strip()
    if s and not s.startswith("#") and "=" in s:
        k, v = s.split("=", 1)
        if k in DEFAULTS and not v.strip():
            ln = f"{k}={DEFAULTS[k]}"
            changed += 1
        seen.add(k)
    new.append(ln)
for k, v in DEFAULTS.items():
    if k not in seen:
        new.append(f"{k}={v}")
        changed += 1
env.write_text("\n".join(new) + "\n")
print(f"  filled {changed} missing/blank defaults")
PY
ok ".env ready"

step "Docker availability"
DOCKER_CMD="docker"
DOCKER_OK=0
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        DOCKER_OK=1
        ok "docker daemon reachable as current user"
    elif id -nG "$(id -un)" | grep -qw docker; then
        # User is in docker group but group not yet applied to this shell.
        # Use sg(1) to enter the docker group context for each docker call.
        if sg docker -c 'docker info' >/dev/null 2>&1; then
            DOCKER_CMD="sg docker -c"
            DOCKER_OK=1
            ok "docker reachable via 'sg docker -c' (new group not yet in shell)"
        fi
    fi
fi
if [[ $DOCKER_OK -eq 0 ]]; then
    warn "docker not available — phase 3 will be SKIPPED"
    warn "Install: bash scripts/install_docker.sh && newgrp docker"
    SKIP_DOCKER=1
fi
export DOCKER_CMD DOCKER_OK

# _docker: run arbitrary docker/compose commands under the right group context
# Usage:  _docker ps
#         _docker compose -f config/docker-compose.honey.yml up -d
_docker() {
    if [[ "$DOCKER_CMD" == "docker" ]]; then
        docker "$@"
    else
        # sg wraps the command string; escape args safely.
        local cmd=(docker "$@")
        local joined
        printf -v joined '%q ' "${cmd[@]}"
        sg docker -c "${joined}"
    fi
}

# ──────────── PHASE 1: Host-level verification ────────────────────────────────
if [[ $ONLY_SWEEP -eq 0 && $SKIP_VERIFY -eq 0 ]]; then
    phase 01 "Host verification (agent + container entrypoint)"
    DUR=$([[ $FAST -eq 1 ]] && echo 6 || echo 10)
    run_phase 01a verify_agent       python3 scripts/verify_honeydrone.py --duration "$DUR" \
        || die "agent verification failed — fix before running other phases"
    CDUR=$([[ $FAST -eq 1 ]] && echo 8 || echo 15)
    run_phase 01b verify_container   python3 scripts/verify_container_entry.py --duration "$CDUR" \
        || warn "container-entry verification failed (non-fatal — continuing)"
else
    phase 01 "Host verification — SKIPPED"
fi

# ──────────── PHASE 2: Offline training ────────────────────────────────────────
train_dqn_eps=3000
train_hdqn_steps=300000
train_game_rounds=4
train_game_eps=1500
tune_eps=200
if [[ $FAST -eq 1 ]]; then
    train_dqn_eps=200
    train_hdqn_steps=20000
    train_game_rounds=2
    train_game_eps=150
    tune_eps=30
fi

if [[ $ONLY_SWEEP -eq 0 && $SKIP_TRAIN -eq 0 ]]; then
    phase 02 "Offline training (GPU)"

    step "2.1 tune Signaling-Eq κ×τ grid (${tune_eps} ep/attacker/cell)"
    run_phase 02a tune_signaling      python3 scripts/tune_signaling.py \
        --episodes "$tune_eps" --apply

    step "2.2 DQN (episodes=${train_dqn_eps}, batch=256, seed=42)"
    run_phase 02b train_dqn           python3 scripts/train_dqn.py \
        --episodes "$train_dqn_eps" --batch-size 256 --seed 42

    step "2.3 h-DQN (total_steps=${train_hdqn_steps}, n_envs=512)"
    run_phase 02c train_hdqn          python3 scripts/train_hdqn.py \
        --n-envs 512 --total-steps "$train_hdqn_steps" --batch-size 1024 \
        || warn "h-DQN training failed (non-fatal)"

    step "2.4 Game-EQ Fictitious Play (rounds=${train_game_rounds}, eps=${train_game_eps})"
    run_phase 02d train_game          python3 scripts/train_game.py \
        --rounds "$train_game_rounds" --episodes "$train_game_eps"

    step "2.5 Adaptive attacker vs frozen Signaling-Eq (eps=${train_game_eps})"
    run_phase 02e train_vs_signaling  python3 scripts/train_game.py \
        --defender-policy signaling_eq --episodes "$train_game_eps"

    ok "training complete — models in results/models/"
    ls -la results/models/*.pt 2>/dev/null | sed 's/^/    /' || warn "no .pt files"
else
    phase 02 "Offline training — SKIPPED"
fi

# ──────────── PHASE 3: Docker stack run ────────────────────────────────────────
run_docker_stack() {
    local policy="$1"
    local level_duration="$2"
    local phase_id="$3"

    step "building mirage-fcu-stub:latest"
    if _docker images --format '{{.Repository}}:{{.Tag}}' | grep -q '^mirage-fcu-stub:latest$'; then
        ok "mirage-fcu-stub already built (reuse)"
    else
        run_phase "${phase_id}a1" build_fcu_stub \
            _docker build -f docker/Dockerfile.fcu-stub -t mirage-fcu-stub:latest .
    fi

    step "building mirage-honeydrone:latest"
    run_phase "${phase_id}a" build_honeydrone  bash scripts/build_honeydrone.sh

    step "building mirage-attacker:latest"
    if _docker images --format '{{.Repository}}:{{.Tag}}' | grep -q '^mirage-attacker:latest$'; then
        ok "mirage-attacker already built (reuse)"
    else
        run_phase "${phase_id}a2" build_attacker \
            _docker build -f docker/Dockerfile.attacker -t mirage-attacker:latest .
    fi

    step "bringing up compose stack (policy=${policy})"
    DEFENDER_POLICY="$policy" _docker compose -f config/docker-compose.honey.yml \
                   --env-file config/.env \
                   --project-name "mirage" up -d --remove-orphans 2>&1 | tee "${LOG_ROOT}/phase_${phase_id}b_compose_up.log"

    step "waiting for all honeydrones healthy (up to 90s)"
    for i in $(seq 1 18); do
        local healthy=0
        for N in 1 2 3; do
            local state
            state=$(_docker inspect -f '{{.State.Health.Status}}' "cc_honey_0${N}" 2>/dev/null || echo none)
            [[ "$state" == "healthy" ]] && healthy=$((healthy+1))
        done
        if [[ $healthy -eq 3 ]]; then
            ok "all 3 honeydrones healthy"
            break
        fi
        sleep 5
    done
    for N in 1 2 3; do
        _docker logs --tail 15 "cc_honey_0${N}" 2>&1 | sed "s/^/    [cc_honey_0${N}] /" \
            > "${LOG_ROOT}/phase_${phase_id}c_boot_cc_honey_0${N}.log" 2>&1 || true
    done

    step "attacker L0-L4 (${level_duration}s per level)"
    export ATTACKER_LEVEL_DURATION_SEC="$level_duration"
    # Run attacker simulator as a standalone container joined to honey_net.
    # We use our own mirage-attacker image (built above). The network name
    # is defined by DOCKER_NETWORK_NAME in config/.env (default: honey_isolated).
    local atk_net="${DOCKER_NETWORK_NAME:-honey_isolated}"
    run_phase "${phase_id}d" attacker_run \
        _docker run --rm --name mirage_attacker \
            --network "$atk_net" \
            -e "ATTACKER_LEVEL_DURATION_SEC=${level_duration}" \
            -e "HONEY_DRONE_TARGETS=cc-honey-01:14550,cc-honey-02:14550,cc-honey-03:14550" \
            -e "WEBCLAW_PORT_BASE=18789" \
            -e "HTTP_PORT_BASE=80" \
            -e "RESULTS_DIR=/results" \
            -v "${ROOT}/results:/results:rw" \
            mirage-attacker:latest || warn "attacker run returned non-zero"

    step "tearing down compose stack"
    _docker compose -f config/docker-compose.honey.yml --project-name mirage down --remove-orphans 2>&1 \
        | tee "${LOG_ROOT}/phase_${phase_id}e_compose_down.log" || true
}

if [[ $ONLY_SWEEP -eq 0 && $SKIP_DOCKER -eq 0 ]]; then
    phase 03 "Docker stack + attacker simulation (policy=${POLICY})"
    run_docker_stack "$POLICY" "$DURATION" 03
else
    phase 03 "Docker stack — SKIPPED ($([[ $DOCKER_OK -eq 0 ]] && echo 'no docker' || echo 'flag'))"
fi

# ──────────── PHASE 4: Analysis ────────────────────────────────────────────────
if [[ $ONLY_SWEEP -eq 0 && $SKIP_ANALYSIS -eq 0 ]]; then
    phase 04 "Analysis (compare_policies, exploitability, figures)"

    eps=$([[ $FAST -eq 1 ]] && echo 100 || echo 500)
    step "4.1 compare_policies (${eps} eval episodes, 6 policies)"
    run_phase 04a compare_policies    python3 scripts/compare_policies.py \
        --episodes "$eps"

    step "4.2 analyze_game (+ signaling exploitability)"
    expl_eps=$([[ $FAST -eq 1 ]] && echo 100 || echo 300)
    run_phase 04b analyze_game        python3 scripts/analyze_game.py \
        --expl-episodes "$expl_eps"

    # Compute merged metrics if docker ran
    if [[ -f results/attacker_log.jsonl ]]; then
        step "4.3 merge per-drone metrics (confusion/CTI/decisions)"
        run_phase 04c merge_metrics       python3 -c "
import json, sys
from pathlib import Path
m = Path('results/metrics')
# Confusion merge
scores = []
for f in sorted(m.glob('confusion_honey_*.json')):
    scores.append(json.loads(f.read_text()).get('avg_confusion_score', 0.5))
avg = round(sum(scores)/max(len(scores),1), 4)
(m / 'confusion_scores.json').write_text(json.dumps({'avg_confusion_score': avg, 'n': len(scores)}, indent=2))
# CTI merge
ttps = set(); events = 0
for f in sorted(m.glob('cti_honey_*.json')):
    d = json.loads(f.read_text())
    ttps.update(d.get('unique_ttps', []))
    events += d.get('total_events', 0)
(m / 'live_cti_summary.json').write_text(json.dumps({'unique_ttps': sorted(ttps), 'total_events': events}, indent=2))
# Decisions merge
decisions = []
for f in sorted(m.glob('decisions_honey_*.json')):
    decisions += json.loads(f.read_text())
(m / 'live_agent_decisions.json').write_text(json.dumps(decisions, indent=2, default=str))
print(f'  avg_confusion={avg} unique_TTPs={len(ttps)} decisions={len(decisions)}')
" || warn "merge_metrics failed"

        step "4.4 figures + LaTeX"
        run_phase 04d figures             python3 -c "
import sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from evaluation.plot_results import main; main()
" || warn "plot_results failed"
        run_phase 04e latex               python3 -c "
import sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv('config/.env')
from evaluation.statistical_test import run_all_tests; run_all_tests()
" || warn "statistical_test failed"
    else
        warn "4.3/4.4 skipped — no attacker_log.jsonl (docker phase was skipped)"
    fi
else
    phase 04 "Analysis — SKIPPED"
fi

# ──────────── PHASE 5: Policy sweep (optional) ────────────────────────────────
if [[ $SKIP_SWEEP -eq 0 ]]; then
    phase 05 "Policy sweep (dqn / signaling_eq / hybrid)"

    if [[ $DOCKER_OK -eq 0 ]]; then
        warn "docker required for sweep; skipping"
    else
        # Back up results/models so the sweep doesn't destroy training artifacts
        MODELS_BACKUP=""
        if [[ -d results/models ]]; then
            MODELS_BACKUP="$(mktemp -d)/models"
            cp -r results/models "$MODELS_BACKUP"
            ok "backed up models → $MODELS_BACKUP"
        fi

        for P in dqn signaling_eq hybrid; do
            echo ""
            step "sweep → DEFENDER_POLICY=${P}"
            local_sub_log="${LOG_ROOT}/phase_05_sweep_${P}"

            # Fresh results/ per-policy but keep models
            OUT="results.${P}"
            rm -rf "$OUT"
            rm -rf results.sweep_tmp
            mv results results.sweep_tmp || true
            mkdir -p results
            [[ -n "$MODELS_BACKUP" ]] && cp -r "$MODELS_BACKUP" results/models

            run_docker_stack "$P" "$DURATION" "05_${P}" || warn "sweep policy ${P} failed"
            mv results "$OUT"

            # Keep baseline results/ for follow-up tools
            rm -rf results
            mv results.sweep_tmp results
        done

        step "compare_runs summary"
        run_phase 05z compare_runs        python3 scripts/compare_runs.py \
            results.dqn results.signaling_eq results.hybrid || warn "compare_runs failed"
    fi
else
    phase 05 "Policy sweep — SKIPPED (use --run-sweep to enable)"
fi

# ──────────── Summary ─────────────────────────────────────────────────────────
banner "PIPELINE COMPLETE  run_${TS}"

echo ""
echo "  Logs:       ${LOG_ROOT}/"
echo "  Models:     results/models/"
echo "  Metrics:    results/metrics/"
echo "  Figures:    results/figures/"
echo "  LaTeX:      results/latex/"

if [[ -f results/metrics/verify_honeydrone.json ]]; then
    echo ""
    echo "  Verify snapshot:"
    python3 -c "
import json
d = json.load(open('results/metrics/verify_honeydrone.json'))
c = d['checks']
print(f'    engine:{c.get(\"engine_started\")} pkts:{c.get(\"packets_sent\")}/{c.get(\"bytes_received_from_drone\")}B fp:{c.get(\"fingerprints_tracked\")} decisions:{c.get(\"agent_decisions\")} μ_A:{c.get(\"avg_mu_a\")}')
sg = c.get('signaling_game') or {}
if sg: print(f'    signaling: policy={sg.get(\"defender_policy\")} last_skill={sg.get(\"last_skill\")}')"
fi

if [[ -f results/policy_comparison.json ]]; then
    echo ""
    echo "  Policy comparison (compare_policies):"
    python3 -c "
import json
rows = json.load(open('results/policy_comparison.json'))
print(f'    {\"policy\":<14s} {\"avg_r\":>8s} {\"p_real\":>8s} {\"f1\":>6s}')
for r in rows:
    print(f'    {r[\"policy\"]:<14s} {r[\"avg_reward\"]:>+8.2f} {r[\"avg_p_real\"]:>8.4f} {r[\"f1\"]:>6.3f}')
"
fi

if [[ -f results/signaling_exploitability.json ]]; then
    echo ""
    echo "  Signaling-Eq exploitability:"
    python3 -c "
import json
d = json.load(open('results/signaling_exploitability.json'))
ra = d.get('random_attacker') or {}
ga = d.get('greedy_attacker') or {}
br = d.get('best_response_attacker') or {}
print(f'    random→def={ra.get(\"avg_r_def\")} atk={ra.get(\"avg_r_atk\")}')
print(f'    greedy→def={ga.get(\"avg_r_def\")} atk={ga.get(\"avg_r_atk\")}')
print(f'    BR    →def={br.get(\"avg_r_def\")} atk={br.get(\"avg_r_atk\")}')
print(f'    exploit Δ vs random: {d.get(\"exploitability_vs_random\")}')
"
fi

echo ""
echo "  Next ideas:"
echo "    bash run.sh --only-sweep              # only run the 3-policy sweep"
echo "    bash run.sh --fast                    # ~3 min smoke test"
echo "    bash run.sh --skip-train --skip-docker # analysis-only (fast re-run on same models)"
echo ""
