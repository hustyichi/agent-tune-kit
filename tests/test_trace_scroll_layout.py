"""Browser regression on generated HTML, with no private archive fixtures."""

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_tune_kit.traces import records, store
from agent_tune_kit.visualize.trace import generate_trace


def test_trace_scroll_layout(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for browser layout checks")
    probe = subprocess.run(
        [
            node,
            "-e",
            'const {chromium} = require("playwright"); console.log(process.env.CHROME_BIN || chromium.executablePath())',
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode or not Path(probe.stdout.strip()).is_file():
        pytest.skip("Install Playwright and Chromium (or set NODE_PATH and CHROME_BIN) for browser layout checks")

    session_id = "SESSION-scroll-layout"
    source = {"type": "local", "instance": "synthetic", "project": "layout", "environment": "test"}
    start = datetime(2026, 1, 1, tzinfo=UTC)
    files = {}
    for track in range(3):
        observations = [
            {
                "id": f"OBS-{track}-{index:03}",
                "type": "EVENT",
                "name": f"Event {track}-{index}",
                "startTime": (start + timedelta(seconds=track * 100 + index)).isoformat(),
                "input": "Synthetic input",
                "output": "Synthetic output",
            }
            for index in range(100)
        ]
        files[f"trace-{track}.json"] = json.dumps(
            {"trace": {"id": f"TRACE-{track}", "sessionId": session_id, "observations": observations}}
        )
    saved = store.create_capture(
        tmp_path, session_id, source, files, scope={}, completeness={}, capture_id="CAP-layout"
    )
    session = Path(saved["session_dir"])

    def ref(index):
        return store.evidence_ref(session, "captures/CAP-layout/raw/trace-0.json", f"/trace/observations/{index}")

    for number in range(1, 9):
        records.save_issue(
            session,
            {
                "id": f"ISSUE-{number:03}",
                "title": f"Synthetic issue {number}",
                "description": "Exercise independent scrolling and fragment navigation.",
                "segments": [
                    {
                        "summary": f"Evidence {index}",
                        "scope": {"from": ref(index), "to": ref(index + 2)},
                        "evidence": [ref(index)],
                    }
                    for index in (10, 40)
                ],
            },
        )
    generated = generate_trace(tmp_path, session_id=session_id, open_browser=False)
    subprocess.run(
        [node, str(Path(__file__).with_name("trace-scroll-layout.cjs")), generated["output_path"]],
        check=True,
        timeout=90,
    )
