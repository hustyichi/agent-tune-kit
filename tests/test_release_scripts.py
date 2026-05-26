from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
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
    def test_release_identity_versions_are_aligned(self) -> None:
        check_release = load_script("check-release.py")
        identity = check_release.read_project_identity()
        self.assertEqual(identity.name, "agent-tune-kit")
        self.assertEqual(identity.version, "0.3.7")
        check_release.assert_versions_aligned(identity)

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
        old = {key: os.environ.pop(key, None) for key in ["UV_PUBLISH_TOKEN", "UV_PUBLISH_USERNAME", "UV_PUBLISH_PASSWORD"]}
        try:
            with self.assertRaises(publish_release.PublishError):
                publish_release.require_publish_credentials(trusted_publishing=None)
            publish_release.require_publish_credentials(trusted_publishing="always")
            os.environ["UV_PUBLISH_TOKEN"] = "pypi-example"
            publish_release.require_publish_credentials(trusted_publishing=None)
        finally:
            for key in ["UV_PUBLISH_TOKEN", "UV_PUBLISH_USERNAME", "UV_PUBLISH_PASSWORD"]:
                os.environ.pop(key, None)
            for key, value in old.items():
                if value is not None:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
