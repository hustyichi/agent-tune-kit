#!/usr/bin/env python3
"""Compatibility wrapper for the Agent Tune Kit installer.

Recommended path:
    atk install

For no-clone installs, use:
    uvx --from agent-tune-kit atk install

This wrapper is kept for contributors running from a source checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_tune_kit.cli import main  # noqa: E402
from agent_tune_kit.installer import authorize_conflicts  # noqa: E402,F401

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
