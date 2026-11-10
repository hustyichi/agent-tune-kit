"""Actual issue progress derived from saved records.

The declared issue ``status`` is not a progress signal; only ``excluded`` is honored so that withdrawn issues do not
stay in the backlog. Everything else comes from diagnoses, changes and verification records. The functions here do no
I/O so the session preview and the project dashboard report the same result.
"""

from datetime import datetime

# Workflow order: unfinished work first, closed outcomes last. Keys double as CSS class names.
PROGRESS = (
    ("pending", "待诊断"),
    ("diagnosed", "已诊断"),
    ("changing", "修复中"),
    ("awaiting", "待验证"),
    ("verifying", "验证中"),
    ("fail", "验证失败"),
    ("inconclusive", "无法判断"),
    ("pass", "验证通过"),
    ("excluded", "已排除"),
)
LABELS = dict(PROGRESS)
ORDER = {key: index for index, (key, _) in enumerate(PROGRESS)}


def parse_instant(value):
    """Return epoch seconds for an ISO timestamp, or None when it is missing, malformed or has no timezone."""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            return stamp.timestamp()
    except (TypeError, ValueError, AttributeError, OverflowError, OSError):
        pass
    return None


def instant(record):
    """Return the creation instant in seconds, or infinity when it is missing or has no timezone."""
    when = parse_instant(record.get("created_at")) if isinstance(record, dict) else None
    return float("inf") if when is None else when


def _recency(record):
    """Sort key where the newest known instant is largest; records with unknown time never count as newest."""
    when = instant(record)
    known = when != float("inf")
    return known, when if known else 0.0, str(record.get("id", ""))


def _result(key):
    return {"key": key, "label": LABELS[key]}


# Verification states whose per-issue conclusions count as an effect outcome. Failed, blocked and interrupted attempts
# stay visible on the session page but never replace an earlier finished outcome.
FINISHED = {"completed", "partial"}


def issue_progress(issue, changes):
    """Return ``{key, label}`` for one issue.

    ``changes`` pairs every change linked to the issue (including a shared change owned by another issue) with all
    verification records of that change; an entry that could not be read is passed as ``None``. The newest completed
    or running change decides the stage, because an older verified attempt says nothing about a newer one. Within that
    change the newest finished verification gives the outcome. An unreadable verification might be the newest one, so
    the outcome is then unknown rather than an older result.
    """
    if issue.get("status") == "excluded":
        return _result("excluded")
    active = [(change, items) for change, items in changes if change.get("status") in {"completed", "running"}]
    if active:
        change, verifications = max(active, key=lambda pair: _recency(pair[0]))
        if change["status"] == "running":
            return _result("changing")
        if any(not isinstance(item, dict) for item in verifications):
            return _result("inconclusive")
        finished = [item for item in verifications if item.get("status") in FINISHED]
        if finished:
            latest = max(finished, key=_recency)
            conclusions = latest.get("conclusions")
            conclusion = conclusions.get(issue.get("id")) if isinstance(conclusions, dict) else None
            outcome = conclusion.get("outcome") if isinstance(conclusion, dict) else None
            return _result(outcome if outcome in {"pass", "fail"} else "inconclusive")
        if any(item.get("status") == "running" for item in verifications):
            return _result("verifying")
        return _result("awaiting")
    return _result("diagnosed" if issue.get("diagnoses") else "pending")


def summarize(keys):
    """Count progress keys in workflow order, omitting nothing so callers can render stable legends."""
    counts = dict.fromkeys(LABELS, 0)
    for key in keys:
        counts[key] += 1
    return counts
