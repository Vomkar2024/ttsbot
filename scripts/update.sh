#!/usr/bin/env bash
# Manual or pre-start GitHub update script for bot-hosting.net / Linux panels.
set -euo pipefail

BRANCH="${AUTO_GIT_PULL_BRANCH:-main}"

echo "[Update] Pulling latest changes from origin/${BRANCH}..."
git fetch origin "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

echo "[Update] Installing/updating Python dependencies from requirements.txt..."
python3 -m pip install --no-cache-dir -r requirements.txt

echo "[Update] Done! Latest commit: $(git rev-parse --short HEAD)"

