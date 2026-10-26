"""Small observer surface; never changes business return values or retry behavior."""

import contextvars
import logging
import threading
import time
import uuid
import weakref
from contextlib import contextmanager

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import json_bytes, now, safe_path

CURRENT = contextvars.ContextVar("atk_execution", default=None)
SPAN = contextvars.ContextVar("atk_span", default=None)
PARENT = contextvars.ContextVar("atk_parent_span", default=None)
LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class Observer:
    def __init__(self, evidence, run):
        self.evidence = evidence
        self.run = run
        self.failed = False
        self.gaps = []
        self.closed_sources = {}

    @contextmanager
    def activate(self):
        token = CURRENT.set(self)
        try:
            yield self
        finally:
            CURRENT.reset(token)

    def event(self, kind, attributes=None):
        try:
            if self.evidence.manifest["sealed"]:
                self.run.event(
                    "late",
                    {"execution_id": self.evidence.manifest["execution_id"], "kind": kind, "attributes": attributes},
                )
                self.run.manifest["gaps"].append("late_event_after_execution_seal")
                return None
            event = self.evidence.event(
                kind, attributes or {}, span_id=SPAN.get(), parent_span_id=PARENT.get(), occurred_at=now()
            )
            if event["kind"] == "source.closed" and isinstance(event["attributes"].get("source"), str):
                self.closed_sources[event["attributes"]["source"]] = event["event_id"]
            return event
        except Exception:
            self.failed = True
            self.run.failed.set()
            return None

    def artifact(self, role, value, *, media_type="application/json"):
        try:
            entry = self.evidence.save(role, value, media_type=media_type)
            self.event("artifact", {"artifact_id": entry["artifact_id"]})
            return entry
        except Exception:
            if role in self.evidence.policy.get("optional_roles", []):
                self.gaps.append({"role": role, "reason": "optional_artifact_rejected"})
                return None
            self.failed = True
            self.run.failed.set()
            return None

    @contextmanager
    def span(self, kind, attributes=None):
        parent = SPAN.get()
        identity = uuid.uuid4().hex
        token = SPAN.set(identity)
        parent_token = PARENT.set(parent)
        start = time.monotonic()
        self.event("span.started", {"kind": kind, "parent_span_id": parent, **(attributes or {})})
        try:
            yield self
        except BaseException as exc:
            self.event(
                "span.failed",
                {
                    "kind": kind,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "duration_seconds": time.monotonic() - start,
                },
            )
            raise
        else:
            self.event("span.completed", {"kind": kind, "duration_seconds": time.monotonic() - start})
        finally:
            SPAN.reset(token)
            PARENT.reset(parent_token)

    @property
    def artifact_directory(self):
        if not self.evidence.policy.get("allow_direct_artifacts", False):
            raise UserActionRequired("Direct artifact writes require an explicit project retention declaration.")
        path = safe_path(self.evidence.directory, "artifacts")
        path.mkdir(mode=0o700, exist_ok=True)
        return path


class EvidenceLogHandler(logging.Handler):
    def __init__(self, run):
        super().__init__()
        self.run = run
        self.seen = weakref.WeakSet()
        self.guard = threading.RLock()

    def emit(self, record):
        with self.guard:
            if record in self.seen:
                return
            self.seen.add(record)
        try:
            attributes = {
                "logger": record.name,
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
                "line": record.lineno,
                "extra": {key: value for key, value in record.__dict__.items() if key not in LOG_RECORD_FIELDS},
            }
            if record.exc_info:
                attributes["exception"] = {"type": record.exc_info[0].__name__, "message": str(record.exc_info[1])}
            observer = CURRENT.get()
            if observer:
                observer.event("log", attributes)
            else:
                self.run.event("unassociated_log", attributes)
        except Exception:
            self.run.failed.set()


@contextmanager
def capture_logs(run, policy):
    handler = EvidenceLogHandler(run)
    loggers = [logging.getLogger(name) for name in dict.fromkeys(policy.get("logging", []))]
    loguru_logger, sink = None, None
    try:
        for logger in loggers:
            logger.addHandler(handler)
        if policy.get("loguru"):
            try:
                from loguru import logger as loguru_logger
            except ImportError:
                raise UserActionRequired(
                    "Loguru was declared but is not installed in the project interpreter."
                ) from None

            def consume(message):
                record = message.record
                try:
                    attrs = {
                        "logger": record["name"],
                        "level": record["level"].name,
                        "message": record["message"],
                        "extra": record["extra"],
                    }
                    observer = CURRENT.get()
                    if observer:
                        observer.event("log", attrs)
                    else:
                        run.event("unassociated_log", attrs)
                except Exception:
                    run.failed.set()

            sink = loguru_logger.add(consume, format="{message}", diagnose=False, backtrace=False, enqueue=False)
        yield
    finally:
        for logger in loggers:
            if handler not in logger.handlers:
                run.manifest["gaps"].append("logging_handler_removed")
            logger.removeHandler(handler)
        handler.close()
        if sink is not None:
            try:
                loguru_logger.remove(sink)
            except ValueError:
                run.manifest["gaps"].append("loguru_sink_removed")


def write_log_view(item):
    from agent_tune_kit.results.evidence import read_events

    # Derive only from retained events; avoid re-reading arbitrary application objects.
    target = safe_path(item.directory, "app.log")
    import os

    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        cursor = 0
        while True:
            page = read_events(item.directory, after_seq=cursor, limit=1000)
            for event in page["events"]:
                if event["kind"] == "log":
                    handle.write(json_bytes(event["attributes"]) + b"\n")
            if not page["events"] or page["next_seq"] >= page["total"]:
                break
            cursor = page["next_seq"]
        handle.flush()
        os.fsync(handle.fileno())
    from agent_tune_kit.results.evidence import digest_file

    item.manifest["files"].append(
        {
            "artifact_id": "app-log",
            "path": "app.log",
            "role": "log-view",
            "media_type": "text/plain",
            "bytes": target.stat().st_size,
            "sha256": digest_file(target),
        }
    )
