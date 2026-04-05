#!/usr/bin/env bash
# run_test_harness.sh — MIRAGE-UAS 테스트 하네스 실행 스크립트
#
# Project  : MIRAGE-UAS
# Author   : DS Lab / ���성 <kmseong0508@kyonggi.ac.kr>
# Created  : 2026-04-06
#
# [ROLE] DVD Docker 이미지 추출 + 공격 시뮬레이터 빌드 + 전체 스택 실행
# [DATA FLOW]
#   Docker pull → Build attacker → Up stack → Wait healthy →
#   Run attacker → Collect logs → Print score → Down stack
#
# [사용법]
#   chmod +x scripts/run_test_harness.sh
#   ./scripts/run_test_harness.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/config/docker-compose.test-harness.yml"
RESULTS_DIR="$PROJECT_ROOT/results"

echo "═���═════════════════════════════════════════════"
echo "  MIRAGE-UAS Test Harness"
echo "═══════════════���═══════════════════════���═══════"
echo ""

# ── 1. Docker Desktop 확인 ────────────────────────────────────
echo "[1/7] Checking Docker..."
if ! docker info > /dev/null 2>&1; then
    echo "ERROR: Docker is not running. Please start Docker Desktop."
    exit 1
fi
echo "  Docker OK"

# ── 2. DVD 이미지 pull ��───────────────────────────────────────
echo "[2/7] Pulling DVD images..."
docker pull nicholasaleks/dvd-flight-controller:latest
docker pull nicholasaleks/dvd-companion-computer:latest
docker pull nicholasaleks/dvd-simulator:lite
echo "  DVD images OK"

# ── 3. 공격 시뮬레이터 이미지 빌드 ────────────────────────────
echo "[3/7] Building attacker simulator image..."
docker build -f "$PROJECT_ROOT/docker/Dockerfile.attacker" \
    -t mirage-attacker:latest \
    "$PROJECT_ROOT"
echo "  Attacker image OK"

# ── 4. 결과 디렉토리 준비 ─────────────────────────────────────
mkdir -p "$RESULTS_DIR/metrics" "$RESULTS_DIR/logs"

# ── 5. 전체 스택 시작 (공격자 제외) ───────────────────────────
echo "[4/7] Starting honey drone stack..."
docker compose -f "$COMPOSE_FILE" up -d \
    fcu-test-01 cc-test-01 \
    fcu-test-02 cc-test-02 \
    fcu-test-03 cc-test-03 \
    simulator cti-api deception-monitor

# ── 6. 허니드론 healthy 대기 (최대 60초) ──────────────────────
echo "[5/7] Waiting for honey drones to be healthy..."
WAIT_MAX=60
WAIT_ELAPSED=0
while [ $WAIT_ELAPSED -lt $WAIT_MAX ]; do
    HEALTHY_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null | \
        python3 -c "
import sys, json
count = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        svc = json.loads(line)
        if 'cc-test' in svc.get('Name', '') and svc.get('Health', '') == 'healthy':
            count += 1
    except (json.JSONDecodeError, KeyError):
        pass
print(count)
" 2>/dev/null || echo "0")
    if [ "$HEALTHY_COUNT" -ge 3 ]; then
        echo "  All 3 honey drones healthy"
        break
    fi
    echo "  Waiting... ($HEALTHY_COUNT/3 healthy, ${WAIT_ELAPSED}s/${WAIT_MAX}s)"
    sleep 5
    WAIT_ELAPSED=$((WAIT_ELAPSED + 5))
done

if [ $WAIT_ELAPSED -ge $WAIT_MAX ]; then
    echo "WARNING: Not all honey drones healthy after ${WAIT_MAX}s. Proceeding anyway..."
fi

# ── 7. 공격 시뮬레이터 실행 ───────────────────────────────────
echo "[6/7] Running attacker simulator..."
docker compose -f "$COMPOSE_FILE" run --rm attacker-simulator || true

# ── 8. 로그 수집 + 결과 출력 ────���─────────────────────────────
echo "[7/7] Collecting results..."
docker compose -f "$COMPOSE_FILE" logs deception-monitor > "$RESULTS_DIR/logs/deception_monitor.log" 2>&1 || true

# 최종 DeceptionScore 출력
TIMELINE_FILE="$RESULTS_DIR/metrics/deception_timeline.jsonl"
if [ -f "$TIMELINE_FILE" ]; then
    echo ""
    echo "═══════════════════════════════════════════════"
    echo "  Final DeceptionScore"
    echo "══════════════════���════════════════════════════"
    tail -1 "$TIMELINE_FILE" | python3 -c "
import sys, json
try:
    record = json.loads(sys.stdin.readline())
    print(f\"  Deception Effectiveness: {record.get('deception_effectiveness', 0):.1%}\")
    print(f\"  Avg Confusion Score:     {record.get('avg_confusion_score', 0):.3f}\")
    print(f\"  Ghost Hit Rate:          {record.get('ghost_service_hit_rate', 0):.1%}\")
    print(f\"  Breadcrumb Follow Rate:  {record.get('breadcrumb_follow_rate', 0):.1%}\")
except Exception as e:
    print(f'  (Could not parse timeline: {e})')
"
    echo "════���══════════════════════════════════════════"
else
    echo "  No timeline data found (deception monitor may not have collected data)"
fi

# ── 9. 스택 정리 ────���─────────────────────────────────────────
echo ""
echo "Shutting down stack..."
docker compose -f "$COMPOSE_FILE" down

echo ""
echo "Done. Results in: $RESULTS_DIR/"
