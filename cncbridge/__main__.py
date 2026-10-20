"""
CNCBridge entry point.

  CNCBridge                      open the window
  CNCBridge --headless           monitor with no window (the background task, a service wrapper)
  CNCBridge --headless --once    process what is in the folder now, then exit
  CNCBridge --restarted          opened again by the window (new look); waits while the old window lets go
  CNCBridge --data DIR           keep settings, ledger, queue and logs in DIR instead of %ProgramData%\\CNCBridge
"""
import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import background, engine, eventlog, stations
from .background import acquire_lock  # noqa: F401  (also used by the demo and the tests)
from .config import APP_NAME, default_data_dir, has_data, legacy_data_dir, load_config, move_data

log = logging.getLogger("cncbridge")


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


def window_mode(data_dir):
    """
    How a window works with this data folder:
      ("normal", lock)     it processes files itself and holds the folder
      ("connected", None)  background processing is set up or running; the window shows and controls it
      ("busy", None)       another window already works on this folder
    """
    if background.task_exists(data_dir):
        return "connected", None
    lock = acquire_lock(data_dir)
    if lock:
        return "normal", lock
    return ("connected", None) if background.holder(data_dir) == "background" else ("busy", None)


def setup_logging(data_dir, console, filename="cncbridge.log"):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    (Path(data_dir) / "logs").mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(Path(data_dir) / "logs" / filename, maxBytes=1_000_000, backupCount=5,
                                    encoding="utf-8")]
    if console and sys.stderr:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    try:
        station = load_config(Path(data_dir) / "config.json")["station_name"]
    except Exception:  # a damaged settings file is reported elsewhere; the log must still start
        station = ""
    eventlog.attach(log, station)  # warnings, errors and the named events also go to the Windows Event Log


def remember_station(data_dir):
    """Add this copy to the list the Stations window shows. Never a reason not to start."""
    try:
        stations.register(data_dir)
    except OSError as e:
        log.warning("Could not add this copy to the stations list: %s", e)


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_NAME, description="Machine CSV -> MES-ready JSON / CSV / TXT")
    ap.add_argument("--headless", action="store_true", help="monitor without opening the window")
    ap.add_argument("--once", action="store_true", help="with --headless: process the waiting files and exit")
    ap.add_argument("--data", help="folder for settings, ledger, queue and logs")
    ap.add_argument("--restarted", action="store_true", help="the window restarted itself; wait for it to close")
    ap.add_argument("--resume", action="store_true", help="with --restarted: start monitoring at once")
    args = ap.parse_args(argv)

    data_dir, notice = choose_data_dir(args.data)  # before anything opens files in the data folder
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.headless:
        setup_logging(data_dir, console=True)
        if notice:
            log.info("%s", notice)
        for _ in range(25):  # a connected window takes the lock for a moment when it checks on us
            lock = acquire_lock(data_dir)
            if lock:
                break
            time.sleep(0.2)
        else:
            log.error("%s is already running with this data folder: %s", APP_NAME, data_dir)
            return 1
        background.mark_holder(data_dir, "background")
        if not args.once:
            remember_station(data_dir)
        config_path = data_dir / "config.json"
        eng = engine.Engine(load_config(config_path), data_dir, config_path=config_path)
        if args.once:
            log.info("Processed %d file(s)", eng.scan_once())
            eng.alerts.flush()  # send any alert before the program ends
            return 0
        try:
            eng.run_forever()
        except KeyboardInterrupt:
            eng.stop()
        return 0

    mode, lock = window_mode(data_dir)
    for _ in range(125 if args.restarted else 0):  # the window that restarted us is still closing
        if mode != "busy":
            break
        time.sleep(0.2)
        mode, lock = window_mode(data_dir)
    # a connected window writes its own log, so two programs never rotate the same file
    setup_logging(data_dir, console=False, filename="cncbridge.log" if mode == "normal" else "window.log")
    if notice:
        log.info("%s", notice)
    if mode == "busy":
        message = f"{APP_NAME} is already open with this data folder:\n{data_dir}"
        log.error(message.replace("\n", " "))
        import tkinter
        from tkinter import messagebox
        tkinter.Tk().withdraw()  # no empty window behind the message
        messagebox.showerror(APP_NAME, message)
        return 1
    if lock:
        background.mark_holder(data_dir, "window")
    remember_station(data_dir)
    from .app import run
    run(data_dir, notice, lock, resume=args.resume)
    return 0


if __name__ == "__main__":
    sys.exit(main())
