"""Console entry point for Agent Tune Kit."""

from __future__ import annotations

import sys

from .installer import main as installer_main


def main(argv: list[str] | None = None) -> int:
    return installer_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
