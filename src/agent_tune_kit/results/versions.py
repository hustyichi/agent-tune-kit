from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.workspace import RESULTS_DIR


def list_version_dirs(results_dir: Path = RESULTS_DIR) -> list[tuple[int, Path]]:
    # Return [(number, path)] for directories named vN where N is a positive integer.
    # Missing results_dir means no versions exist yet; runner first run will create v1.
    if not results_dir.exists():
        return []
    return sorted(
        (int(path.name[1:]), path)
        for path in results_dir.iterdir()
        if path.is_dir() and path.name.startswith("v") and path.name[1:].isdigit() and int(path.name[1:]) > 0
    )


def allocate_next_results_version(results_dir: Path = RESULTS_DIR) -> Path:
    # Used only by eval_runner.py. Current max version determines create vs reuse.
    versions = list_version_dirs(results_dir)
    if not versions:
        target = results_dir / "v1"
    else:
        max_n, current = versions[-1]
        target = results_dir / f"v{max_n + 1}" if (current / "eval_results.csv").exists() else current
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_current_version(results_dir=RESULTS_DIR):
    versions = list_version_dirs(Path(results_dir))
    if not versions:
        raise UserActionRequired("No vN results directory exists; run eval_runner.py first or confirm repair.")
    return versions[-1][1]


def resolve_previous_version(current_dir, results_dir=RESULTS_DIR):
    versions = [
        path for _, path in list_version_dirs(Path(results_dir)) if int(path.name[1:]) < int(current_dir.name[1:])
    ]
    return versions[-1] if versions else None


def require_current_file(current_dir, filename):
    path = Path(current_dir) / filename
    if not path.is_file():
        raise UserActionRequired(
            f"Current version {current_dir.name} is missing {filename}; fix or rerun the prior step."
        )
    return path
