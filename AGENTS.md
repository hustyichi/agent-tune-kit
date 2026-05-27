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

Use `uv run atk --version` or `uv run atk install` for local CLI smoke tests after changing package or installer behavior.

## Coding Style & Naming Conventions

Python targets 3.11+. Use 4-space indentation, double quotes, and a 120-character line length. Ruff is the source of truth for formatting and linting; avoid manual style exceptions unless the existing code already requires them. Prefer clear snake_case names for functions, variables, and test modules. Skill directories use kebab-case names such as `skills/atk-find-failures/`.

## Testing Guidelines

Tests use `pytest` and should be named `tests/test_*.py`. Add focused regression tests for CLI behavior, installer behavior, release checks, and generated artifact paths. When changing skill payloads or templates, run both `uv run pytest` and `python scripts/validate_skill_pack.py`.

## Commit & Pull Request Guidelines

Recent history uses short intent-focused subjects, including imperative sentences and prefixed forms such as `ADD:` or `MOD:`. Keep commits narrow and explain why the change exists. Pull requests should include a concise description, affected commands or workflows, linked issues when applicable, and verification evidence such as `uv run pytest`, Ruff checks, build output, or screenshots for generated HTML assets.

## Security & Configuration Tips

Do not commit local `.venv/`, `.ruff_cache/`, build artifacts, or private evaluation datasets. Treat files under `.atk/results/` in downstream projects as generated outputs unless a test fixture explicitly needs them.
