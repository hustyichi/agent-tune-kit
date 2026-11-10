"""Project-level issue category vocabulary.

The host decides which category fits an issue; this module only keeps the vocabulary consistent. Entries are never
deleted, so issues that reference a category stay displayable after it is deprecated.
"""

import re
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import now, safe_path, write_manifest

from .store import ensure_workspace, read_json, session_write_lock, trace_root

FILENAME = "categories.json"
CATEGORY_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_ID = 64


def _path(root):
    return safe_path(Path(root), FILENAME)


def _check_catalog(value):
    if (
        value.get("schema_version") != 1
        or not isinstance(value.get("revision"), int)
        or isinstance(value.get("revision"), bool)
        or not isinstance(value.get("categories"), list)
    ):
        raise UserActionRequired("Invalid Trace category vocabulary.")
    seen = set()
    for item in value["categories"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not CATEGORY_ID.fullmatch(item["id"])
            or item["id"] in seen
            or any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("name", "definition"))
            or not isinstance(item.get("deprecated"), bool)
        ):
            raise UserActionRequired("Invalid Trace category entry.")
        seen.add(item["id"])
    return value


def load_catalog(root):
    """Read the vocabulary from a traces root; a missing file is an empty vocabulary."""
    path = _path(root)
    if not path.exists():
        return {"schema_version": 1, "revision": 0, "categories": []}
    return _check_catalog(read_json(path))


def category_map(root):
    """Return ``{id: entry}`` for display, tolerating a damaged file so one bad record cannot hide sessions."""
    try:
        return {item["id"]: item for item in load_catalog(root)["categories"]}
    except UserActionRequired:
        return {}


def check_issue_category(root, category, previous=None):
    """Validate an issue category against the vocabulary; an unchanged deprecated value stays valid."""
    if not isinstance(category, str) or not category:
        raise UserActionRequired("Issue category must be a category ID from the project vocabulary.")
    entry = next((item for item in load_catalog(root)["categories"] if item["id"] == category), None)
    if entry is None:
        raise UserActionRequired(
            f"Unknown issue category {category!r}; list the vocabulary with `atk trace category --list` first."
        )
    if entry["deprecated"] and category != previous:
        raise UserActionRequired(f"Issue category {category!r} is deprecated; choose a current category.")
    return category


def save_category(project_root, data):
    """Add or revise one category. Supplying ``revision`` is required once the vocabulary exists."""
    if not isinstance(data, dict):
        raise UserActionRequired("Category data must be an object.")
    unknown = set(data) - {"id", "name", "definition", "deprecated", "revision", "expected_revision"}
    if unknown:
        raise UserActionRequired(f"Unsupported category fields: {', '.join(sorted(unknown))}.")
    category_id = data.get("id")
    if not isinstance(category_id, str) or not CATEGORY_ID.fullmatch(category_id) or len(category_id) > MAX_ID:
        raise UserActionRequired("Category id must be lowercase kebab-case (letters, digits and single hyphens).")
    expected = data.get("revision", data.get("expected_revision"))
    if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool)):
        raise UserActionRequired("Category revision must be an integer.")
    # The vocabulary lives in the private archive, so it gets the same Git ignore rule as captured sessions.
    root, _ = ensure_workspace(project_root)
    with session_write_lock(root):
        catalog = load_catalog(root)
        if expected is None and catalog["revision"] == 0:
            expected = 0
        if expected != catalog["revision"]:
            raise UserActionRequired(
                f"Stale or missing category revision; current vocabulary revision is {catalog['revision']}."
            )
        entries = catalog["categories"]
        existing = next((item for item in entries if item["id"] == category_id), None)
        stamp = now()
        if existing is None:
            for key in ("name", "definition"):
                if not isinstance(data.get(key), str) or not data[key].strip():
                    raise UserActionRequired(f"A new category needs a nonempty {key}.")
            if data.get("deprecated") not in (None, False):
                raise UserActionRequired("A new category cannot start deprecated.")
            entry = {
                "id": category_id,
                "name": data["name"].strip(),
                "definition": data["definition"].strip(),
                "deprecated": False,
                "created_at": stamp,
                "updated_at": stamp,
            }
            entries.append(entry)
        else:
            entry = existing
            for key in ("name", "definition"):
                if key in data:
                    if not isinstance(data[key], str) or not data[key].strip():
                        raise UserActionRequired(f"Category {key} must be a nonempty string.")
                    entry[key] = data[key].strip()
            if "deprecated" in data:
                if not isinstance(data["deprecated"], bool):
                    raise UserActionRequired("Category deprecated must be true or false.")
                entry["deprecated"] = data["deprecated"]
            entry["updated_at"] = stamp
        catalog["revision"] += 1
        write_manifest(_path(root), catalog)
    return {**entry, "vocabulary_revision": catalog["revision"], "created": existing is None}


def list_categories(project_root):
    """Return the vocabulary with how many issues in each session reference every category."""
    from . import records

    root = trace_root(project_root)
    catalog = load_catalog(root)
    usage = {item["id"]: {} for item in catalog["categories"]}
    uncategorized = 0
    warnings = []
    sessions = sorted(child for child in root.iterdir() if child.is_dir()) if root.exists() else []
    for session_dir in sessions:
        if not (session_dir / "session.json").is_file() or session_dir.is_symlink():
            continue
        issue_root = session_dir / "issues"
        for issue_file in sorted(issue_root.glob("*/issue.json")) if issue_root.is_dir() else []:
            try:
                issue = records.load_issue(session_dir, issue_file.parent.name)
            except UserActionRequired as exc:
                warnings.append({"session": session_dir.name, "issue_id": issue_file.parent.name, "error": str(exc)})
                continue
            category = issue.get("category")
            if category is None:
                uncategorized += 1
            elif category in usage:
                usage[category][session_dir.name] = usage[category].get(session_dir.name, 0) + 1
    return {
        "revision": catalog["revision"],
        "categories": [
            {
                **item,
                "sessions": len(usage[item["id"]]),
                "issues": sum(usage[item["id"]].values()),
                "by_session": usage[item["id"]],
            }
            for item in catalog["categories"]
        ],
        "uncategorized_issues": uncategorized,
        "warnings": warnings,
    }
