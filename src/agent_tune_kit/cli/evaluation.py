from agent_tune_kit.execution import run_evaluation


def register(subparsers):
    parser = subparsers.add_parser("eval", help="run the project Agent with the shared engine")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--only-failures", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--python")
    parser.add_argument("--json", action="store_true")


def execute(args):
    return run_evaluation(
        **{
            key: getattr(args, key)
            for key in ["check", "limit", "offset", "concurrency", "only_failures", "no_progress", "python"]
        }
    )
