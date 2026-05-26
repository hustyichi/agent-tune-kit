"""Install Agent Tune Kit into a local Codex personal marketplace."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata, resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from . import __version__

PLUGIN_NAME = "agent-tune-kit"
PACKAGE_NAME = "agent-tune-kit"
SOURCE_PATH = f"./plugins/{PLUGIN_NAME}"
DEFAULT_MARKETPLACE = Path("~/.agents/plugins/marketplace.json").expanduser()
DEFAULT_PLUGIN_STORE = Path("~/plugins").expanduser()
DEFAULT_BACKUP_ROOT = Path("~/.agents/plugins/backups") / PLUGIN_NAME
PAYLOAD_PACKAGE_PATH = ("plugin_payload", PLUGIN_NAME)
DEV_PAYLOAD_NAMES = [".codex-plugin", "skills", "templates", "docs", "README.md", "README.en.md", "README.zh-CN.md"]
COPY_IGNORE_NAMES = {".git", ".omx", "__pycache__", ".DS_Store", "build", "dist", ".venv", "*.egg-info"}
INSTALL_MARKER = ".codex-plugin/agent-tune-kit-install.json"


class InstallError(RuntimeError):
    """Raised for unsafe install, rollback, or smoke-check failures."""


@dataclass(frozen=True)
class PayloadSource:
    kind: str
    package_version: str
    manifest_version: str
    resource_origin: str
    install_mode: str
    root: Path | Traversable
    dev_root: Path | None = None


@dataclass(frozen=True)
class TargetState:
    exists: bool
    kind: str
    symlink_target: str | None = None
    resolves_to_current_source: bool = False
    has_agent_tune_kit_manifest: bool = False


PayloadRoot = Path | Traversable


def package_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return __version__


def child(root: PayloadRoot, *parts: str) -> PayloadRoot:
    if isinstance(root, Path):
        return root.joinpath(*parts)
    return root.joinpath(*parts)


def exists(node: PayloadRoot) -> bool:
    return node.exists() if isinstance(node, Path) else node.is_file() or node.is_dir()


def is_dir(node: PayloadRoot) -> bool:
    return node.is_dir()


def is_file(node: PayloadRoot) -> bool:
    return node.is_file()


def read_text(node: PayloadRoot) -> str:
    if isinstance(node, Path):
        return node.read_text(encoding="utf-8")
    return node.read_text(encoding="utf-8")


def load_manifest_from_payload(root: PayloadRoot) -> dict[str, Any]:
    manifest_path = child(root, ".codex-plugin", "plugin.json")
    if not exists(manifest_path):
        raise InstallError(f"missing plugin manifest: {manifest_path}")
    manifest = json.loads(read_text(manifest_path))
    if not isinstance(manifest, dict):
        raise InstallError("plugin manifest must be a JSON object")
    return manifest


def validate_manifest_payload(root: PayloadRoot) -> dict[str, Any]:
    manifest = load_manifest_from_payload(root)
    if manifest.get("name") != PLUGIN_NAME:
        raise InstallError(f"manifest name must be {PLUGIN_NAME!r}")
    if manifest.get("skills") != "./skills/":
        raise InstallError("manifest skills must be ./skills/")
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        raise InstallError("manifest interface must be an object")
    for key in ["displayName", "shortDescription", "longDescription", "developerName", "category", "capabilities", "defaultPrompt"]:
        if key not in interface:
            raise InstallError(f"manifest interface missing {key}")
    if len(interface.get("defaultPrompt", [])) > 3:
        raise InstallError("manifest defaultPrompt must have at most three entries")
    skills_dir = child(root, "skills")
    if not is_dir(skills_dir):
        raise InstallError(f"manifest skills directory does not exist: {skills_dir}")
    return manifest


def validate_manifest(path: Path) -> dict[str, Any]:
    return validate_manifest_payload(path.parents[1]) if path.name == "plugin.json" else validate_manifest_payload(path)


def repo_dev_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    try:
        if (candidate / ".codex-plugin" / "plugin.json").is_file() and (candidate / "skills").is_dir():
            return candidate
    except OSError:
        return None
    return None


def resolve_payload_source(*, install_mode_hint: str | None = None) -> PayloadSource:
    dev_root = repo_dev_root()
    version = package_version()
    if dev_root is not None:
        manifest = validate_manifest_payload(dev_root)
        mode = install_mode_hint or "symlink-dev"
        return PayloadSource(
            kind="dev-root",
            package_version=version,
            manifest_version=str(manifest.get("version", "unknown")),
            resource_origin=str(dev_root),
            install_mode=mode,
            root=dev_root,
            dev_root=dev_root,
        )

    root: Traversable = resources.files("agent_tune_kit")
    for part in PAYLOAD_PACKAGE_PATH:
        root = root.joinpath(part)
    manifest = validate_manifest_payload(root)
    return PayloadSource(
        kind="package-resource",
        package_version=version,
        manifest_version=str(manifest.get("version", "unknown")),
        resource_origin="importlib.resources:agent_tune_kit/" + "/".join(PAYLOAD_PACKAGE_PATH),
        install_mode="copy",
        root=root,
        dev_root=None,
    )


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise InstallError(f"marketplace must be a JSON object: {path}")
    data.setdefault("name", "personal")
    interface = data.setdefault("interface", {})
    if not isinstance(interface, dict):
        raise InstallError("marketplace interface must be an object")
    interface.setdefault("displayName", "Personal")
    plugins = data.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise InstallError("marketplace plugins must be an array")
    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink()


def install_marker_path(target: Path) -> Path:
    return target / INSTALL_MARKER


def install_marker(payload_source: PayloadSource) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "plugin_name": PLUGIN_NAME,
        "package_name": PACKAGE_NAME,
        "package_version": payload_source.package_version,
        "manifest_version": payload_source.manifest_version,
        "payload_source_kind": payload_source.kind,
        "payload_resource_origin": payload_source.resource_origin,
        "install_mode": payload_source.install_mode,
        "marketplace_source_path": SOURCE_PATH,
    }


def write_install_marker(target: Path, payload_source: PayloadSource) -> None:
    write_json_atomic(install_marker_path(target), install_marker(payload_source))


def read_install_marker(target: Path) -> dict[str, Any] | None:
    marker_path = install_marker_path(target)
    if not marker_path.exists():
        return None
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def marker_matches_backup(marker: dict[str, Any], backup_metadata: dict[str, Any]) -> bool:
    expected_pairs = {
        "plugin_name": PLUGIN_NAME,
        "package_name": backup_metadata.get("package_name"),
        "package_version": backup_metadata.get("package_version"),
        "manifest_version": backup_metadata.get("manifest_version"),
        "payload_source_kind": backup_metadata.get("payload_source_kind"),
        "payload_resource_origin": backup_metadata.get("payload_resource_origin"),
        "install_mode": backup_metadata.get("install_mode"),
        "marketplace_source_path": SOURCE_PATH,
    }
    return all(marker.get(key) == value for key, value in expected_pairs.items() if value is not None)


def current_manifest_version(target: Path) -> str | None:
    try:
        manifest = validate_manifest(target / ".codex-plugin" / "plugin.json")
    except (InstallError, json.JSONDecodeError, OSError):
        return None
    return str(manifest.get("version", "unknown"))


def marketplace_entry() -> dict[str, Any]:
    return {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": SOURCE_PATH},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Coding",
    }


def find_marketplace_entry(data: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    for index, existing in enumerate(data.get("plugins", [])):
        if isinstance(existing, dict) and existing.get("name") == PLUGIN_NAME:
            return index, existing
    return None


def marketplace_conflict(data: dict[str, Any]) -> bool:
    found = find_marketplace_entry(data)
    if not found:
        return False
    _, existing = found
    source = existing.get("source")
    existing_path = source.get("path") if isinstance(source, dict) else None
    return existing_path != SOURCE_PATH


def update_marketplace(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    plugins = data["plugins"]
    entry = marketplace_entry()
    found = find_marketplace_entry(data)
    if found:
        index, _ = found
        plugins[index] = entry
        return data, "update marketplace entry"
    plugins.append(entry)
    return data, "add marketplace entry"


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except FileNotFoundError:
        return False


def target_has_valid_manifest(target: Path) -> bool:
    manifest_at_target = target / ".codex-plugin" / "plugin.json"
    if not manifest_at_target.exists():
        return False
    try:
        validate_manifest(manifest_at_target)
        return True
    except (InstallError, json.JSONDecodeError, OSError):
        return False


def target_state(target: Path, payload_source: PayloadSource | None = None) -> TargetState:
    target = target.expanduser()
    resolves = False
    if payload_source and payload_source.dev_root is not None:
        resolves = same_path(target, payload_source.dev_root)
    has_manifest = target_has_valid_manifest(target) if target.exists() or target.is_symlink() else False
    if target.is_symlink():
        link = os.readlink(target)
        return TargetState(True, "symlink", link, resolves, has_manifest)
    if target.is_dir():
        return TargetState(True, "directory", None, resolves, has_manifest)
    if target.is_file():
        return TargetState(True, "file", None, resolves, has_manifest)
    if target.exists():
        return TargetState(True, "other", None, resolves, has_manifest)
    return TargetState(False, "missing")


def plugin_store_conflict(target: Path, payload_source: PayloadSource) -> bool:
    state = target_state(target, payload_source)
    return state.exists and not state.resolves_to_current_source


def prompt_confirm(message: str) -> bool:
    answer = input(f"{message} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def authorize_conflicts(conflicts: list[str], *, yes: bool, force: bool, no_input: bool) -> None:
    if not conflicts:
        return
    summary = "; ".join(conflicts)
    if yes and not force:
        raise InstallError(f"refusing destructive replacement with --yes alone ({summary}); use --yes --force")
    noninteractive = no_input or not sys.stdin.isatty()
    if noninteractive:
        if yes and force:
            return
        raise InstallError(f"conflict requires interactive confirmation or --yes --force: {summary}")
    if yes and force:
        return
    if not prompt_confirm(f"Replace existing {PLUGIN_NAME} installer state ({summary})?"):
        raise InstallError("replacement cancelled")


def copy_state(source: Path, backup_dir: Path) -> str | None:
    if not source.exists() and not source.is_symlink():
        return None
    if source.is_symlink():
        return None
    payload = backup_dir / "plugin_store_payload"
    if source.is_dir():
        shutil.copytree(source, payload, symlinks=True)
    else:
        payload.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, payload)
    return str(payload)


def make_backup(
    *,
    marketplace_path: Path,
    plugin_target: Path,
    backup_root: Path,
    operation: str,
    dry_run: bool,
    payload_source: PayloadSource,
) -> tuple[str | None, str | None]:
    if dry_run:
        return None, None
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = backup_root.expanduser() / backup_id
    suffix = 1
    while backup_dir.exists():
        suffix += 1
        backup_dir = backup_root.expanduser() / f"{backup_id}-{suffix}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    market_backup_path: str | None = None
    if marketplace_path.exists():
        market_backup = backup_dir / "marketplace.json"
        shutil.copy2(marketplace_path, market_backup)
        market_backup_path = str(market_backup)

    state = target_state(plugin_target, payload_source)
    payload = copy_state(plugin_target, backup_dir)
    metadata: dict[str, Any] = {
        "schema_version": 2,
        "id": backup_dir.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "marketplace_path": str(marketplace_path),
        "plugin_store_target": str(plugin_target),
        "prior_existence": state.exists,
        "prior_target_type": state.kind,
        "symlink_target": state.symlink_target,
        "copied_backup_path": payload,
        "operation": operation,
        "plugin_name": PLUGIN_NAME,
        "package_name": PACKAGE_NAME,
        "package_version": payload_source.package_version,
        "manifest_version": payload_source.manifest_version,
        "payload_source_kind": payload_source.kind,
        "payload_resource_origin": payload_source.resource_origin,
        "install_mode": payload_source.install_mode,
        "marketplace": {
            "existed": marketplace_path.exists(),
            "backup_path": market_backup_path,
        },
        "plugin_store": {
            "existed": state.exists,
            "type": state.kind,
            "symlink_target": state.symlink_target,
            "copied_backup_path": payload,
        },
    }
    if payload_source.dev_root is not None:
        metadata["repo_root"] = str(payload_source.dev_root)
    write_json_atomic(backup_dir / "manifest.json", metadata)
    return backup_dir.name, str(backup_dir)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def copy_traversable(src: PayloadRoot, dest: Path) -> None:
    if is_dir(src):
        dest.mkdir(parents=True, exist_ok=True)
        children = src.iterdir() if not isinstance(src, Path) else src.iterdir()
        for item in children:
            if item.name in COPY_IGNORE_NAMES:
                continue
            copy_traversable(item, dest / item.name)
    elif is_file(src):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, Path):
            shutil.copy2(src, dest)
        else:
            with src.open("rb") as source, dest.open("wb") as target:
                shutil.copyfileobj(source, target)


def copy_payload_tree(payload_source: PayloadSource, target: Path) -> None:
    if payload_source.kind == "dev-root" and isinstance(payload_source.root, Path):
        target.mkdir(parents=True, exist_ok=True)
        for name in DEV_PAYLOAD_NAMES:
            src = payload_source.root / name
            if not src.exists():
                continue
            copy_traversable(src, target / name)
        return
    copy_traversable(payload_source.root, target)


def ensure_plugin_store(target: Path, *, use_copy: bool, dry_run: bool, payload_source: PayloadSource) -> str:
    target = target.expanduser()
    state = target_state(target, payload_source)
    if state.resolves_to_current_source:
        if target.is_symlink():
            return f"existing symlink is current: {target} -> {payload_source.resource_origin}"
        return f"plugin store already points at payload source: {target}"

    if state.exists:
        if dry_run:
            return f"would replace existing plugin-store path: {target}"
        remove_path(target)

    if dry_run:
        mode = "copy" if (use_copy or payload_source.kind == "package-resource") else "symlink-dev"
        return f"would create {mode}: {target} from {payload_source.resource_origin}"

    target.parent.mkdir(parents=True, exist_ok=True)
    if payload_source.kind == "package-resource" or use_copy:
        copy_payload_tree(payload_source, target)
        write_install_marker(target, payload_source)
        return f"copied payload to {target}"

    if not payload_source.dev_root:
        raise InstallError("symlink-dev install requires a source checkout payload")
    try:
        target.symlink_to(payload_source.dev_root, target_is_directory=True)
        return f"created symlink: {target} -> {payload_source.dev_root}"
    except OSError as exc:
        raise InstallError(f"symlink failed ({exc}); rerun with --copy for explicit copy fallback") from exc


def collect_status(marketplace_path: Path, plugin_store: Path, payload_source: PayloadSource | None = None) -> tuple[dict[str, bool], list[str]]:
    payload_source = payload_source or resolve_payload_source()
    target = plugin_store.expanduser() / PLUGIN_NAME
    facts: dict[str, bool] = {}
    lines: list[str] = []

    lines.append(f"payload source: {payload_source.kind}")
    lines.append(f"package: {PACKAGE_NAME} {payload_source.package_version}")
    lines.append(f"payload origin: {payload_source.resource_origin}")
    try:
        manifest = validate_manifest_payload(payload_source.root)
        facts["manifest_valid"] = True
        lines.append(f"manifest valid: yes ({manifest['name']} {manifest['version']})")
    except (InstallError, json.JSONDecodeError) as exc:
        facts["manifest_valid"] = False
        lines.append(f"manifest valid: no ({exc})")

    marketplace_path = marketplace_path.expanduser()
    try:
        data = load_json(marketplace_path)
        found = find_marketplace_entry(data)
        registered = bool(found)
        source_ok = False
        if found:
            _, entry = found
            source = entry.get("source")
            source_ok = isinstance(source, dict) and source.get("path") == SOURCE_PATH
        facts["marketplace_registered"] = registered
        facts["source_path_ok"] = source_ok
        lines.append(f"marketplace registered: {'yes' if registered else 'no'}")
        lines.append(f"source.path ok: {'yes' if source_ok else 'no'} ({SOURCE_PATH})")
    except (InstallError, json.JSONDecodeError) as exc:
        facts["marketplace_registered"] = False
        facts["source_path_ok"] = False
        lines.append(f"marketplace readable: no ({exc})")

    state = target_state(target, payload_source)
    target_manifest_ok = state.has_agent_tune_kit_manifest
    facts["plugin_store_target_exists"] = state.exists
    facts["plugin_store_target_resolved"] = state.exists and target_manifest_ok
    lines.append(f"plugin-store target exists: {'yes' if state.exists else 'no'} ({target})")
    lines.append(f"plugin-store target resolved: {'yes' if target_manifest_ok else 'no'}")
    lines.append("local availability: should be visible/available in /plugins after Codex UI refresh when marketplace and target checks are yes")
    lines.append("Codex UI boundary: installer does not modify or observe hidden Codex UI enablement state")
    lines.append("next step: open /plugins, enable Agent Tune Kit if needed, then restart/open a new session if $atk-* autocomplete is missing")
    return facts, lines


def smoke_check(
    marketplace_path: Path,
    plugin_store: Path,
    *,
    dry_run: bool,
    backup_dir: str | None = None,
    payload_source: PayloadSource | None = None,
) -> list[str]:
    payload_source = payload_source or resolve_payload_source()
    target = plugin_store.expanduser() / PLUGIN_NAME
    manifest = validate_manifest_payload(payload_source.root) if dry_run else validate_manifest(target / ".codex-plugin" / "plugin.json")

    if not dry_run and not target.exists():
        raise InstallError(f"marketplace source path does not resolve to an installed plugin: {target}")

    if dry_run:
        preview_data = load_json(marketplace_path.expanduser())
        marketplace_data = preview_data if find_marketplace_entry(preview_data) else update_marketplace(preview_data)[0]
    elif marketplace_path.expanduser().exists():
        marketplace_data = load_json(marketplace_path.expanduser())
    else:
        marketplace_data = {"plugins": [marketplace_entry()]}
    entries = [entry for entry in marketplace_data.get("plugins", []) if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME]
    if not entries:
        raise InstallError("marketplace entry missing")
    entry = entries[-1]
    if entry.get("source", {}).get("path") != SOURCE_PATH:
        raise InstallError(f"marketplace entry source.path must be {SOURCE_PATH}")
    if entry.get("policy", {}).get("installation") != "AVAILABLE":
        raise InstallError("marketplace policy.installation must be AVAILABLE")
    if entry.get("policy", {}).get("authentication") != "ON_INSTALL":
        raise InstallError("marketplace policy.authentication must be ON_INSTALL")
    if entry.get("category") != "Coding":
        raise InstallError("marketplace category must be Coding")

    facts, status_lines = collect_status(marketplace_path, plugin_store, payload_source)
    if not dry_run and not facts.get("plugin_store_target_resolved"):
        raise InstallError("status failed to resolve plugin-store target")
    if not any("/plugins" in line for line in status_lines):
        raise InstallError("status output missing /plugins next-step guidance")
    if backup_dir:
        manifest_file = Path(backup_dir) / "manifest.json"
        if not manifest_file.exists():
            raise InstallError(f"backup manifest missing: {manifest_file}")
        backup_metadata = json.loads(manifest_file.read_text(encoding="utf-8"))
        required_new = [
            "id",
            "timestamp",
            "marketplace_path",
            "plugin_store_target",
            "prior_target_type",
            "operation",
            "plugin_name",
            "schema_version",
            "package_name",
            "package_version",
            "manifest_version",
            "payload_source_kind",
            "payload_resource_origin",
            "install_mode",
        ]
        required_legacy = ["id", "timestamp", "marketplace_path", "plugin_store_target", "prior_target_type", "operation", "repo_root", "plugin_name"]
        if not all(key in backup_metadata for key in required_new) and not all(key in backup_metadata for key in required_legacy):
            missing = [key for key in required_new if key not in backup_metadata]
            raise InstallError(f"backup manifest missing package-era keys: {', '.join(missing)}")

    return [
        f"manifest ok: {manifest['name']} {manifest['version']}",
        f"skills path ok: {manifest.get('skills')}",
        f"payload source ok: {payload_source.kind}",
        f"marketplace source.path ok: {SOURCE_PATH}",
        "marketplace policy/category ok: AVAILABLE/ON_INSTALL/Coding",
        f"smoke-resolved plugin path: {target}",
        "status guidance ok: /plugins and Codex UI boundary present",
    ] + ([f"backup metadata ok: {backup_dir}"] if backup_dir else [])


def run_preview(args: argparse.Namespace) -> int:
    marketplace_path = args.marketplace_path.expanduser()
    plugin_store = args.plugin_store.expanduser()
    payload_source = resolve_payload_source(install_mode_hint="copy" if args.copy else None)
    target = plugin_store / PLUGIN_NAME
    validate_manifest_payload(payload_source.root)
    marketplace = load_json(marketplace_path)
    marketplace, market_action = update_marketplace(marketplace)
    store_action = ensure_plugin_store(target, use_copy=args.copy, dry_run=True, payload_source=payload_source)

    print("mode: preview")
    print(f"payload source: {payload_source.kind}")
    print(f"package: {PACKAGE_NAME} {payload_source.package_version}")
    print(f"manifest version: {payload_source.manifest_version}")
    print(f"payload origin: {payload_source.resource_origin}")
    print(f"marketplace: {marketplace_path}")
    print(f"plugin store: {plugin_store}")
    print(f"marketplace source.path: {SOURCE_PATH}")
    print(f"marketplace action: would {market_action}")
    print(f"plugin-store action: {store_action}")
    print("marketplace write: skipped")
    print("backup: skipped for preview")
    if args.smoke:
        print("smoke:")
        for line in smoke_check(marketplace_path, plugin_store, dry_run=True, payload_source=payload_source):
            print(f"- {line}")
        print("temp smoke cleanup: no temporary files created")
    return 0


def run_install(args: argparse.Namespace) -> int:
    marketplace_path = args.marketplace_path.expanduser()
    plugin_store = args.plugin_store.expanduser()
    payload_source = resolve_payload_source(install_mode_hint="copy" if args.copy else None)
    target = plugin_store / PLUGIN_NAME

    validate_manifest_payload(payload_source.root)
    marketplace = load_json(marketplace_path)
    conflicts: list[str] = []
    if marketplace_conflict(marketplace):
        found = find_marketplace_entry(marketplace)
        existing_path = None
        if found:
            source = found[1].get("source")
            existing_path = source.get("path") if isinstance(source, dict) else None
        conflicts.append(f"marketplace entry points to {existing_path!r}")
    if plugin_store_conflict(target, payload_source):
        conflicts.append(f"plugin-store target exists at {target}")
    authorize_conflicts(conflicts, yes=args.yes, force=args.force, no_input=args.no_input)

    backup_id = backup_dir = None
    if conflicts:
        backup_id, backup_dir = make_backup(
            marketplace_path=marketplace_path,
            plugin_target=target,
            backup_root=args.backup_root,
            operation="install replace conflicts",
            dry_run=False,
            payload_source=payload_source,
        )

    marketplace, market_action = update_marketplace(marketplace)
    store_action = ensure_plugin_store(target, use_copy=args.copy, dry_run=False, payload_source=payload_source)
    write_json_atomic(marketplace_path, marketplace)

    print("mode: install")
    print(f"payload source: {payload_source.kind}")
    print(f"package: {PACKAGE_NAME} {payload_source.package_version}")
    print(f"manifest version: {payload_source.manifest_version}")
    print(f"payload origin: {payload_source.resource_origin}")
    print(f"marketplace: {marketplace_path}")
    print(f"plugin store: {plugin_store}")
    print(f"marketplace source.path: {SOURCE_PATH}")
    print(f"marketplace action: {market_action}")
    print(f"plugin-store action: {store_action}")
    print("marketplace write: complete")
    if backup_id:
        print(f"backup: {backup_id} at {backup_dir}")
        print(f"rollback: atk rollback --backup {backup_id} --backup-root {args.backup_root}")
    else:
        print("backup: not needed")

    if args.smoke:
        print("smoke:")
        for line in smoke_check(marketplace_path, plugin_store, dry_run=False, backup_dir=backup_dir, payload_source=payload_source):
            print(f"- {line}")

    print("status:")
    for line in collect_status(marketplace_path, plugin_store, payload_source)[1]:
        print(f"- {line}")
    return 0


def run_status(args: argparse.Namespace) -> int:
    payload_source = resolve_payload_source(install_mode_hint="copy" if args.copy else None)
    print("mode: status")
    print(f"payload source: {payload_source.kind}")
    print(f"package: {PACKAGE_NAME} {payload_source.package_version}")
    print(f"payload origin: {payload_source.resource_origin}")
    print(f"marketplace: {args.marketplace_path.expanduser()}")
    print(f"plugin store: {args.plugin_store.expanduser()}")
    for line in collect_status(args.marketplace_path, args.plugin_store, payload_source)[1]:
        print(f"- {line}")
    return 0


def backup_dir_from_args(args: argparse.Namespace) -> Path:
    backup = Path(args.backup).expanduser()
    if backup.is_absolute() and backup.exists():
        return backup
    return args.backup_root.expanduser() / args.backup


def expected_marketplace_after_install(metadata: dict[str, Any]) -> dict[str, Any]:
    market = metadata.get("marketplace", {})
    backup_path = market.get("backup_path")
    if market.get("existed") and backup_path:
        base = load_json(Path(backup_path))
    else:
        base = {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
    return update_marketplace(base)[0]


def target_is_current_installer_state(target: Path, metadata: dict[str, Any]) -> bool:
    state = target_state(target)
    if not state.exists:
        return False

    repo_root = metadata.get("repo_root")
    if repo_root and target.is_symlink() and same_path(target, Path(repo_root)):
        return True

    marker = read_install_marker(target)
    if not marker or not marker_matches_backup(marker, metadata):
        return False
    return current_manifest_version(target) == marker.get("manifest_version")


def current_state_is_expected_for_rollback(
    marketplace_path: Path, target: Path, metadata: dict[str, Any]
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    try:
        if marketplace_path.exists():
            current = load_json(marketplace_path)
            expected = expected_marketplace_after_install(metadata)
            if current != expected:
                found = find_marketplace_entry(current)
                if found:
                    source = found[1].get("source")
                    path = source.get("path") if isinstance(source, dict) else None
                    if path != SOURCE_PATH:
                        reasons.append(f"marketplace entry now points to {path!r}")
                    else:
                        reasons.append("marketplace JSON changed since installer backup")
                else:
                    reasons.append("marketplace entry is missing before rollback")
        elif metadata.get("marketplace", {}).get("existed"):
            reasons.append("marketplace JSON is missing before rollback")
    except (InstallError, json.JSONDecodeError) as exc:
        reasons.append(f"marketplace unreadable: {exc}")
    state = target_state(target)
    if state.exists and not target_is_current_installer_state(target, metadata):
        reasons.append(f"plugin-store target is not current installer state: {target}")
    return not reasons, reasons


def restore_plugin_store(metadata: dict[str, Any], target: Path) -> str:
    if target.exists() or target.is_symlink():
        remove_path(target)
    kind = metadata.get("prior_target_type")
    if kind == "missing" or not metadata.get("prior_existence"):
        return f"restored missing plugin-store target: {target}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "symlink":
        link = metadata.get("symlink_target")
        if not link:
            raise InstallError("backup metadata missing symlink_target")
        target.symlink_to(link, target_is_directory=True)
        return f"restored symlink: {target} -> {link}"
    payload = metadata.get("copied_backup_path")
    if not payload:
        raise InstallError("backup metadata missing copied_backup_path")
    payload_path = Path(payload)
    if kind == "directory":
        shutil.copytree(payload_path, target, symlinks=True)
        return f"restored directory: {target}"
    if kind == "file":
        shutil.copy2(payload_path, target)
        return f"restored file: {target}"
    raise InstallError(f"unsupported prior target type in backup: {kind}")


def run_rollback(args: argparse.Namespace) -> int:
    backup_dir = backup_dir_from_args(args)
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise InstallError(f"backup manifest not found: {manifest_path}")
    backup_metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    if backup_metadata.get("plugin_name") != PLUGIN_NAME:
        raise InstallError(f"backup is not for {PLUGIN_NAME}")

    marketplace_path = Path(backup_metadata["marketplace_path"]).expanduser()
    target = Path(backup_metadata["plugin_store_target"]).expanduser()
    ok, reasons = current_state_is_expected_for_rollback(marketplace_path, target, backup_metadata)
    if not ok and not args.force:
        raise InstallError("rollback would overwrite newer unrelated state; use --force. " + "; ".join(reasons))

    market = backup_metadata.get("marketplace", {})
    market_backup = market.get("backup_path")
    if market.get("existed") and market_backup:
        marketplace_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(market_backup), marketplace_path)
        market_result = f"restored marketplace JSON: {marketplace_path}"
    else:
        if marketplace_path.exists():
            marketplace_path.unlink()
        market_result = f"restored missing marketplace JSON: {marketplace_path}"

    store_result = restore_plugin_store(backup_metadata, target)
    print("mode: rollback")
    print(f"backup: {backup_metadata.get('id')} at {backup_dir}")
    print(market_result)
    print(store_result)
    print("result: rollback complete")
    print("next step: run status, then refresh /plugins if needed")
    return 0


def version_text() -> str:
    return f"{PACKAGE_NAME} {package_version()}"


def run_version(args: argparse.Namespace) -> int:
    print(version_text())
    return 0


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS, help="allow replacement when paired with confirmation; noninteractive destructive replacement also requires --yes")
    parser.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="answer yes for noninteractive operations; destructive replacement also requires --force")
    parser.add_argument("--no-input", action="store_true", default=argparse.SUPPRESS, help="never prompt; fail instead of waiting when confirmation is required")
    parser.add_argument("--marketplace-path", type=Path, default=argparse.SUPPRESS, help="marketplace.json path")
    parser.add_argument("--plugin-store", type=Path, default=argparse.SUPPRESS, help="directory containing personal plugins")
    parser.add_argument("--backup-root", type=Path, default=argparse.SUPPRESS, help="directory containing installer backups")
    parser.add_argument("--copy", action="store_true", default=argparse.SUPPRESS, help="copy the resolved payload instead of creating a developer checkout symlink")
    parser.add_argument("--smoke", action="store_true", default=argparse.SUPPRESS, help="run manifest, marketplace, status, and path smoke checks")
    parser.add_argument("--no-smoke", action="store_true", default=argparse.SUPPRESS, help="skip install's default smoke checks")


def parse_args(argv: list[str]) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    add_common_flags(common)
    parser = argparse.ArgumentParser(description="Register Agent Tune Kit as a local Codex plugin.")
    add_common_flags(parser)
    parser.add_argument("--version", action="version", version=version_text(), help="print package version and exit")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="print package version and exit")
    subparsers.add_parser("preview", parents=[common], help="preview planned marketplace/plugin-store changes without writing")
    subparsers.add_parser("install", parents=[common], help="install locally, then run smoke/status by default")
    subparsers.add_parser("status", parents=[common], help="print read-only local install status and Codex UI boundary guidance")
    rollback = subparsers.add_parser("rollback", parents=[common], help="restore marketplace/plugin-store state from an installer backup")
    rollback.add_argument("--backup", required=True, help="backup id under --backup-root, or an absolute backup directory")

    args = parser.parse_args(argv)
    for name, value in {
        "force": False,
        "yes": False,
        "no_input": False,
        "marketplace_path": DEFAULT_MARKETPLACE,
        "plugin_store": DEFAULT_PLUGIN_STORE,
        "backup_root": DEFAULT_BACKUP_ROOT.expanduser(),
        "copy": False,
        "smoke": False,
        "no_smoke": False,
    }.items():
        if not hasattr(args, name):
            setattr(args, name, value)

    if args.command == "preview":
        args.smoke = bool(args.smoke)
    elif args.command == "install":
        args.smoke = not args.no_smoke if not args.smoke else True
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "version":
            return run_version(args)
        if args.command == "preview":
            return run_preview(args)
        if args.command == "install":
            return run_install(args)
        if args.command == "status":
            return run_status(args)
        if args.command == "rollback":
            return run_rollback(args)
        raise InstallError(f"unknown command: {args.command}")
    except (InstallError, json.JSONDecodeError, OSError) as exc:
        print(f"atk: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
