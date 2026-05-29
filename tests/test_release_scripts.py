from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    module_name = name.replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseScriptTests(unittest.TestCase):
    def test_project_defaults_to_ruff_formatting(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("ruff>=0.8", pyproject["dependency-groups"]["dev"])
        self.assertEqual(pyproject["tool"]["ruff"]["line-length"], 120)
        self.assertEqual(pyproject["tool"]["ruff"]["format"]["quote-style"], "double")
        self.assertEqual(pyproject["tool"]["ruff"]["lint"]["select"], ["E", "F", "I", "UP", "B", "SIM"])
        self.assertEqual(pyproject["tool"]["ruff"]["lint"]["ignore"], ["E501", "UP022"])

    def test_release_gate_runs_ruff_auto_fix_before_validation(self) -> None:
        check_release = load_script("check-release.py")
        commands: list[list[str]] = []

        def record(command: list[str], **_: object) -> None:
            commands.append(command)

        original_run = check_release.run
        try:
            check_release.run = record
            check_release.run_static_python_checks()
        finally:
            check_release.run = original_run

        self.assertEqual(
            commands,
            [
                ["uv", "run", "ruff", "format", "."],
                ["uv", "run", "ruff", "check", "--fix", "."],
                ["uv", "run", "ruff", "format", "--check", "."],
                ["uv", "run", "ruff", "check", "."],
                ["uv", "run", "python", "-m", "py_compile", *check_release.PYTHON_FILES],
            ],
        )
        self.assertFalse(any("--unsafe-fixes" in command for command in commands))

    def test_release_identity_versions_are_aligned(self) -> None:
        check_release = load_script("check-release.py")
        identity = check_release.read_project_identity()
        self.assertEqual(identity.name, "agent-tune-kit")
        self.assertEqual(identity.version, "0.3.8")
        check_release.assert_versions_aligned(identity)

    def test_skill_pack_validation_does_not_phrase_scan_readmes(self) -> None:
        validate_skill_pack = load_script("validate_skill_pack.py")

        readme_paths = {"README.md", "README.en.md", "README.zh-CN.md"}
        self.assertTrue(readme_paths.issubset(set(validate_skill_pack.REQUIRED_FILES)))
        self.assertTrue(readme_paths.isdisjoint(validate_skill_pack.PER_FILE_PHRASES))
        self.assertNotIn("explicit subcommands only", validate_skill_pack.PLUGIN_DOC_PHRASES)

    def test_publish_targets_and_commands_are_safe_by_default(self) -> None:
        publish_release = load_script("publish-release.py")
        pypi = publish_release.target_for("pypi")
        testpypi = publish_release.target_for("testpypi")
        self.assertEqual(pypi.simple_url, "https://pypi.org/simple/")
        self.assertIsNone(pypi.publish_url)
        self.assertEqual(testpypi.simple_url, "https://test.pypi.org/simple/")
        self.assertEqual(testpypi.publish_url, "https://test.pypi.org/legacy/")

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = [Path(tmp) / "a.whl", Path(tmp) / "a.tar.gz"]
            command = publish_release.publish_command(testpypi, artifacts, trusted_publishing=None)
        self.assertEqual(command[:4], ["uv", "publish", "--check-url", "https://test.pypi.org/simple/"])
        self.assertIn("--publish-url", command)
        self.assertIn("https://test.pypi.org/legacy/", command)

    def test_publish_requires_explicit_credentials_without_trusted_publishing(self) -> None:
        publish_release = load_script("publish-release.py")
        old = {
            key: os.environ.pop(key, None)
            for key in ["HOME", "UV_PUBLISH_TOKEN", "UV_PUBLISH_USERNAME", "UV_PUBLISH_PASSWORD"]
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["HOME"] = tmp
                with self.assertRaises(publish_release.PublishError):
                    publish_release.publish_credentials_env(target_name="pypi", trusted_publishing=None)
                self.assertEqual(
                    publish_release.publish_credentials_env(target_name="pypi", trusted_publishing="always"),
                    {},
                )
                os.environ["UV_PUBLISH_TOKEN"] = "pypi-example"
                self.assertEqual(
                    publish_release.publish_credentials_env(target_name="pypi", trusted_publishing=None), {}
                )
        finally:
            for key in ["UV_PUBLISH_TOKEN", "UV_PUBLISH_USERNAME", "UV_PUBLISH_PASSWORD"]:
                os.environ.pop(key, None)
            for key, value in old.items():
                if value is not None:
                    os.environ[key] = value

    def test_publish_can_use_pypirc_credentials_when_env_credentials_are_missing(self) -> None:
        publish_release = load_script("publish-release.py")
        old = {
            key: os.environ.pop(key, None)
            for key in ["HOME", "UV_PUBLISH_TOKEN", "UV_PUBLISH_USERNAME", "UV_PUBLISH_PASSWORD"]
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                (home / ".pypirc").write_text(
                    "\n".join(
                        [
                            "[distutils]",
                            "index-servers =",
                            "    pypi",
                            "",
                            "[pypi]",
                            "username = __token__",
                            "password = pypi-example",
                        ]
                    ),
                    encoding="utf-8",
                )
                os.environ["HOME"] = str(home)

                self.assertEqual(
                    publish_release.publish_credentials_env(target_name="pypi", trusted_publishing=None),
                    {"UV_PUBLISH_USERNAME": "__token__", "UV_PUBLISH_PASSWORD": "pypi-example"},
                )
        finally:
            for key in ["HOME", "UV_PUBLISH_TOKEN", "UV_PUBLISH_USERNAME", "UV_PUBLISH_PASSWORD"]:
                os.environ.pop(key, None)
            for key, value in old.items():
                if value is not None:
                    os.environ[key] = value

    def test_release_version_updates_all_version_files(self) -> None:
        release_version = load_script("release-version.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".codex-plugin").mkdir()
            (root / "src" / "agent_tune_kit").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "scripts").mkdir()

            (root / "pyproject.toml").write_text('version = "0.3.8"\n', encoding="utf-8")
            (root / "uv.lock").write_text(
                '\n'.join(
                    [
                        '[[package]]',
                        'name = "agent-tune-kit"',
                        'version = "0.3.8"',
                        '',
                        '[[package]]',
                        'name = "colorama"',
                        'version = "0.4.6"',
                        '',
                    ]
                ),
                encoding="utf-8",
            )
            (root / ".codex-plugin" / "plugin.json").write_text('"version": "0.3.8"\n', encoding="utf-8")
            (root / "src" / "agent_tune_kit" / "__init__.py").write_text('__version__ = "0.3.8"\n', encoding="utf-8")
            (root / "tests" / "test_release_scripts.py").write_text(
                'self.assertEqual(identity.version, "0.3.8")\nexample_version = "1.2.3"\n',
                encoding="utf-8",
            )
            (root / "tests" / "test_install_plugin.py").write_text('"agent-tune-kit 0.3.8"\n', encoding="utf-8")
            (root / "scripts" / "validate_skill_pack.py").write_text('\'"version": "0.3.8"\'\n', encoding="utf-8")

            changed = release_version.update_version_files(root, "0.4.0")

            self.assertEqual(
                changed,
                [
                    ".codex-plugin/plugin.json",
                    "pyproject.toml",
                    "scripts/validate_skill_pack.py",
                    "src/agent_tune_kit/__init__.py",
                    "tests/test_install_plugin.py",
                    "tests/test_release_scripts.py",
                    "uv.lock",
                ],
            )
            for path in changed:
                content = (root / path).read_text(encoding="utf-8")
                self.assertIn("0.4.0", content)
                self.assertNotIn("0.3.8", content)
            self.assertIn(
                'example_version = "1.2.3"',
                (root / "tests" / "test_release_scripts.py").read_text(encoding="utf-8"),
            )
            self.assertIn(
                'name = "colorama"\nversion = "0.4.6"',
                (root / "uv.lock").read_text(encoding="utf-8"),
            )

    def test_release_version_plans_full_release_flow(self) -> None:
        release_version = load_script("release-version.py")
        commands = release_version.release_commands("0.4.0", publish=True, skip_release_check=True)

        self.assertEqual(commands[0], ["git", "status", "--porcelain"])
        self.assertIn(["uv", "lock"], commands)
        self.assertIn(["uv", "run", "pytest"], commands)
        self.assertIn(["uv", "run", "atk", "--version"], commands)
        self.assertIn(["git", "tag", "0.4.0"], commands)
        self.assertIn(["git", "push", "origin", "main"], commands)
        self.assertIn(["git", "push", "origin", "0.4.0"], commands)
        self.assertEqual(
            commands[-1],
            [
                "uv",
                "run",
                "python",
                "scripts/publish-release.py",
                "--repository",
                "pypi",
                "--publish",
                "--skip-release-check",
            ],
        )


if __name__ == "__main__":
    unittest.main()
