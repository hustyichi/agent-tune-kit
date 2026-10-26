"""Judge calibration on reviewed outputs; never allocate an Agent evaluation run."""

import hashlib
import json
import os
import runpy
import subprocess
import tempfile
from pathlib import Path

from agent_tune_kit.datasets.truth import digest
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.csv_io import parse_csv, write_csv_atomic

from .runtime import ExecutionFailed, python_command

GOLD_FIELDS = {"expected_verdict", "expected_reason", "source", "reviewer"}
VERDICTS = {"passed", "failed", "unknown"}


def calibrate(samples_path, output_path, *, rule_path=None, judgments_path=None, judge_id=None, instructions=None):
    source, output = Path(samples_path), Path(output_path)
    if bool(rule_path) == bool(judgments_path):
        raise UserActionRequired("Choose a rule or recorded semantic judgments for calibration.")
    if output.exists() or output.resolve() == source.resolve():
        raise UserActionRequired("Calibration output must be a new file; prior evidence is preserved.")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    fields, samples = parse_csv(source)
    required = GOLD_FIELDS | {"atk_id", "agent_output", "criteria"}
    if not required <= set(fields) or not samples:
        raise UserActionRequired(
            "Calibration requires reviewed outputs, expected_verdict/reason, source, reviewer and criteria."
        )
    ids = [r["atk_id"] for r in samples]
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        raise UserActionRequired("Calibration IDs must be unique and non-empty.")
    if any(
        r["expected_verdict"] not in VERDICTS
        or any(not r[k].strip() for k in GOLD_FIELDS - {"expected_verdict"} | {"criteria"})
        for r in samples
    ):
        raise UserActionRequired("Calibration labels require known verdicts and explicit review evidence.")
    predictions = {}
    if rule_path:
        rule_path = Path(rule_path)
        code = rule_path.read_text(encoding="utf-8")
        judge_info = {"kind": "rule", "identity": str(rule_path), "source": code, "sha256": digest(code)}
        namespace = runpy.run_path(str(rule_path), run_name="atk_calibration")
        judge = namespace.get("judge")
        if not callable(judge):
            raise UserActionRequired("Calibration requires judge(row); legacy filters are not supported.")
        for row in samples:
            try:
                verdict, reason = judge({k: v for k, v in row.items() if k not in GOLD_FIELDS})
                if verdict not in VERDICTS or not isinstance(reason, str) or not reason.strip():
                    raise ValueError("invalid verdict or reason")
                predictions[row["atk_id"]] = (verdict, reason, "")
            except Exception as exc:
                predictions[row["atk_id"]] = ("unknown", f"Judge error: {type(exc).__name__}: {exc}", "judge_error")
        if rule_path.read_text(encoding="utf-8") != code:
            raise UserActionRequired("Rule changed during calibration; nothing was written.")
    else:
        if not judge_id or not instructions:
            raise UserActionRequired("Semantic calibration requires actual judge identity and instructions.")
        judge_info = {"kind": "semantic", "identity": judge_id, "instructions": instructions}
        prediction_fields, rows = parse_csv(Path(judgments_path))
        if not {"atk_id", "verdict", "reason"} <= set(prediction_fields):
            raise UserActionRequired("Calibration judgments require atk_id,verdict,reason.")
        if len(rows) != len(ids) or {r["atk_id"] for r in rows} != set(ids):
            raise UserActionRequired("Calibration judgments must cover each sample exactly once.")
        for row in rows:
            if row["verdict"] not in VERDICTS or not row["reason"].strip():
                raise UserActionRequired("Invalid calibration judgment.")
            predictions[row["atk_id"]] = (row["verdict"], row["reason"], "")
    added = ["judge_verdict", "judge_reason", "judge_error", "matched", "judge_snapshot", "calibration_source_sha256"]
    if set(fields) & set(added):
        raise UserActionRequired("Calibration source contains reserved output fields.")
    output_rows = []
    for row in samples:
        verdict, reason, error = predictions[row["atk_id"]]
        output_rows.append(
            dict(
                row,
                judge_verdict=verdict,
                judge_reason=reason,
                judge_error=error,
                matched=str(not error and verdict == row["expected_verdict"]).lower(),
                judge_snapshot=json.dumps(judge_info, ensure_ascii=False, sort_keys=True),
                calibration_source_sha256=source_hash,
            )
        )
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise UserActionRequired("Calibration labels changed during judging; nothing was written.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".atk-calibration-", dir=output.parent) as directory:
        pending = Path(directory) / "result.csv"
        write_csv_atomic(pending, fields + added, output_rows)
        try:
            os.link(pending, output)
        except FileExistsError as exc:
            raise UserActionRequired("Calibration output was created concurrently; it was not overwritten.") from exc
    return {
        "output": str(output),
        "samples": len(samples),
        "matched": sum(r["matched"] == "true" for r in output_rows),
        "false_passes": sum(r["judge_verdict"] == "passed" and r["expected_verdict"] == "failed" for r in output_rows),
        "false_failures": sum(
            r["judge_verdict"] == "failed" and r["expected_verdict"] == "passed" for r in output_rows
        ),
        "unknown": sum(r["judge_verdict"] == "unknown" for r in output_rows),
        "errors": sum(bool(r["judge_error"]) for r in output_rows),
        "disagreement_ids": [r["atk_id"] for r in output_rows if r["matched"] != "true"],
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "judge": judge_info,
    }


def launch_calibration(*, python=None, **options):
    """Use the project interpreter without modifying the regular run scheduler."""
    root = str(Path(__file__).resolve().parents[2])
    bootstrap = (
        "import sys,json;from contextlib import redirect_stdout;sys.path.insert(0,sys.argv[1]);"
        "from agent_tune_kit.execution.calibration import calibrate;"
        "\nwith redirect_stdout(sys.stderr): result=calibrate(**json.loads(sys.argv[2]))"
        "\nprint(json.dumps(result,ensure_ascii=False))"
    )
    result = subprocess.run(
        python_command(python) + ["-c", bootstrap, root, json.dumps(options)],
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ExecutionFailed(result.returncode)
    return json.loads(result.stdout)
