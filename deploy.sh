#!/usr/bin/env bash
set -euo pipefail

# Build openclaw image from a repo path (one-time / when updating)
# Usage: ./deploy.sh --build-openclaw ~/openclaw
if [[ "${1:-}" == "--build-openclaw" ]]; then
    repo="${2:?Usage: ./deploy.sh --build-openclaw /path/to/openclaw}"
    echo "==> Building openclaw image from $repo..."
    docker build -t openclaw:latest "$repo"
    shift 2
fi

echo "==> Building manager..."
docker build -t flyclawd-manager:latest ./manager

echo "==> Starting services..."
docker compose up -d

echo "==> Done."
docker compose ps
