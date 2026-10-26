import ast
import hashlib
import json
import runpy
import tempfile
from pathlib import Path

from agent_tune_kit.datasets.truth import digest as content_digest
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results import apply_judgments
from agent_tune_kit.results.assessment import parse_assessment, validate_assessment
from agent_tune_kit.results.judgments import JUDGMENT_FIELDS
from agent_tune_kit.results.versions import require_current_file, resolve_current_version
from agent_tune_kit.utils.csv_io import parse_csv, write_csv_atomic
from agent_tune_kit.workspace import RESULTS_DIR, RULE_PATH


def run_rule(rule_path=str(RULE_PATH), results_dir=str(RESULTS_DIR)):
    current = resolve_current_version(Path(results_dir))
    source = require_current_file(current, "eval_results.csv")
    if not Path(rule_path).is_file():
        raise UserActionRequired(f"Rule {rule_path} is missing; prepare it with atk-diagnose.")
    tree = ast.parse(Path(rule_path).read_text(encoding="utf-8"))
    if not any(isinstance(node, ast.FunctionDef) and node.name == "judge" for node in tree.body):
        # Run old filters once; their complement never establishes success.
        try:
            runpy.run_path(str(rule_path), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise
        return {"version": current.name, "legacy": True}
    rule_source = Path(rule_path).read_text(encoding="utf-8")
    namespace = runpy.run_path(str(rule_path), run_name="atk_rule")
    judge = namespace["judge"]
    assessment = None
    if "ATK_CRITERIA" in namespace:
        criteria = namespace["ATK_CRITERIA"]
        assessment = json.dumps(
            {
                "schema": 1,
                "basis": namespace.get("ATK_BASIS", "truth"),
                "input_fields": namespace.get("ATK_INPUT_FIELDS", []),
                "truth_fields": namespace.get("ATK_TRUTH_FIELDS", []),
                "standard": {"text": criteria, "sha256": content_digest(criteria)},
                "judge": {
                    "kind": "rule",
                    "identity": str(rule_path),
                    "source": rule_source,
                    "sha256": content_digest(rule_source),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        parse_assessment(assessment)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    _, rows = parse_csv(source)
    judgments = []
    for row in rows:
        verdict, reason = judge(row)
        judgment = dict(atk_id=row.get("atk_id", ""), verdict=verdict, reason=reason, results_sha256=digest)
        if assessment is not None:
            judgment["assessment"] = assessment
        if not validate_assessment(row, judgment):
            judgment.update(
                verdict="unknown", reason=f"Unconfirmed or stale judgment grounds; provisional result: {reason}"
            )
        judgments.append(judgment)
    if Path(rule_path).read_text(encoding="utf-8") != rule_source:
        raise UserActionRequired("Rule changed during judging; nothing was written.")
    with tempfile.TemporaryDirectory(prefix="atk-rule-") as directory:
        path = Path(directory) / "judgments.csv"
        write_csv_atomic(path, JUDGMENT_FIELDS + (["assessment"] if assessment else []), judgments)
        return apply_judgments(path, source_results=source, results_dir=results_dir)
