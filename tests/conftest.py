from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_plugin_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Installer tests must never inspect or clear the user's plugin cache."""
    root = tmp_path / "cache" / "personal"
    monkeypatch.setenv("CODEX_PERSONAL_PLUGIN_CACHE_ROOT", str(root))
    return root
