#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# install_docker.sh — Docker CE + Compose installer
#
# [Inputs]
#   - sudo capability
#   - Debian/Ubuntu-family distro (WSL2 Ubuntu also supported)
#
# [Outputs]
#   - docker CE + docker compose plugin
#   - current user added to `docker` group
#
# [REF] Damn-Vulnerable-Drone install guide (Kali/Debian) — adapted to
#       autodetect the distro and codename for Ubuntu/WSL2 compatibility.
#
# Usage:
#   bash scripts/install_docker.sh
#
# After completion, re-login OR run `newgrp docker` to pick up group.
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

step() { echo -e "\n${CYAN}▶${NC} $1"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1" >&2; }

# ── Detect distro (ID + codename) ─────────────────────────────
if [ ! -f /etc/os-release ]; then
    err "/etc/os-release not found — cannot detect distro"; exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release

DISTRO_ID="${ID:-unknown}"
DISTRO_LIKE="${ID_LIKE:-}"
CODENAME="${VERSION_CODENAME:-}"

echo "  Distro:   ${DISTRO_ID} ${VERSION_ID:-} (${CODENAME:-no-codename})"
echo "  ID_LIKE:  ${DISTRO_LIKE}"

# Kali reports ID=kali but derives from debian — docker repo uses Debian codenames.
# For WSL2 Ubuntu we use ubuntu repo; for Debian/Kali we use debian repo.
case "${DISTRO_ID}" in
    ubuntu)
        DOCKER_REPO="https://download.docker.com/linux/ubuntu"
        [ -z "${CODENAME}" ] && CODENAME="jammy"
        ;;
    debian)
        DOCKER_REPO="https://download.docker.com/linux/debian"
        [ -z "${CODENAME}" ] && CODENAME="bookworm"
        ;;
    kali)
        DOCKER_REPO="https://download.docker.com/linux/debian"
        # Kali tracks Debian testing → use bullseye (DVD docs) or bookworm
        CODENAME="${CODENAME:-bullseye}"
        ;;
    *)
        case "${DISTRO_LIKE}" in
            *debian*) DOCKER_REPO="https://download.docker.com/linux/debian"; CODENAME="${CODENAME:-bookworm}" ;;
            *ubuntu*) DOCKER_REPO="https://download.docker.com/linux/ubuntu"; CODENAME="${CODENAME:-jammy}" ;;
            *) err "Unsupported distro: ${DISTRO_ID}. Install Docker manually."; exit 1 ;;
        esac
        ;;
esac
ok "Using repo: ${DOCKER_REPO} / ${CODENAME}"

# ── Short-circuit if docker already works ─────────────────────
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        ok "docker already installed AND daemon reachable — skipping"
        docker --version
        docker compose version 2>/dev/null || warn "compose plugin missing, continuing install"
        exit 0
    else
        warn "docker binary present but daemon unreachable — will attempt to start service"
    fi
fi

# ── Apt sources + GPG ─────────────────────────────────────────
step "Configuring Docker APT source"
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL "${DOCKER_REPO}/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] ${DOCKER_REPO} ${CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
ok "APT source written → /etc/apt/sources.list.d/docker.list"

# ── Install packages ──────────────────────────────────────────
step "Installing docker-ce, CLI, containerd, compose plugin, buildx"
sudo apt-get update -y
sudo apt-get install -y \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
ok "Packages installed"

# ── Start the daemon ──────────────────────────────────────────
step "Starting Docker daemon"
if pidof systemd >/dev/null 2>&1; then
    sudo systemctl enable docker --now
    ok "systemd enabled + started docker.service"
else
    # WSL2 without systemd — fall back to sysv service
    if [ -x /etc/init.d/docker ]; then
        sudo service docker start || warn "service docker start returned non-zero"
        ok "sysv init started docker"
    else
        warn "Neither systemd nor sysv init available — you may need to start dockerd manually:"
        warn "  sudo dockerd &"
    fi
fi

# ── Add user to docker group ──────────────────────────────────
step "Adding $USER to docker group"
if getent group docker >/dev/null; then
    sudo usermod -aG docker "$USER"
    ok "usermod -aG docker $USER"
    warn "Log out + in (or run: newgrp docker) to pick up the new group in your shell."
else
    warn "docker group does not exist — skipping"
fi

# ── Verify ────────────────────────────────────────────────────
step "Verification"
docker --version || err "docker binary not on PATH"
docker compose version || warn "docker compose plugin missing"

if docker info >/dev/null 2>&1; then
    ok "daemon is reachable"
else
    warn "daemon not reachable yet — try: newgrp docker && docker info"
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Docker install complete.${NC}"
echo -e "${GREEN}  Next: newgrp docker && docker info${NC}"
echo -e "${GREEN}        bash run.sh${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
