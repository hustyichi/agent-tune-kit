from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_rel(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_local_context_contract_is_private_and_standard_focused() -> None:
    text = read_rel("docs/local-context.md")
    shared_text = read_rel("docs/shared-versioning-and-confirmation.md")
    read_only_skill = read_rel("skills/atk-diagnose/SKILL.md")
    tune_skill = read_rel("skills/atk-optimize/SKILL.md")
    report_skill = read_rel("skills/atk-report/SKILL.md")
    dataset_skill = read_rel("skills/atk-dataset/SKILL.md")

    for phrase in [
        "`./.atk/context.md` is a local, private tuning-consensus document",
        "It is not a dataset metadata registry, run log, or team collaboration file.",
        "## Tuning Objective",
        "## Agent Behavior Standard",
        "## Ground Truth Standard",
        "## User Feedback",
        "## Tuning Decisions",
        "dataset path, row count, headers, field types",
        "result version, latest run path, metrics snapshots",
        "Missing `.atk/context.md` must never block a Skill.",
    ]:
        assert phrase in text

    assert "Local private tuning consensus: `.atk/context.md`" in shared_text
    assert "must not become a registry for dataset headers, field" in shared_text
    assert "It should not write `.atk/context.md`." in shared_text

    assert "Do not write `.atk/context.md`" in read_only_skill
    assert "Do not modify `Ground Truth Standard`" in tune_skill
    assert "Append to `.atk/context.md` only when" in report_skill
    assert "changes the confirmed `Ground Truth Standard`" in dataset_skill
    assert "Do not use `.atk/context.md` for dataset metadata" in dataset_skill
