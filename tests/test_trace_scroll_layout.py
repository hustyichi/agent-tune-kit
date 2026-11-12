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
    source_file = tmp_path / "reader.py"
    source_file.write_text("def read(root):\n    return root\n", encoding="utf-8")
    plan = """## 问题描述
Relative reads use the wrong directory.
## 问题根因
The reader ignores the selected root.
## 最小修复方案
Bind the selected root.
### 详细实施方案
| 文件与位置 | 具体修改 | 验证 |
| --- | --- | --- |
| [reader.py / read](reader.py#L1) | Resolve paths from the selected root. | Check reads from another cwd. |

```python
return root / requested_path
```

Do not change the public signature.
"""
    records.save_diagnosis(session, "ISSUE-001", plan)
    # Both language variants must remain expanded beneath the original three headings.
    records.save_diagnosis(
        session,
        "ISSUE-002",
        plan.replace("问题描述", "Issue description")
        .replace("问题根因", "Root cause")
        .replace("最小修复方案", "Minimal fix")
        .replace("详细实施方案", "Detailed implementation plan"),
    )
    generated = generate_trace(tmp_path, session_id=session_id, open_browser=False)
    subprocess.run(
        [node, str(Path(__file__).with_name("trace-scroll-layout.cjs")), generated["output_path"]],
        check=True,
        timeout=90,
    )
