"""
CNCBridge entry point.

  CNCBridge                      open the window
  CNCBridge --headless           monitor with no window (Task Scheduler, service wrapper)
  CNCBridge --headless --once    process what is in the folder now, then exit
  CNCBridge --data DIR           keep settings, ledger, queue and logs in DIR instead of %ProgramData%\\CNCBridge
"""
import argparse
import logging
import msvcrt
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import engine
from .config import APP_NAME, default_data_dir, has_data, legacy_data_dir, load_config, move_data

log = logging.getLogger("cncbridge")


def acquire_lock(data_dir):
    """One running copy per data folder; two would process the same machine files twice."""
    f = open(Path(data_dir) / "instance.lock", "a+")
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        return None


def choose_data_dir(requested=None, old=None, new=None):
    """
    The data folder to use, and a one-time message for the user (or None).

    Version 1.0 kept its data in 'data' beside the program, where a rebuild could delete it. That folder
    is moved once to the new place, unless:
    - the new place already belongs to another copy of the program (two copies for two machine folders):
      this copy keeps its own folder, so the two never mix;
    - a copy is still running with the old folder: it is left alone, and the single-copy lock then
      reports that this data folder is in use.
    """
    if requested:
        return Path(requested).resolve(), None
    old = Path(old) if old else legacy_data_dir()
    new = Path(new) if new else default_data_dir()
    if not has_data(old):
        return new, None
    if has_data(new):
        log.info("Keeping the data folder beside the program (%s): %s belongs to another copy", old, new)
        return old, None
    lock = acquire_lock(old)
    if not lock:
        return old, None
    lock.close()
    return new, move_data(old, new)


def setup_logging(data_dir, console):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    (Path(data_dir) / "logs").mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(Path(data_dir) / "logs" / "cncbridge.log", maxBytes=1_000_000, backupCount=5,
                                    encoding="utf-8")]
    if console and sys.stderr:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_NAME, description="Machine CSV -> MES-ready JSON / CSV / TXT")
    ap.add_argument("--headless", action="store_true", help="monitor without opening the window")
    ap.add_argument("--once", action="store_true", help="with --headless: process the waiting files and exit")
    ap.add_argument("--data", help="folder for settings, ledger, queue and logs")
    args = ap.parse_args(argv)

    data_dir, notice = choose_data_dir(args.data)  # before anything opens files in the data folder
    data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(data_dir, console=args.headless)
    if notice:
        log.info("%s", notice)
    lock = acquire_lock(data_dir)
    if not lock:
        message = f"{APP_NAME} is already running with this data folder:\n{data_dir}"
        log.error(message.replace("\n", " "))
        if not args.headless:
            import tkinter
            from tkinter import messagebox
            tkinter.Tk().withdraw()  # no empty window behind the message
            messagebox.showerror(APP_NAME, message)
        return 1

    if not args.headless:
        from .app import run
        run(data_dir, notice)
        return 0

    eng = engine.Engine(load_config(data_dir / "config.json"), data_dir)
    if args.once:
        log.info("Processed %d file(s)", eng.scan_once())
        return 0
    try:
        eng.run_forever()
    except KeyboardInterrupt:
        eng.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
