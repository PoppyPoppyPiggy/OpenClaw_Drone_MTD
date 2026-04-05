#!/usr/bin/env bash
# setup_wsl2_network.sh — WSL2 Docker 네트워크 초기화
#
# Project  : MIRAGE-UAS
# Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
# Created  : 2026-04-06
#
# [ROLE] WSL2 환경에서 Docker 네트워크 생성 + 포트 가용성 확인
#
# [네트워크 구성]
#   honey_isolated  172.30.0.0/24 : 공격자 트래픽 허용 (production)
#   honey_internal  172.31.0.0/24 : CTI 파이프라인 (internal, 외부 접근 불가)
#   test_net        172.40.0.0/24 : 테스트 하네스 전용
#
# [사용법]
#   chmod +x scripts/setup_wsl2_network.sh
#   ./scripts/setup_wsl2_network.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ERRORS=0

echo "═══════════════════════════════════════════════"
echo "  MIRAGE-UAS WSL2 Network Setup"
echo "═══════════════════════════════════════════════"
echo ""

# ── 1. WSL2 확인 ──────────────────────────────────────────────
echo -n "[1/5] Checking WSL2... "
if cat /proc/version 2>/dev/null | grep -qi microsoft; then
    echo -e "${GREEN}OK${NC} (WSL2 detected)"
else
    echo -e "${YELLOW}WARNING${NC} (not WSL2 — proceeding anyway)"
fi

# ── 2. Docker Desktop 확인 ────────────────────────────────────
echo -n "[2/5] Checking Docker... "
if docker info > /dev/null 2>&1; then
    DOCKER_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "unknown")
    echo -e "${GREEN}OK${NC} (Docker ${DOCKER_VER})"
else
    echo -e "${RED}FAIL${NC} — Docker is not running"
    echo "  → Start Docker Desktop with WSL2 backend enabled"
    ERRORS=$((ERRORS + 1))
fi

# ── 3. 네트워크 생성 ─────────────────────────────────────────
echo "[3/5] Creating Docker networks..."

create_network() {
    local NAME="$1"
    local SUBNET="$2"
    local INTERNAL="${3:-false}"

    if docker network ls --format '{{.Name}}' | grep -q "^${NAME}$"; then
        echo -e "  ${NAME}: ${YELLOW}already exists${NC}"
        return 0
    fi

    local CMD="docker network create --driver bridge --subnet ${SUBNET}"
    if [ "$INTERNAL" = "true" ]; then
        CMD="${CMD} --internal"
    fi
    CMD="${CMD} ${NAME}"

    if eval "${CMD}" > /dev/null 2>&1; then
        echo -e "  ${NAME}: ${GREEN}created${NC} (${SUBNET})"
    else
        echo -e "  ${NAME}: ${RED}FAILED${NC}"
        ERRORS=$((ERRORS + 1))
    fi
}

create_network "honey_isolated"  "172.30.0.0/24" "false"
create_network "honey_internal"  "172.31.0.0/24" "true"
create_network "test_net"        "172.40.0.0/24" "false"

# ── 4. 포트 가용성 확인 ──────────────────────────────────────
echo "[4/5] Checking port availability..."

PORTS_OK=true
for PORT in 14551 14552 14553; do
    if ss -ulnp 2>/dev/null | grep -q ":${PORT} "; then
        echo -e "  UDP :${PORT} — ${RED}IN USE${NC}"
        PORTS_OK=false
        ERRORS=$((ERRORS + 1))
    else
        echo -e "  UDP :${PORT} — ${GREEN}available${NC}"
    fi
done

for PORT in 8081 8082 8083 8765; do
    if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
        echo -e "  TCP :${PORT} — ${RED}IN USE${NC}"
        PORTS_OK=false
        ERRORS=$((ERRORS + 1))
    else
        echo -e "  TCP :${PORT} — ${GREEN}available${NC}"
    fi
done

# ── 5. 접근성 테이블 출력 ─────────────────────────────────────
echo ""
echo "[5/5] Port accessibility matrix:"
echo "  ┌──────────────────────────────────────────────────────┐"
echo "  │ Port        │ Protocol │ WSL2  │ Windows │ Purpose   │"
echo "  ├──────────────────────────────────────────────────────┤"
echo "  │ 14551-14553 │ UDP      │  ✓    │   ✓     │ MAVLink   │"
echo "  │ 8081-8083   │ TCP      │  ✓    │   ✗     │ Web UI    │"
echo "  │ 8554-8556   │ TCP      │  ✓    │   ✓     │ RTSP      │"
echo "  │ 8765        │ TCP      │  ✓    │   ✗     │ CTI API   │"
echo "  │ 18790-18792 │ TCP      │  ✓    │   ✓     │ OpenClaw  │"
echo "  │ 5761-5763   │ TCP      │  ✓    │   ✗     │ SITL      │"
echo "  └──────────────────────────────────────────────────────┘"
echo "  (✓ = accessible, ✗ = localhost only via 127.0.0.1 bind)"
echo ""

# ── 결과 ─────────────────────────────────────────────────────
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  SUCCESS — All networks ready, all ports free${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
    exit 0
else
    echo -e "${RED}═══════════════════════════════════════════════${NC}"
    echo -e "${RED}  FAILED — ${ERRORS} error(s) detected${NC}"
    echo -e "${RED}═══════════════════════════════════════════════${NC}"
    exit 1
fi
