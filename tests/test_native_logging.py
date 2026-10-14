"""Native outlets keep project sinks and source records intact."""

import importlib.util
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def helper():
    path = Path(__file__).parents[1] / "src/agent_tune_kit/logging_capture.py"
    assert path.is_file(), "ATK must provide additive native logging outlets"
    spec = importlib.util.spec_from_file_location("logging_capture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stdlib_outlet_preserves_project_handler_and_cleans_up_after_error(tmp_path):
    capture = helper()
    logger = logging.getLogger("native-test.application")
    original = logging.StreamHandler(io.StringIO())
    logger.addHandler(original)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with (
            pytest.raises(RuntimeError, match="business"),
            capture.capture_logging(
                tmp_path / "logs/service.log", [logger.name], logging.Formatter("%(levelname)s %(message)s")
            ),
        ):
            with ThreadPoolExecutor() as pool:
                pool.submit(logger.info, "thread api_key=DO_NOT_STORE").result()
            raise RuntimeError("business")
        assert logger.handlers == [original]
        assert "thread" in original.stream.getvalue()
        assert (tmp_path / "logs/service.log").read_text() == "INFO thread api_key=[REDACTED]\n"
        logger.info("after")
        assert "after" not in (tmp_path / "logs/service.log").read_text()
    finally:
        logger.removeHandler(original)
        logger.setLevel(previous_level)


def test_loguru_outlet_keeps_native_schema_original_sink_and_extras(tmp_path):
    capture = helper()
    from loguru import logger

    original = io.StringIO()
    sink = logger.add(original, format="{message}")
    try:
        with capture.capture_loguru(tmp_path / "logs/service.jsonl", logger):

            def work():
                logger.bind(run_id="run-a", custom={"detail": 42}, api_key="DO_NOT_STORE").info("done")

            with ThreadPoolExecutor() as pool:
                pool.submit(work).result()
        logger.info("after")
        assert "done" in original.getvalue() and "after" in original.getvalue()
        rows = [json.loads(line) for line in (tmp_path / "logs/service.jsonl").read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["record"]["extra"] == {"run_id": "run-a", "custom": {"detail": 42}, "api_key": "[REDACTED]"}
        assert rows[0]["record"]["file"]["name"] == Path(__file__).name
        assert rows[0]["record"]["line"] > 0
        assert "text" in rows[0] and "thread" in rows[0]["record"]
        assert "DO_NOT_STORE" not in (tmp_path / "logs/service.jsonl").read_text()
    finally:
        logger.remove(sink)


def test_native_outlet_does_not_overwrite_a_previous_execution(tmp_path):
    capture = helper()
    path = tmp_path / "service.log"
    path.write_text("previous")
    with (
        pytest.raises(FileExistsError),
        capture.capture_logging(path, ["native-test"], logging.Formatter("%(message)s")),
    ):
        pass
    assert path.read_text() == "previous"


def test_logging_bridge_is_captured_once_and_json_text_secrets_are_scrubbed(tmp_path):
    capture = helper()
    from loguru import logger

    class Bridge(logging.Handler):
        def emit(self, record):
            logger.bind(std_logger=record.name).info(record.getMessage())

    standard = logging.getLogger("native-bridge")
    bridge = Bridge()
    original_level = standard.level
    standard.setLevel(logging.INFO)
    standard.addHandler(bridge)
    try:
        with capture.capture_loguru(tmp_path / "service.jsonl", logger, format="{message} | {extra}"):
            standard.info("bridged")
            logger.bind(api_key="DO_NOT_STORE").info("secret fields")
        rows = [json.loads(line) for line in (tmp_path / "service.jsonl").read_text().splitlines()]
        assert [r["record"]["message"] for r in rows] == ["bridged", "secret fields"]
        assert "DO_NOT_STORE" not in (tmp_path / "service.jsonl").read_text()
    finally:
        standard.removeHandler(bridge)
        standard.setLevel(original_level)
