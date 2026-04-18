#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# build_honeydrone.sh — MIRAGE-UAS honeydrone image build
#
# Build context MUST be repo root so COPY src/ works.
# Image: mirage-honeydrone:latest
# Referenced by: config/docker-compose.honey.yml (x-cc-base)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

IMAGE_TAG="${IMAGE_TAG:-mirage-honeydrone:latest}"
DOCKERFILE="docker/Dockerfile.honeydrone"

if [ ! -f "$DOCKERFILE" ]; then
    echo "ERROR: $DOCKERFILE not found (cwd=$ROOT)" >&2
    exit 1
fi

echo "Building ${IMAGE_TAG} from ${ROOT}..."

# Route through sg(1) if the current shell lacks docker group membership
# (freshly installed docker — newgrp not yet invoked).
if docker info >/dev/null 2>&1; then
    DOCKER_BUILD=(docker build -f "$DOCKERFILE" -t "$IMAGE_TAG" "$ROOT")
    "${DOCKER_BUILD[@]}"
elif id -nG "$(id -un)" | grep -qw docker; then
    sg docker -c "docker build -f $(printf %q "$DOCKERFILE") -t $(printf %q "$IMAGE_TAG") $(printf %q "$ROOT")"
else
    echo "ERROR: docker daemon unreachable and user not in docker group." >&2
    exit 1
fi
echo "OK: ${IMAGE_TAG}"
