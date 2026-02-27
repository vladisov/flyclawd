#!/usr/bin/env bash
set -euo pipefail

echo "==> Pulling openclaw image..."
docker pull "${OPENCLAW_IMAGE:-alpine/openclaw:latest}"

echo "==> Deploying..."
docker compose up -d --build

echo "==> Done."
docker compose ps
