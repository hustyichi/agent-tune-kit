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
    for name in ["inspect", "run-rule", "apply-judgments", "compare", "evidence", "import-evidence"]:
        parser = commands.add_parser(name)
        parser.add_argument("--results-dir", default=".atk/results")
        parser.add_argument("--json", action="store_true")
        if name != "import-evidence":
            parser.add_argument("--version")
        if name == "run-rule":
            parser.add_argument("--rule-path", default=".atk/runner/failure_rule.py")
            parser.add_argument("--python")
        elif name == "apply-judgments":
            parser.add_argument("--judgments-path", required=True)
            parser.add_argument("--source-results")
        elif name == "compare":
            parser.add_argument("--ids", nargs="+", required=True)
        elif name == "import-evidence":
            parser.add_argument("--source", required=True)
            parser.add_argument("--adapter", required=True, help="explicit trusted Python adapter (not sandboxed)")
        elif name == "evidence":
            parser.add_argument("--execution-dir")
            parser.add_argument("--atk-id")
            parser.add_argument("--execution-id")
            parser.add_argument("--event-id")
            parser.add_argument("--artifact-id")
            parser.add_argument("--after-seq", type=int)
            parser.add_argument("--offset", type=int, default=0)
            parser.add_argument("--limit", type=int)


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
    if args.action == "import-evidence":
        from agent_tune_kit.results.evidence import import_evidence

        return import_evidence(args.source, args.adapter, args.results_dir)
    if args.action == "evidence":
        from agent_tune_kit.results.evidence import query_evidence

        return query_evidence(
            **{
                key: getattr(args, key)
                for key in [
                    "results_dir",
                    "version",
                    "execution_dir",
                    "atk_id",
                    "execution_id",
                    "event_id",
                    "artifact_id",
                    "after_seq",
                    "offset",
                    "limit",
                ]
            }
        )
    if args.action == "inspect":
        return results.inspect_results(args.results_dir, version=args.version)
    if args.action == "compare":
        return results.compare_targets(args.ids, args.results_dir, version=args.version)
    if args.action == "apply-judgments":
        return results.apply_judgments(
            args.judgments_path, source_results=args.source_results, results_dir=args.results_dir, version=args.version
        )
    return run_rule(rule_path=args.rule_path, results_dir=args.results_dir, python=args.python, version=args.version)
