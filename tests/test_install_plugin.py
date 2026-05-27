from __future__ import annotations

import builtins
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
                "--backup-root",
                str(base / "backups"),
            ]
            result = run_cli("preview", "--smoke", *common)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mode: preview", result.stdout)
            self.assertIn("payload source: dev-root", result.stdout)
            self.assertIn("marketplace write: skipped", result.stdout)
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
        self.assertIn("rollback", help_result.stdout)
        self.assertIn("version", help_result.stdout)
        self.assertNotIn("--dry-run", help_result.stdout)
        self.assertNotIn("--apply", help_result.stdout)
        rollback_help = run_cli("rollback", "--help")
        self.assertEqual(rollback_help.returncode, 0)
        self.assertIn("--backup", rollback_help.stdout)

    def test_version_flag_and_subcommand_print_package_version(self) -> None:
        for args in [("--version",), ("version",)]:
            result = run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "agent-tune-kit 0.3.7")
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
                "--backup-root",
                str(base / "backups"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mode: install", result.stdout)
            self.assertIn("payload source: dev-root", result.stdout)
            self.assertIn("smoke:", result.stdout)
            self.assertIn("status:", result.stdout)
            self.assertIn("/plugins", result.stdout)
            data = json.loads((base / "marketplace.json").read_text())
            entry = next(item for item in data["plugins"] if item["name"] == "agent-tune-kit")
            self.assertEqual(entry["source"]["path"], "./plugins/agent-tune-kit")
            self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
            self.assertTrue((base / "plugins" / "agent-tune-kit" / ".codex-plugin" / "plugin.json").exists())

    def test_status_semantics_are_local_and_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
                "--backup-root",
                str(base / "backups"),
            ]
            self.assertEqual(run_cli("install", *common).returncode, 0)
            status = run_cli("status", *common)
            self.assertEqual(status.returncode, 0, status.stderr)
            for phrase in [
                "payload source: dev-root",
                "manifest valid: yes",
                "marketplace registered: yes",
                "source.path ok: yes",
                "plugin-store target resolved: yes",
                "installer does not modify or observe hidden Codex UI enablement state",
                "open /plugins",
                "$atk-* autocomplete",
            ]:
                self.assertIn(phrase, status.stdout)
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
                "--backup-root",
                str(base / "backups"),
            ]
            for extra in [["--no-input"], ["--yes"], ["--force"]]:
                result = run_cli(*common, *extra, timeout=2)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("atk: error:", result.stderr)
            success = run_cli(*common, "--yes", "--force")
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertIn("backup:", success.stdout)
            self.assertIn("rollback: atk rollback", success.stdout)
            self.assertTrue((base / "plugins" / "agent-tune-kit" / ".codex-plugin" / "plugin.json").exists())

    def test_interactive_prompt_can_authorize_conflict(self) -> None:
        sys.path.insert(0, str(SRC))
        from agent_tune_kit.installer import authorize_conflicts

        with (
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(builtins, "input", return_value="y"),
        ):
            authorize_conflicts(["plugin-store target exists"], yes=False, force=False, no_input=False)

    def test_backup_manifest_and_rollback_restore_directory_and_refuse_unrelated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "plugins" / "agent-tune-kit"
            target.mkdir(parents=True)
            (target / "stale.txt").write_text("stale", encoding="utf-8")
            original_market = {
                "plugins": [{"name": "agent-tune-kit", "source": {"source": "local", "path": "./plugins/old"}}]
            }
            (base / "marketplace.json").write_text(json.dumps(original_market), encoding="utf-8")
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
                "--backup-root",
                str(base / "backups"),
            ]
            install = run_cli("install", *common, "--yes", "--force")
            self.assertEqual(install.returncode, 0, install.stderr)
            backup_dirs = list((base / "backups").iterdir())
            self.assertEqual(len(backup_dirs), 1)
            metadata = json.loads((backup_dirs[0] / "manifest.json").read_text())
            for key in [
                "id",
                "timestamp",
                "marketplace_path",
                "plugin_store_target",
                "prior_existence",
                "prior_target_type",
                "copied_backup_path",
                "operation",
                "plugin_name",
                "schema_version",
                "package_name",
                "package_version",
                "manifest_version",
                "payload_source_kind",
                "payload_resource_origin",
                "install_mode",
            ]:
                self.assertIn(key, metadata)
            self.assertEqual(metadata["prior_target_type"], "directory")

            current_market = json.loads((base / "marketplace.json").read_text())
            current_market["plugins"].append(
                {"name": "other-plugin", "source": {"source": "local", "path": "./plugins/other"}}
            )
            (base / "marketplace.json").write_text(json.dumps(current_market), encoding="utf-8")
            blocked_market = run_cli("rollback", "--backup", metadata["id"], "--backup-root", str(base / "backups"))
            self.assertNotEqual(blocked_market.returncode, 0)
            self.assertIn("newer unrelated state", blocked_market.stderr)

            current_market["plugins"].pop()
            (base / "marketplace.json").write_text(json.dumps(current_market), encoding="utf-8")

            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
            target.write_text("new unrelated", encoding="utf-8")
            blocked = run_cli("rollback", "--backup", metadata["id"], "--backup-root", str(base / "backups"))
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("newer unrelated state", blocked.stderr)

            rollback = run_cli(
                "rollback", "--backup", metadata["id"], "--backup-root", str(base / "backups"), "--force"
            )
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertIn("rollback complete", rollback.stdout)
            self.assertEqual(json.loads((base / "marketplace.json").read_text()), original_market)
            self.assertEqual((target / "stale.txt").read_text(encoding="utf-8"), "stale")

    def test_rollback_refuses_newer_valid_copied_payload_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "plugins" / "agent-tune-kit"
            target.mkdir(parents=True)
            (target / "stale.txt").write_text("stale", encoding="utf-8")
            (base / "marketplace.json").write_text(
                '{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}',
                encoding="utf-8",
            )
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
                "--backup-root",
                str(base / "backups"),
                "--copy",
            ]
            install = run_cli("install", *common, "--yes", "--force")
            self.assertEqual(install.returncode, 0, install.stderr)
            backup_id = next((base / "backups").iterdir()).name

            manifest_path = target / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "9.9.9"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            blocked = run_cli("rollback", "--backup", backup_id, "--backup-root", str(base / "backups"))
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("newer unrelated state", blocked.stderr)

            forced = run_cli("rollback", "--backup", backup_id, "--backup-root", str(base / "backups"), "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual((target / "stale.txt").read_text(encoding="utf-8"), "stale")

    def test_rollback_restores_missing_file_and_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
                "--backup-root",
                str(base / "backups"),
            ]
            (base / "marketplace.json").write_text(
                '{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}',
                encoding="utf-8",
            )
            install = run_cli("install", *common, "--yes", "--force")
            self.assertEqual(install.returncode, 0, install.stderr)
            backup_id = next((base / "backups").iterdir()).name
            rollback = run_cli("rollback", "--backup", backup_id, "--backup-root", str(base / "backups"))
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertFalse((base / "plugins" / "agent-tune-kit").exists())

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real = base / "real-plugin"
            real.mkdir()
            target = base / "plugins" / "agent-tune-kit"
            target.parent.mkdir()
            target.symlink_to(real, target_is_directory=True)
            (base / "marketplace.json").write_text(
                '{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}',
                encoding="utf-8",
            )
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
                "--backup-root",
                str(base / "backups"),
            ]
            install = run_cli("install", *common, "--yes", "--force")
            self.assertEqual(install.returncode, 0, install.stderr)
            backup_id = next((base / "backups").iterdir()).name
            rollback = run_cli("rollback", "--backup", backup_id, "--backup-root", str(base / "backups"))
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertTrue(target.is_symlink())
            self.assertEqual(Path(os.readlink(target)), real)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "plugins" / "agent-tune-kit"
            target.parent.mkdir()
            target.write_text("old file", encoding="utf-8")
            (base / "marketplace.json").write_text(
                '{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}',
                encoding="utf-8",
            )
            common = [
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
                "--backup-root",
                str(base / "backups"),
            ]
            install = run_cli("install", *common, "--yes", "--force")
            self.assertEqual(install.returncode, 0, install.stderr)
            backup_id = next((base / "backups").iterdir()).name
            rollback = run_cli("rollback", "--backup", backup_id, "--backup-root", str(base / "backups"))
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "old file")

    def test_smoke_failure_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "marketplace.json").write_text(
                '{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/wrong"},"policy":{"installation":"AVAILABLE","authentication":"ON_INSTALL"},"category":"Coding"}]}',
                encoding="utf-8",
            )
            result = run_cli(
                "preview",
                "--smoke",
                "--marketplace-path",
                str(base / "marketplace.json"),
                "--plugin-store",
                str(base / "plugins"),
                "--backup-root",
                str(base / "backups"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source.path must be ./plugins/agent-tune-kit", result.stderr)

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
                self.assertIn("agent_tune_kit/plugin_payload/agent-tune-kit/skills/atk-status/SKILL.md", names)
                self.assertIn(
                    "agent_tune_kit/plugin_payload/agent-tune-kit/templates/.atk/runner/eval_runner.py.md", names
                )
                self.assertIn(
                    "agent_tune_kit/plugin_payload/agent-tune-kit/templates/.atk/runner/failure_rule.py.md", names
                )
                self.assertIn("agent_tune_kit/plugin_payload/agent-tune-kit/docs/skill-template-pack-usage.md", names)
            with tarfile.open(sdist) as archive:
                names = set(archive.getnames())
                prefix = sdist.name.removesuffix(".tar.gz")
                self.assertIn(f"{prefix}/.codex-plugin/plugin.json", names)
                self.assertIn(f"{prefix}/skills/atk-status/SKILL.md", names)
                self.assertIn(f"{prefix}/templates/.atk/runner/eval_runner.py.md", names)
                self.assertIn(f"{prefix}/templates/.atk/runner/failure_rule.py.md", names)

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
            "--backup-root",
            str(run_dir / "backups"),
        ]
        preview = subprocess.run(
            [str(atk), "preview", "--smoke", *common],
            cwd=run_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("payload source: package-resource", preview.stdout)
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
        self.assertIn("payload source: package-resource", install_cli.stdout)
        target = run_dir / "plugins" / "agent-tune-kit"
        self.assertFalse(target.is_symlink())
        self.assertTrue((target / ".codex-plugin" / "plugin.json").exists())
        self.assertTrue((target / ".codex-plugin" / "agent-tune-kit-install.json").exists())
        self.assertTrue((target / "templates" / ".atk" / "runner" / "eval_runner.py.md").exists())
        self.assertTrue((target / "templates" / ".atk" / "runner" / "failure_rule.py.md").exists())
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
        self.assertIn("plugin-store target resolved: yes", status.stdout)


if __name__ == "__main__":
    unittest.main()
