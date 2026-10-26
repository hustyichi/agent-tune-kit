import hashlib
import json

import pytest

from agent_tune_kit.datasets.updates import update_dataset
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.judgments import apply_judgments, read_judgments
from agent_tune_kit.results.summary import compare_targets, inspect_results
from agent_tune_kit.utils.csv_io import parse_csv, write_csv_atomic
from tests.test_truth_contract import dataset, review


def metadata(**changes):
    from agent_tune_kit.datasets.truth import digest

    value = {
        "schema": 1,
        "standard": {"text": "Use policy R1.", "sha256": digest("Use policy R1.")},
        "judge": {"kind": "semantic", "identity": "test judge", "instructions": "Compare against policy."},
        "input_fields": ["input"],
        "truth_fields": ["expected"],
        "basis": "truth",
    }
    value.update(changes)
    return json.dumps(value)


def result_fixture(tmp_path, *, confirmed=True, version="v1"):
    data = dataset(tmp_path)
    if confirmed:
        update_dataset(None, data, review_path=review(data, tmp_path), field="expected")
    fields, rows = parse_csv(data)
    rows[0]["agent_output"] = "answer"
    path = tmp_path / ".atk/results" / version / "eval_results.csv"
    write_csv_atomic(path, fields + ["agent_output"], rows)
    return path


def judgments(tmp_path, source, verdict="passed", assessment=None):
    path = tmp_path / "judgments.csv"
    row = dict(
        atk_id="1",
        verdict=verdict,
        reason="Explicit criterion and observed output",
        results_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        assessment=assessment or metadata(),
    )
    write_csv_atomic(path, list(row), [row])
    return path


def test_truth_dependent_pass_needs_confirmed_reference(tmp_path):
    source = result_fixture(tmp_path, confirmed=False)
    with pytest.raises(UserActionRequired):
        apply_judgments(judgments(tmp_path, source), results_dir=source.parent.parent)
    apply_judgments(judgments(tmp_path, source, "unknown"), results_dir=source.parent.parent)
    assert read_judgments(source.parent) == {"1": "unknown"}
    apply_judgments(
        judgments(tmp_path, source, "failed", metadata(basis="independent", truth_fields=[])),
        results_dir=source.parent.parent,
    )
    assert read_judgments(source.parent) == {"1": "failed"}


def test_confirmed_truth_is_bound_to_the_applied_standard(tmp_path):
    source = result_fixture(tmp_path)
    from agent_tune_kit.datasets.truth import digest

    changed = metadata(standard={"text": "Different policy", "sha256": digest("Different policy")})
    with pytest.raises(UserActionRequired):
        apply_judgments(judgments(tmp_path, source, assessment=changed), results_dir=source.parent.parent)


def test_assessment_is_saved_and_rejudgment_preserves_history(tmp_path):
    source = result_fixture(tmp_path)
    # The review binds the named standard section, including its heading.
    _, rows = parse_csv(source)
    std = json.loads(rows[0]["atk_truth"])["fields"]["expected"]["standard"]
    meta = metadata(standard=std)
    apply_judgments(judgments(tmp_path, source, assessment=meta), results_dir=source.parent.parent)
    old = (source.parent / "judgments.csv").read_bytes()
    assert parse_csv(source.parent / "judgments.csv")[1][0]["assessment"] == meta
    apply_judgments(judgments(tmp_path, source, "unknown", meta), results_dir=source.parent.parent)
    history = list((source.parent / "judgment-history").glob("*.csv"))
    assert any(p.read_bytes() == old for p in history)
    assert inspect_results(source.parent.parent)["truth_counts"]["confirmed"] == 1


def test_corrupt_assessment_cannot_restore_failure_from_projection(tmp_path):
    source = result_fixture(tmp_path)
    meta = metadata(basis="independent", truth_fields=[])
    apply_judgments(judgments(tmp_path, source, "failed", meta), results_dir=source.parent.parent)
    fields, rows = parse_csv(source.parent / "judgments.csv")
    rows[0]["assessment"] = "{bad json"
    write_csv_atomic(source.parent / "judgments.csv", fields, rows)
    assert read_judgments(source.parent) == {"1": "unknown"}
    assert inspect_results(source.parent.parent)["failed"] == 0


def test_comparison_exposes_standard_changes_without_claiming_improvement(tmp_path):
    from agent_tune_kit.datasets.truth import digest

    first = result_fixture(tmp_path, version="v1")
    apply_judgments(
        judgments(tmp_path, first, "failed", metadata(basis="independent", truth_fields=[])),
        results_dir=first.parent.parent,
    )
    second = result_fixture(tmp_path, version="v2")
    new = metadata(basis="independent", truth_fields=[], standard={"text": "R2", "sha256": digest("R2")})
    apply_judgments(judgments(tmp_path, second, assessment=new), results_dir=second.parent.parent)
    comparison = compare_targets(["1"], second.parent.parent)
    assert comparison["current_status"] == "已解决"
    assert comparison["status"] == "无法判断"
    assert "standard" in comparison["comparison_differences"]["1"]


def test_rule_records_its_actual_source_and_declared_standard(tmp_path):
    from agent_tune_kit.execution.rules import run_rule

    source = result_fixture(tmp_path)
    _, rows = parse_csv(source)
    standard = json.loads(rows[0]["atk_truth"])["fields"]["expected"]["standard"]["text"]
    rule = tmp_path / "rule.py"
    rule.write_text(
        f'ATK_CRITERIA = {standard!r}\nATK_INPUT_FIELDS = ["input"]\n'
        'ATK_TRUTH_FIELDS = ["expected"]\ndef judge(row):\n'
        '    return "passed", "Matches the confirmed reference"\n'
    )
    run_rule(rule_path=rule, results_dir=source.parent.parent)
    saved = parse_csv(source.parent / "judgments.csv")[1][0]
    assert json.loads(saved["assessment"])["judge"]["source"] == rule.read_text()
    assert saved["verdict"] == "passed"


def test_rejudge_under_new_confirmed_reference_without_rerunning_agent(tmp_path):
    source = result_fixture(tmp_path)
    original_result = source.read_bytes()
    data = tmp_path / ".atk/datasets/dataset.csv"
    context = data.parent.parent / "context.md"
    context.write_text(context.read_text().replace("R1", "R2"))
    fields, rows = parse_csv(data)
    rows[0]["expected"] = "revised answer"
    write_csv_atomic(data, fields, rows)
    update_dataset(None, data, review_path=review(data, tmp_path), field="expected")
    reference = parse_csv(data)[1][0]
    standard = json.loads(reference["atk_truth"])["fields"]["expected"]["standard"]
    meta = metadata(standard=standard, reference_snapshot=reference)
    apply_judgments(judgments(tmp_path, source, "failed", meta), results_dir=source.parent.parent)
    assert read_judgments(source.parent) == {"1": "failed"}
    assert source.read_bytes() == original_result
    reference["input"] = "different task"
    with pytest.raises(UserActionRequired):
        apply_judgments(
            judgments(tmp_path, source, "passed", metadata(standard=standard, reference_snapshot=reference)),
            results_dir=source.parent.parent,
        )


def test_deleted_judgment_does_not_restore_trust_failure_projection(tmp_path):
    source = result_fixture(tmp_path)
    apply_judgments(
        judgments(tmp_path, source, "failed", metadata(basis="independent", truth_fields=[])),
        results_dir=source.parent.parent,
    )
    (source.parent / "judgments.csv").unlink()
    assert inspect_results(source.parent.parent)["failed"] == 0


def test_legacy_browser_keeps_invalid_assessment_unknown_without_scene_warning(tmp_path):
    from agent_tune_kit.visualize.failures import generate_failures
    from tests.test_generate_failure_browser import extract_failure_payload

    source = result_fixture(tmp_path)
    apply_judgments(
        judgments(tmp_path, source, "failed", metadata(basis="independent", truth_fields=[])),
        results_dir=source.parent.parent,
    )
    (source.parent / "judgments.csv").unlink()
    generate_failures(source.parent.parent, open_browser=False)
    payload = extract_failure_payload((source.parent / "failure_cases.html").read_text())
    assert payload["rows"][0]["values"]["is_abnormal"] == "unknown"
    assert "execution_evidence" not in payload["fieldnames"]
