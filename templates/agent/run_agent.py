#!/usr/bin/env python3
"""CLI smoke runner for the minimal ATK-generated Agent."""

from __future__ import annotations

import argparse
import json
import sys

from agent import AgentConfigurationError, run_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the generated Agent once.")
    parser.add_argument("--input", required=True, help="Plain text input or a JSON object for the Agent.")
    return parser.parse_args()


def build_input(raw_input: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw_input)
    except json.JSONDecodeError:
        return {"input": raw_input}
    if isinstance(parsed, dict):
        return {str(key): "" if value is None else str(value) for key, value in parsed.items()}
    return {"input": raw_input}


def main() -> int:
    args = parse_args()
    try:
        output = run_agent(build_input(args.input))
    except AgentConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
