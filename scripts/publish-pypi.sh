#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -z "${UV_PUBLISH_TOKEN:-}" ]]; then
  cat >&2 <<'EOF'
publish-pypi: UV_PUBLISH_TOKEN is not set.

Create a PyPI token, then add it to your shell environment, for example:
  echo 'export UV_PUBLISH_TOKEN="pypi-your-token"' >> ~/.zshrc
  source ~/.zshrc
EOF
  exit 1
fi

export UV_NO_CONFIG=1
exec uv run python scripts/publish-release.py --repository pypi --publish
