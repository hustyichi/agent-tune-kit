#!/usr/bin/env python3
"""Install Agent Tune Kit into a local Codex personal marketplace.

Dry-run is the default. Use --apply to write the marketplace entry and plugin-store
link/copy. The marketplace source.path is always ./plugins/agent-tune-kit for the
personal entry; --plugin-store controls where that path is smoke-resolved locally.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_NAME = "agent-tune-kit"
SOURCE_PATH = f"./plugins/{PLUGIN_NAME}"
DEFAULT_MARKETPLACE = Path("~/.agents/plugins/marketplace.json").expanduser()
DEFAULT_PLUGIN_STORE = Path("~/plugins").expanduser()
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"


class InstallError(RuntimeError):
    """Raised for unsafe install or smoke-check failures."""


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


def update_marketplace(data: dict[str, Any], *, force: bool) -> tuple[dict[str, Any], str]:
    plugins = data["plugins"]
    entry = marketplace_entry()
    for index, existing in enumerate(plugins):
        if not isinstance(existing, dict) or existing.get("name") != PLUGIN_NAME:
            continue
        existing_path = existing.get("source", {}).get("path") if isinstance(existing.get("source"), dict) else None
        if existing_path != SOURCE_PATH and not force:
            raise InstallError(
                f"refusing to replace existing {PLUGIN_NAME!r} marketplace entry pointing to {existing_path!r}; use --force"
            )
        plugins[index] = entry
        return data, "update marketplace entry"
    plugins.append(entry)
    return data, "add marketplace entry"


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except FileNotFoundError:
        return False


def ensure_plugin_store(target: Path, *, use_copy: bool, force: bool, dry_run: bool) -> str:
    target = target.expanduser()
    if same_path(target, ROOT):
        return f"plugin store already points at repo: {target}"

    if target.exists() or target.is_symlink():
        if target.is_symlink() and same_path(target, ROOT):
            return f"existing symlink is current: {target} -> {ROOT}"
        if not force:
            raise InstallError(f"refusing to replace existing plugin-store path {target}; use --force")
        if target.is_dir() and not target.is_symlink() and not use_copy:
            raise InstallError(f"refusing to delete directory {target} for symlink install; use --copy --force")
        if dry_run:
            return f"would replace existing plugin-store path: {target}"
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)

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


def smoke_check(marketplace_path: Path, plugin_store: Path, *, dry_run: bool) -> list[str]:
    target = plugin_store.expanduser() / PLUGIN_NAME
    manifest_path = target / ".codex-plugin" / "plugin.json"
    if dry_run and not manifest_path.exists():
        manifest_path = MANIFEST
    manifest = validate_manifest(manifest_path)

    expected_path = plugin_store.expanduser() / PLUGIN_NAME
    resolved_source = expected_path
    if not dry_run and not resolved_source.exists():
        raise InstallError(f"marketplace source path does not resolve to an installed plugin: {resolved_source}")

    marketplace_data = load_json(marketplace_path.expanduser()) if marketplace_path.expanduser().exists() else {
        "plugins": [marketplace_entry()]
    }
    entries = [entry for entry in marketplace_data.get("plugins", []) if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME]
    if entries and entries[-1].get("source", {}).get("path") != SOURCE_PATH:
        raise InstallError(f"marketplace entry source.path must be {SOURCE_PATH}")

    return [
        f"manifest ok: {manifest['name']} {manifest['version']}",
        f"skills path ok: {manifest.get('skills')}",
        f"marketplace source.path ok: {SOURCE_PATH}",
        f"smoke-resolved plugin path: {expected_path}",
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register Agent Tune Kit as a local Codex plugin.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="preview actions without writing (default)")
    mode.add_argument("--apply", action="store_false", dest="dry_run", help="write marketplace and plugin-store changes")
    parser.add_argument("--force", action="store_true", help="replace an existing same-name marketplace entry or safe plugin-store target")
    parser.add_argument("--marketplace-path", type=Path, default=DEFAULT_MARKETPLACE, help="marketplace.json path")
    parser.add_argument("--plugin-store", type=Path, default=DEFAULT_PLUGIN_STORE, help="directory containing personal plugins")
    parser.add_argument("--copy", action="store_true", help="copy this repo instead of creating a symlink")
    parser.add_argument("--smoke", action="store_true", help="run manifest and marketplace source-path smoke checks")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    marketplace_path = args.marketplace_path.expanduser()
    plugin_store = args.plugin_store.expanduser()
    target = plugin_store / PLUGIN_NAME

    try:
        validate_manifest(MANIFEST)
        marketplace = load_json(marketplace_path)
        marketplace, market_action = update_marketplace(marketplace, force=args.force)
        store_action = ensure_plugin_store(target, use_copy=args.copy, force=args.force, dry_run=args.dry_run)

        print(f"mode: {'dry-run' if args.dry_run else 'apply'}")
        print(f"repo: {ROOT}")
        print(f"marketplace: {marketplace_path}")
        print(f"plugin store: {plugin_store}")
        print(f"marketplace source.path: {SOURCE_PATH}")
        print(f"marketplace action: {'would ' if args.dry_run else ''}{market_action}")
        print(f"plugin-store action: {store_action}")

        if not args.dry_run:
            write_json_atomic(marketplace_path, marketplace)
            print("marketplace write: complete")
        else:
            print("marketplace write: skipped")

        if args.smoke:
            print("smoke:")
            for line in smoke_check(marketplace_path, plugin_store, dry_run=args.dry_run):
                print(f"- {line}")
            print("temp smoke cleanup: no temporary files created")
        if not args.dry_run:
            print("next step: open /plugins in Codex, select Agent Tune Kit, and install/enable it")
            print("verify: plugin status should change from Available to Installed; then use $atk-start")
        return 0
    except (InstallError, json.JSONDecodeError) as exc:
        print(f"install_plugin.py: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
