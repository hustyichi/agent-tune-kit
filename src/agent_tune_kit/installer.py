"""Install Agent Tune Kit into a local Codex personal marketplace."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
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
PAYLOAD_PACKAGE_PATH = ("plugin_payload", PLUGIN_NAME)
DEV_PAYLOAD_NAMES = [".codex-plugin", "skills", "templates", "docs", "README.md", "README.en.md"]
COPY_IGNORE_NAMES = {".git", ".omx", "__pycache__", ".DS_Store", "build", "dist", ".venv", "*.egg-info"}
INSTALL_MARKER = ".codex-plugin/agent-tune-kit-install.json"


class InstallError(RuntimeError):
    """Raised when installation is unsafe or incomplete."""


@dataclass(frozen=True)
class PayloadSource:
    kind: str
    package_version: str
    install_mode: str
    root: Path | Traversable
    dev_root: Path | None = None


@dataclass(frozen=True)
class TargetState:
    exists: bool
    kind: str
    resolves_to_current_source: bool = False


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
    for key in [
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "capabilities",
        "defaultPrompt",
    ]:
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
        if manifest["version"] != version:
            raise InstallError(f"CLI {version} and bundled plugin {manifest['version']} differ")
        mode = install_mode_hint or "symlink-dev"
        return PayloadSource(
            kind="dev-root",
            package_version=version,
            install_mode=mode,
            root=dev_root,
            dev_root=dev_root,
        )

    root: Traversable = resources.files("agent_tune_kit")
    for part in PAYLOAD_PACKAGE_PATH:
        root = root.joinpath(part)
    manifest = validate_manifest_payload(root)
    if manifest["version"] != version:
        raise InstallError(f"CLI {version} and bundled plugin {manifest['version']} differ")
    return PayloadSource(
        kind="package-resource",
        package_version=version,
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
        "plugin_name": PLUGIN_NAME,
        "package_name": PACKAGE_NAME,
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


def update_marketplace(data: dict[str, Any]) -> dict[str, Any]:
    plugins = data["plugins"]
    entry = marketplace_entry()
    found = find_marketplace_entry(data)
    if found:
        index, _ = found
        plugins[index] = entry
        return data
    plugins.append(entry)
    return data


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except FileNotFoundError:
        return False


def target_state(target: Path, payload_source: PayloadSource | None = None) -> TargetState:
    resolves = bool(payload_source and payload_source.dev_root and same_path(target, payload_source.dev_root))
    if target.is_symlink():
        return TargetState(True, "symlink", resolves)
    if target.is_dir():
        return TargetState(True, "directory", resolves)
    return TargetState(target.exists(), "other", resolves)


def plugin_store_conflict(target: Path, payload_source: PayloadSource) -> bool:
    state = target_state(target, payload_source)
    marker = read_install_marker(target) if state.kind == "directory" else None
    owned_copy = marker is not None and all(
        marker.get(key) == value
        for key, value in {
            "plugin_name": PLUGIN_NAME,
            "package_name": PACKAGE_NAME,
            "marketplace_source_path": SOURCE_PATH,
            "install_mode": "copy",
        }.items()
    )
    return state.exists and not state.resolves_to_current_source and not owned_copy


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
    if not prompt_confirm(f"Replace existing files/registration ({summary})? This cannot be undone."):
        raise InstallError("replacement cancelled")


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


def ensure_plugin_store(target: Path, *, use_copy: bool, payload_source: PayloadSource) -> None:
    if target_state(target, payload_source).resolves_to_current_source:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # Prepare the new files before replacing the installation, so copy failures preserve the old files.
    with tempfile.TemporaryDirectory(prefix=".atk-install-", dir=target.parent) as tmp:
        staged = Path(tmp) / PLUGIN_NAME
        if payload_source.kind == "package-resource" or use_copy:
            copy_payload_tree(payload_source, staged)
            write_install_marker(staged, payload_source)
        else:
            if not payload_source.dev_root:
                raise InstallError("developer install requires a source checkout")
            try:
                staged.symlink_to(payload_source.dev_root, target_is_directory=True)
            except OSError as exc:
                raise InstallError(f"could not link plugin files ({exc}); rerun with --copy") from exc
        validate_manifest(staged / ".codex-plugin" / "plugin.json")
        remove_path(target)
        staged.rename(target)


def run_preview(args: argparse.Namespace) -> int:
    payload_source = resolve_payload_source(install_mode_hint="copy" if args.copy else None)
    target = args.plugin_store.expanduser() / PLUGIN_NAME
    marketplace = load_json(args.marketplace_path.expanduser())
    print(f"Will install Agent Tune Kit {payload_source.package_version} for Codex.")
    print(f"Location: {target}")
    if marketplace_conflict(marketplace) or plugin_store_conflict(target, payload_source):
        print("Existing files or registration conflict with this installation; replacement requires confirmation.")
    print("No files changed. Run atk install to install.")
    return 0


def run_install(args: argparse.Namespace) -> int:
    marketplace_path = args.marketplace_path.expanduser()
    plugin_store = args.plugin_store.expanduser()
    payload_source = resolve_payload_source(install_mode_hint="copy" if args.copy else None)
    target = plugin_store / PLUGIN_NAME
    marketplace = load_json(marketplace_path)
    conflicts: list[str] = []
    if marketplace_conflict(marketplace):
        conflicts.append(f"Agent Tune Kit is registered at another location in {marketplace_path}")
    if plugin_store_conflict(target, payload_source):
        conflicts.append(f"{target} contains files not managed by this installer")
    authorize_conflicts(conflicts, yes=args.yes, force=args.force, no_input=args.no_input)

    ensure_plugin_store(target, use_copy=args.copy, payload_source=payload_source)
    write_json_atomic(marketplace_path, update_marketplace(marketplace))
    manifest = check_installation(marketplace_path, target)
    if manifest.get("version") != payload_source.package_version:
        raise InstallError("installed plugin version differs from CLI; run atk install again")
    print(f"Agent Tune Kit {payload_source.package_version} installed locally for Codex.")
    print("Next: open /plugins in Codex and enable Agent Tune Kit if needed.")
    print("If $atk-* skills are missing, start a new Codex session.")
    return 0


def check_installation(marketplace_path: Path, target: Path) -> dict[str, Any]:
    found = find_marketplace_entry(load_json(marketplace_path))
    if not found or any(found[1].get(key) != value for key, value in marketplace_entry().items()):
        raise InstallError("plugin registration is missing or invalid; run atk install")
    return validate_manifest(target / ".codex-plugin" / "plugin.json")


def run_status(args: argparse.Namespace) -> int:
    target = args.plugin_store.expanduser() / PLUGIN_NAME
    manifest = check_installation(args.marketplace_path.expanduser(), target)
    print(f"Agent Tune Kit {manifest.get('version', 'unknown')} is installed locally.")
    print(f"Location: {target}")
    if manifest.get("version") != package_version():
        print(f"CLI version is {package_version()}; run atk install to update Skills when convenient.")
    print("Open /plugins in Codex to check whether Agent Tune Kit is enabled.")
    return 0


def version_text() -> str:
    return f"{PACKAGE_NAME} {package_version()}"


def run_version(args: argparse.Namespace) -> int:
    print(version_text())
    return 0


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--force",
        action="store_true",
        default=argparse.SUPPRESS,
        help="allow replacing conflicting files with --yes",
    )
    parser.add_argument(
        "--yes", action="store_true", default=argparse.SUPPRESS, help="confirm replacement with --force"
    )
    parser.add_argument("--no-input", action="store_true", default=argparse.SUPPRESS, help="fail instead of prompting")
    parser.add_argument("--marketplace-path", type=Path, default=argparse.SUPPRESS, help="local plugin registry file")
    parser.add_argument("--plugin-store", type=Path, default=argparse.SUPPRESS, help="plugin installation directory")
    parser.add_argument(
        "--copy",
        action="store_true",
        default=argparse.SUPPRESS,
        help="copy plugin files instead of linking a developer checkout",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    add_common_flags(common)
    parser = argparse.ArgumentParser(description="Register Agent Tune Kit as a local Codex plugin.")
    add_common_flags(parser)
    parser.add_argument("--version", action="version", version=version_text(), help="print package version and exit")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="print package version and exit")
    subparsers.add_parser("preview", parents=[common], help="preview installation without changing files")
    subparsers.add_parser("install", parents=[common], help="install or update Skills for Codex")
    subparsers.add_parser("status", parents=[common], help="check the local installation")
    args = parser.parse_args(argv)
    for name, value in {
        "force": False,
        "yes": False,
        "no_input": False,
        "marketplace_path": DEFAULT_MARKETPLACE,
        "plugin_store": DEFAULT_PLUGIN_STORE,
        "copy": False,
    }.items():
        if not hasattr(args, name):
            setattr(args, name, value)
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
        raise InstallError(f"unknown command: {args.command}")
    except (InstallError, json.JSONDecodeError, OSError) as exc:
        print(f"atk: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
