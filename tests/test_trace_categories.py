import json
from pathlib import Path

import pytest

from agent_tune_kit.cli.main import main
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces import categories
from agent_tune_kit.traces.records import load_issue, save_diagnosis, save_issue
from agent_tune_kit.traces.store import create_capture, evidence_ref, trace_root

SHELL = {"id": "shell-arg-retry", "name": "Shell argument retries", "definition": "Tool calls retried after bad paths."}
GAP = {"id": "acceptance-gap", "name": "Acceptance gap", "definition": "Completion claimed without a check."}


@pytest.fixture
def archive(tmp_path):
    result = create_capture(
        tmp_path,
        "session-1",
        {"type": "local", "instance": "fixture", "project": "agent"},
        {"trace.json": '{"sessionId":"session-1","output":"wrong"}'},
    )
    directory = Path(result["session_dir"])
    return tmp_path, directory, evidence_ref(directory, f"captures/{result['capture_id']}/raw/trace.json", "/output")


def issue(directory, ref, **extra):
    return save_issue(
        directory, {"title": "Wrong answer", "description": "Output differs from the task.", "evidence": [ref], **extra}
    )


def add(project, entry, revision=None):
    data = {**entry} if revision is None else {**entry, "revision": revision}
    return categories.save_category(project, data)


def test_first_category_needs_no_revision_but_later_edits_do(archive):
    project, directory, _ = archive
    created = add(project, SHELL)
    assert created["created"] and created["vocabulary_revision"] == 1 and created["deprecated"] is False
    with pytest.raises(UserActionRequired, match="revision"):
        add(project, GAP)  # the vocabulary exists now, so the caller must have read it
    with pytest.raises(UserActionRequired, match="revision"):
        add(project, GAP, revision=0)
    add(project, GAP, revision=1)
    catalog = categories.load_catalog(trace_root(project))
    assert [item["id"] for item in catalog["categories"]] == [SHELL["id"], GAP["id"]] and catalog["revision"] == 2
    assert directory.parent == trace_root(project)


def test_revision_must_be_a_real_integer(archive):
    project, *_ = archive
    add(project, SHELL)
    for bad in (True, 1.0, "1"):
        with pytest.raises(UserActionRequired, match="revision"):
            add(project, {**SHELL, "name": "Renamed"}, revision=bad)
    assert categories.load_catalog(trace_root(project))["revision"] == 1


def test_first_category_in_a_new_project_keeps_the_archive_out_of_git(tmp_path):
    add(tmp_path, SHELL)
    assert (trace_root(tmp_path) / categories.FILENAME).is_file()
    assert (tmp_path / ".gitignore").read_text().splitlines()[-1] == "/.atk/traces/"


@pytest.mark.parametrize("bad", ["Shell", "shell_retry", "-x", "x-", "a--b", "", "a b", "x" * 65, None, 7])
def test_category_ids_are_lowercase_kebab_case(archive, bad):
    project, *_ = archive
    with pytest.raises(UserActionRequired, match="kebab"):
        add(project, {**SHELL, "id": bad})
    assert not (trace_root(project) / categories.FILENAME).exists()


@pytest.mark.parametrize(
    "data, message",
    [
        ({"id": "x", "definition": "d"}, "name"),
        ({"id": "x", "name": "n"}, "definition"),
        ({"id": "x", "name": "n", "definition": " "}, "definition"),
        ({"id": "x", "name": "n", "definition": "d", "deprecated": True}, "deprecated"),
        ({"id": "x", "name": "n", "definition": "d", "colour": "red"}, "Unsupported"),
    ],
)
def test_invalid_new_categories_do_not_write(archive, data, message):
    project, *_ = archive
    with pytest.raises(UserActionRequired, match=message):
        categories.save_category(project, data)
    assert not (trace_root(project) / categories.FILENAME).exists()


def test_revision_edits_keep_identity_and_never_delete(archive):
    project, *_ = archive
    add(project, SHELL)
    changed = categories.save_category(
        project, {"id": SHELL["id"], "name": "Shell retries", "definition": "Narrower.", "revision": 1}
    )
    assert not changed["created"] and changed["name"] == "Shell retries" and changed["created_at"]
    with pytest.raises(UserActionRequired, match="nonempty string"):
        categories.save_category(project, {"id": SHELL["id"], "name": "", "revision": 2})
    with pytest.raises(UserActionRequired, match="true or false"):
        categories.save_category(project, {"id": SHELL["id"], "deprecated": "yes", "revision": 2})
    assert categories.load_catalog(trace_root(project))["revision"] == 2


def test_damaged_vocabulary_blocks_writes_but_not_display(archive):
    project, directory, ref = archive
    path = trace_root(project) / categories.FILENAME
    path.write_text(json.dumps({"schema_version": 1, "revision": 1, "categories": [{"id": "BAD ID"}]}))
    with pytest.raises(UserActionRequired, match="Invalid Trace category"):
        add(project, SHELL, revision=1)
    with pytest.raises(UserActionRequired, match="Invalid Trace category"):
        issue(directory, ref, category="x")
    assert categories.category_map(trace_root(project)) == {}  # pages still render


def test_issue_category_must_come_from_the_vocabulary_without_mutating(archive):
    project, directory, ref = archive
    with pytest.raises(UserActionRequired, match="Unknown issue category"):
        issue(directory, ref, category=SHELL["id"])  # no vocabulary yet
    assert not (directory / "issues").exists()
    add(project, SHELL)
    first = issue(directory, ref, category=SHELL["id"])
    assert load_issue(directory, first["id"])["category"] == SHELL["id"]
    for bad in ("missing", "", None, 3):
        with pytest.raises(UserActionRequired, match="category"):
            save_issue(directory, {"id": first["id"], "revision": first["revision"], "category": bad})
    assert load_issue(directory, first["id"])["revision"] == first["revision"]


def test_category_changes_keep_history_and_omission_preserves_the_value(archive):
    project, directory, ref = archive
    add(project, SHELL)
    add(project, GAP, revision=1)
    first = issue(directory, ref, category=SHELL["id"])
    kept = save_issue(directory, {"id": first["id"], "revision": first["revision"], "notes": ["reviewed"]})
    assert kept["category"] == SHELL["id"]
    moved = save_issue(directory, {"id": first["id"], "revision": kept["revision"], "category": GAP["id"]})
    saved = load_issue(directory, moved["id"])
    assert saved["category"] == GAP["id"] and saved["revision"] == 3
    assert [entry.get("category") for entry in saved["history"]] == [SHELL["id"], SHELL["id"]]
    assert saved["title"] == first["title"] and saved["segments"] == first["segments"]


def test_deprecated_category_is_kept_where_used_and_refused_for_new_choices(archive):
    project, directory, ref = archive
    add(project, SHELL)
    add(project, GAP, revision=1)
    old = issue(directory, ref, category=SHELL["id"])
    categories.save_category(project, {"id": SHELL["id"], "deprecated": True, "revision": 2})
    with pytest.raises(UserActionRequired, match="deprecated"):
        issue(directory, ref, category=SHELL["id"])
    # Re-saving the unchanged value (for example while revising other fields) is not a new choice.
    again = save_issue(
        directory,
        {"id": old["id"], "revision": old["revision"], "category": SHELL["id"], "title": "Wrong answer, narrowed"},
    )
    assert again["category"] == SHELL["id"]
    moved = save_issue(directory, {"id": old["id"], "revision": again["revision"], "category": GAP["id"]})
    assert moved["category"] == GAP["id"]
    with pytest.raises(UserActionRequired, match="deprecated"):  # and cannot go back
        save_issue(directory, {"id": old["id"], "revision": moved["revision"], "category": SHELL["id"]})


def test_legacy_issue_without_category_stays_valid_and_diagnosis_snapshots_the_category(archive):
    project, directory, ref = archive
    legacy = issue(directory, ref)
    assert "category" not in load_issue(directory, legacy["id"])
    no_category = save_diagnosis(directory, legacy["id"], "Cause and plan.")
    assert '"category"' not in Path(no_category["path"]).read_text().split("\n", 1)[0]
    add(project, SHELL)
    tagged = save_issue(
        directory, {"id": legacy["id"], "revision": no_category["issue_revision"], "category": SHELL["id"]}
    )
    diagnosis = save_diagnosis(directory, tagged["id"], "A second, clearer explanation.")
    first_line = Path(diagnosis["path"]).read_text().split("\n", 1)[0]
    assert f'"category":"{SHELL["id"]}"' in first_line


def test_list_counts_references_per_session_and_uncategorized(archive):
    project, directory, ref = archive
    add(project, SHELL)
    add(project, GAP, revision=1)
    issue(directory, ref, category=SHELL["id"])
    issue(directory, ref, category=SHELL["id"])
    issue(directory, ref)
    listing = categories.list_categories(project)
    by_id = {item["id"]: item for item in listing["categories"]}
    assert by_id[SHELL["id"]]["issues"] == 2 and by_id[SHELL["id"]]["sessions"] == 1
    assert by_id[GAP["id"]]["issues"] == 0 and by_id[GAP["id"]]["sessions"] == 0
    assert listing["uncategorized_issues"] == 1 and listing["revision"] == 2 and listing["warnings"] == []


def test_list_with_no_vocabulary_or_sessions_is_empty(tmp_path):
    assert categories.list_categories(tmp_path) == {
        "revision": 0,
        "categories": [],
        "uncategorized_issues": 0,
        "warnings": [],
    }


def test_category_command_lists_and_saves_through_the_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "category.json"
    data.write_text(json.dumps(SHELL), encoding="utf-8")
    assert main(["trace", "category", "--data", str(data), "--json"]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["id"] == SHELL["id"] and saved["created"]
    assert main(["trace", "category", "--list", "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in listing["categories"]] == [SHELL["id"]] and listing["revision"] == 1
    assert main(["trace", "category", "--data", str(data), "--json"]) == 2  # stale: the vocabulary now exists
    assert "revision" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # one of --list/--data is required, and not both
        main(["trace", "category", "--list", "--data", str(data)])
