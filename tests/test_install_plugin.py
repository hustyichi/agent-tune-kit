from __future__ import annotations

import builtins
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_plugin.py"


def run_cli(*args: str, timeout: float = 5) -> subprocess.CompletedProcess[str]:
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
    def test_compatibility_help_and_preview_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            common = ["--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups")]
            for args in [[], ["--dry-run"], ["preview", "--smoke"]]:
                result = run_cli(*args, *common)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("mode: preview", result.stdout)
                self.assertIn("marketplace write: skipped", result.stdout)
            self.assertFalse((base / "marketplace.json").exists())
            self.assertFalse((base / "plugins" / "agent-tune-kit").exists())
            self.assertFalse((base / "backups").exists())

        help_result = run_cli("--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("status", help_result.stdout)
        self.assertIn("rollback", help_result.stdout)
        rollback_help = run_cli("rollback", "--help")
        self.assertEqual(rollback_help.returncode, 0)
        self.assertIn("--backup", rollback_help.stdout)

    def test_legacy_apply_smoke_and_install_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            common = ["--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups")]
            legacy = run_cli("--apply", "--smoke", *common)
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertIn("mode: apply (legacy)", legacy.stdout)
            self.assertIn("smoke:", legacy.stdout)
            self.assertTrue((base / "marketplace.json").exists())
            self.assertTrue((base / "plugins" / "agent-tune-kit").exists())

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = run_cli(
                "install",
                "--marketplace-path", str(base / "marketplace.json"),
                "--plugin-store", str(base / "plugins"),
                "--backup-root", str(base / "backups"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mode: install", result.stdout)
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
            common = ["--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups")]
            self.assertEqual(run_cli("install", *common).returncode, 0)
            status = run_cli("status", *common)
            self.assertEqual(status.returncode, 0, status.stderr)
            for phrase in [
                "manifest valid: yes",
                "marketplace registered: yes",
                "source.path ok: yes",
                "plugin-store target resolved: yes",
                "installer does not modify or observe hidden Codex UI enablement state",
                "open /plugins",
                "$atk-* autocomplete",
            ]:
                self.assertIn(phrase, status.stdout)
            self.assertNotIn("status should change from Available to Installed", status.stdout)

    def test_noninteractive_conflicts_do_not_hang_and_require_yes_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store_target = base / "plugins" / "agent-tune-kit"
            store_target.mkdir(parents=True)
            (store_target / "stale.txt").write_text("stale", encoding="utf-8")
            (base / "marketplace.json").write_text('{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}', encoding="utf-8")
            common = ["install", "--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups")]
            for extra in [["--no-input"], ["--yes"], ["--force"]]:
                result = run_cli(*common, *extra, timeout=2)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("install_plugin.py: error:", result.stderr)
            success = run_cli(*common, "--yes", "--force")
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertIn("backup:", success.stdout)
            self.assertIn("rollback: python3 scripts/install_plugin.py rollback", success.stdout)
            self.assertTrue((base / "plugins" / "agent-tune-kit" / ".codex-plugin" / "plugin.json").exists())

    def test_interactive_prompt_can_authorize_conflict(self) -> None:
        spec = importlib.util.spec_from_file_location("install_plugin", SCRIPT)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        assert spec and spec.loader
        sys.modules["install_plugin"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        with mock.patch.object(sys.stdin, "isatty", return_value=True), mock.patch.object(builtins, "input", return_value="y"):
            module.authorize_conflicts(["plugin-store target exists"], yes=False, force=False, no_input=False)

    def test_backup_manifest_and_rollback_restore_directory_and_refuse_unrelated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "plugins" / "agent-tune-kit"
            target.mkdir(parents=True)
            (target / "stale.txt").write_text("stale", encoding="utf-8")
            original_market = {"plugins": [{"name": "agent-tune-kit", "source": {"source": "local", "path": "./plugins/old"}}]}
            (base / "marketplace.json").write_text(json.dumps(original_market), encoding="utf-8")
            common = ["--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups")]
            install = run_cli("install", *common, "--yes", "--force")
            self.assertEqual(install.returncode, 0, install.stderr)
            backup_dirs = list((base / "backups").iterdir())
            self.assertEqual(len(backup_dirs), 1)
            metadata = json.loads((backup_dirs[0] / "manifest.json").read_text())
            for key in ["id", "timestamp", "marketplace_path", "plugin_store_target", "prior_existence", "prior_target_type", "copied_backup_path", "operation", "repo_root", "plugin_name"]:
                self.assertIn(key, metadata)
            self.assertEqual(metadata["prior_target_type"], "directory")

            # New unrelated marketplace changes also block rollback unless forced.
            current_market = json.loads((base / "marketplace.json").read_text())
            current_market["plugins"].append({"name": "other-plugin", "source": {"source": "local", "path": "./plugins/other"}})
            (base / "marketplace.json").write_text(json.dumps(current_market), encoding="utf-8")
            blocked_market = run_cli("rollback", "--backup", metadata["id"], "--backup-root", str(base / "backups"))
            self.assertNotEqual(blocked_market.returncode, 0)
            self.assertIn("newer unrelated state", blocked_market.stderr)

            current_market["plugins"].pop()
            (base / "marketplace.json").write_text(json.dumps(current_market), encoding="utf-8")

            # New unrelated target blocks rollback unless forced.
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                subprocess.run(["rm", "-rf", str(target)], check=True)
            target.write_text("new unrelated", encoding="utf-8")
            blocked = run_cli("rollback", "--backup", metadata["id"], "--backup-root", str(base / "backups"))
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("newer unrelated state", blocked.stderr)

            rollback = run_cli("rollback", "--backup", metadata["id"], "--backup-root", str(base / "backups"), "--force")
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertIn("rollback complete", rollback.stdout)
            self.assertEqual(json.loads((base / "marketplace.json").read_text()), original_market)
            self.assertEqual((target / "stale.txt").read_text(encoding="utf-8"), "stale")

    def test_rollback_restores_missing_file_and_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            common = ["--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups")]
            (base / "marketplace.json").write_text('{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}', encoding="utf-8")
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
            (base / "marketplace.json").write_text('{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}', encoding="utf-8")
            common = ["--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups")]
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
            (base / "marketplace.json").write_text('{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/old"}}]}', encoding="utf-8")
            common = ["--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups")]
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
            (base / "marketplace.json").write_text('{"plugins":[{"name":"agent-tune-kit","source":{"source":"local","path":"./plugins/wrong"},"policy":{"installation":"AVAILABLE","authentication":"ON_INSTALL"},"category":"Coding"}]}', encoding="utf-8")
            result = run_cli("preview", "--smoke", "--marketplace-path", str(base / "marketplace.json"), "--plugin-store", str(base / "plugins"), "--backup-root", str(base / "backups"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source.path must be ./plugins/agent-tune-kit", result.stderr)


if __name__ == "__main__":
    unittest.main()
