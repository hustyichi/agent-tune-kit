#!/usr/bin/env python3
"""Build and optionally publish Agent Tune Kit release artifacts to PyPI.

Default mode is a safe dry-run: it verifies the local release gate and leaves fresh
``dist/`` artifacts. Add ``--publish`` only when the matching PyPI/TestPyPI token is
available in the environment.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build"


class PublishError(RuntimeError):
    """Raised when a release should not be published."""


@dataclass(frozen=True)
class ProjectIdentity:
    name: str
    version: str


@dataclass(frozen=True)
class PublishTarget:
    name: str
    json_base: str
    simple_url: str
    publish_url: str | None


def target_for(name: str) -> PublishTarget:
    if name == "pypi":
        return PublishTarget(
            name="pypi",
            json_base="https://pypi.org/pypi",
            simple_url="https://pypi.org/simple/",
            publish_url=None,
        )
    if name == "testpypi":
        return PublishTarget(
            name="testpypi",
            json_base="https://test.pypi.org/pypi",
            simple_url="https://test.pypi.org/simple/",
            publish_url="https://test.pypi.org/legacy/",
        )
    raise PublishError(f"unknown repository: {name}")


def release_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("UV_NO_CONFIG", "1")
    return env


def run(
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
    timeout: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    env = release_env()
    if extra_env:
        env.update(extra_env)
    subprocess.run(command, cwd=cwd, env=env, text=True, check=True, timeout=timeout)


def capture(command: list[str], *, cwd: Path = REPO_ROOT) -> str:
    print(f"+ {' '.join(command)}", flush=True)
    return subprocess.check_output(command, cwd=cwd, env=release_env(), text=True).strip()


def read_project_identity() -> ProjectIdentity:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise PublishError("pyproject.toml is missing project.name")
    if not isinstance(version, str) or not version:
        raise PublishError("pyproject.toml is missing project.version")
    return ProjectIdentity(name=name, version=version)


def assert_clean_git(*, allow_dirty: bool) -> None:
    if allow_dirty:
        return
    status = capture(["git", "status", "--porcelain"])
    if status:
        raise PublishError(
            "working tree is not clean; commit or stash changes before publishing "
            "or rerun with --allow-dirty for an intentional local release"
        )


def version_json_url(target: PublishTarget, identity: ProjectIdentity) -> str:
    return f"{target.json_base}/{identity.name}/{identity.version}/json"


def assert_not_already_published(target: PublishTarget, identity: ProjectIdentity) -> None:
    url = version_json_url(target, identity)
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            if response.status == 200:
                raise PublishError(
                    f"{identity.name}=={identity.version} already exists on {target.name}; bump the version first"
                )
            raise PublishError(f"unexpected {target.name} response for {url}: HTTP {response.status}")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print(f"{target.name} availability: OK ({identity.name}=={identity.version} is not published)")
            return
        raise PublishError(f"unexpected {target.name} response for {url}: HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise PublishError(
            f"could not verify {target.name} availability for {identity.name}=={identity.version}: {error}"
        ) from error


def clean_dist() -> None:
    for path in [DIST_DIR, BUILD_DIR]:
        if path.exists():
            shutil.rmtree(path)
    for path in REPO_ROOT.glob("*.egg-info"):
        if path.is_dir():
            shutil.rmtree(path)


def build_artifacts() -> list[Path]:
    clean_dist()
    run(["uv", "build", "--no-sources", "--out-dir", str(DIST_DIR)], timeout=180)
    files = sorted(path for path in DIST_DIR.iterdir() if path.is_file())
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    artifacts = [*wheels, *sdists]
    if len(wheels) != 1 or len(sdists) != 1:
        raise PublishError(f"expected one wheel and one sdist in dist/, found {[path.name for path in files]}")
    return artifacts


def read_pypirc_credentials(target_name: str) -> dict[str, str]:
    pypirc = Path.home() / ".pypirc"
    if not pypirc.is_file():
        return {}

    parser = configparser.ConfigParser()
    try:
        parser.read(pypirc, encoding="utf-8")
    except configparser.Error:
        return {}

    if not parser.has_section(target_name):
        return {}

    username = parser.get(target_name, "username", fallback="").strip()
    password = parser.get(target_name, "password", fallback="").strip()
    if not username or not password:
        return {}
    return {"UV_PUBLISH_USERNAME": username, "UV_PUBLISH_PASSWORD": password}


def publish_credentials_env(*, target_name: str, trusted_publishing: str | None) -> dict[str, str]:
    env = os.environ
    if trusted_publishing:
        return {}
    if env.get("UV_PUBLISH_TOKEN"):
        return {}
    if env.get("UV_PUBLISH_USERNAME") and env.get("UV_PUBLISH_PASSWORD"):
        return {}
    pypirc_credentials = read_pypirc_credentials(target_name)
    if pypirc_credentials:
        return pypirc_credentials
    raise PublishError(
        "missing publish credentials: set UV_PUBLISH_TOKEN='pypi-...' "
        "or configure ~/.pypirc for the target repository, or pass --trusted-publishing "
        "when running in a configured OIDC publisher"
    )


def publish_command(target: PublishTarget, artifacts: list[Path], *, trusted_publishing: str | None) -> list[str]:
    command = ["uv", "publish", "--check-url", target.simple_url]
    if target.publish_url:
        command.extend(["--publish-url", target.publish_url])
    if trusted_publishing:
        command.extend(["--trusted-publishing", trusted_publishing])
    command.extend(str(path) for path in artifacts)
    return command


def wait_for_published_version(target: PublishTarget, identity: ProjectIdentity, *, timeout_seconds: int = 120) -> None:
    url = version_json_url(target, identity)
    deadline = time.monotonic() + timeout_seconds
    last_error = "not checked"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                if response.status == 200:
                    payload = json.loads(response.read().decode("utf-8"))
                    if payload.get("info", {}).get("version") == identity.version:
                        print(f"{target.name} verification: OK ({identity.name}=={identity.version})")
                        return
                    last_error = "version metadata mismatch"
                else:
                    last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
        except Exception as error:  # noqa: BLE001 - transient index propagation/network state is reported below.
            last_error = str(error)
        time.sleep(5)
    raise PublishError(
        f"upload finished, but {target.name} did not show {identity.name}=={identity.version}: {last_error}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run release checks, build clean artifacts, and optionally publish Agent Tune Kit to PyPI/TestPyPI."
    )
    parser.add_argument(
        "--repository",
        choices=["pypi", "testpypi"],
        default="pypi",
        help="package index to publish to (default: pypi)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="actually upload dist artifacts; without this flag the script stops after a verified dry run",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow publishing from a dirty working tree (not recommended for normal releases)",
    )
    parser.add_argument(
        "--skip-release-check",
        action="store_true",
        help="skip scripts/check-release.py (only for rerunning immediately after a just-passed gate)",
    )
    parser.add_argument(
        "--skip-availability-check",
        action="store_true",
        help="skip pre-upload package-version availability check",
    )
    parser.add_argument(
        "--skip-upload-verify",
        action="store_true",
        help="skip polling PyPI/TestPyPI JSON after upload",
    )
    parser.add_argument(
        "--trusted-publishing",
        choices=["automatic", "always", "never"],
        help="forward uv's trusted-publishing mode for configured CI/OIDC publishers",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    identity = read_project_identity()
    target = target_for(args.repository)
    print(f"Release target: {identity.name}=={identity.version} -> {target.name}")

    assert_clean_git(allow_dirty=args.allow_dirty)
    if not args.skip_availability_check:
        assert_not_already_published(target, identity)
    if not args.skip_release_check:
        run([sys.executable, "scripts/check-release.py"], timeout=600)

    artifacts = build_artifacts()
    if not args.publish:
        print("dry-run: OK")
        print("Artifacts ready:")
        for artifact in artifacts:
            print(f"- {artifact.relative_to(REPO_ROOT)}")
        print(
            f"To publish: UV_PUBLISH_TOKEN='pypi-...' uv run python scripts/publish-release.py --repository {target.name} --publish"
        )
        return 0

    credential_env = publish_credentials_env(target_name=target.name, trusted_publishing=args.trusted_publishing)
    if not args.skip_availability_check:
        assert_not_already_published(target, identity)
    run(publish_command(target, artifacts, trusted_publishing=args.trusted_publishing), timeout=180, extra_env=credential_env)
    if not args.skip_upload_verify:
        wait_for_published_version(target, identity)
    print("publish-release: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PublishError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"publish-release: FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from None
