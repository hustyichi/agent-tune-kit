"""Workspace project registry and issue associations for human review."""

import configparser
import hashlib
import subprocess
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import safe_path, write_manifest

from .store import ensure_workspace, read_json, session_write_lock, validate_ref


def load_projects(root):
    path = safe_path(Path(root), "projects.json")
    if not path.exists():
        return {"schema_version": 1, "revision": 0, "projects": []}
    value = read_json(path)
    if (
        value.get("schema_version") != 1
        or type(value.get("revision")) is not int
        or not isinstance(value.get("projects"), list)
    ):
        raise UserActionRequired("Invalid project registry.")
    seen = set()
    for item in value["projects"]:
        if (
            not isinstance(item, dict)
            or any(not isinstance(item.get(k), str) or not item[k] for k in ("id", "name", "path"))
            or item["id"] in seen
        ):
            raise UserActionRequired("Invalid project entry.")
        seen.add(item["id"])
    return value


def project_map(root):
    try:
        return {item["id"]: item for item in load_projects(root)["projects"]}
    except (UserActionRequired, KeyError, TypeError):
        return {}


def save_project(project_root, data):
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"].strip():
        raise UserActionRequired("Project needs a stable id.")
    root, _ = ensure_workspace(project_root)
    with session_write_lock(root):
        catalog = load_projects(root)
        if type(data.get("revision", 0)) is not int or data.get("revision", 0) != catalog["revision"]:
            raise UserActionRequired("Stale project registry revision.")
        entries = {item["id"]: item for item in catalog["projects"]}
        entry = {**entries.get(data["id"], {}), **{k: v for k, v in data.items() if k != "revision"}}
        for key in ("name", "path"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise UserActionRequired(f"Project needs {key}.")
        base = Path(project_root).resolve()
        target = (base / entry["path"]).resolve()
        if Path(entry["path"]).is_absolute() or not target.is_relative_to(base):
            raise UserActionRequired("Project path must stay within workspace.")
        entry["path"] = target.relative_to(base).as_posix()
        if entry.get("kind", "directory") not in {"repository", "submodule", "directory"}:
            raise UserActionRequired("Invalid project kind.")
        for key in ("abbreviation", "description"):
            if key in entry and not isinstance(entry[key], str):
                raise UserActionRequired(f"Project {key} must be text.")
        for key in ("aliases", "entrypoints"):
            if key in entry and (not isinstance(entry[key], list) or not all(isinstance(v, str) for v in entry[key])):
                raise UserActionRequired(f"Project {key} must be a list of strings.")
        if entry.get("parent_id") is not None and not isinstance(entry["parent_id"], str):
            raise UserActionRequired("Invalid parent project.")
        if "deprecated" in entry and not isinstance(entry["deprecated"], bool):
            raise UserActionRequired("deprecated must be boolean.")
        entries[entry["id"]] = entry
        for item in entries.values():
            seen = {item["id"]}
            parent = item.get("parent_id")
            while parent:
                if parent in seen or parent not in entries:
                    raise UserActionRequired("Unknown or cyclic parent project.")
                seen.add(parent)
                parent = entries[parent].get("parent_id")
        if len({e["path"] for e in entries.values()}) != len(entries):
            raise UserActionRequired("Project path already registered.")
        catalog.update(revision=catalog["revision"] + 1, projects=list(entries.values()))
        write_manifest(root / "projects.json", catalog)
        return catalog


def discover_projects(project_root):
    base = Path(project_root).resolve()
    found = []
    visited = set()

    def visit(path, parent=None):
        if path in visited:
            return
        visited.add(path)
        relative = path.relative_to(base).as_posix()
        project_id = "project-" + hashlib.sha256(relative.encode()).hexdigest()[:16]
        readable = path.is_dir() and (parent is None or (path / ".git").exists())
        found.append(
            {
                "id": project_id,
                "name": path.name,
                "path": relative,
                "parent_id": parent,
                "kind": "submodule" if parent else "repository",
                "readable": readable,
                "entrypoints": [
                    str(f.relative_to(base))
                    for f in path.iterdir()
                    if f.is_file() and f.name.lower() in {"readme.md", "claude.md", "agents.md", "rules.md"}
                ]
                if readable
                else [],
            }
        )
        modules = path / ".gitmodules"
        children = set()
        if modules.is_file():
            config = configparser.ConfigParser()
            config.read(modules)
            for section in config.sections():
                child = (path / config.get(section, "path")).resolve()
                if not child.is_relative_to(base) or child == path or path.is_relative_to(child):
                    raise UserActionRequired("Submodule path escapes project.")
                children.add(child)

        if readable:
            result = subprocess.run(
                ["git", "-C", str(path), "ls-files", "--stage", "-z"], capture_output=True, check=False
            )
            if result.returncode == 0:
                for row in result.stdout.decode("utf-8", errors="replace").split("\0"):
                    if row.startswith("160000 ") and "\t" in row:
                        children.add((path / row.split("\t", 1)[1]).resolve())
        for child in sorted(children):
            if not child.is_relative_to(base) or child == path or path.is_relative_to(child):
                raise UserActionRequired("Submodule path escapes project.")
            visit(child, project_id)

    visit(base)
    return {"projects": found}


def validate_links(session_dir, links):
    if not isinstance(links, list):
        raise UserActionRequired("project_links must be a list.")
    projects = project_map(Path(session_dir).parent)
    seen = set()
    for link in links:
        if not isinstance(link, dict) or set(link) - {"project_id", "reason", "evidence", "source_paths"}:
            raise UserActionRequired("Invalid project link.")
        key = link.get("project_id")
        if not isinstance(key, str) or key not in projects or key in seen:
            raise UserActionRequired("Unknown or duplicate project link.")
        seen.add(key)
        if (
            not isinstance(link.get("reason"), str)
            or not link["reason"].strip()
            or not isinstance(link.get("evidence"), list)
            or not link["evidence"]
        ):
            raise UserActionRequired("Project attribution needs reason and retained evidence.")
        for ref in link["evidence"]:
            validate_ref(session_dir, ref)
        if not isinstance(link.get("source_paths", []), list):
            raise UserActionRequired("source_paths must be a list.")
        for path in link.get("source_paths", []):
            if not isinstance(path, str):
                raise UserActionRequired("Source path must be workspace relative text.")
            safe_path(Path(session_dir).parent.parent.parent, path)
    return links


def linked_projects(root, issue):
    projects = project_map(root)
    result = []
    links = issue.get("project_links", [])
    if not isinstance(links, list):
        return result
    for link in links:
        if not isinstance(link, dict) or not isinstance(link.get("project_id"), str):
            continue
        entry = projects.get(link["project_id"], {})
        result.append(
            {
                **entry,
                "id": link["project_id"],
                "name": entry.get("abbreviation") or entry.get("name") or link["project_id"],
            }
        )
    return result
