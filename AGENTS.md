# Repository Guidelines

## Project Structure & Module Organization

This repository packages Agent Tune Kit, a local Codex plugin distributed as a Python package. Core Python code lives in `src/agent_tune_kit/`, with the CLI entry point in `cli.py` and installer logic in `installer.py`. Plugin payload files are under `.codex-plugin/`, `skills/`, `templates/`, and `docs/`; keep these paths stable because `pyproject.toml` force-includes them in built wheels. Tests are in `tests/`, while release and validation helpers are in `scripts/`.

## Build, Test, and Development Commands

- `uv run pytest` runs the full test suite.
- `uv run ruff check .` checks lint rules, import ordering, and common Python issues.
- `uv run ruff format .` formats Python files using the configured Ruff style.
- `uv build --no-sources` builds the package artifact using only declared package inputs.
- `python scripts/validate_skill_pack.py` validates bundled skill pack structure.
- `python scripts/check-release.py` runs release sanity checks before publishing.
- `./scripts/release-version.sh <version> --publish` performs the full reusable release flow: bump versions, lock, test, commit, tag, push, publish, and verify PyPI.

Use `uv run atk --version` or `uv run atk install` for local CLI smoke tests after changing package or installer behavior.

## Natural-Language Release Requests

When a user asks in natural language to upgrade, release, publish, tag, or push a new version, extract the requested `MAJOR.MINOR.PATCH` value and use the release automation directly. For example, "升级到 0.3.9 并发布" maps to `./scripts/release-version.sh 0.3.9 --publish`; "只升级到 0.3.9" maps to `./scripts/release-version.sh 0.3.9`. Do not manually edit the individual version files unless the release script fails and the failure requires a narrow fix.

Use `./scripts/release-version.sh <version> --publish --dry-run` only when the user asks for a preview or when you need to explain the planned commands. The release script intentionally treats the current `validate_skill_pack.py` README/plugin documentation phrase failures as warnings unless strict mode is requested, and publishing reuses `scripts/publish-release.py` with existing PyPI credential support (`UV_PUBLISH_TOKEN`, username/password env vars, or `~/.pypirc`). After a successful natural-language release request, report the created commit, tag, branch push, tag push, and PyPI verification result.

## Coding Style & Naming Conventions

Python targets 3.11+. Use 4-space indentation, double quotes, and a 120-character line length. Ruff is the source of truth for formatting and linting; avoid manual style exceptions unless the existing code already requires them. Prefer clear snake_case names for functions, variables, and test modules. Skill directories use kebab-case names such as `skills/atk-find-failures/`.

## Testing Guidelines

Tests use `pytest` and should be named `tests/test_*.py`. Add focused regression tests for CLI behavior, installer behavior, release checks, and generated artifact paths. When changing skill payloads or templates, run both `uv run pytest` and `python scripts/validate_skill_pack.py`.

## Commit & Pull Request Guidelines

Recent history uses short intent-focused subjects, including imperative sentences and prefixed forms such as `ADD:` or `MOD:`. Keep commits narrow and explain why the change exists. Pull requests should include a concise description, affected commands or workflows, linked issues when applicable, and verification evidence such as `uv run pytest`, Ruff checks, build output, or screenshots for generated HTML assets.

## Security & Configuration Tips

Do not commit local `.venv/`, `.ruff_cache/`, build artifacts, or private evaluation datasets. Treat files under `.atk/results/` in downstream projects as generated outputs unless a test fixture explicitly needs them.
