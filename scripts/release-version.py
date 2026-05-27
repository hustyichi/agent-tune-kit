#!/usr/bin/env python3
"""Bump, tag, push, and optionally publish an Agent Tune Kit release.

Codex natural-language release requests such as "upgrade to 0.3.9 and publish"
should map directly to ``./scripts/release-version.sh 0.3.9 --publish``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"(?<!\d)\d+\.\d+\.\d+(?!\d)")

VERSION_FILES = [
    ".codex-plugin/plugin.json",
    "pyproject.toml",
    "scripts/validate_skill_pack.py",
    "src/agent_tune_kit/__init__.py",
    "tests/test_install_plugin.py",
    "tests/test_release_scripts.py",
    "uv.lock",
]

VERSION_PATTERNS = {
    ".codex-plugin/plugin.json": re.compile(r'("version":\s*")(?<!\d)\d+\.\d+\.\d+(?!\d)(")'),
    "pyproject.toml": re.compile(r'(version\s*=\s*")(?<!\d)\d+\.\d+\.\d+(?!\d)(")'),
    "scripts/validate_skill_pack.py": re.compile(r'(\'"version":\s*")(?<!\d)\d+\.\d+\.\d+(?!\d)("\')'),
    "src/agent_tune_kit/__init__.py": re.compile(r'(__version__\s*=\s*")(?<!\d)\d+\.\d+\.\d+(?!\d)(")'),
    "tests/test_install_plugin.py": re.compile(r'(agent-tune-kit )(?<!\d)\d+\.\d+\.\d+(?!\d)(")'),
    "tests/test_release_scripts.py": re.compile(
        r'(self\.assertEqual\(identity\.version,\s*")(?<!\d)\d+\.\d+\.\d+(?!\d)("\))'
    ),
    "uv.lock": re.compile(r'(version\s*=\s*")(?<!\d)\d+\.\d+\.\d+(?!\d)(")'),
}


class ReleaseVersionError(RuntimeError):
    """Raised when the one-command release flow cannot continue."""


def validate_version(version: str) -> None:
    if not VERSION_RE.fullmatch(version):
        raise ReleaseVersionError(f"version must use MAJOR.MINOR.PATCH, got: {version}")


def run(command: list[str], *, cwd: Path = REPO_ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(command)}", flush=True)
    return subprocess.run(command, cwd=cwd, text=True, check=check)


def capture(command: list[str], *, cwd: Path = REPO_ROOT) -> str:
    print(f"+ {' '.join(command)}", flush=True)
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def update_version_files(root: Path, version: str) -> list[str]:
    validate_version(version)
    changed: list[str] = []
    for relative in VERSION_FILES:
        path = root / relative
        content = path.read_text(encoding="utf-8")
        pattern = VERSION_PATTERNS[relative]
        new_content, count = pattern.subn(rf"\g<1>{version}\g<2>", content)
        if count == 0:
            raise ReleaseVersionError(f"no version literal found in {relative}")
        if new_content != content:
            path.write_text(new_content, encoding="utf-8")
            changed.append(relative)
    return changed


def release_commands(
    version: str,
    *,
    publish: bool,
    skip_release_check: bool,
    remote: str = "origin",
    branch: str = "main",
    repository: str = "pypi",
) -> list[list[str]]:
    validate_version(version)
    commands = [
        ["git", "status", "--porcelain"],
        ["uv", "lock"],
        ["uv", "run", "pytest"],
        ["uv", "run", "atk", "--version"],
        ["python", "scripts/validate_skill_pack.py"],
        ["git", "diff", "--check"],
        ["git", "add", *VERSION_FILES],
        ["git", "commit", "-m", f"Prepare Agent Tune Kit {version} release"],
        ["git", "tag", version],
        ["git", "push", remote, branch],
        ["git", "push", remote, version],
    ]
    if publish:
        publish_command = [
            "uv",
            "run",
            "python",
            "scripts/publish-release.py",
            "--repository",
            repository,
            "--publish",
        ]
        if skip_release_check:
            publish_command.append("--skip-release-check")
        commands.append(publish_command)
    return commands


def commit_message(version: str) -> list[str]:
    return [
        "git",
        "commit",
        "-m",
        f"Prepare Agent Tune Kit {version} release",
        "-m",
        "Constraint: package metadata, plugin manifest, module fallback, lockfile, and version assertions must stay aligned.",
        "-m",
        "Rejected: update only pyproject.toml | package, plugin, tests, and release validation would disagree.",
        "-m",
        "Confidence: high",
        "-m",
        "Scope-risk: narrow",
        "-m",
        "Directive: use scripts/release-version.sh for future version bumps so tag, push, and publish steps remain consistent.",
        "-m",
        "Tested: uv run pytest; uv run atk --version; git diff --check",
        "-m",
        "Not-tested: python scripts/validate_skill_pack.py may report known README/plugin documentation phrase gaps unless strict mode is used.",
        "-m",
        "Co-authored-by: OmX <omx@oh-my-codex.dev>",
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-command release bump for Codex natural-language requests: update versions, test, commit, tag, "
            "push, and optionally publish."
        )
    )
    parser.add_argument("version", help="new release version, for example 0.3.9")
    parser.add_argument("--publish", action="store_true", help="publish to PyPI/TestPyPI after pushing the tag")
    parser.add_argument("--repository", choices=["pypi", "testpypi"], default="pypi", help="publish target")
    parser.add_argument("--remote", default="origin", help="git remote to push (default: origin)")
    parser.add_argument("--branch", default="main", help="branch to push (default: main)")
    parser.add_argument(
        "--strict-skill-pack",
        action="store_true",
        help="fail if scripts/validate_skill_pack.py reports the known documentation phrase gaps",
    )
    parser.add_argument(
        "--strict-release-check",
        action="store_true",
        help="do not pass --skip-release-check to publish-release.py when publishing",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the planned command sequence and stop")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_version(args.version)
    skip_release_check = not args.strict_release_check
    planned = release_commands(
        args.version,
        publish=args.publish,
        skip_release_check=skip_release_check,
        remote=args.remote,
        branch=args.branch,
        repository=args.repository,
    )
    if args.dry_run:
        for command in planned:
            print(" ".join(command))
        return 0

    if capture(["git", "status", "--porcelain"]):
        raise ReleaseVersionError("working tree is not clean; commit or stash changes before starting a release")

    changed = update_version_files(REPO_ROOT, args.version)
    print("updated version files:")
    for path in changed:
        print(f"- {path}")

    run(["uv", "lock"])
    run(["uv", "run", "pytest"])
    version_output = capture(["uv", "run", "atk", "--version"])
    expected_version_output = f"agent-tune-kit {args.version}"
    if version_output != expected_version_output:
        raise ReleaseVersionError(f"unexpected CLI version output: {version_output!r}")

    skill_pack = run(["python", "scripts/validate_skill_pack.py"], check=False)
    if skill_pack.returncode != 0 and args.strict_skill_pack:
        raise ReleaseVersionError("scripts/validate_skill_pack.py failed in strict mode")
    if skill_pack.returncode != 0:
        print("warning: scripts/validate_skill_pack.py failed; continuing because strict skill-pack mode is off")

    run(["git", "diff", "--check"])
    run(["git", "add", *VERSION_FILES])
    run(commit_message(args.version))
    run(["git", "tag", args.version])
    run(["git", "push", args.remote, args.branch])
    run(["git", "push", args.remote, args.version])

    if args.publish:
        publish_command = [
            "uv",
            "run",
            "python",
            "scripts/publish-release.py",
            "--repository",
            args.repository,
            "--publish",
        ]
        if skip_release_check:
            publish_command.append("--skip-release-check")
        run(publish_command)

    print(f"release-version: OK ({args.version})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseVersionError, subprocess.CalledProcessError) as error:
        print(f"release-version: FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from None
