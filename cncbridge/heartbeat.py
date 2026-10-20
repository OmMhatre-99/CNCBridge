"""
Heartbeat: a regular sign of life for whatever watches this PC from outside.

Every heartbeat_minutes the running copy sends one small JSON message: who it is, whether all is well or what
is wrong, the counters and the health facts (last file, oldest waiting file, back-up drive). It goes to a web
address (a JSON POST, like an alert) and/or into a folder as <station>.status.json, which an MES or a
monitoring tool can read. A deliberate stop sends one last message with the state "stopped", so a receiver can
tell a stop from a PC that has died: a copy that stops sending without saying "stopped" is down.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import alerts, audit
from .config import APP_NAME, VERSION
from .delivery import atomic_write
from .housekeeping import count_queued, count_rejected, count_waiting, health

STATES = ("ok", "problem", "stopped", "test")


def station_file_name(cfg):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", cfg["station_name"] or APP_NAME).strip("_") or APP_NAME
    return name + ".status.json"


def _safely(work, default=None):
    try:
        return work()
    except Exception:  # a folder that cannot be read must not stop the heartbeat that reports it
        return default


def snapshot(cfg, data_dir, ledger, state, problems=None, now=None):
    """The message. problems: [{"subject", "text", "since"}] for the open alerts."""
    now = now or datetime.now()
    stats = _safely(ledger.stats, {})
    return {
        "app": APP_NAME, "version": VERSION, "kind": "heartbeat", "state": state,
        "station": cfg["station_name"] or APP_NAME, "computer": os.environ.get("COMPUTERNAME", ""),
        "data_folder": str(data_dir), "time": now.isoformat(timespec="seconds"),
        "every_minutes": cfg["heartbeat_minutes"],
        "problems": list(problems or []),
        "counts": {"processed": stats.get("ok", 0), "failed": stats.get("failed", 0),
                   "duplicate": stats.get("duplicate", 0), "waiting": _safely(lambda: count_waiting(cfg)),
                   "queued": _safely(lambda: sum(count_queued(data_dir).values())),
                   "refused": _safely(lambda: count_rejected(data_dir))},
        "health": _safely(lambda: health(cfg, data_dir, ledger, now.timestamp()), {}),
    }


def send(cfg, payload):
    """Deliver the message to the web address and/or the folder. Returns where it went; raises if any failed."""
    went, errors = [], []
    if cfg["heartbeat_url"]:
        try:
            alerts.SENDERS["webhook"](cfg["heartbeat_url"], payload)
            went.append(audit.redact_url(cfg["heartbeat_url"]))
        except Exception as e:
            errors.append(f"{audit.redact_url(cfg['heartbeat_url'])}: {e}")
    if cfg["heartbeat_folder"]:
        target = Path(cfg["heartbeat_folder"]) / station_file_name(cfg)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, json.dumps(payload, indent=2, ensure_ascii=False))
            went.append(str(target))
        except OSError as e:
            errors.append(f"{target}: {e}")
    if errors:
        raise OSError("; ".join(errors))
    return went


def send_test(cfg, data_dir, ledger):
    """Send one message marked as a test. Returns where it went."""
    if not cfg["heartbeat_url"] and not cfg["heartbeat_folder"]:
        raise ValueError("enter the heartbeat address or folder first")
    return send(cfg, snapshot(cfg, data_dir, ledger, "test"))
