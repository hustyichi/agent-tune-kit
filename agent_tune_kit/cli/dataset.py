from agent_tune_kit import datasets
from agent_tune_kit.datasets import updates
from agent_tune_kit.workspace import DATASET_PATH


def add_options(parser):
    parser.add_argument("--dataset-path", default=str(DATASET_PATH))
    parser.add_argument("--field", default="ground_truth")
    parser.add_argument("--home-dir")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--review-path")
    mode.add_argument("--direct", action="store_true")
    parser.add_argument("--json", action="store_true")


def register(subparsers):
    commands = subparsers.add_parser("dataset", help="prepare, inspect and update evaluation data").add_subparsers(
        dest="action", required=True
    )
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source", required=True)
    prepare.add_argument("--dataset-path", default=str(DATASET_PATH))
    mode = prepare.add_mutually_exclusive_group()
    mode.add_argument("--append", action="store_true")
    mode.add_argument("--overwrite", action="store_true")
    prepare.add_argument("--json", action="store_true")
    add_options(commands.add_parser("inspect"))
    update = commands.add_parser("update")
    add_options(update)
    update.add_argument("--updates-path", required=True)


def execute(args):
    if args.action == "prepare":
        return datasets.prepare_dataset(args.source, args.dataset_path, append=args.append, overwrite=args.overwrite)
    options = dict(
        dataset_path=args.dataset_path,
        direct=args.direct,
        review_path=args.review_path,
        field=args.field,
        home_dir=args.home_dir,
    )
    if args.action == "inspect":
        return datasets.inspect_dataset(**options)
    return datasets.update_dataset(args.updates_path, **options)


def legacy_main(argv=None):
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser()
    add_options(parser)
    parser.add_argument("--updates-path")
    parser.add_argument("--dump-context", action="store_true")
    args = parser.parse_args(argv)
    options = dict(
        dataset_path=args.dataset_path,
        direct=args.direct,
        review_path=args.review_path,
        field=args.field,
        home_dir=args.home_dir,
    )
    try:
        if args.dump_context or not args.updates_path:
            print(json.dumps(datasets.inspect_dataset(**options), ensure_ascii=False, indent=2))
        if args.updates_path:
            from .output import emit

            emit(datasets.update_dataset(args.updates_path, **options))
        return 0
    except updates.UserActionRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1
