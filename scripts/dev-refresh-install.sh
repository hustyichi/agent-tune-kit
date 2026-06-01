#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PLUGIN_NAME="agent-tune-kit"
DEFAULT_CACHE_ROOT="${HOME}/.codex/plugins/cache/personal"
CACHE_ROOT="${CODEX_PERSONAL_PLUGIN_CACHE_ROOT:-$DEFAULT_CACHE_ROOT}"

VERSION="$(
  python3 - <<'PY'
import json
from pathlib import Path

manifest = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))
version = str(manifest.get("version", "")).strip()
if not version:
    raise SystemExit("missing .codex-plugin/plugin.json version")
print(version)
PY
)"

if [[ -z "$VERSION" ]]; then
  echo "error: plugin version is empty" >&2
  exit 2
fi

CACHE_DIR="${CACHE_ROOT%/}/${PLUGIN_NAME}/${VERSION}"

echo "Agent Tune Kit dev refresh install"
echo "repo: $(pwd)"
echo "plugin: ${PLUGIN_NAME} ${VERSION}"
echo "cache root: ${CACHE_ROOT}"

if [[ -e "$CACHE_DIR" || -L "$CACHE_DIR" ]]; then
  echo "clearing Codex plugin cache: ${CACHE_DIR}"
  rm -rf "$CACHE_DIR"
else
  echo "Codex plugin cache already absent: ${CACHE_DIR}"
fi

echo "installing local checkout with copy mode"
uv run atk install --copy --yes --force "$@"

echo "checking local install status"
uv run atk status --no-input "$@"

echo "done: restart Codex or open a new session, then enable/check Agent Tune Kit in /plugins"
