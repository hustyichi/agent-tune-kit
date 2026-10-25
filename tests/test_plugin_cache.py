from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.test_install_plugin import run_cli


@pytest.fixture
def installed(tmp_path: Path) -> tuple[list[str], Path]:
    common = ["--marketplace-path", str(tmp_path / "marketplace.json"), "--plugin-store", str(tmp_path / "plugins")]
    result = run_cli("install", "--copy", *common)
    assert result.returncode == 0, result.stderr
    return common, tmp_path / "plugins" / "agent-tune-kit"


def seed_cache(root: Path, target: Path, version: str = "2.0.0") -> Path:
    cache = root / "agent-tune-kit" / version
    shutil.copytree(target, cache)
    return cache


def test_reinstall_clears_all_atk_cache_only(installed, isolated_plugin_cache):
    common, target = installed
    for version in ("2.0.0", "1.0.0", "local"):
        cache = seed_cache(isolated_plugin_cache, target, version)
        (cache / "docs" / "shared-versioning-and-confirmation.md").write_text("old")
    other = isolated_plugin_cache / "other-plugin" / "keep"
    other.parent.mkdir(parents=True)
    other.write_text("keep")
    for _ in range(2):
        result = run_cli("install", "--copy", "--yes", "--force", *common)
        assert result.returncode == 0, result.stderr
        assert not (isolated_plugin_cache / "agent-tune-kit").exists()
        assert other.read_text() == "keep"
        assert "缓存" in result.stdout


def test_status_missing_cache_is_pending(installed):
    common, _ = installed
    result = run_cli("status", *common)
    assert result.returncode == 0, result.stderr
    assert "缓存尚未生成" in result.stdout


@pytest.mark.parametrize("version", ["2.0.0", "local"])
def test_status_matching_cache_ignores_repo_extras(installed, isolated_plugin_cache, version):
    common, target = installed
    cache = seed_cache(isolated_plugin_cache, target, version)
    (cache / "src").mkdir()
    (cache / "src" / "extra.py").write_text("repository file")
    result = run_cli("status", *common)
    assert result.returncode == 0, result.stderr
    assert "缓存与安装内容一致" in result.stdout


@pytest.mark.parametrize("change", ["changed", "missing", "extra", "version"])
def test_status_detects_content_drift_without_writing(installed, isolated_plugin_cache, change):
    common, target = installed
    cache = seed_cache(isolated_plugin_cache, target, "1.0.0" if change == "version" else "2.0.0")
    file = cache / "docs" / "shared-versioning-and-confirmation.md"
    if change == "changed":
        file.write_text("old")
    elif change == "missing":
        file.unlink()
    elif change == "extra":
        (cache / "docs" / "stale.md").write_text("old")
    before = {str(p): p.read_bytes() for p in cache.rglob("*") if p.is_file()}
    result = run_cli("status", *common)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "atk install" in result.stdout
    assert before == {str(p): p.read_bytes() for p in cache.rglob("*") if p.is_file()}


def test_install_refusal_and_preview_keep_cache(installed, isolated_plugin_cache):
    common, target = installed
    cache = seed_cache(isolated_plugin_cache, target)
    marker = cache / "keep.txt"
    marker.write_text("keep")
    result = run_cli("install", "--copy", "--no-input", *common)
    assert result.returncode != 0
    assert marker.read_text() == "keep"
    result = run_cli("preview", "--smoke", *common)
    assert result.returncode == 0, result.stderr
    assert "缓存" in result.stdout
    assert marker.read_text() == "keep"


def test_cache_symlink_is_rejected_without_touching_target(installed, isolated_plugin_cache, tmp_path):
    common, target = installed
    isolated_plugin_cache.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    cache = isolated_plugin_cache / "agent-tune-kit"
    cache.symlink_to(outside, target_is_directory=True)
    for command in ("status", "install"):
        result = run_cli(command, "--yes", "--force", *common)
        assert result.returncode == 1, result.stdout
        assert "cache" in result.stderr.lower()
        assert cache.is_symlink()
        assert (outside / "keep").read_text() == "keep"
        assert (target / ".codex-plugin" / "plugin.json").is_file()


def test_cache_cannot_overlap_installation(installed, monkeypatch):
    common, target = installed
    monkeypatch.setenv("CODEX_PERSONAL_PLUGIN_CACHE_ROOT", str(target.parent))
    result = run_cli("install", "--yes", "--force", *common)
    assert result.returncode == 1, result.stdout
    assert (target / ".codex-plugin" / "plugin.json").is_file()


def test_codex_home_used_when_cache_override_absent(installed, monkeypatch, tmp_path):
    common, target = installed
    codex_home = tmp_path / "codex-home"
    monkeypatch.delenv("CODEX_PERSONAL_PLUGIN_CACHE_ROOT")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    cache = seed_cache(codex_home / "plugins" / "cache" / "personal", target)
    result = run_cli("install", "--yes", "--force", *common)
    assert result.returncode == 0, result.stderr
    assert not cache.exists()


def test_install_smoke_failure_preserves_cache(installed, isolated_plugin_cache, monkeypatch, capsys):
    from agent_tune_kit import installer

    common, target = installed
    cache = seed_cache(isolated_plugin_cache, target)
    marker = cache / "keep.txt"
    marker.write_text("keep")

    def fail(*args, **kwargs):
        raise installer.InstallError("injected smoke failure")

    monkeypatch.setattr(installer, "smoke_check", fail)
    assert installer.main(["install", "--copy", "--yes", "--force", *common]) == 1
    assert "injected smoke failure" in capsys.readouterr().err
    assert marker.read_text() == "keep"


def test_cache_clear_failure_reports_partial_install(installed, isolated_plugin_cache, monkeypatch, capsys):
    from agent_tune_kit import installer

    common, target = installed
    cache = seed_cache(isolated_plugin_cache, target)
    real_remove = installer.remove_path

    def fail_cache(path):
        if path == cache.parent:
            raise PermissionError("injected permission failure")
        real_remove(path)

    monkeypatch.setattr(installer, "remove_path", fail_cache)
    assert installer.main(["install", "--copy", "--yes", "--force", *common]) == 1
    output = capsys.readouterr()
    assert "cache refresh failed" in output.err
    assert "已为 Codex 本地安装" not in output.out
    assert (target / ".codex-plugin" / "plugin.json").is_file()
    assert cache.exists()


def test_status_unreadable_cache_is_error(installed, isolated_plugin_cache, monkeypatch, capsys):
    from agent_tune_kit import installer

    common, target = installed
    cache = seed_cache(isolated_plugin_cache, target)
    original_open = Path.open

    def unreadable(path, *args, **kwargs):
        if path == cache / "README.md":
            raise PermissionError("unreadable cache file")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unreadable)
    assert installer.main(["status", *common]) == 1
    assert "unreadable cache file" in capsys.readouterr().err


def test_nested_cache_symlink_is_not_followed_on_clear(installed, isolated_plugin_cache, tmp_path):
    common, target = installed
    cache = seed_cache(isolated_plugin_cache, target)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (cache / "docs" / "external").symlink_to(outside, target_is_directory=True)
    status = run_cli("status", *common)
    assert status.returncode == 1
    result = run_cli("install", "--yes", "--force", *common)
    assert result.returncode == 0, result.stderr
    assert (outside / "keep").read_text() == "keep"


def test_same_version_payload_update_then_codex_cache_reload(installed, isolated_plugin_cache):
    common, target = installed
    current = (target / "README.md").read_bytes()
    (target / "README.md").write_text("same version, old content")
    cache = seed_cache(isolated_plugin_cache, target)
    result = run_cli("install", "--copy", "--yes", "--force", *common)
    assert result.returncode == 0, result.stderr
    assert (target / "README.md").read_bytes() == current
    assert not cache.exists()
    # Simulate Codex's copy step; actual session activation remains a desktop check.
    seed_cache(isolated_plugin_cache, target)
    status = run_cli("status", *common)
    assert status.returncode == 0, status.stderr
    assert "缓存与安装内容一致" in status.stdout
