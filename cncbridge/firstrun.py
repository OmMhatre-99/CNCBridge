"""
Whether the first-start checklist has done its job: a small marker file in the data folder.

It is a file of its own, not a setting, so that an operator without the right to change the settings can still
start monitoring for the first time, and so that moving the data folder to another PC takes the marker along.
"""
import json
from datetime import datetime
from pathlib import Path

FILE = "first_start.json"


def is_done(data_dir):
    return (Path(data_dir) / FILE).exists()


def mark_done(data_dir):
    """Monitoring was started (or the person closed the checklist): it is not shown again by itself."""
    path = Path(data_dir) / FILE
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"done": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    except OSError:
        pass  # the checklist then shows again next time; nothing depends on it
