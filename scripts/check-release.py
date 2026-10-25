#!/usr/bin/env python3
"""Run the local release gate for Agent Tune Kit.

The gate intentionally mirrors the user-facing install path: build clean wheel/sdist
artifacts, inspect that the bundled Codex plugin payload survived packaging, install
those artifacts outside the repository, and smoke the packaged ``atk`` command.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_IMPORT = "agent_tune_kit"
PLUGIN_NAME = "agent-tune-kit"

WHEEL_REQUIRED_PATHS = {
    "agent_tune_kit/plugin_payload/agent-tune-kit/.codex-plugin/plugin.json",
    "agent_tune_kit/cli/main.py",
    "agent_tune_kit/execution/runner.py",
    "agent_tune_kit/results/judgments.py",
    "agent_tune_kit/visualize/assets/dataset/vendor/echarts.min.js",
    "agent_tune_kit/visualize/assets/failures/app.js",
    "agent_tune_kit/plugin_payload/agent-tune-kit/skills/atk-dataset/SKILL.md",
    "agent_tune_kit/plugin_payload/agent-tune-kit/templates/.atk/runner/eval_runner.py.md",
    "agent_tune_kit/plugin_payload/agent-tune-kit/templates/.atk/runner/failure_rule.py.md",
    "agent_tune_kit/plugin_payload/agent-tune-kit/docs/skill-template-pack-usage.md",
}

SDIST_REQUIRED_PATHS = {
    ".codex-plugin/plugin.json",
    "skills/atk-dataset/SKILL.md",
    "templates/.atk/runner/eval_runner.py.md",
    "templates/.atk/runner/failure_rule.py.md",
    "docs/skill-template-pack-usage.md",
    "scripts/check-release.py",
    "scripts/publish-release.py",
}

PYTHON_FILES = [
    "src/agent_tune_kit/__init__.py",
    "src/agent_tune_kit/cli/main.py",
    "src/agent_tune_kit/installer.py",
    "scripts/install_plugin.py",
    "scripts/validate_skill_pack.py",
    "scripts/check-release.py",
    "scripts/publish-release.py",
    "tests/test_install_plugin.py",
    "tests/test_release_scripts.py",
]


class ReleaseCheckError(RuntimeError):
    """Raised when the local release gate finds an unsafe artifact."""


@dataclass(frozen=True)
class ProjectIdentity:
    name: str
    version: str
    plugin_version: str
    module_version: str


def release_env() -> dict[str, str]:
    env = os.environ.copy()
    # This repository is routinely validated with uv's project-local/default index
    # behavior. Ignoring user-level uv.toml prevents a broken personal mirror from
    # changing release results or uploads.
    env.setdefault("UV_NO_CONFIG", "1")
    return env


def run(command: list[str], *, cwd: Path = REPO_ROOT, timeout: int | None = None) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, env=release_env(), text=True, check=True, timeout=timeout)


def capture(command: list[str], *, cwd: Path = REPO_ROOT, extra_env: dict[str, str] | None = None) -> str:
    print(f"+ {' '.join(command)}", flush=True)
    return subprocess.check_output(command, cwd=cwd, env=release_env() | (extra_env or {}), text=True).strip()


def require_uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise ReleaseCheckError("uv is required for release checks; install uv first")
    return uv


def run_static_python_checks() -> None:
    run(["uv", "run", "ruff", "format", "."])
    run(["uv", "run", "ruff", "check", "--fix", "."])
    run(["uv", "run", "ruff", "format", "--check", "."])
    run(["uv", "run", "ruff", "check", "."])
    run(["uv", "run", "python", "-m", "py_compile", *PYTHON_FILES], timeout=120)


def read_project_identity() -> ProjectIdentity:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise ReleaseCheckError("pyproject.toml is missing project.name")
    if not isinstance(version, str) or not version:
        raise ReleaseCheckError("pyproject.toml is missing project.version")

    manifest = json.loads((REPO_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    plugin_version = manifest.get("version")
    if not isinstance(plugin_version, str) or not plugin_version:
        raise ReleaseCheckError(".codex-plugin/plugin.json is missing version")

    init_text = (REPO_ROOT / "src" / PACKAGE_IMPORT / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.MULTILINE)
    if not match:
        raise ReleaseCheckError("src/agent_tune_kit/__init__.py is missing __version__")
    return ProjectIdentity(name=name, version=version, plugin_version=plugin_version, module_version=match.group(1))


def assert_versions_aligned(identity: ProjectIdentity) -> None:
    versions = {identity.version, identity.plugin_version, identity.module_version}
    if len(versions) != 1:
        raise ReleaseCheckError(
            "version mismatch: "
            f"pyproject={identity.version}, plugin={identity.plugin_version}, module={identity.module_version}"
        )


def clean_generated_artifacts() -> None:
    for path in [REPO_ROOT / "build", REPO_ROOT / "dist"]:
        if path.exists():
            shutil.rmtree(path)
    for path in REPO_ROOT.glob("*.egg-info"):
        if path.is_dir():
            shutil.rmtree(path)


def assert_archive_contains(archive_name: str, names: Iterable[str], required: Iterable[str]) -> None:
    name_set = set(names)
    missing = sorted(path for path in required if path not in name_set)
    if missing:
        raise ReleaseCheckError(f"{archive_name} is missing required packaged paths: {missing}")


def inspect_artifacts(artifacts: list[Path], identity: ProjectIdentity) -> tuple[Path, Path]:
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseCheckError(f"expected one wheel and one sdist, found {[p.name for p in artifacts]}")

    wheel, sdist = wheels[0], sdists[0]
    expected_stem = f"{identity.name.replace('-', '_')}-{identity.version}"
    if not wheel.name.startswith(expected_stem):
        raise ReleaseCheckError(f"unexpected wheel filename {wheel.name!r}; expected prefix {expected_stem!r}")
    expected_sdist_prefix = f"{identity.name.replace('-', '_')}-{identity.version}"
    if not sdist.name.startswith(expected_sdist_prefix):
        raise ReleaseCheckError(f"unexpected sdist filename {sdist.name!r}; expected prefix {expected_sdist_prefix!r}")

    with zipfile.ZipFile(wheel) as archive:
        assert_archive_contains(wheel.name, archive.namelist(), WHEEL_REQUIRED_PATHS)

    with tarfile.open(sdist) as archive:
        names = set(archive.getnames())
        prefix = sdist.name.removesuffix(".tar.gz")
        required = {f"{prefix}/{path}" for path in SDIST_REQUIRED_PATHS}
        assert_archive_contains(sdist.name, names, required)

    return wheel, sdist


def build_distributions(out_dir: Path, identity: ProjectIdentity) -> tuple[Path, Path]:
    uv = require_uv()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    run([uv, "build", "--no-sources", "--out-dir", str(out_dir)], timeout=180)
    artifacts = sorted(path for path in out_dir.iterdir() if path.is_file())
    if not artifacts:
        raise ReleaseCheckError("uv build produced no artifacts")
    return inspect_artifacts(artifacts, identity)


def bin_path(venv_dir: Path, executable: str) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv_dir / scripts / f"{executable}{suffix}"


def smoke_installed_artifact(artifact: Path, work_dir: Path) -> None:
    uv = require_uv()
    work_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = work_dir / "venv"
    run([uv, "venv", str(venv_dir)], cwd=work_dir, timeout=120)
    python = bin_path(venv_dir, "python")
    run([uv, "pip", "install", "--python", str(python), str(artifact)], cwd=work_dir, timeout=180)

    atk = bin_path(venv_dir, "atk")
    run_dir = work_dir / "run"
    run_dir.mkdir()
    common = [
        "--marketplace-path",
        str(run_dir / "marketplace.json"),
        "--plugin-store",
        str(run_dir / "plugins"),
    ]
    cache_env = {"CODEX_PERSONAL_PLUGIN_CACHE_ROOT": str(run_dir / "cache" / "personal")}
    run([str(atk), "--help"], cwd=run_dir, timeout=30)
    preview = capture([str(atk), "preview", "--smoke", *common], cwd=run_dir, extra_env=cache_env)
    if "未修改文件。运行 atk install 进行安装。" not in preview:
        raise ReleaseCheckError(f"{artifact.name} preview failed")
    install_output = capture([str(atk), "install", *common], cwd=run_dir, extra_env=cache_env)
    if "已为 Codex 本地安装 Agent Tune Kit" not in install_output:
        raise ReleaseCheckError(f"{artifact.name} install failed")
    target = run_dir / "plugins" / PLUGIN_NAME
    for required in [
        target / ".codex-plugin" / "plugin.json",
        target / "skills" / "atk-dataset" / "SKILL.md",
        target / "templates" / ".atk" / "runner" / "eval_runner.py.md",
        target / "templates" / ".atk" / "runner" / "failure_rule.py.md",
    ]:
        if not required.exists():
            raise ReleaseCheckError(f"installed artifact missing {required}")
    if target.is_symlink():
        raise ReleaseCheckError(f"{artifact.name} install did not copy the packaged payload")
    status = capture([str(atk), "status", *common], cwd=run_dir, extra_env=cache_env)
    if "已本地安装。" not in status:
        raise ReleaseCheckError(f"{artifact.name} status smoke failed")
    smoke_shared_capabilities(atk, run_dir, target)


def smoke_shared_capabilities(atk: Path, project: Path, plugin: Path) -> None:
    """Exercise installed code and resources outside the source checkout."""

    def command(*args: str) -> dict:
        return json.loads(capture([str(atk), *args, "--json"], cwd=project))

    source = project / "source.csv"
    source.write_text("input,expected\nhello,hello\n", encoding="utf-8")
    command("dataset", "prepare", "--source", str(source))
    adapter = project / ".atk/runner/eval_runner.py"
    adapter.parent.mkdir()
    adapter.write_text(
        "ATK_MANAGED_RUNNER = True\n"
        "def build_agent_input(row): return row['input']\n"
        "def call_agent(value): return {'answer': value}\n",
        encoding="utf-8",
    )
    runtime = project / "project-python"
    run([require_uv(), "venv", str(runtime)], cwd=project, timeout=60)
    python = bin_path(runtime, "python")
    command("eval", "--check", "--python", str(python))
    if (project / ".atk/results").exists():
        raise ReleaseCheckError("eval --check created result versions")
    command("eval", "--python", str(python))
    current = project / ".atk/results/v1"
    results = current / "eval_results.csv"
    digest = hashlib.sha256(results.read_bytes()).hexdigest()
    judgments = project / "judgments.csv"
    with judgments.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["atk_id", "verdict", "reason", "results_sha256"])
        writer.writerow(["1", "failed", "smoke criterion", digest])
    command("results", "apply-judgments", "--judgments-path", str(judgments), "--source-results", str(results))
    if command("results", "inspect")["failed"] != 1:
        raise ReleaseCheckError("installed judgment projection failed")
    command("visualize", "dataset", "--no-open")
    command("visualize", "failures", "--no-open")
    command("eval", "--python", str(python), "--only-failures")
    if not (project / ".atk/results/v2/eval_results.csv").is_file():
        raise ReleaseCheckError("installed failure-only rerun did not create v2")
    wrapper = plugin / "skills/atk-visualize-dataset/scripts/generate_dataset_browser.py"
    output = capture(
        [str(python), str(wrapper), "--no-open", "--overwrite"],
        cwd=project,
        extra_env={"PATH": str(atk.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    if "rows=1" not in output:
        raise ReleaseCheckError("legacy Skill wrapper could not find the installed CLI")


def main() -> int:
    identity = read_project_identity()
    print(f"Release check target: {identity.name}=={identity.version}")
    assert_versions_aligned(identity)
    clean_generated_artifacts()
    try:
        run(["uv", "sync"], timeout=180)
        run_static_python_checks()
        run(["uv", "run", "python", "scripts/validate_skill_pack.py"], timeout=120)
        run(["uv", "run", "pytest", "-q"], timeout=240)
        with tempfile.TemporaryDirectory(prefix="atk-release-check-") as tmp:
            temp_dir = Path(tmp)
            wheel, sdist = build_distributions(temp_dir / "dist", identity)
            smoke_installed_artifact(wheel, temp_dir / "wheel-smoke")
            smoke_installed_artifact(sdist, temp_dir / "sdist-smoke")
    finally:
        clean_generated_artifacts()
    print("release-check: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseCheckError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"release-check: FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from None
