from agent_tune_kit import results
from agent_tune_kit.execution import run_rule


def register(subparsers):
    commands = subparsers.add_parser("results", help="inspect and judge versioned results").add_subparsers(
        dest="action", required=True
    )
    calibration = commands.add_parser(
        "calibrate", help="check a judge against reviewed outputs without running the Agent"
    )
    calibration.add_argument("--samples-path", required=True)
    calibration.add_argument("--output-path", required=True)
    mode = calibration.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rule-path")
    mode.add_argument("--judgments-path")
    calibration.add_argument("--judge-id")
    calibration.add_argument("--instructions")
    calibration.add_argument("--python")
    calibration.add_argument("--json", action="store_true")
    for name in ["inspect", "run-rule", "apply-judgments", "compare"]:
        parser = commands.add_parser(name)
        parser.add_argument("--results-dir", default=".atk/results")
        parser.add_argument("--json", action="store_true")
        if name == "run-rule":
            parser.add_argument("--rule-path", default=".atk/runner/failure_rule.py")
            parser.add_argument("--python")
        elif name == "apply-judgments":
            parser.add_argument("--judgments-path", required=True)
            parser.add_argument("--source-results")
        elif name == "compare":
            parser.add_argument("--ids", nargs="+", required=True)


def execute(args):
    if args.action == "calibrate":
        from agent_tune_kit.execution.calibration import launch_calibration

        return launch_calibration(
            **{
                key: getattr(args, key)
                for key in (
                    "samples_path",
                    "output_path",
                    "rule_path",
                    "judgments_path",
                    "judge_id",
                    "instructions",
                    "python",
                )
            }
        )
    if args.action == "inspect":
        return results.inspect_results(args.results_dir)
    if args.action == "compare":
        return results.compare_targets(args.ids, args.results_dir)
    if args.action == "apply-judgments":
        return results.apply_judgments(
            args.judgments_path, source_results=args.source_results, results_dir=args.results_dir
        )
    return run_rule(rule_path=args.rule_path, results_dir=args.results_dir, python=args.python)
