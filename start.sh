#!/usr/bin/env bash
# Convenience launcher for macOS/Linux. See README.md for details.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env found. Copying .env.example -> .env"
  echo "IMPORTANT: edit .env and set ANTHROPIC_API_KEY before using chat/diary features."
  cp .env.example .env
fi

docker compose up --build
