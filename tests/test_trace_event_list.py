import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", ["trace-event-list.cjs", "trace-fragments.cjs"])
def test_event_list(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the dependency-free Event list check")
    subprocess.run([node, str(Path(__file__).with_name(script))], check=True)
