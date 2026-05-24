#!/usr/bin/env python3
"""Install Agent Tune Kit into a local Codex personal marketplace.

Recommended path:
    python3 scripts/install_plugin.py install

Bare invocation and --dry-run remain non-destructive previews. The installer only
manages local marketplace/plugin-store files; it does not observe, bypass, or
modify hidden Codex UI plugin enablement state.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_NAME = "agent-tune-kit"
SOURCE_PATH = f"./plugins/{PLUGIN_NAME}"
DEFAULT_MARKETPLACE = Path("~/.agents/plugins/marketplace.json").expanduser()
DEFAULT_PLUGIN_STORE = Path("~/plugins").expanduser()
DEFAULT_BACKUP_ROOT = Path("~/.agents/plugins/backups") / PLUGIN_NAME
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"


class InstallError(RuntimeError):
    """Raised for unsafe install, rollback, or smoke-check failures."""


@dataclass(frozen=True)
class TargetState:
    exists: bool
    kind: str
    symlink_target: str | None = None
    resolves_to_repo: bool = False


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


def target_state(target: Path) -> TargetState:
    target = target.expanduser()
    if target.is_symlink():
        link = os.readlink(target)
        return TargetState(True, "symlink", link, same_path(target, ROOT))
    if target.is_dir():
        return TargetState(True, "directory", None, same_path(target, ROOT))
    if target.is_file():
        return TargetState(True, "file", None, same_path(target, ROOT))
    if target.exists():
        return TargetState(True, "other", None, same_path(target, ROOT))
    return TargetState(False, "missing")


def plugin_store_conflict(target: Path) -> bool:
    state = target_state(target)
    return state.exists and not state.resolves_to_repo


def validate_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise InstallError(f"missing plugin manifest: {path}")
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
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
    skills_dir = path.parents[1] / "skills"
    if not skills_dir.is_dir():
        raise InstallError(f"manifest skills directory does not exist: {skills_dir}")
    return manifest


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
        # The link target is recorded in metadata; no payload copy is needed.
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

    state = target_state(plugin_target)
    payload = copy_state(plugin_target, backup_dir)
    metadata = {
        "id": backup_dir.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "marketplace_path": str(marketplace_path),
        "plugin_store_target": str(plugin_target),
        "prior_existence": state.exists,
        "prior_target_type": state.kind,
        "symlink_target": state.symlink_target,
        "copied_backup_path": payload,
        "operation": operation,
        "repo_root": str(ROOT),
        "plugin_name": PLUGIN_NAME,
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
    write_json_atomic(backup_dir / "manifest.json", metadata)
    return backup_dir.name, str(backup_dir)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def ensure_plugin_store(target: Path, *, use_copy: bool, dry_run: bool) -> str:
    target = target.expanduser()
    state = target_state(target)
    if state.resolves_to_repo:
        if target.is_symlink():
            return f"existing symlink is current: {target} -> {ROOT}"
        return f"plugin store already points at repo: {target}"

    if state.exists:
        if dry_run:
            return f"would replace existing plugin-store path: {target}"
        remove_path(target)

    if dry_run:
        mode = "copy" if use_copy else "symlink"
        return f"would create {mode}: {target} -> {ROOT}"

    target.parent.mkdir(parents=True, exist_ok=True)
    if use_copy:
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(".git", ".omx", "__pycache__", ".DS_Store"))
        return f"copied repo to {target}"

    try:
        target.symlink_to(ROOT, target_is_directory=True)
        return f"created symlink: {target} -> {ROOT}"
    except OSError as exc:
        raise InstallError(f"symlink failed ({exc}); rerun with --copy for explicit copy fallback") from exc


def collect_status(marketplace_path: Path, plugin_store: Path) -> tuple[dict[str, bool], list[str]]:
    target = plugin_store.expanduser() / PLUGIN_NAME
    facts: dict[str, bool] = {}
    lines: list[str] = []

    try:
        manifest = validate_manifest(MANIFEST)
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

    state = target_state(target)
    manifest_at_target = target / ".codex-plugin" / "plugin.json"
    target_manifest_ok = False
    if manifest_at_target.exists():
        try:
            validate_manifest(manifest_at_target)
            target_manifest_ok = True
        except (InstallError, json.JSONDecodeError):
            target_manifest_ok = False
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
) -> list[str]:
    target = plugin_store.expanduser() / PLUGIN_NAME
    manifest_path = target / ".codex-plugin" / "plugin.json"
    if dry_run and not manifest_path.exists():
        manifest_path = MANIFEST
    manifest = validate_manifest(manifest_path)

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

    facts, status_lines = collect_status(marketplace_path, plugin_store)
    if not dry_run and not facts.get("plugin_store_target_resolved"):
        raise InstallError("status failed to resolve plugin-store target")
    if not any("/plugins" in line for line in status_lines):
        raise InstallError("status output missing /plugins next-step guidance")
    if backup_dir:
        manifest_file = Path(backup_dir) / "manifest.json"
        if not manifest_file.exists():
            raise InstallError(f"backup manifest missing: {manifest_file}")
        metadata = json.loads(manifest_file.read_text(encoding="utf-8"))
        for key in ["id", "timestamp", "marketplace_path", "plugin_store_target", "prior_target_type", "operation", "repo_root", "plugin_name"]:
            if key not in metadata:
                raise InstallError(f"backup manifest missing {key}")

    return [
        f"manifest ok: {manifest['name']} {manifest['version']}",
        f"skills path ok: {manifest.get('skills')}",
        f"marketplace source.path ok: {SOURCE_PATH}",
        f"marketplace policy/category ok: AVAILABLE/ON_INSTALL/Coding",
        f"smoke-resolved plugin path: {target}",
        "status guidance ok: /plugins and Codex UI boundary present",
    ] + ([f"backup metadata ok: {backup_dir}"] if backup_dir else [])


def run_preview(args: argparse.Namespace) -> int:
    marketplace_path = args.marketplace_path.expanduser()
    plugin_store = args.plugin_store.expanduser()
    target = plugin_store / PLUGIN_NAME
    validate_manifest(MANIFEST)
    marketplace = load_json(marketplace_path)
    marketplace, market_action = update_marketplace(marketplace)
    store_action = ensure_plugin_store(target, use_copy=args.copy, dry_run=True)

    print("mode: preview")
    print(f"repo: {ROOT}")
    print(f"marketplace: {marketplace_path}")
    print(f"plugin store: {plugin_store}")
    print(f"marketplace source.path: {SOURCE_PATH}")
    print(f"marketplace action: would {market_action}")
    print(f"plugin-store action: {store_action}")
    print("marketplace write: skipped")
    print("backup: skipped for preview")
    if args.smoke:
        print("smoke:")
        for line in smoke_check(marketplace_path, plugin_store, dry_run=True):
            print(f"- {line}")
        print("temp smoke cleanup: no temporary files created")
    return 0


def run_install(args: argparse.Namespace) -> int:
    marketplace_path = args.marketplace_path.expanduser()
    plugin_store = args.plugin_store.expanduser()
    target = plugin_store / PLUGIN_NAME

    validate_manifest(MANIFEST)
    marketplace = load_json(marketplace_path)
    conflicts: list[str] = []
    if marketplace_conflict(marketplace):
        found = find_marketplace_entry(marketplace)
        existing_path = None
        if found:
            source = found[1].get("source")
            existing_path = source.get("path") if isinstance(source, dict) else None
        conflicts.append(f"marketplace entry points to {existing_path!r}")
    if plugin_store_conflict(target):
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
        )

    marketplace, market_action = update_marketplace(marketplace)
    store_action = ensure_plugin_store(target, use_copy=args.copy, dry_run=False)
    write_json_atomic(marketplace_path, marketplace)

    print("mode: install" if not args.legacy_apply else "mode: apply (legacy)")
    print(f"repo: {ROOT}")
    print(f"marketplace: {marketplace_path}")
    print(f"plugin store: {plugin_store}")
    print(f"marketplace source.path: {SOURCE_PATH}")
    print(f"marketplace action: {market_action}")
    print(f"plugin-store action: {store_action}")
    print("marketplace write: complete")
    if backup_id:
        print(f"backup: {backup_id} at {backup_dir}")
        print(f"rollback: python3 scripts/install_plugin.py rollback --backup {backup_id} --backup-root {args.backup_root}")
    else:
        print("backup: not needed")

    if args.smoke:
        print("smoke:")
        for line in smoke_check(marketplace_path, plugin_store, dry_run=False, backup_dir=backup_dir):
            print(f"- {line}")

    print("status:")
    for line in collect_status(marketplace_path, plugin_store)[1]:
        print(f"- {line}")
    return 0


def run_status(args: argparse.Namespace) -> int:
    print("mode: status")
    print(f"repo: {ROOT}")
    print(f"marketplace: {args.marketplace_path.expanduser()}")
    print(f"plugin store: {args.plugin_store.expanduser()}")
    for line in collect_status(args.marketplace_path, args.plugin_store)[1]:
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
    if state.exists and not state.resolves_to_repo:
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
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    if metadata.get("plugin_name") != PLUGIN_NAME:
        raise InstallError(f"backup is not for {PLUGIN_NAME}")

    marketplace_path = Path(metadata["marketplace_path"]).expanduser()
    target = Path(metadata["plugin_store_target"]).expanduser()
    ok, reasons = current_state_is_expected_for_rollback(marketplace_path, target, metadata)
    if not ok and not args.force:
        raise InstallError("rollback would overwrite newer unrelated state; use --force. " + "; ".join(reasons))

    market = metadata.get("marketplace", {})
    market_backup = market.get("backup_path")
    if market.get("existed") and market_backup:
        marketplace_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(market_backup), marketplace_path)
        market_result = f"restored marketplace JSON: {marketplace_path}"
    else:
        if marketplace_path.exists():
            marketplace_path.unlink()
        market_result = f"restored missing marketplace JSON: {marketplace_path}"

    store_result = restore_plugin_store(metadata, target)
    print("mode: rollback")
    print(f"backup: {metadata.get('id')} at {backup_dir}")
    print(market_result)
    print(store_result)
    print("result: rollback complete")
    print("next step: run status, then refresh /plugins if needed")
    return 0


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS, help="allow replacement when paired with confirmation; noninteractive destructive replacement also requires --yes")
    parser.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="answer yes for noninteractive operations; destructive replacement also requires --force")
    parser.add_argument("--no-input", action="store_true", default=argparse.SUPPRESS, help="never prompt; fail instead of waiting when confirmation is required")
    parser.add_argument("--marketplace-path", type=Path, default=argparse.SUPPRESS, help="marketplace.json path")
    parser.add_argument("--plugin-store", type=Path, default=argparse.SUPPRESS, help="directory containing personal plugins")
    parser.add_argument("--backup-root", type=Path, default=argparse.SUPPRESS, help="directory containing installer backups")
    parser.add_argument("--copy", action="store_true", default=argparse.SUPPRESS, help="copy this repo instead of creating a symlink")
    parser.add_argument("--smoke", action="store_true", default=argparse.SUPPRESS, help="run manifest, marketplace, status, and path smoke checks")
    parser.add_argument("--no-smoke", action="store_true", default=argparse.SUPPRESS, help="skip install's default smoke checks")


def parse_args(argv: list[str]) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    add_common_flags(common)
    parser = argparse.ArgumentParser(description="Register Agent Tune Kit as a local Codex plugin.")
    add_common_flags(parser)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="preview actions without writing (default legacy behavior)")
    mode.add_argument("--apply", action="store_true", default=argparse.SUPPRESS, help="legacy install path: write marketplace and plugin-store changes")
    subparsers = parser.add_subparsers(dest="command")
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
        "dry_run": False,
        "apply": False,
    }.items():
        if not hasattr(args, name):
            setattr(args, name, value)

    args.legacy_apply = False
    if args.command is None:
        if args.apply:
            args.command = "install"
            args.legacy_apply = True
            # Preserve legacy behavior: --apply only smokes when --smoke is supplied.
        else:
            args.command = "preview"
    if args.command == "preview":
        args.smoke = bool(args.smoke)
    elif args.command == "install" and not args.legacy_apply:
        args.smoke = not args.no_smoke if not args.smoke else True
    elif args.command == "install" and args.legacy_apply:
        args.smoke = bool(args.smoke)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
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
        print(f"install_plugin.py: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
