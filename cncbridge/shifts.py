"""
Shift changes: when a shift starts, the operator is asked to confirm (or change) the typed values such as
Operator and Work order, so a value from the last shift is not written into the next shift's files.

Processing never waits for the confirmation. When the answer is still missing, the window keeps a banner
up. The time of the last confirmation is kept in shift.json in the data folder, so a restart does not
forget an unanswered shift change and does not ask twice for an answered one.
"""
import json
import os
import re
from datetime import datetime, time, timedelta
from pathlib import Path

STATE_FILE = "shift.json"
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_times(text, strict=False):
    """'06:00, 14:00 22:00' -> [time(6, 0), time(14, 0), time(22, 0)], sorted, without repeats."""
    found = set()
    for token in re.split(r"[,;\s]+", str(text or "").strip()):
        if not token:
            continue
        m = _TIME.match(token)
        if not m:
            if strict:
                raise ValueError(f"'{token}' is not a time. Write the shift times like 06:00, 14:00, 22:00.")
            continue
        found.add(time(int(m.group(1)), int(m.group(2))))
    return sorted(found)


def latest_boundary(times, now):
    """The latest shift start at or before `now`: today's, or yesterday's last one. None without shift times."""
    if not times:
        return None
    today = [datetime.combine(now.date(), t) for t in times]
    started = [b for b in today if b <= now]
    return max(started) if started else datetime.combine(now.date() - timedelta(days=1), times[-1])


def pending(times, confirmed_at, now):
    """The shift start that still waits for a confirmation, or None."""
    boundary = latest_boundary(times, now)
    if boundary is not None and (confirmed_at is None or confirmed_at < boundary):
        return boundary
    return None


def read_confirmed(data_dir):
    """When the typed values were last confirmed, or None."""
    try:
        return datetime.fromisoformat(json.loads((Path(data_dir) / STATE_FILE).read_text(encoding="utf-8"))
                                      ["confirmed_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_confirmed(data_dir, when):
    path = Path(data_dir) / STATE_FILE
    tmp = path.with_name(STATE_FILE + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"confirmed_at": when.isoformat(timespec="seconds")}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # the confirmation then only counts for this session
