#!/usr/bin/env python3
"""Check the public ATK v2 plugin surface before packaging."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = {"atk-init", "atk-dataset", "atk-eval", "atk-diagnose", "atk-optimize", "atk-validate", "atk-decide"}


def main() -> int:
    skills = ROOT / "skills"
    found = {path.name for path in skills.iterdir() if path.is_dir() and (path / "SKILL.md").exists()}
    if found != PUBLIC:
        raise ValueError(f"public Skills differ from v2 contract: missing={PUBLIC - found}, extra={found - PUBLIC}")
    if not (skills / "WORKFLOW.md").is_file() or not (ROOT / "templates" / "runner.py").is_file():
        raise ValueError("shared workflow or project runner template is missing")
    for name in sorted(PUBLIC):
        source = (skills / name / "SKILL.md").read_text(encoding="utf-8")
        if not source.startswith(f"---\nname: {name}\n") or "Read `../WORKFLOW.md`" not in source:
            raise ValueError(f"invalid Skill identity or shared contract reference: {name}")
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    if manifest.get("skills") != "./skills/" or manifest.get("name") != "agent-tune-kit":
        raise ValueError("plugin manifest does not expose the expected skills directory")
    print("skill-pack: OK (seven ATK v2 Skills)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
