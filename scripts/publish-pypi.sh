#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

export UV_NO_CONFIG=1
exec uv run python scripts/publish-release.py --repository pypi --publish
