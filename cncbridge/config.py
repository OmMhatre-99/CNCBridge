"""CNCBridge settings: defaults, load/save, the data folder, and API token protection (Windows DPAPI)."""
import base64
import ctypes
import json
import logging
import os
import sys
from pathlib import Path

APP_NAME = "CNCBridge"
VERSION = "1.0.0"

MAX_SELECTIONS = 10
MAX_MANUAL = 2
FORMATS = ("json", "csv", "txt")
API_BODIES = ("raw", "multipart")

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
    "autostart": False,                   # start monitoring when the program opens
}

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
                "api_url", "api_token", "encoding", "delimiter"):
        base[key] = str(base[key] or "").strip()
    base["api_headers"] = str(base["api_headers"] or "")
    for key in ("shared_enabled", "api_enabled", "skip_duplicates", "autostart"):
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
    try:
        cfg["api_token"] = unprotect(cfg["api_token"])
    except (OSError, ValueError):
        log.warning("The saved API token cannot be read on this PC - enter it again")
        cfg["api_token"] = ""
    return cfg


def save_config(cfg, path):
    p = Path(path)
    out = normalize_config(cfg)
    out["api_token"] = protect(out["api_token"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def default_data_dir():
    """Settings, ledger, outbox and logs live in 'data' beside the program, or in LocalAppData if that is read-only."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    d = base / "data"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
        d.mkdir(parents=True, exist_ok=True)
    return d
