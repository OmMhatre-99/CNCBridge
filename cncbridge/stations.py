"""
The CNCBridge copies on this PC, and a read-only look at each.

One machine folder per copy and per data folder stays the rule (two copies for two machine lines). So that
they can be seen together, every copy writes its data folder into a small list, stations.json, kept beside the
default data folder. The Stations window reads that list and shows the state of each copy: it never writes to
another copy's data, and it never takes that copy's lock.
"""
import contextlib
import ctypes
import json
import msvcrt
import os
import sqlite3
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from . import alerts, background, config, engine

REGISTRY_NAME = "stations.json"
STILL_ACTIVE = 259


def registry_path():
    """Where the list is kept: CNCBRIDGE_STATIONS (for tests and special set-ups), else beside the default data."""
    override = os.environ.get("CNCBRIDGE_STATIONS")
    return Path(override) if override else config.default_data_dir() / REGISTRY_NAME


def _key(folder):
    return str(Path(folder).resolve()).casefold()


@contextlib.contextmanager
def _locked(path):
    """Two copies starting together must not overwrite each other's entry: take a lock on a side file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(str(path) + ".lock", "a+")
    got = False
    try:
        for _ in range(60):
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                got = True
                break
            except OSError:
                time.sleep(0.05)
        yield
    finally:
        if got:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()


def load(path=None):
    """The listed copies: [{"folder": ..., "added": ...}]. A missing or damaged file is an empty list."""
    try:
        data = json.loads(Path(path or registry_path()).read_text(encoding="utf-8"))
        return [e for e in data.get("stations", []) if isinstance(e, dict) and e.get("folder")]
    except (OSError, ValueError, AttributeError):
        return []


def _save(entries, path):
    engine.atomic_write(path, json.dumps({"stations": entries}, indent=2).encode("utf-8"))


def register(data_dir, path=None):
    """Add this copy's data folder to the list, once. Returns True when it was added."""
    path = Path(path or registry_path())
    with _locked(path):
        entries = load(path)
        if any(_key(e["folder"]) == _key(data_dir) for e in entries):
            return False
        entries.append({"folder": str(Path(data_dir).resolve()), "added": datetime.now().isoformat(timespec="seconds")})
        _save(entries, path)
        return True


def unregister(data_dir, path=None):
    """Take a data folder off the list. Returns True when it was there."""
    path = Path(path or registry_path())
    with _locked(path):
        entries = load(path)
        kept = [e for e in entries if _key(e["folder"]) != _key(data_dir)]
        if len(kept) == len(entries):
            return False
        _save(kept, path)
        return True


def launch_command(data_dir):
    """The command that opens CNCBridge on a data folder."""
    base = config.program_dir()
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        command = [str(pythonw if pythonw.exists() else python), str(base / "CNCBridge.pyw")]
    return command + ["--data", str(data_dir)]


# ---------------------------------------------------------------- reading one copy
def _alive(pid):
    try:
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = wintypes.DWORD()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == STILL_ACTIVE
    except (AttributeError, OSError, ValueError, TypeError):
        return False


def run_state(folder):
    """'window', 'background' or 'stopped': who works on this data folder right now (read from its marker file,
    so that looking never touches the lock the copy itself needs)."""
    try:
        marker = json.loads((Path(folder) / background.INSTANCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "stopped"
    if marker.get("pid") and _alive(marker["pid"]):
        return marker.get("mode", "window")
    return "stopped"


def read_ledger(folder):
    """(counts by status, time of the latest file) from a copy's history, opened read-only."""
    db = Path(folder) / "ledger.db"
    if not db.exists():
        return {}, None
    try:
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        try:
            counts = dict(con.execute("SELECT status, COUNT(*) FROM files GROUP BY status").fetchall())
            row = con.execute("SELECT processed_at FROM files ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()
        return counts, row[0] if row else None
    except sqlite3.Error:
        return {}, None


def snapshot(folder, this_folder=None, now=None):
    """Everything the Stations window shows about one copy. Never raises: a copy that cannot be read says so."""
    folder = Path(folder)
    now = time.time() if now is None else now
    row = {"folder": str(folder), "this": bool(this_folder) and _key(folder) == _key(this_folder),
           "exists": folder.is_dir(), "name": "", "source_folder": "", "state": "missing", "processed": 0,
           "failed": 0, "waiting": 0, "queued": 0, "rejected": 0, "last_file": None, "last_file_age": None,
           "problem": None, "backup_free": None, "disk_level": None}
    if not row["exists"]:
        return row
    try:
        cfg = config.load_config(folder / "config.json")
    except (OSError, ValueError):
        cfg = config.normalize_config({})
    row["name"], row["source_folder"] = cfg["station_name"], cfg["source_folder"]
    row["state"] = run_state(folder)
    counts, last = read_ledger(folder)
    row["processed"], row["failed"] = counts.get("ok", 0), counts.get("failed", 0)
    row["waiting"] = engine.count_waiting(cfg) if cfg["source_folder"] else 0
    row["queued"] = sum(engine.count_queued(folder).values())
    row["rejected"] = engine.count_rejected(folder)
    row["last_file"] = last
    if last:
        try:
            row["last_file_age"] = max(0.0, now - datetime.fromisoformat(last).timestamp())
        except ValueError:
            pass
    problems = alerts.read_state(folder)
    if problems:
        row["problem"] = next(iter(problems.values())).get("text") or next(iter(problems))
    space = engine.disk_space(cfg["backup_folder"]) if cfg["backup_folder"] else None
    if space:
        row["backup_free"], row["disk_level"] = space[0], engine.disk_level(*space)
    return row


def snapshots(this_folder=None, path=None):
    """One row per listed copy, plus this one if it is not listed yet."""
    entries = load(path)
    folders = [e["folder"] for e in entries]
    if this_folder and all(_key(f) != _key(this_folder) for f in folders):
        folders.insert(0, str(this_folder))
    return [snapshot(f, this_folder) for f in folders]
