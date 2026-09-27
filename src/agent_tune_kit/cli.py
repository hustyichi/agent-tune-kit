"""Console entry point for Agent Tune Kit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .checkpoints import (
    create_round,
    decide_candidate,
    freeze_round,
    inspect_or_recover_operation,
    prepare_candidate,
    rollback_to,
    seal_candidate,
)
from .core import ATKError, store_assessment, write_json
from .evidence import import_evidence
from .execution import initialize_project, run_evaluation, store_dataset
from .governance import compare_and_gate, finish_round, knowledge_applicability, store_diagnosis, store_knowledge
from .installer import main as installer_main


def internal_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="ATK deterministic internal operation")
    parser.add_argument("operation")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    repo = Path(request["project_path"]).expanduser().resolve()
    root = repo / ".atk"
    operations = {
        "initialize_project": lambda: initialize_project(repo, request),
        "store_dataset": lambda: store_dataset(root, request),
        "import_evidence": lambda: import_evidence(root, request),
        "run_evaluation": lambda: run_evaluation(root, request),
        "store_assessment": lambda: store_assessment(root, request),
        "create_round": lambda: create_round(repo, root, request),
        "freeze_round": lambda: freeze_round(repo, root, request),
        "store_diagnosis": lambda: store_diagnosis(root, request),
        "store_knowledge": lambda: store_knowledge(root, request),
        "knowledge_applicability": lambda: knowledge_applicability(root, request),
        "prepare_candidate": lambda: prepare_candidate(repo, root, request),
        "seal_candidate": lambda: seal_candidate(repo, root, request),
        "compare_and_gate": lambda: compare_and_gate(root, request),
        "decide_candidate": lambda: decide_candidate(repo, root, request),
        "rollback_to": lambda: rollback_to(repo, root, request),
        "inspect_or_recover_operation": lambda: inspect_or_recover_operation(repo, root, request),
        "finish_round": lambda: finish_round(repo, root, request),
    }
    try:
        if args.operation not in operations:
            parser.error(f"unknown operation: {args.operation}")
        value = operations[args.operation]()
        response = {
            "status": "ok",
            "artifact_refs": [str(value)] if isinstance(value, Path) else [value],
            "warnings": [],
            "error_code": None,
            "next_required_action": None,
        }
    except ATKError as exc:
        response = {
            "status": "error",
            "artifact_refs": [],
            "warnings": [],
            "error_code": exc.code,
            "next_required_action": str(exc),
        }
    write_json(args.output, response)
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response["status"] == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    return internal_main(argv[1:]) if argv and argv[0] == "internal" else installer_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
