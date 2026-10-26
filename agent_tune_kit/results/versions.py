import csv
import json
import os
import re
import socket
import sys
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.workspace import RESULTS_DIR


def list_version_dirs(results_dir: Path = RESULTS_DIR) -> list[tuple[int, Path]]:
    # Return [(number, path)] for directories named vN where N is a positive integer.
    # Missing results_dir means no versions exist yet; runner first run will create v1.
    if not results_dir.exists():
        return []
    return sorted(
        (int(path.name[1:]), path)
        for path in results_dir.iterdir()
        if path.is_dir() and path.name.startswith("v") and path.name[1:].isdigit() and int(path.name[1:]) > 0
    )


def allocate_next_results_version(results_dir: Path = RESULTS_DIR) -> Path:
    # Used only by eval_runner.py. Current max version determines create vs reuse.
    versions = list_version_dirs(results_dir)
    if not versions:
        target = results_dir / "v1"
    else:
        max_n, current = versions[-1]
        target = results_dir / f"v{max_n + 1}" if any(current.iterdir()) else current
    if target.is_symlink():
        raise UserActionRequired("Unsafe result version path.")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


def resolve_current_version(results_dir=RESULTS_DIR):
    versions = list_version_dirs(Path(results_dir))
    if not versions:
        raise UserActionRequired("No vN results directory exists; run eval_runner.py first or confirm repair.")
    return versions[-1][1]


def resolve_previous_version(current_dir, results_dir=RESULTS_DIR):
    versions = [
        path for _, path in list_version_dirs(Path(results_dir)) if int(path.name[1:]) < int(current_dir.name[1:])
    ]
    for path in reversed(versions):
        if run_kind(path) != "evidence-only":
            return path
    return None


def run_kind(directory):
    from .evidence import read_manifest, verify_manifest

    directory = Path(directory)
    if directory.is_symlink():
        raise UserActionRequired("Unsafe result version path.")
    if any((directory / name).exists() or (directory / name).is_symlink() for name in ["run.json", "executions"]):
        manifest = read_manifest(directory, "run.json")
        kind = manifest.get("run_kind")
        if kind not in {"evaluation", "evidence-only"}:
            raise UserActionRequired("Unknown Run purpose; version selection stopped.")
        if kind == "evidence-only":
            verify_manifest(directory, manifest)
        return kind
    # A surviving evidence column must not resurrect legacy failure-table fallback.
    path = directory / "judgments.csv"
    if path.is_file():
        with path.open(encoding="utf-8-sig") as handle:
            if "evidence_refs" in next(csv.reader(handle), []):
                raise UserActionRequired("New evidence identity is missing.")
    return "legacy"


def resolve_evaluation_version(results_dir=RESULTS_DIR, version=None):
    if version is not None:
        if not re.fullmatch(r"v[1-9][0-9]*", version):
            raise UserActionRequired("Invalid result version.")
        target = Path(results_dir) / version
        if not target.is_dir() or run_kind(target) == "evidence-only":
            raise UserActionRequired("Selected version has no evaluation mapping.")
        return target
    for _, target in reversed(list_version_dirs(Path(results_dir))):
        if run_kind(target) != "evidence-only":
            return target
    raise UserActionRequired("No evaluation version exists; evidence-only Runs cannot be scored.")


@contextmanager
def write_occupation(results_dir=RESULTS_DIR):
    from .evidence import json_bytes, now, sync_directory

    root = Path(results_dir)
    created_root = not root.exists()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / ".writer.json"
    owner = uuid.uuid4().hex
    record = {"owner": owner, "host": socket.gethostname(), "pid": os.getpid(), "acquired_at": now()}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise UserActionRequired("Project write occupation exists; verify its owner before manual recovery.") from None
    with os.fdopen(fd, "wb") as handle:
        handle.write(json_bytes(record))
        handle.flush()
        os.fsync(handle.fileno())
    sync_directory(root)
    try:
        yield record
    finally:
        unwinding = sys.exc_info()[0] is not None
        try:
            if path.is_symlink() or json.loads(path.read_text()).get("owner") != owner:
                raise ValueError
            path.unlink()
            sync_directory(root)
            if created_root:
                with suppress(OSError):
                    root.rmdir()
        except (OSError, ValueError):
            message = "Could not release project occupation; verify owner before manual recovery."
            if unwinding:
                print(message, file=sys.stderr)
            else:
                raise UserActionRequired(message) from None


def require_current_file(current_dir, filename):
    path = Path(current_dir) / filename
    if not path.is_file():
        raise UserActionRequired(
            f"Current version {current_dir.name} is missing {filename}; fix or rerun the prior step."
        )
    return path
