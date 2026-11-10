import shutil
import subprocess
from pathlib import Path

import pytest


def test_event_preview_uses_raw_fields_and_explicit_failure_states():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the dependency-free Event list check")
    subprocess.run([node, str(Path(__file__).with_name("trace-event-list.cjs"))], check=True)
