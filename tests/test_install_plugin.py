from __future__ import annotations

import builtins
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPT = ROOT / "scripts" / "install_plugin.py"


def run_cli(*args: str, timeout: float = 10, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "agent_tune_kit.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def run_script(*args: str, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


class InstallPluginCliTests(unittest.TestCase):
    def test_explicit_subcommands_and_preview_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
            ]
            result = run_cli("preview", *common)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Will install Agent Tune Kit", result.stdout)
            self.assertIn("No files changed", result.stdout)
            self.assertFalse((base / "marketplace.json").exists())
            self.assertFalse((base / "plugins" / "agent-tune-kit").exists())
            self.assertFalse((base / "backups").exists())

        for old_args in [[], ["--dry-run"], ["--apply"]]:
            old = run_cli(*old_args)
            self.assertNotEqual(old.returncode, 0)

        help_result = run_cli("--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("install", help_result.stdout)
        self.assertIn("preview", help_result.stdout)
        self.assertIn("status", help_result.stdout)
        for removed in ("rollback", "--backup-root", "--smoke", "--no-smoke", "--verbose"):
            self.assertNotIn(removed, help_result.stdout)
        self.assertIn("version", help_result.stdout)
        self.assertNotIn("--dry-run", help_result.stdout)
        self.assertNotIn("--apply", help_result.stdout)

    def test_version_flag_and_subcommand_print_package_version(self) -> None:
        for args in [("--version",), ("version",)]:
            result = run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "agent-tune-kit 1.0.0")
            self.assertEqual(result.stderr, "")

    def test_script_wrapper_delegates_to_atk_cli(self) -> None:
        result = run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Register Agent Tune Kit", result.stdout)

    def test_install_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = run_cli(
                "install",
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("installed locally for Codex", result.stdout)
            self.assertLessEqual(len(result.stdout.splitlines()), 3)
            for detail in ("payload", "smoke", "backup", "rollback", "status:"):
                self.assertNotIn(detail, result.stdout)
            self.assertIn("/plugins", result.stdout)
            data = json.loads((base / "marketplace.json").read_text())
            entry = next(item for item in data["plugins"] if item["name"] == "agent-tune-kit")
            self.assertEqual(entry["source"]["path"], "./plugins/agent-tune-kit")
            self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
            self.assertTrue((base / "plugins" / "agent-tune-kit" / ".codex-plugin" / "plugin.json").exists())

    def test_failed_check_does_not_report_success(self) -> None:
        from agent_tune_kit import installer

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(installer, "check_installation", side_effect=installer.InstallError("check failed")),
                mock.patch("sys.stdout", stdout),
                mock.patch("sys.stderr", stderr),
            ):
                result = installer.main(
                    [
                        "install",
                        "--copy",
                        "--marketplace-path",
                        str(base / "marketplace.json"),
                        "--plugin-store",
                        str(base / "plugins"),
                    ]
                )
            self.assertEqual(result, 1)
            self.assertNotIn("installed locally", stdout.getvalue())
            self.assertIn("check failed", stderr.getvalue())

    def test_copy_failure_preserves_existing_installation(self) -> None:
        from agent_tune_kit import installer

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            args = [
                "install",
                "--copy",
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
            ]
            self.assertEqual(run_cli(*args).returncode, 0)
            target = base / "plugins" / "agent-tune-kit"
            original = (target / ".codex-plugin" / "plugin.json").read_bytes()
            with (
                mock.patch.object(installer, "copy_payload_tree", side_effect=OSError("disk full")),
                mock.patch("sys.stderr", io.StringIO()),
            ):
                self.assertEqual(installer.main(args), 1)
            self.assertEqual((target / ".codex-plugin" / "plugin.json").read_bytes(), original)
            self.assertEqual(list((base / "plugins").iterdir()), [target])

    def test_cli_warns_on_mismatched_skill_and_repairs_owned_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugin = base / "plugins" / "agent-tune-kit"
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
            ]
            other = {"name": "other-plugin", "source": {"source": "local", "path": "./plugins/other"}}
            (base / "marketplace.json").write_text(json.dumps({"plugins": [other]}), encoding="utf-8")
            self.assertEqual(run_cli("install", "--copy", *common).returncode, 0)
            manifest_path = plugin / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "0.9.9"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            project = base / "project"
            project.mkdir()
            request = base / "request.json"
            output = base / "response.json"
            request.write_text(json.dumps({"project_path": str(project), "analysis_only": True}), encoding="utf-8")
            internal = [
                "internal",
                "initialize_project",
                "--plugin-root",
                str(plugin),
                "--request",
                str(request),
                "--output",
                str(output),
            ]
            mismatch = run_cli(*internal)
            self.assertEqual(mismatch.returncode, 0, mismatch.stderr)
            mismatch_response = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(mismatch_response["status"], "ok")
            self.assertIn("consider atk install", mismatch_response["warnings"][0])
            self.assertTrue((project / ".atk" / "project.json").exists())
            status = run_cli("status", *common)
            self.assertEqual(status.returncode, 0)
            self.assertIn("CLI version is 1.0.0", status.stdout)
            self.assertIn("run atk install to update Skills when convenient", status.stdout)

            reinstall = run_cli("install", "--copy", *common, "--no-input")
            self.assertEqual(reinstall.returncode, 0, reinstall.stderr)
            self.assertNotIn("backup", reinstall.stdout)
            self.assertFalse((base / "backups").exists())
            entries = json.loads((base / "marketplace.json").read_text())["plugins"]
            self.assertIn(other, entries)
            self.assertEqual(len(entries), 2)
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["version"], "1.0.0")
            second_project = base / "second-project"
            second_project.mkdir()
            request.write_text(
                json.dumps({"project_path": str(second_project), "analysis_only": True}), encoding="utf-8"
            )
            self.assertEqual(run_cli(*internal).returncode, 0)
            response = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["warnings"], [])

    def test_status_semantics_are_local_and_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
            ]
            self.assertEqual(run_cli("install", *common).returncode, 0)
            status = run_cli("status", *common)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("is installed locally.", status.stdout)
            self.assertIn("Open /plugins", status.stdout)
            self.assertIn("check whether Agent Tune Kit is enabled", status.stdout)
            self.assertLessEqual(len(status.stdout.splitlines()), 3)
            self.assertNotIn("repo:", status.stdout)
            self.assertNotIn("status should change from Available to Installed", status.stdout)

    def test_noninteractive_conflicts_do_not_hang_and_require_yes_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store_target = base / "plugins" / "agent-tune-kit"
            store_target.mkdir(parents=True)
            (store_target / "stale.txt").write_text("stale", encoding="utf-8")
            (base / "marketplace.json").write_text(
                '{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}',
                encoding="utf-8",
            )
            common = [
                "install",
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
            ]
            for extra in [["--no-input"], ["--yes"], ["--force"]]:
                result = run_cli(*common, *extra, timeout=2)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("atk: error:", result.stderr)
            success = run_cli(*common, "--yes", "--force")
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertTrue((base / "plugins" / "agent-tune-kit" / ".codex-plugin" / "plugin.json").exists())

    def test_interactive_prompt_can_authorize_conflict(self) -> None:
        sys.path.insert(0, str(SRC))
        from agent_tune_kit.installer import authorize_conflicts

        with (
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(builtins, "input", return_value="y"),
        ):
            authorize_conflicts(["plugin-store target exists"], yes=False, force=False, no_input=False)

    @unittest.skipUnless(shutil.which("uv"), "uv is required for distribution smoke tests")
    def test_distribution_archives_and_installed_cli_use_package_resource_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dist = base / "dist"
            env = os.environ.copy()
            env["UV_NO_CONFIG"] = "1"
            build = subprocess.run(
                ["uv", "build", "--out-dir", str(dist)],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            wheel = next(dist.glob("agent_tune_kit-*.whl"))
            sdist = next(dist.glob("agent_tune_kit-*.tar.gz"))

            hidden_manifest = "agent_tune_kit/plugin_payload/agent-tune-kit/.codex-plugin/plugin.json"
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
                self.assertIn(hidden_manifest, names)
                for skill in (
                    "atk-init",
                    "atk-dataset",
                    "atk-eval",
                    "atk-diagnose",
                    "atk-optimize",
                    "atk-validate",
                    "atk-decide",
                ):
                    self.assertIn(f"agent_tune_kit/plugin_payload/agent-tune-kit/skills/{skill}/SKILL.md", names)
                self.assertIn("agent_tune_kit/plugin_payload/agent-tune-kit/templates/runner.py", names)
            with tarfile.open(sdist) as archive:
                names = set(archive.getnames())
                prefix = sdist.name.removesuffix(".tar.gz")
                self.assertIn(f"{prefix}/.codex-plugin/plugin.json", names)
                for skill in (
                    "atk-init",
                    "atk-dataset",
                    "atk-eval",
                    "atk-diagnose",
                    "atk-optimize",
                    "atk-validate",
                    "atk-decide",
                ):
                    self.assertIn(f"{prefix}/skills/{skill}/SKILL.md", names)
                self.assertIn(f"{prefix}/templates/runner.py", names)

            self._assert_installed_artifact_smoke(wheel, base / "wheel-venv", base / "wheel-run")
            self._assert_installed_artifact_smoke(sdist, base / "sdist-venv", base / "sdist-run")

    def _assert_installed_artifact_smoke(self, artifact: Path, venv_dir: Path, run_dir: Path) -> None:
        env = os.environ.copy()
        env["UV_NO_CONFIG"] = "1"
        create = subprocess.run(
            ["uv", "venv", str(venv_dir)],
            cwd=run_dir.parent,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        self.assertEqual(create.returncode, 0, create.stderr)
        python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        install = subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(artifact)],
            cwd=run_dir.parent,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            check=False,
        )
        self.assertEqual(install.returncode, 0, install.stderr)
        run_dir.mkdir(parents=True, exist_ok=True)
        atk = venv_dir / ("Scripts/atk.exe" if os.name == "nt" else "bin/atk")
        common = [
            "--marketplace-path",
            str(run_dir / "marketplace.json"),
            "--plugin-store",
            str(run_dir / "plugins"),
        ]
        preview = subprocess.run(
            [str(atk), "preview", *common],
            cwd=run_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("No files changed", preview.stdout)
        install_cli = subprocess.run(
            [str(atk), "install", *common],
            cwd=run_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(install_cli.returncode, 0, install_cli.stderr)
        self.assertIn("installed locally for Codex", install_cli.stdout)
        target = run_dir / "plugins" / "agent-tune-kit"
        self.assertFalse(target.is_symlink())
        self.assertTrue((target / ".codex-plugin" / "plugin.json").exists())
        self.assertTrue((target / ".codex-plugin" / "agent-tune-kit-install.json").exists())
        for skill in (
            "atk-init",
            "atk-dataset",
            "atk-eval",
            "atk-diagnose",
            "atk-optimize",
            "atk-validate",
            "atk-decide",
        ):
            self.assertTrue((target / "skills" / skill / "SKILL.md").exists())
        self.assertTrue((target / "templates" / "runner.py").exists())
        status = subprocess.run(
            [str(atk), "status", *common],
            cwd=run_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("is installed locally.", status.stdout)
        project = run_dir / "project"
        project.mkdir()
        request = run_dir / "request.json"
        output = run_dir / "response.json"
        request.write_text(json.dumps({"project_path": str(project), "analysis_only": True}), encoding="utf-8")
        internal = subprocess.run(
            [
                str(atk),
                "internal",
                "initialize_project",
                "--plugin-root",
                str(target),
                "--request",
                str(request),
                "--output",
                str(output),
            ],
            cwd=run_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(internal.returncode, 0, internal.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "ok")


if __name__ == "__main__":
    unittest.main()
