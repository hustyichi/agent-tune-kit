import json

import pytest

from agent_tune_kit.datasets.updates import inspect_dataset, update_dataset
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.csv_io import parse_csv, write_csv_atomic


def dataset(tmp_path):
    path = tmp_path / ".atk/datasets/dataset.csv"
    write_csv_atomic(
        path,
        ["atk_id", "input", "expected", "label", "notes"],
        [
            {"atk_id": "1", "input": "question", "expected": "answer", "label": "yes", "notes": "note"},
        ],
    )
    context = path.parent.parent / "context.md"
    context.write_text("## Ground Truth Standard\nUse policy R1.\n\n## Tuning Decisions\nUnrelated.\n")
    return path


def review(path, tmp_path, **changes):
    info = inspect_dataset(path, direct=True, field="expected", input_fields=["input"])
    row = dict(
        atk_id="1",
        row_number="1",
        review_feedback="",
        review_status="confirmed",
        field="expected",
        input_fields='["input"]',
        source="policy.md#R1",
        evidence="The policy explicitly says answer.",
        reviewer="owner",
        dataset_sha256=info["dataset_sha256"],
        dataset_scope=info["dataset_scope"],
        standard_sha256=info["standard"]["sha256"],
    )
    row.update(changes)
    target = tmp_path / "review.csv"
    write_csv_atomic(target, list(row), [row])
    return target


def test_confirmation_is_scoped_and_manual_edits_expire_it(tmp_path):
    path = dataset(tmp_path)
    feedback = review(path, tmp_path)
    update_dataset(None, path, review_path=feedback, field="expected")
    info = inspect_dataset(path, direct=True, field="expected")
    assert info["truth"][0]["status"] == "confirmed"
    assert inspect_dataset(path, direct=True, field="label")["truth"][0]["status"] == "unreviewed"
    fields, rows = parse_csv(path)
    rows[0]["notes"] = "unrelated change"
    write_csv_atomic(path, fields, rows)
    assert inspect_dataset(path, direct=True, field="expected")["truth"][0]["status"] == "confirmed"
    rows[0]["input"] = "different question"
    write_csv_atomic(path, fields, rows)
    assert inspect_dataset(path, direct=True, field="expected")["truth"][0]["status"] == "stale"


def test_only_relevant_standard_sections_expire_confirmation(tmp_path):
    path = dataset(tmp_path)
    update_dataset(None, path, review_path=review(path, tmp_path), field="expected")
    context = path.parent.parent / "context.md"
    context.write_text(context.read_text().replace("Unrelated.", "New tuning decision."))
    assert inspect_dataset(path, direct=True, field="expected")["truth"][0]["status"] == "confirmed"
    context.write_text(context.read_text().replace("R1", "R2"))
    assert inspect_dataset(path, direct=True, field="expected")["truth"][0]["status"] == "stale"


@pytest.mark.parametrize("kind", ["changed", "other-project", "standard"])
def test_stale_or_foreign_feedback_never_writes(tmp_path, kind):
    path = dataset(tmp_path)
    feedback = review(path, tmp_path)
    if kind == "changed":
        path.write_text(path.read_text().replace("question", "changed"))
    elif kind == "standard":
        context = path.parent.parent / "context.md"
        context.write_text(context.read_text().replace("R1", "R2"))
    else:
        original = path
        path = tmp_path / "other/.atk/datasets/dataset.csv"
        path.parent.mkdir(parents=True)
        path.write_bytes(original.read_bytes())
    before = path.read_bytes()
    with pytest.raises(UserActionRequired):
        update_dataset(None, path, review_path=feedback, field="expected")
    assert path.read_bytes() == before


def test_generated_correction_does_not_inherit_confirmation(tmp_path):
    path = dataset(tmp_path)
    feedback = review(path, tmp_path)
    updates = tmp_path / "updates.csv"
    write_csv_atomic(updates, ["atk_id", "expected"], [{"atk_id": "1", "expected": "new answer"}])
    update_dataset(updates, path, review_path=feedback, field="expected")
    assert inspect_dataset(path, direct=True, field="expected")["truth"][0]["status"] == "pending"


@pytest.mark.parametrize("missing", ["source", "evidence", "reviewer"])
def test_confirmation_requires_evidence_and_reviewer(tmp_path, missing):
    path = dataset(tmp_path)
    feedback = review(path, tmp_path, **{missing: ""})
    before = path.read_bytes()
    with pytest.raises(UserActionRequired):
        update_dataset(None, path, review_path=feedback, field="expected")
    assert path.read_bytes() == before


def test_malformed_truth_cannot_be_imported_as_trusted(tmp_path):
    from agent_tune_kit.datasets.prepare import prepare_dataset

    path = dataset(tmp_path)
    fields, rows = parse_csv(path)
    rows[0]["atk_truth"] = json.dumps({"schema": 1, "fields": {"expected": {"status": "confirmed"}}})
    source = tmp_path / "source.csv"
    write_csv_atomic(source, fields + ["atk_truth"], rows)
    with pytest.raises(UserActionRequired):
        prepare_dataset(source, path, overwrite=True)


def test_changed_evidence_expires_review_and_identical_review_is_noop(tmp_path):
    path = dataset(tmp_path)
    update_dataset(None, path, review_path=review(path, tmp_path), field="expected")
    before = path.read_bytes()
    update_dataset(None, path, review_path=review(path, tmp_path), field="expected")
    assert path.read_bytes() == before
    fields, rows = parse_csv(path)
    data = json.loads(rows[0]["atk_truth"])
    data["fields"]["expected"]["source"] = "policy R2"
    rows[0]["atk_truth"] = json.dumps(data)
    write_csv_atomic(path, fields, rows)
    assert inspect_dataset(path, direct=True, field="expected")["truth"][0]["status"] == "stale"


def test_append_legacy_samples_does_not_require_copying_review_metadata(tmp_path):
    from agent_tune_kit.datasets.prepare import prepare_dataset

    path = dataset(tmp_path)
    update_dataset(None, path, review_path=review(path, tmp_path), field="expected")
    incoming = tmp_path / "new.csv"
    fields, rows = parse_csv(path)
    row = {k: v for k, v in rows[0].items() if k != "atk_truth"}
    row["atk_id"] = "2"
    write_csv_atomic(incoming, [k for k in fields if k != "atk_truth"], [row])
    prepare_dataset(incoming, path, append=True)
    assert [r["status"] for r in inspect_dataset(path, direct=True, field="expected")["truth"]] == [
        "confirmed",
        "unreviewed",
    ]
