"""CNCBridge settings: defaults, load/save, the data folder, and API token protection (Windows DPAPI)."""
import base64
import ctypes
import hashlib
import hmac
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

APP_NAME = "CNCBridge"
VERSION = "1.1.0"

MAX_SELECTIONS = 10
MAX_MANUAL = 2
FORMATS = ("json", "csv", "txt")
API_BODIES = ("raw", "multipart")
BACKUPS_KEPT = 20  # copies of the settings file kept in <data folder>\backups

DEFAULT_CONFIG = {
    "station_name": "",
    "source_folder": "",                  # where the machine drops its CSV files
    "file_pattern": "*.csv",
    "backup_folder": "",                  # used source files are moved here (dated sub-folders)
    "output_format": "json",              # json | csv | txt
    "selections": [{"name": "", "range": "", "header": True} for _ in range(MAX_SELECTIONS)],
    "manual_inputs": [{"name": "", "value": ""} for _ in range(MAX_MANUAL)],
    "shared_enabled": False,
    "shared_folder": "",                  # local or UNC path, e.g. \\MES-PC\inbox
    "api_enabled": False,
    "api_url": "",
    "api_token": "",                      # sent as "Authorization: Bearer <token>"; encrypted on disk
    "api_headers": "",                    # extra headers, one "Name: value" per line
    "api_body": "raw",                    # raw | multipart
    "api_timeout": 20,
    "skip_duplicates": True,              # identical file content is never sent twice
    "encoding": "auto",                   # auto | utf-8 | cp1252 | utf-16 | shift_jis | ...
    "delimiter": "auto",                  # auto | , | ; | tab | |
    "poll_seconds": 3,
    "stable_seconds": 2,                  # file must be untouched this long before it is picked up
    "complete_marker": "",                # text the machine writes last (e.g. End_Time); empty = idle time only
    "autostart": False,                   # start monitoring when the program opens
    # output switches: all off, so the files stay exactly as in version 1.0
    "json_numbers": False,                # JSON: write 12 and 40.53 as numbers, not as text
    "csv_flat": False,                    # CSV: one plain table, one row per record
    "time_zone": False,                   # processed_at carries the PC's time zone, e.g. +05:30
    "rename_columns": "",                 # one "Old=New" per line, applied to the column names of every block
    "setup_password": "",                 # hash of the password that locks the setup tabs; empty = no password
    "shift_times": "",                    # e.g. "06:00, 14:00, 22:00": ask the operator to confirm the typed values
    "keep_backup_days": 0,                # delete back-up files older than this; 0 = keep them all
    "keep_history_days": 0,               # delete history rows older than this; 0 = keep them all
    # alerts: a problem that lasts alert_after_minutes, or a file set aside, is announced
    "alert_after_minutes": 10,
    "alert_notify": True,                 # Windows notification on this PC
    "alert_webhook_enabled": False,
    "alert_webhook_url": "",
    "alert_email_enabled": False,
    "alert_email_server": "",
    "alert_email_port": 587,
    "alert_email_starttls": True,
    "alert_email_user": "",
    "alert_email_password": "",           # encrypted on disk, like the API token
    "alert_email_from": "",
    "alert_email_to": "",                 # one or more addresses, separated by commas
}
SECRET_KEYS = ("api_token", "alert_email_password")  # encrypted on disk, never exported

log = logging.getLogger("cncbridge")


def _number(value, default, low):
    try:
        return max(low, float(value))
    except (TypeError, ValueError):
        return default


def normalize_config(cfg):
    """Fill in defaults and force every setting into its expected shape."""
    base = json.loads(json.dumps(DEFAULT_CONFIG))
    base.update({k: v for k, v in (cfg or {}).items() if k in base})

    sels = [s for s in base["selections"] if isinstance(s, dict)]
    sels += [{} for _ in range(MAX_SELECTIONS)]
    base["selections"] = [{"name": str(s.get("name", "")).strip(), "range": str(s.get("range", "")).strip(),
                           "header": bool(s.get("header", True))} for s in sels[:MAX_SELECTIONS]]
    mans = [m for m in base["manual_inputs"] if isinstance(m, dict)]
    mans += [{} for _ in range(MAX_MANUAL)]
    base["manual_inputs"] = [{"name": str(m.get("name", "")).strip(), "value": str(m.get("value", ""))}
                             for m in mans[:MAX_MANUAL]]

    for key in ("station_name", "source_folder", "file_pattern", "backup_folder", "shared_folder",
                "api_url", "api_token", "encoding", "delimiter", "complete_marker", "setup_password", "shift_times",
                "alert_webhook_url",
                "alert_email_server",
                "alert_email_user", "alert_email_from", "alert_email_to"):
        base[key] = str(base[key] or "").strip()
    base["api_headers"] = str(base["api_headers"] or "")
    base["rename_columns"] = str(base["rename_columns"] or "")
    base["alert_email_password"] = str(base["alert_email_password"] or "")  # spaces may be part of it
    for key in ("shared_enabled", "api_enabled", "skip_duplicates", "autostart", "json_numbers", "csv_flat",
                "time_zone", "alert_notify",
                "alert_webhook_enabled", "alert_email_enabled", "alert_email_starttls"):
        base[key] = bool(base[key])
    base["file_pattern"] = base["file_pattern"] or "*.csv"
    base["encoding"] = base["encoding"] or "auto"
    base["delimiter"] = base["delimiter"] or "auto"
    if base["output_format"] not in FORMATS:
        base["output_format"] = "json"
    if base["api_body"] not in API_BODIES:
        base["api_body"] = "raw"
    base["poll_seconds"] = _number(base["poll_seconds"], 3, 0.5)
    base["stable_seconds"] = _number(base["stable_seconds"], 2, 0)
    base["api_timeout"] = _number(base["api_timeout"], 20, 1)
    base["keep_backup_days"] = int(_number(base["keep_backup_days"], 0, 0))
    base["keep_history_days"] = int(_number(base["keep_history_days"], 0, 0))
    base["alert_after_minutes"] = _number(base["alert_after_minutes"], 10, 0)
    base["alert_email_port"] = int(_number(base["alert_email_port"], 587, 1))
    return base


# ---------------------------------------------------------------- token protection
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    src, out = _Blob(len(data), ctypes.create_string_buffer(data, len(data))), _Blob()
    if protect:
        # 0x1 = never show UI, 0x4 = machine scope, so a scheduled task under another Windows account can read it
        ok = crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0x1 | 0x4, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(text):
    """Encrypt a secret for storage in config.json. Only this PC can decrypt it."""
    if not text or os.name != "nt":
        return text
    return "dpapi:" + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")


def unprotect(stored):
    if not stored.startswith("dpapi:"):
        return stored  # typed into the file by hand
    return _dpapi(base64.b64decode(stored[6:]), False).decode("utf-8")


# ---------------------------------------------------------------- load / save
def load_config(path):
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg = normalize_config(raw)
    for key in SECRET_KEYS:
        try:
            cfg[key] = unprotect(cfg[key])
        except (OSError, ValueError):
            log.warning("The saved %s cannot be read on this PC - enter it again", key.replace("_", " "))
            cfg[key] = ""
    return cfg


def save_config(cfg, path):
    p = Path(path)
    out = normalize_config(cfg)
    for key in SECRET_KEYS:
        out[key] = protect(out[key])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    _keep_backup(p)


def _keep_backup(path):
    """A copy of every saved settings file in backups\\, so a lost file or a bad change can be undone."""
    folder = path.parent / "backups"
    try:
        folder.mkdir(exist_ok=True)
        shutil.copy2(path, folder / f"config-{datetime.now():%Y%m%d-%H%M%S}.json")
        for old in sorted(folder.glob("config-*.json"))[:-BACKUPS_KEPT]:
            old.unlink()
    except OSError as e:
        log.warning("Could not keep a backup copy of the settings: %s", e)


def export_settings(cfg, path):
    """
    Write the settings to a file, for another PC or for safekeeping. The API token and the email password
    are left out: they are encrypted for this PC and would not work anywhere else.
    """
    out = normalize_config(cfg)
    for key in SECRET_KEYS:
        out[key] = ""
    out["setup_password"] = ""  # a settings file never carries or changes a password
    doc = {"exported_by": f"{APP_NAME} {VERSION}", "exported_at": datetime.now().isoformat(timespec="seconds")}
    doc.update(out)
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def import_settings(path, current):
    """
    Settings from an exported file or a backup copy. The current API token and email password are kept
    when the file has none, or has one that cannot be read on this PC.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not set(raw) & set(DEFAULT_CONFIG):
        raise ValueError("this is not a CNCBridge settings file")
    cfg = normalize_config(raw)
    for key in SECRET_KEYS:
        try:
            secret = unprotect(cfg[key])
        except (OSError, ValueError):
            secret = ""
        cfg[key] = secret or current.get(key, "")
    cfg["setup_password"] = current.get("setup_password", "")  # importing or restoring never changes the password
    return cfg


# ---------------------------------------------------------------- setup password
PASSWORD_ROUNDS = 200_000
MIN_PASSWORD = 4


def hash_password(password, salt=None, rounds=PASSWORD_ROUNDS):
    """'pbkdf2$<rounds>$<salt>$<hash>': the password itself is never stored."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2${rounds}${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    """True when the password matches a stored hash (and is not empty)."""
    try:
        scheme, rounds, salt, digest = stored.split("$")
        if scheme != "pbkdf2" or not password:
            return False
        again = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(again.hex(), digest)


def program_dir():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def legacy_data_dir():
    """Where version 1.0 kept settings and history: 'data' beside the program."""
    return program_dir() / "data"


def _writable(folder):
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def default_data_dir():
    """
    Settings, history, send queue and logs. They are kept outside the program folder, so rebuilding,
    replacing or tidying up the program cannot delete them: %ProgramData%\\CNCBridge (shared by the
    window and a background task), else %LOCALAPPDATA%\\CNCBridge.
    """
    for root in (os.environ.get("PROGRAMDATA"), os.environ.get("LOCALAPPDATA")):
        if root and _writable(Path(root) / APP_NAME):
            return Path(root) / APP_NAME
    folder = Path.home() / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def has_data(folder):
    """True when a data folder holds settings or history worth keeping."""
    return any((Path(folder) / name).exists() for name in ("config.json", "ledger.db"))


def move_data(old, new, today=None):
    """
    Bring an old data folder over to the new location, once: copy everything except the lock file,
    then rename the old folder so that nothing is processed twice. Returns a sentence for the user,
    or None when there is nothing to move.
    """
    old, new = Path(old), Path(new)
    if not has_data(old) or has_data(new):
        return None
    if old.resolve() == new.resolve():
        return None
    shutil.copytree(old, new, dirs_exist_ok=True, ignore=shutil.ignore_patterns("instance.lock"))
    stamp = f"{today or datetime.now():%Y%m%d}"
    moved, n = old.with_name(f"{old.name}.moved-{stamp}"), 2
    while moved.exists():
        moved, n = old.with_name(f"{old.name}.moved-{stamp}-{n}"), n + 1
    try:
        old.rename(moved)
    except OSError as e:
        log.warning("Copied %s to %s but could not rename the old folder: %s", old, new, e)
        return (f"Your settings and history were copied from {old} to {new}. The old folder could not be "
                f"renamed ({e.strerror}); it is no longer used and can be deleted.")
    return (f"Your settings and history were moved from {old} to {new}, where a rebuild of the program "
            f"cannot delete them. The old folder is kept as {moved}.")
