"""Additive, process-local logging outlets; copied into the target Python runtime."""

import json
import logging
import os
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

SECRET_KEY = re.compile(r"^(authorization|cookie|password|secret|api[_-]?key|token|private[_-]?key)$", re.I)
INLINE_SECRET = re.compile(
    r"(?i)([\"']?(?:authorization|cookie|password|secret|api[_-]?key|token|private[_-]?key)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)


def redact_text(text):
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+", "Bearer [REDACTED]", text)
    return INLINE_SECRET.sub(r"\1[REDACTED]", text)


def redact_record(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_KEY.fullmatch(k) else redact_record(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_record(item) for item in value]
    return redact_text(value) if isinstance(value, str) else value


def log_records(path, format):
    """Yield native records and physical line numbers with a bounded read."""
    with Path(path).open(encoding="utf-8") as handle:
        number = 0
        while line := handle.readline(1024 * 1024 + 1):
            number += 1
            if len(line) > 1024 * 1024:
                raise ValueError("native_log_line_too_large")
            if format == "text":
                yield number, line.rstrip("\r\n"), None
            else:
                try:
                    raw = json.loads(line)
                except ValueError:
                    yield number, line.rstrip("\r\n"), None
                    continue
                record = raw.get("record") if isinstance(raw, dict) else None
                if (
                    not isinstance(record, dict)
                    or not isinstance(record.get("message"), str)
                    or not isinstance(record.get("extra", {}), dict)
                ):
                    record = None
                yield number, raw, record


class _Outlet:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = open(path, "x", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600))  # noqa: SIM115 - outlet lifetime is managed by capture context
        self.lock = threading.Lock()

    def write(self, message):
        text = str(message)
        try:
            raw = json.loads(text)
        except ValueError:
            rendered = redact_text(text)
        else:
            clean = redact_record(raw)
            rendered = text if clean == raw else json.dumps(clean, ensure_ascii=False) + "\n"
        with self.lock:
            try:
                self.handle.write(rendered)
                self.handle.flush()
            except (OSError, ValueError):
                print("ATK_CAPTURE_ERROR native_outlet_error", file=sys.stderr, flush=True)
                raise

    def flush(self):
        if not self.handle.closed:
            self.handle.flush()


class _Handler(logging.StreamHandler):
    def emit(self, record):
        # File failures must surface, rather than silently yielding apparently complete evidence.
        try:
            text = self.format(record)
        except (ValueError, TypeError):
            print("ATK_CAPTURE_ERROR native_outlet_error", file=sys.stderr, flush=True)
            raise
        self.stream.write(text + "\n")


@contextmanager
def capture_logging(path, names, formatter, level="INFO"):
    """Attach to declared logger roots after project setup; keep project levels and handlers."""
    names = list(names)
    if (
        not names
        or len(set(names)) != len(names)
        or any(a != b and (not a or b.startswith(a + ".")) for a in names for b in names)
    ):
        raise ValueError("Declare non-overlapping logger roots")
    handler = _Handler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    outlet = _Outlet(path)
    handler.setStream(outlet)
    loggers = [logging.getLogger(name) for name in names]
    try:
        for logger in loggers:
            logger.addHandler(handler)
        yield handler
    finally:
        for logger in loggers:
            logger.removeHandler(handler)
        handler.close()
        outlet.handle.close()


@contextmanager
def capture_loguru(path, logger=None, **options):
    """Keep Loguru's native serialization; remove only the additional sink on exit."""
    if logger is None:
        from loguru import logger
    outlet = _Outlet(path)
    sink = None
    try:
        sink = logger.add(
            outlet.write,
            **{"serialize": True, "enqueue": False, "backtrace": False, "diagnose": False, "catch": False, **options},
        )
        yield sink
    finally:
        if sink is not None:
            logger.complete()
            logger.remove(sink)
        outlet.handle.close()
