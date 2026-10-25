#!/usr/bin/env python3
"""Compatibility entrypoint; install the ATK CLI before using this Skill."""

import subprocess
import sys

try:
    from agent_tune_kit.cli.visualize import legacy_main
except ModuleNotFoundError:
    import shutil

    atk = shutil.which("atk")
    if not atk:
        print("Install the CLI with: uv tool install agent-tune-kit; then run atk install", file=sys.stderr)
        raise SystemExit(2) from None
    raise SystemExit(subprocess.call([atk, "_legacy", "atk-visualize-failures", *sys.argv[1:]])) from None
if __name__ == "__main__":
    raise SystemExit(legacy_main("failures", sys.argv[1:]))
