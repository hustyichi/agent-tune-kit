import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_text_writer(path, *, newline=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_text_atomic(path, content):
    with atomic_text_writer(path) as handle:
        handle.write(content)


def read_text_bounded(path, max_bytes):
    with Path(path).open("rb") as handle:
        data = handle.read(max_bytes + 1)
    return data[:max_bytes].decode("utf-8"), len(data) > max_bytes
