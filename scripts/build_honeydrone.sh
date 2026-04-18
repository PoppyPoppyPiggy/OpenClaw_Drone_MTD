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
docker build -f "$DOCKERFILE" -t "$IMAGE_TAG" "$ROOT"
echo "OK: ${IMAGE_TAG}"
