"""Public command dispatch; business behavior lives in domain packages."""

import argparse
import sys

from agent_tune_kit import installer
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.execution.runtime import ExecutionFailed

from . import dataset, evaluation, results, visualize
from .output import emit

HANDLERS = {"dataset": dataset, "eval": evaluation, "results": results, "visualize": visualize}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "_legacy":
        if argv[1] == "atk-dataset":
            return dataset.legacy_main(argv[2:])
        return visualize.legacy_main("dataset" if argv[1] == "atk-visualize-dataset" else "failures", argv[2:])
    if not argv or argv[0] not in HANDLERS:
        if argv and argv[0] in ["-h", "--help"]:
            parser = argparse.ArgumentParser(description="Register Agent Tune Kit and run shared project capabilities")
            subparsers = parser.add_subparsers(dest="command")
            for module in HANDLERS.values():
                module.register(subparsers)
            for command in ["install", "preview", "status", "version"]:
                subparsers.add_parser(command)
            parser.print_help()
            return 0
        return installer.main(argv)
    parser = argparse.ArgumentParser(prog="atk")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for module in HANDLERS.values():
        module.register(subparsers)
    args = parser.parse_args(argv)
    try:
        emit(HANDLERS[args.command].execute(args), args.json)
        return 0
    except UserActionRequired as exc:
        print(f"atk: {exc}", file=sys.stderr)
        return 2
    except ExecutionFailed as exc:
        print(f"atk: {exc}", file=sys.stderr)
        return exc.returncode if exc.returncode > 0 else 1
    except KeyboardInterrupt:
        print("Evaluation interrupted; partial results were preserved.", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print(f"atk: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"atk: unexpected error: {exc}", file=sys.stderr)
        return 1
