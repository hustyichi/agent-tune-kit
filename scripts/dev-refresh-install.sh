#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "Agent Tune Kit dev refresh install"
# atk install owns cache invalidation, including CODEX_PERSONAL_PLUGIN_CACHE_ROOT.
uv run atk install --copy --yes --force "$@"
uv run atk status --no-input "$@"
