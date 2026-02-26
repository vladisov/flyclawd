#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_REPO="${OPENCLAW_REPO_PATH:-../openclaw}"

echo "==> Building openclaw image..."
docker build -t openclaw:latest "$OPENCLAW_REPO"

echo "==> Building manager image..."
docker build -t flyclawd-manager:latest ./manager

echo "==> Starting services..."
docker compose up -d

echo "==> Done."
docker compose ps
