"""Looking after the files around the processing: the queues, the back-up drive, second chances, clean-up."""

import hashlib
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from .csvread import decode, machine_files
from .delivery import DESTINATIONS, atomic_write, destination_name

log = logging.getLogger("cncbridge")


def _unique(dest_dir, name):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    p, n = dest_dir / name, 1
    while p.exists():
        p = dest_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ready(path, stable_seconds):
    """True once the machine has finished with the file: untouched for a while and not held open."""
    try:
        # with 0 the age test is skipped: file times can be a few ms ahead of the clock right after a write
        if stable_seconds and time.time() - path.stat().st_mtime < stable_seconds:
            return False
        os.rename(path, path)  # fails on Windows while another program still has the file open
        return True
    except OSError:
        return False


def has_marker(path, marker, encoding="auto"):
    """True when the file contains the text the machine writes last (ignoring case)."""
    try:
        text = decode(Path(path).read_bytes(), encoding)
    except OSError:
        return False
    return marker.lower() in text.lower()


def _visible(folder):
    return [f for f in Path(folder).glob("*") if f.is_file() and not f.name.startswith(".")]


def count_waiting(cfg):
    try:
        return len(machine_files(cfg["source_folder"], cfg["file_pattern"]))
    except OSError:
        return 0


def count_queued(data_dir):
    return {k: len(_visible(Path(data_dir) / "outbox" / k)) for k in DESTINATIONS}


def count_rejected(data_dir):
    return sum(len(_visible(Path(data_dir) / "rejected" / k)) for k in DESTINATIONS)


LOW_DISK_BYTES = 2 * 1024 ** 3       # the back-up drive is "low" under this, or under LOW_DISK_FRACTION of its size


LOW_DISK_FRACTION = 0.05


CRITICAL_DISK_BYTES = 1024 ** 3      # ... and "critical" under this


def disk_space(folder):
    """(free, total) bytes of the drive holding this folder, or its nearest existing parent; None if unknown."""
    path = Path(folder) if folder else None
    try:
        while path is not None:
            if path.exists():
                usage = shutil.disk_usage(path)
                return usage.free, usage.total
            if path.parent == path:
                return None
            path = path.parent
    except OSError:
        pass
    return None


def disk_level(free, total):
    """'ok', 'low' or 'critical'."""
    if free < CRITICAL_DISK_BYTES:
        return "critical"
    if free < LOW_DISK_BYTES or (total and free / total < LOW_DISK_FRACTION):
        return "low"
    return "ok"


def oldest_queued_age(data_dir, now=None):
    """Seconds since the oldest file waiting in any send queue was put there; None when the queues are empty."""
    now = time.time() if now is None else now
    ages = []
    for kind in DESTINATIONS:
        for f in _visible(Path(data_dir) / "outbox" / kind):
            try:
                ages.append(now - f.stat().st_mtime)
            except OSError:
                pass
    return max(ages) if ages else None


def health(cfg, data_dir, ledger, now=None):
    """What an operator wants to know at a glance: is it alive, is anything stuck, is there room to keep going."""
    now = time.time() if now is None else now
    last = ledger.last_processed()
    last_age = None
    if last:
        try:
            last_age = max(0.0, now - datetime.fromisoformat(last).timestamp())
        except ValueError:
            pass
    space = disk_space(cfg["backup_folder"]) if cfg.get("backup_folder") else None
    return {
        "last_file": last, "last_file_age": last_age,
        "oldest_queued_age": oldest_queued_age(data_dir, now),
        "backup_free": space[0] if space else None, "backup_total": space[1] if space else None,
        "disk_level": disk_level(*space) if space else None,
        "source_reachable": Path(cfg["source_folder"]).is_dir() if cfg.get("source_folder") else None}


ALLOW_ONCE = "allow_once.json"  # fingerprints that may pass the duplicate check one more time


def _allowed(data_dir):
    try:
        return json.loads((Path(data_dir) / ALLOW_ONCE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def allow_once(data_dir, sha):
    shas = _allowed(data_dir)
    if sha not in shas:
        atomic_write(Path(data_dir) / ALLOW_ONCE, json.dumps(shas + [sha]))


def take_allowance(data_dir, sha):
    """True (once) when this content was sent back on purpose and may be processed again."""
    shas = _allowed(data_dir)
    if sha not in shas:
        return False
    shas.remove(sha)
    atomic_write(Path(data_dir) / ALLOW_ONCE, json.dumps(shas))
    return True


def _kept_copy(folder, name):
    """The file kept under this name in a back-up folder, also when it was stored as name_1.csv etc."""
    stem, suffix = Path(name).stem, Path(name).suffix
    pattern = re.compile(rf"^{re.escape(stem)}(_\d+)?{re.escape(suffix)}$", re.I)
    found = [f for f in Path(folder).glob(f"{stem}*{suffix}") if pattern.match(f.name)] if Path(folder).is_dir() else []
    return max(found, key=lambda f: f.stat().st_mtime) if found else None


def resend_rejected(data_dir, row, ledger):
    """Put an output file that the receiving site refused back in the send queue. Returns the destinations."""
    sent = []
    for kind in DESTINATIONS:
        refused = _kept_copy(Path(data_dir) / "rejected" / kind, row["output"] or "")
        if row["output"] and refused:
            queue = Path(data_dir) / "outbox" / kind
            queue.mkdir(parents=True, exist_ok=True)
            shutil.move(str(refused), str(queue / row["output"]))
            ledger.set_delivery(row["output"], kind, "queued")
            sent.append(kind)
    if sent:
        ledger.note(row["id"], f"queued to send again at {datetime.now():%H:%M}")
        log.info("%s queued to send again (%s)", row["output"], ", ".join(destination_name(k) for k in sent))
    return sent


def reprocess(cfg, data_dir, row, ledger):
    """
    Put a machine file that was set aside (failed or duplicate) back in the machine folder, so it is
    processed again. A duplicate is let through the duplicate check once. Returns the new path or None.
    """
    folder = Path(cfg["backup_folder"]) / ("_failed" if row["status"] == "failed" else "_duplicates")
    kept = _kept_copy(folder, row["source"])
    if not kept or not cfg["source_folder"]:
        return None
    if row["status"] == "duplicate" and row["sha256"]:
        allow_once(data_dir, row["sha256"])
    target = _unique(Path(cfg["source_folder"]), row["source"])
    shutil.move(str(kept), str(target))
    ledger.note(row["id"], f"sent back to the machine folder at {datetime.now():%H:%M}")
    log.info("%s sent back to the machine folder to be processed again", row["source"])
    return target


DAY_FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_up(cfg, ledger, now=None):
    """
    Delete what is older than the configured number of days (0 = keep everything):
    back-up day folders (judged by the date in their name), old files in back-up\\_failed and
    _duplicates, and old history rows. Only ever deletes inside the back-up folder; the send queue and
    refused files are never touched. Returns (back-up items deleted, history rows deleted).
    """
    now = now or datetime.now()
    removed = forgotten = 0
    days, backup = cfg["keep_backup_days"], Path(cfg["backup_folder"]) if cfg["backup_folder"] else None
    if days and backup and backup.is_dir():
        cutoff = now - timedelta(days=days)
        for folder in backup.iterdir():
            if folder.is_dir() and DAY_FOLDER.match(folder.name):
                try:
                    if datetime.strptime(folder.name, "%Y-%m-%d") < cutoff.replace(hour=0, minute=0, second=0,
                                                                                   microsecond=0):
                        shutil.rmtree(folder)
                        removed += 1
                except (ValueError, OSError) as e:
                    log.warning("Could not delete the old back-up folder %s: %s", folder, e)
        for name in ("_failed", "_duplicates"):
            for f in (backup / name).glob("*") if (backup / name).is_dir() else []:
                try:
                    if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                        f.unlink()
                        removed += 1
                except OSError as e:
                    log.warning("Could not delete the old file %s: %s", f, e)
    if cfg["keep_history_days"]:
        forgotten = ledger.forget_before(now - timedelta(days=cfg["keep_history_days"]))
    if removed or forgotten:
        log.info("Clean-up: %d old back-up folder(s) or file(s) and %d history row(s) deleted", removed, forgotten)
    return removed, forgotten
