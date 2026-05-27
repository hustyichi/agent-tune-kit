#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Credential handling lives in publish-release.py; it accepts UV_PUBLISH_TOKEN
# or a matching ~/.pypirc section for the target repository.
export UV_NO_CONFIG=1
exec uv run python scripts/publish-release.py --repository pypi --publish
