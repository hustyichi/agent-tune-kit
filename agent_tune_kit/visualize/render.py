import json
import re
import webbrowser
from pathlib import Path
from typing import Any


def safe_json_for_html(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def neutralize_script_close(text: str) -> str:
    """Prevent any literal </script in inlined CSS/JS from terminating the host script."""
    return re.sub(r"</(script)", r"<\\/\1", text, flags=re.IGNORECASE)


def open_in_browser(output_path: Path) -> tuple[bool, str]:
    try:
        url = output_path.resolve().as_uri()
    except (OSError, ValueError) as exc:
        return False, f"could not resolve file URI: {exc}"
    try:
        opened = webbrowser.open(url, new=2)
    except webbrowser.Error as exc:
        return False, f"webbrowser raised: {exc}"
    if not opened:
        return False, "no controlling browser available"
    return True, url
