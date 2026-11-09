from __future__ import annotations

import json
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


def seed_cache(root: Path, target: Path, version: str | None = None) -> Path:
    if version is None:
        version = json.loads((target / ".codex-plugin" / "plugin.json").read_text())["version"]
    cache = root / "agent-tune-kit" / version
    if cache.exists():
        shutil.rmtree(cache)
    shutil.copytree(target, cache)
    return cache


def test_reinstall_refreshes_existing_paths_and_local_cache_only(installed, isolated_plugin_cache):
    from agent_tune_kit.installer import payload_hashes

    common, target = installed
    for version in ("2.0.0", "1.0.0", "local"):
        cache = seed_cache(isolated_plugin_cache, target, version)
        (cache / "docs" / "shared-versioning-and-confirmation.md").write_text("old")
        (cache / "skills" / "retired-skill").mkdir()
        (cache / "skills" / "retired-skill" / "SKILL.md").write_text("obsolete")
    other = isolated_plugin_cache / "other-plugin" / "keep"
    other.parent.mkdir(parents=True)
    other.write_text("keep")
    for _ in range(2):
        result = run_cli("install", "--copy", "--yes", "--force", *common)
        assert result.returncode == 0, result.stderr
        for version in ("2.0.0", "1.0.0", "local"):
            assert payload_hashes(isolated_plugin_cache / "agent-tune-kit" / version) == payload_hashes(target)
        assert run_cli("status", *common).returncode == 0
        assert other.read_text() == "keep"
        assert "缓存" in result.stdout


def test_install_creates_local_cache_immediately(installed, isolated_plugin_cache):
    from agent_tune_kit.installer import payload_hashes

    common, target = installed
    cache = isolated_plugin_cache / "agent-tune-kit" / "local"
    assert payload_hashes(cache) == payload_hashes(target)
    assert (cache / "skills" / "atk-diagnose" / "SKILL.md").is_file()
    assert "缓存与安装内容一致" in run_cli("status", *common).stdout


def test_status_missing_cache_needs_repair(installed, isolated_plugin_cache):
    common, _ = installed
    shutil.rmtree(isolated_plugin_cache / "agent-tune-kit")
    result = run_cli("status", *common)
    assert result.returncode == 1, result.stderr
    assert "atk install" in result.stdout


@pytest.mark.parametrize("version", [None, "local", "1.0.0"])
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
    cache = seed_cache(isolated_plugin_cache, target, "1.0.0" if change == "version" else None)
    file = cache / "docs" / "shared-versioning-and-confirmation.md"
    if change == "changed":
        file.write_text("old")
    elif change == "missing":
        file.unlink()
    elif change == "extra":
        (cache / "docs" / "stale.md").write_text("old")
    elif change == "version":
        manifest_path = cache / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = "1.0.0"
        manifest_path.write_text(json.dumps(manifest))
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


@pytest.mark.parametrize("entry", [None, "local"])
def test_cache_symlink_is_rejected_without_touching_target(installed, isolated_plugin_cache, tmp_path, entry):
    common, target = installed
    isolated_plugin_cache.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    cache = isolated_plugin_cache / "agent-tune-kit"
    if entry:
        cache /= entry
    shutil.rmtree(cache)
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
    assert (cache / "skills" / "atk-diagnose" / "SKILL.md").is_file()
    assert (cache.parent / "local" / "skills" / "atk-diagnose" / "SKILL.md").is_file()


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


@pytest.mark.parametrize("failure", ["copy", "validation", "replace"])
def test_cache_refresh_failure_preserves_previous_contents(
    installed, isolated_plugin_cache, monkeypatch, capsys, failure
):
    from agent_tune_kit import installer

    common, target = installed
    cache = seed_cache(isolated_plugin_cache, target)
    before = {str(p.relative_to(cache.parent)): p.read_bytes() for p in cache.parent.rglob("*") if p.is_file()}
    real_copy = installer.copy_payload_tree
    real_replace = installer.os.replace

    def copy(source, destination):
        if destination.name == "local" and failure == "copy":
            raise PermissionError("injected permission failure")
        real_copy(source, destination)
        if destination.name == "local" and failure == "validation":
            (destination / "skills" / "atk-diagnose" / "SKILL.md").write_text("incomplete")

    def replace(source, destination):
        if Path(source).name == "new" and Path(destination) == cache.parent and failure == "replace":
            raise PermissionError("injected replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(installer, "copy_payload_tree", copy)
    monkeypatch.setattr(installer.os, "replace", replace)
    assert installer.main(["install", "--copy", "--yes", "--force", *common]) == 1
    output = capsys.readouterr()
    assert "cache refresh failed" in output.err
    assert "已为 Codex 本地安装" not in output.out
    assert (target / ".codex-plugin" / "plugin.json").is_file()
    assert before == {str(p.relative_to(cache.parent)): p.read_bytes() for p in cache.parent.rglob("*") if p.is_file()}
    assert sorted(p.name for p in isolated_plugin_cache.iterdir()) == ["agent-tune-kit"]


def test_failed_cache_restore_keeps_previous_cache_for_recovery(installed, isolated_plugin_cache, monkeypatch, capsys):
    from agent_tune_kit import installer

    common, _ = installed
    cache = isolated_plugin_cache / "agent-tune-kit"
    before = installer.payload_hashes(cache / "local")
    real_replace = installer.os.replace

    def replace(source, destination):
        if Path(destination) == cache:
            raise PermissionError("injected publish and restore failure")
        return real_replace(source, destination)

    monkeypatch.setattr(installer.os, "replace", replace)
    assert installer.main(["install", "--copy", "--yes", "--force", *common]) == 1
    preserved = list(isolated_plugin_cache.glob(".agent-tune-kit-*/previous"))
    assert len(preserved) == 1
    assert installer.payload_hashes(preserved[0] / "local") == before
    assert str(preserved[0]) in capsys.readouterr().err


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


def test_nested_cache_symlink_is_not_followed_on_refresh(installed, isolated_plugin_cache, tmp_path):
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
    assert not (cache / "docs" / "external").exists()


@pytest.mark.parametrize("relative", ["README.md", "skills/atk-diagnose/SKILL.md"])
def test_same_version_payload_update_is_readable_without_codex_rebuild(installed, isolated_plugin_cache, relative):
    common, target = installed
    current = (target / relative).read_bytes()
    (target / relative).write_text("same version, old content")
    cache = seed_cache(isolated_plugin_cache, target)
    result = run_cli("install", "--copy", "--yes", "--force", *common)
    assert result.returncode == 0, result.stderr
    assert (target / relative).read_bytes() == current
    assert (cache / relative).read_bytes() == current
    assert (cache.parent / "local" / relative).read_bytes() == current
    status = run_cli("status", *common)
    assert status.returncode == 0, status.stderr
    assert "缓存与安装内容一致" in status.stdout
