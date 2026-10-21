"""
Diagnostics: one zip file that a customer can send to support, so that nobody has to ask for the log, then for the
settings, then for a screenshot of the history.

    summary.txt               version, PC, how the program runs, counters, health, open problems
    settings.json             the settings, with every password, token and header value left out
    history.csv               the last 500 files and where each went
    logs\\                    the program's log files, newest first, about 5 MB at most
    alerts.json               the problems that are open
    settings_changes.jsonl    the last 200 changes to the settings
    files.txt                 the names (not the contents) of the files in the send queue and the refused ones
    stations.txt              the copies of CNCBridge on this PC

Nothing in it is a password. Every text is scrubbed before it is written: the saved passwords and header values are
replaced wherever they appear (a log line can quote them), "Authorization:" values are hidden, and a web address
loses its query part, which is where keys are sent. Reading is read-only: the history is opened read-only, the
single-copy lock is never touched, and nothing in the data folder is changed.
"""
import csv
import io
import json
import os
import platform
import re
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from . import audit, background, config, permissions, stations
from .config import APP_NAME, SECRET_KEYS, SETTINGS_VERSION, VERSION
from .housekeeping import count_queued, count_rejected, count_waiting, disk_space, oldest_queued_age

LOG_CAP = 5 * 1024 * 1024
HISTORY_ROWS = 500
CHANGE_ROWS = 200
MIN_SECRET = 4  # shorter values are not replaced everywhere: they would damage ordinary words

URL_WITH_QUERY = re.compile(r"(https?://[^\s\"'<>?#]+)[?#][^\s\"'<>]*")
AUTHORIZATION = re.compile(r"(?i)(authorization\s*[:=]\s*(?:\w+\s+)?)\S+")


# ---------------------------------------------------------------- scrubbing
def secret_values(cfg):
    """Every value that must never appear in the zip, longest first so that a long one is not left half-replaced."""
    values = [cfg.get(key) for key in SECRET_KEYS] + [cfg.get("setup_password")]
    for line in (cfg.get("api_headers") or "").splitlines():
        if ":" in line:
            values.append(line.split(":", 1)[1].strip())  # header values often are keys
    return sorted({v for v in values if v and len(v) >= MIN_SECRET}, key=lambda v: (-len(v), v))


def redact(text, secrets):
    for value in secrets:
        text = text.replace(value, "***")
    text = AUTHORIZATION.sub(r"\1***", text)
    return URL_WITH_QUERY.sub(r"\1?...", text)


# ---------------------------------------------------------------- the parts
def _size(count):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            return f"{count:.0f} {unit}" if unit == "bytes" else f"{count:.1f} {unit}"
        count /= 1024


def settings_text(cfg):
    out = config.normalize_config(cfg)
    doc = {"secrets_set": {key: bool(out[key]) for key in SECRET_KEYS}, "setup_password_set": bool(out["setup_password"])}
    for key in SECRET_KEYS:
        out[key] = ""
    out["setup_password"] = ""
    out["api_headers"] = chr(10).join(line.split(":", 1)[0] + ": ***" if ":" in line else line
                                      for line in out["api_headers"].splitlines())
    for key in audit.URL_KEYS:
        out[key] = audit.redact_url(out[key])
    doc["settings"] = out
    return json.dumps(doc, indent=2, ensure_ascii=False)


def history_csv(data):
    db = data / "ledger.db"
    if not db.exists():
        return "No history yet." + chr(10)
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM files ORDER BY id DESC LIMIT ?", (HISTORY_ROWS,))]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        delivered = {}
        if "deliveries" in tables and rows:
            for r in con.execute(f"SELECT file_id, kind, status FROM deliveries WHERE file_id IN "
                                 f"({','.join('?' * len(rows))})", [row["id"] for row in rows]):
                delivered.setdefault(r["file_id"], {})[r["kind"]] = r["status"]
    finally:
        con.close()
    kinds = ["shared", "api", "ftp"] + sorted({k for d in delivered.values() for k in d} - {"shared", "api", "ftp"})
    for row in rows:  # histories from before the deliveries table keep the status in the old columns
        for kind in kinds:
            row[kind] = delivered.get(row["id"], {}).get(kind) or row.get(kind) or "off"
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=chr(10))
    columns = ["id", "processed_at", "source", "sha256", "size", "status", "output", *kinds, "message"]
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") for c in columns])
    return buffer.getvalue()


def changes_tail(data):
    try:
        lines = audit.log_path(data).read_text(encoding="utf-8").splitlines()
    except OSError:
        return "No changes recorded." + chr(10)
    return chr(10).join(lines[-CHANGE_ROWS:]) + chr(10)


def files_text(data):
    lines = ["Names only: the contents of these files are not included.", ""]
    for group in ("outbox", "rejected"):
        for folder in sorted((data / group).glob("*")) if (data / group).is_dir() else []:
            files = sorted(f for f in folder.iterdir() if f.is_file() and not f.name.startswith("."))
            lines.append(f"{group}{chr(92)}{folder.name}: {len(files)} file(s)")
            for f in files[:200]:
                stat = f.stat()
                lines.append(f"    {f.name}  {_size(stat.st_size)}  {datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M:%S}")
            if len(files) > 200:
                lines.append(f"    ... and {len(files) - 200} more")
    if len(lines) == 2:
        lines.append("The send queue is empty and nothing was refused.")
    return chr(10).join(lines) + chr(10)


def stations_text(data):
    lines = []
    for entry in stations.load():
        folder = entry["folder"]
        lines.append(f"{folder}  {stations.run_state(folder)}")
    return (chr(10).join(lines) if lines else "No other copies are listed.") + chr(10)


def log_files(data):
    """[(zip name, text)] for the log files, newest first, about LOG_CAP bytes together."""
    folder = data / "logs"
    found = sorted((f for f in folder.glob("*.log*") if f.is_file()), key=lambda f: f.stat().st_mtime,
                   reverse=True) if folder.is_dir() else []
    out, left = [], LOG_CAP
    for f in found:
        if left <= 0:
            break
        raw = f.read_bytes()
        cut = len(raw) > left
        text = raw[-left:].decode("utf-8", errors="replace")
        if cut:
            text = "(the older part of this file is left out)" + chr(10) + text
        out.append((f"logs/{f.name}", text))
        left -= min(len(raw), left)
    return out


def summary_text(data, cfg, now, notes, names):
    who = audit.who()
    lines = [f"{APP_NAME} diagnostics", "",
             f"Created:      {now:%Y-%m-%d %H:%M:%S} by {who['user']} on {who['computer']}"
             f"{' (administrator)' if who['elevated'] else ''}",
             f"Version:      {VERSION} (settings version {SETTINGS_VERSION})",
             f"Program:      {sys.executable}{' (the built program)' if getattr(sys, 'frozen', False) else ' (from source)'}",
             f"Python:       {platform.python_version()}",
             f"Windows:      {platform.platform()}", "",
             f"Station:      {cfg['station_name'] or '(no name yet)'}",
             f"Data folder:  {data}",
             f"Running as:   {stations.run_state(data)}"]
    try:
        lines.append(f"Background task: {'set up' if background.task_exists(data) else 'not set up'}")
    except Exception as e:
        lines.append(f"Background task: unknown ({e})")
    lines += [f"Look:         {cfg['theme']}, {cfg['color_mode']}, language {cfg['language']}",
              f"Protection:   {'on' if cfg['protect_settings'] else 'off'}, settings file "
              f"{ {True: 'protected', False: 'not protected', None: 'unknown'}[permissions.settings_protected(data)] }",
              f"Heartbeat:    {'on, every ' + format(cfg['heartbeat_minutes'], 'g') + ' min' if cfg['heartbeat_enabled'] else 'off'}",
              f"Destinations: " + (", ".join(name for name, on in (("shared folder", cfg["shared_enabled"]),
                                                                    ("API link", cfg["api_enabled"]),
                                                                    ("FTP server", cfg["ftp_enabled"])) if on) or "none turned on"),
              ""]
    counts, last = stations.read_ledger(data)
    lines += ["Counters", f"  Done: {counts.get('ok', 0)}   Could not be read: {counts.get('failed', 0)}   "
                           f"Duplicates: {counts.get('duplicate', 0)}",
              f"  Waiting to be read: {count_waiting(cfg)}   Waiting to be sent: {sum(count_queued(data).values())}   "
              f"Refused: {count_rejected(data)}",
              f"  Last file handled: {last or 'none yet'}"]
    age = oldest_queued_age(data)
    lines.append(f"  Oldest waiting to be sent: {_age(age) if age is not None else 'nothing waiting'}")
    space = disk_space(cfg["backup_folder"]) if cfg["backup_folder"] else None
    lines.append(f"  Back-up drive: {_size(space[0]) + ' free of ' + _size(space[1]) if space else 'unknown'}")
    lines.append(f"  Machine folder reachable: "
                 f"{'yes' if cfg['source_folder'] and Path(cfg['source_folder']).is_dir() else 'no'}")
    opened = alerts_open(data)
    lines += ["", "Open problems"] + ([f"  {name}: {item.get('text', '')} (since {item.get('since', '?')})"
                                        for name, item in opened.items()] or ["  none"])
    changes = audit.read(data, 1)
    lines += ["", "Last settings change: " + (f"{changes[0]['time']} by {changes[0]['user']} ({changes[0]['how']})"
                                               if changes else "none recorded")]
    if notes:
        lines += ["", "Parts that could not be collected"] + [f"  {note}" for note in notes]
    lines += ["", "Contents of this file"] + [f"  {name}" for name in sorted(names)]
    lines += ["", "No password or key is in this file: they are replaced by *** and web addresses lose their query part."]
    return chr(10).join(lines) + chr(10)


def alerts_open(data):
    from . import alerts
    return alerts.read_state(data)


def _age(seconds):
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:.1f} days"


# ---------------------------------------------------------------- the zip
def default_name(cfg, now=None):
    """'CNCBridge diagnostics Line 7 2026-10-21.zip'"""
    now = now or datetime.now()
    station = re.sub(r"[^A-Za-z0-9._ -]+", "_", cfg.get("station_name") or "").strip()
    return f"{APP_NAME} diagnostics {station + ' ' if station else ''}{now:%Y-%m-%d}.zip"


def build(data_dir, zip_path, cfg=None, now=None):
    """Write the zip. Returns the names it holds. One part that cannot be collected does not stop the others."""
    data, now, notes, parts = Path(data_dir), now or datetime.now(), [], {}
    if cfg is None:
        try:
            cfg = config.load_config(data / "config.json")
        except Exception as e:
            notes.append(f"settings: {e}")
            cfg = config.normalize_config({})
    secrets = secret_values(cfg)

    def part(name, make):
        try:
            parts[name] = redact(make(), secrets)
        except Exception as e:
            notes.append(f"{name}: {e}")

    part("settings.json", lambda: settings_text(cfg))
    part("history.csv", lambda: history_csv(data))
    part("settings_changes.jsonl", lambda: changes_tail(data))
    part("files.txt", lambda: files_text(data))
    part("stations.txt", lambda: stations_text(data))
    if (data / "alerts.json").exists():
        part("alerts.json", lambda: (data / "alerts.json").read_text(encoding="utf-8"))
    try:
        for name, text in log_files(data):
            parts[name] = redact(text, secrets)
    except Exception as e:
        notes.append(f"logs: {e}")
    part("summary.txt", lambda: summary_text(data, cfg, now, notes, [*parts, "summary.txt"]))
    if "summary.txt" not in parts:  # even then the person gets a file that says what went wrong
        parts["summary.txt"] = "The summary could not be written:" + chr(10) + chr(10).join(notes) + chr(10)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("summary.txt", *sorted(n for n in parts if n != "summary.txt")):
            archive.writestr(name, parts[name])
    return list(parts)
