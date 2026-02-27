#!/usr/bin/env bash
set -euo pipefail

echo "==> Pulling openclaw image..."
docker pull "${OPENCLAW_IMAGE:-alpine/openclaw:latest}"

echo "==> Building manager..."
docker build -t flyclawd-manager:latest ./manager

echo "==> Deploying..."
docker compose up -d

echo "==> Done."
docker compose ps
