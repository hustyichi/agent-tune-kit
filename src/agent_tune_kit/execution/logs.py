import contextvars
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

APP_LOG_FILENAME = "app.log"
ROW_LOGS_DIRNAME = "logs"
ROW_LOGGER_NAMES = ("",)
ROW_LOG_LEVEL = logging.INFO
ROW_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_ACTIVE_ROW_LOG_CONTEXT = contextvars.ContextVar("atk_active_row_log_context", default=None)


def row_log_relative_path(source_index: int) -> Path:
    """Return the stable source-row-numbered path stored in eval_results.csv."""
    return Path(ROW_LOGS_DIRNAME) / f"row_{source_index:06d}.log"


def _configured_row_loggers() -> list[logging.Logger]:
    """Resolve configured Python loggers without mutating project handlers."""
    loggers: list[logging.Logger] = []
    seen: set[int] = set()
    for logger_name in ROW_LOGGER_NAMES:
        logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
        logger_id = id(logger)
        if logger_id not in seen:
            loggers.append(logger)
            seen.add(logger_id)
    return loggers


class ATKRowLogHandler(logging.Handler):
    """ATK-owned handler that routes records to the active row log only."""

    def __init__(self) -> None:
        super().__init__(ROW_LOG_LEVEL)
        self.setFormatter(logging.Formatter(ROW_LOG_FORMAT))
        self._lock = threading.RLock()
        self._handles: dict[Path, Any] = {}

    def emit(self, record: logging.LogRecord) -> None:
        row_context = _ACTIVE_ROW_LOG_CONTEXT.get()
        if row_context is None:
            return
        log_path = row_context.get("log_path")
        if not isinstance(log_path, Path):
            return

        if getattr(record, "_atk_row_log_written", False):
            return
        record._atk_row_log_written = True
        try:
            message = self.format(record)
            with self._lock:
                handle = self._handles.get(log_path)
                if handle is None:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    handle = log_path.open("a", encoding="utf-8")
                    self._handles[log_path] = handle
                handle.write(message + "\n")
                handle.flush()
        except (OSError, RuntimeError):
            self.handleError(record)

    def close(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                handle.close()
            self._handles.clear()
        super().close()


@contextmanager
def capture_python_row_logging(*, enabled: bool) -> Iterator[None]:
    """Install one run-scoped ATK row-log router for configured loggers."""
    if not enabled:
        yield
        return

    row_handler = ATKRowLogHandler()
    owned_loggers = _configured_row_loggers()
    previous_levels: dict[logging.Logger, int] = {}
    try:
        for logger in owned_loggers:
            previous_levels[logger] = logger.level
            logger.setLevel(ROW_LOG_LEVEL)
            logger.addHandler(row_handler)
        yield
    finally:
        for logger in owned_loggers:
            logger.removeHandler(row_handler)
            logger.setLevel(previous_levels[logger])
        row_handler.close()


@contextmanager
def capture_python_app_logging(version_dir: Path, *, enabled: bool) -> Iterator[None]:
    """Install one run-scoped app.log handler for configured Python loggers."""
    if not enabled:
        yield
        return

    log_path = version_dir / APP_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    app_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    app_handler.setLevel(ROW_LOG_LEVEL)
    app_handler.setFormatter(logging.Formatter(ROW_LOG_FORMAT))
    owned_loggers = _configured_row_loggers()
    previous_levels: dict[logging.Logger, int] = {}
    try:
        for logger in owned_loggers:
            previous_levels[logger] = logger.level
            logger.setLevel(ROW_LOG_LEVEL)
            logger.addHandler(app_handler)
        yield
    finally:
        for logger in owned_loggers:
            logger.removeHandler(app_handler)
            logger.setLevel(previous_levels[logger])
        app_handler.close()


@contextmanager
def capture_row_python_logs(
    version_dir: Path,
    source_index: int,
    *,
    enabled: bool,
) -> Iterator[str]:
    """Set ATK row context for one row and create its referenced log file."""
    if not enabled:
        yield ""
        return

    relative_path = row_log_relative_path(source_index)
    log_path = version_dir / relative_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.open("w", encoding="utf-8").close()

    token = _ACTIVE_ROW_LOG_CONTEXT.set({"relative_path": relative_path, "log_path": log_path})
    try:
        yield relative_path.as_posix()
    finally:
        _ACTIVE_ROW_LOG_CONTEXT.reset(token)
