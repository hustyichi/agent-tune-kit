import pytest

from agent_tune_kit.traces.progress import LABELS, ORDER, PROGRESS, instant, issue_progress, summarize

ISSUE = {"id": "ISSUE-001", "status": "open", "diagnoses": [{"path": "d.md"}]}


def change(identifier="CHG-1", status="completed", created="2026-01-01T00:00:00Z"):
    return {"id": identifier, "status": status, "created_at": created}


def verification(identifier, created, outcome="pass", status="completed", issue="ISSUE-001"):
    conclusions = {issue: {"outcome": outcome, "reason": "observed"}} if outcome else {}
    return {"id": identifier, "status": status, "created_at": created, "conclusions": conclusions}


def key(issue, changes):
    return issue_progress(issue, changes)["key"]


def test_progress_follows_records_and_ignores_declared_status():
    assert key({**ISSUE, "diagnoses": []}, []) == "pending"
    assert key(ISSUE, []) == "diagnosed"
    # Declared states that claim more than the records show change nothing.
    for declared in ("resolved", "verifying", "diagnosed", "changing", "blocked"):
        assert key({**ISSUE, "diagnoses": [], "status": declared}, []) == "pending"
    assert key({**ISSUE, "status": "excluded"}, [(change(), [verification("V1", "2026-01-02T00:00:00Z")])]) == (
        "excluded"
    )


def test_change_and_verification_stages():
    assert key(ISSUE, [(change(status="running"), [])]) == "changing"
    assert key(ISSUE, [(change(), [])]) == "awaiting"
    assert key(ISSUE, [(change(), [verification("V1", "2026-01-02T00:00:00Z", status="running", outcome=None)])]) == (
        "verifying"
    )
    # Failed, blocked and interrupted changes never reached a candidate, so the diagnosis is the last real stage.
    for state in ("failed", "blocked", "interrupted"):
        assert key(ISSUE, [(change(status=state), [])]) == "diagnosed"
    assert issue_progress(ISSUE, [(change(), [])]) == {"key": "awaiting", "label": "待验证"}


@pytest.mark.parametrize("outcome", ["pass", "fail", "inconclusive"])
def test_outcome_comes_from_this_issues_conclusion(outcome):
    assert key(ISSUE, [(change(), [verification("V1", "2026-01-02T00:00:00Z", outcome)])]) == outcome


def test_missing_or_malformed_conclusion_is_inconclusive_never_a_pass():
    other = verification("V1", "2026-01-02T00:00:00Z", "pass", issue="ISSUE-002")
    assert key(ISSUE, [(change(), [other])]) == "inconclusive"
    for conclusions in (None, [], {"ISSUE-001": "pass"}, {"ISSUE-001": {"outcome": "great"}}):
        record = {**verification("V1", "2026-01-02T00:00:00Z"), "conclusions": conclusions}
        assert key(ISSUE, [(change(), [record])]) == "inconclusive"


def test_latest_verification_is_chosen_by_instant_not_id_or_offset_string():
    # "Z" sorts after "+" as text and the random IDs are in the wrong order; only the instant is the truth.
    older = verification("VER-z", "2026-01-01T09:00:00+08:00", "pass")  # 01:00 UTC
    newer = verification("VER-a", "2026-01-01T02:00:00Z", "fail")
    assert key(ISSUE, [(change(), [older, newer])]) == "fail"
    assert key(ISSUE, [(change(), [newer, older])]) == "fail"


def test_unknown_time_never_counts_as_newest_but_still_counts_when_alone():
    known = verification("V1", "2026-01-01T02:00:00Z", "fail")
    for bad in ("unknown", "2026-01-01T10:00:00", None):  # unparseable, no timezone, missing
        record = verification("V2", bad or "x", "pass")
        if bad is None:
            del record["created_at"]
        assert key(ISSUE, [(change(), [record, known])]) == "fail"
        assert key(ISSUE, [(change(), [record])]) == "pass"
    assert instant({"created_at": "2026-01-01T10:00:00"}) == float("inf")


def test_equal_instants_fall_back_to_id_deterministically():
    first = verification("VER-a", "2026-01-01T02:00:00Z", "pass")
    second = verification("VER-b", "2026-01-01T02:00:00Z", "fail")
    assert key(ISSUE, [(change(), [first, second])]) == key(ISSUE, [(change(), [second, first])]) == "fail"


def test_newest_change_decides_over_an_older_verified_one():
    verified = (change("CHG-1", created="2026-01-01T00:00:00Z"), [verification("V1", "2026-01-01T01:00:00Z")])
    assert key(ISSUE, [verified, (change("CHG-2", "running", "2026-01-02T00:00:00Z"), [])]) == "changing"
    assert key(ISSUE, [verified, (change("CHG-2", "completed", "2026-01-02T00:00:00Z"), [])]) == "awaiting"
    # An older replacement attempt does not hide the newer verified one.
    assert key(ISSUE, [verified, (change("CHG-0", "running", "2025-12-31T00:00:00Z"), [])]) == "pass"


def test_only_finished_verifications_give_an_outcome():
    done = verification("V1", "2026-01-02T00:00:00Z", "pass")
    # A later re-check that is still running, or that stopped early, does not erase the finished result.
    for state in ("running", "interrupted", "failed", "blocked"):
        later = verification("V2", "2026-01-03T00:00:00Z", "inconclusive", status=state)
        assert key(ISSUE, [(change(), [done, later])]) == "pass"
    assert key(ISSUE, [(change(), [verification("V1", "2026-01-02T00:00:00Z", "fail", status="partial")])]) == "fail"
    # Attempts that never finished leave the change waiting for verification.
    stopped = verification("V1", "2026-01-02T00:00:00Z", "inconclusive", status="interrupted")
    assert key(ISSUE, [(change(), [stopped])]) == "awaiting"
    running = verification("V2", "2026-01-03T00:00:00Z", None, status="running")
    assert key(ISSUE, [(change(), [stopped, running])]) == "verifying"


def test_an_unreadable_verification_makes_the_outcome_unknown_not_an_older_result():
    done = verification("V1", "2026-01-02T00:00:00Z", "pass")
    for unreadable in (None, [], "broken"):
        assert key(ISSUE, [(change(), [done, unreadable])]) == "inconclusive"
    # Unreadable records of an older change do not matter once a newer change decides.
    older = (change("CHG-1", created="2026-01-01T00:00:00Z"), [None])
    assert key(ISSUE, [older, (change("CHG-2", created="2026-01-02T00:00:00Z"), [done])]) == "pass"


def test_shared_change_uses_each_issues_own_conclusion():
    shared = {
        "id": "VER-1",
        "status": "completed",
        "created_at": "2026-01-02T00:00:00Z",
        "conclusions": {"ISSUE-001": {"outcome": "pass"}, "ISSUE-002": {"outcome": "fail"}},
    }
    pairs = [(change(), [shared])]
    assert key({**ISSUE, "id": "ISSUE-001"}, pairs) == "pass"
    assert key({**ISSUE, "id": "ISSUE-002"}, pairs) == "fail"


def test_legend_is_ordered_complete_and_summaries_keep_every_key():
    assert [key_ for key_, _ in PROGRESS] == list(ORDER) == list(LABELS)
    assert len(set(LABELS.values())) == len(LABELS)
    counts = summarize(["pending", "pending", "pass"])
    assert list(counts) == list(LABELS)
    assert counts["pending"] == 2 and counts["pass"] == 1 and counts["excluded"] == 0
    with pytest.raises(KeyError):
        summarize(["verified"])
