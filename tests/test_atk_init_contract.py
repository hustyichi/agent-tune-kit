from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_atk_init_contract_cleans_runner_smoke_results() -> None:
    skill_text = (ROOT / "skills/atk-init/SKILL.md").read_text(encoding="utf-8")
    shared_text = (ROOT / "docs/shared-versioning-and-confirmation.md").read_text(encoding="utf-8")

    assert "if a smoke execution is needed" in skill_text
    assert "record the exact result version that existed before and after the command" in skill_text
    assert "clean up only the result directory created or reused solely for that smoke check" in skill_text
    assert "must record the pre-smoke version state" in shared_text
    assert "clean up only the version directory created or reused solely by that smoke run" in shared_text


def test_atk_init_handoff_keeps_first_real_run_on_v1_when_no_prior_results() -> None:
    skill_text = (ROOT / "skills/atk-init/SKILL.md").read_text(encoding="utf-8")

    assert (
        "expected next output path `.atk/results/v1/eval_results.csv` when no prior result version exists" in skill_text
    )
    assert (
        "do not say a smoke test has already occupied `v1` when the smoke-created version was cleaned up" in skill_text
    )


def test_failure_finding_handoffs_route_to_report() -> None:
    direct_text = (ROOT / "skills/atk-find-failures/SKILL.md").read_text(encoding="utf-8")
    rule_text = (ROOT / "skills/atk-find-failures-by-rule/SKILL.md").read_text(encoding="utf-8")
    shared_text = (ROOT / "docs/shared-versioning-and-confirmation.md").read_text(encoding="utf-8")

    expected_handoff = "next command: `atk-report` to generate `.atk/results/vN/report.md`"
    assert expected_handoff in direct_text
    assert expected_handoff in rule_text
    assert "After writing `failure_cases.csv`, tell the user to run `atk-report` next." in shared_text
