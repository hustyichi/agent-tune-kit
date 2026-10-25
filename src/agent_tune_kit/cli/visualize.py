import argparse
import sys

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.visualize import generate_dataset, generate_failures

from .output import emit


def add_options(parser, kind):
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--open", dest="open_browser", action="store_true", default=True)
    parser.add_argument("--no-open", dest="open_browser", action="store_false")
    parser.add_argument("--json", action="store_true")
    if kind == "dataset":
        parser.add_argument("--dataset-path", default=".atk/datasets/dataset.csv")
    else:
        parser.add_argument("--results-dir", default=".atk/results")
        parser.add_argument("--no-report", action="store_true")
        parser.add_argument("--no-history", action="store_true")


def register(subparsers):
    commands = subparsers.add_parser("visualize", help="generate offline review pages").add_subparsers(
        dest="action", required=True
    )
    for kind in ["dataset", "failures"]:
        add_options(commands.add_parser(kind), kind)


def execute(args):
    options = dict(overwrite=args.overwrite, open_browser=args.open_browser)
    if args.action == "dataset":
        return generate_dataset(args.dataset_path, **options)
    return generate_failures(args.results_dir, no_report=args.no_report, no_history=args.no_history, **options)


def legacy_main(kind, argv=None):
    parser = argparse.ArgumentParser()
    add_options(parser, kind)
    args = parser.parse_args(argv)
    args.action = kind
    try:
        result = execute(args)
        emit(result)
        return 0
    except UserActionRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Unexpected HTML generation error: {exc}", file=sys.stderr)
        return 1
