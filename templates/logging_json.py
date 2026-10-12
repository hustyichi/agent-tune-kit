"""Optional stdlib logging outlet for an ATK Python attempt; no ATK dependency."""

import json
import logging
import sys
from datetime import UTC, datetime


class JsonHandler(logging.Handler):
    def emit(self, record):
        fields = {k: v for k, v in record.__dict__.items() if k not in logging.makeLogRecord({}).__dict__}
        event = {
            "message": record.getMessage(),
            "level": record.levelname,
            "logger": record.name,
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "extra": fields,
            "file": {"name": record.filename, "path": record.pathname},
            "line": record.lineno,
            "thread": {"id": record.thread, "name": record.threadName},
            "process": {"id": record.process, "name": record.processName},
        }
        if record.exc_info:
            event["extra"]["error_type"] = record.exc_info[0].__name__
        sys.stderr.write("ATK_LOG " + json.dumps({"atk_log": event}, ensure_ascii=False, default=str) + "\n")
        sys.stderr.flush()


def install_logging(names=("",), level="INFO"):
    """Attach after project logging setup; declared logger levels remain project-owned."""
    handlers = []
    for name in names:
        logger = logging.getLogger(name)
        if any(isinstance(handler, JsonHandler) for handler in logger.handlers):
            continue
        handler = JsonHandler(level)
        logger.addHandler(handler)
        handlers.append((logger, handler))
    return handlers
