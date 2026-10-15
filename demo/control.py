"""Integrated simulator controls embedded in the production CNCBridge desktop window."""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from cncbridge import engine
from cncbridge.config import load_config, normalize_config, save_config
from . import testdata
from .mock_mes import MockMES


def open_path(target):
    target = str(target)
    if os.name == "nt":
        os.startfile(target)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


class DemoSession:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.machine = self.workspace / "machine"
        self.machine.mkdir(parents=True, exist_ok=True)
        self.data = self.workspace / "data"
        self.data.mkdir(exist_ok=True)
        self.test_files = testdata.write_test_files(self.workspace / "test_files")
        (self.workspace / "TEST_SCRIPT.md").write_text(testdata.test_script(), encoding="utf-8")
        self.mes = MockMES(self.workspace / "mes_received").start()
        self.cfg_path = self.data / "config.json"
        # Preserve the demonstrator's format, ranges, appearance and manual inputs on subsequent runs.
        cfg = load_config(self.cfg_path) if self.cfg_path.exists() else normalize_config(
            testdata.demo_settings(self.workspace, self.mes.url))
        cfg.update(source_folder=str(self.machine), backup_folder=str(self.workspace / "backup"),
                   shared_folder=str(self.workspace / "shared"), shared_enabled=True,
                   api_url=self.mes.url, api_token="demo-token", api_enabled=True, autostart=True)
        save_config(cfg, self.cfg_path)
        self.stop_event = threading.Event()
        self.machine_thread = None
        self.counter, self.last_data = 0, None
        self.session_id = uuid.uuid4().hex[:8]
        self.write_lock = threading.Lock()
        self.slow_thread = None

    @property
    def running(self):
        return bool(self.machine_thread and self.machine_thread.is_alive() and not self.stop_event.is_set())

    def drop_board(self):
        with self.write_lock:
            self.counter += 1
            text = testdata.make_board(self.counter)
            data = text.encode("utf-8")
            dest = self.machine / f"{self.session_id}_board_{self.counter:05d}.csv"
            engine.atomic_write(dest, data)
            self.last_data = data
            return dest

    def duplicate(self):
        with self.write_lock:
            if self.last_data is None:
                raise ValueError("Drop one board first, wait for delivery, then send its duplicate.")
            dest = self.machine / f"duplicate_{uuid.uuid4().hex[:8]}.csv"
            engine.atomic_write(dest, self.last_data)
            return dest

    def broken(self):
        dest = self.machine / f"broken_{uuid.uuid4().hex[:8]}.csv"
        engine.atomic_write(dest, b"this,is,not,a,board\r\n")
        return dest

    def slow(self):
        if self.slow_thread and self.slow_thread.is_alive():
            raise ValueError("A slow write is already in progress.")
        dest = self.machine / f"slow_{uuid.uuid4().hex[:8]}.csv"
        temporary = dest.with_suffix(".csv.part")
        data = testdata.make_board(1000 + self.counter).encode()

        def write():
            with temporary.open("wb") as stream:
                midpoint = len(data) // 2
                stream.write(data[:midpoint])
                stream.flush()
                if self.stop_event.wait(5):
                    return
                stream.write(data[midpoint:])
            os.replace(temporary, dest)
        self.slow_thread = threading.Thread(target=write, daemon=True)
        self.slow_thread.start()
        return temporary

    def start_machine(self):
        if self.running:
            return
        if self.machine_thread and self.machine_thread.is_alive():
            raise ValueError("The machine is stopping. Try again in a moment.")
        self.stop_event.clear()

        def run():
            while not self.stop_event.is_set():
                try:
                    self.drop_board()
                except OSError:
                    self.stop_event.set()
                    return
                self.stop_event.wait(5)
        self.machine_thread = threading.Thread(target=run, daemon=True)
        self.machine_thread.start()

    def stop_machine(self):
        self.stop_event.set()

    def close(self):
        self.stop_machine()
        for worker in (self.machine_thread, self.slow_thread):
            if worker:
                worker.join(timeout=2)
        self.mes.close()


def build_demo_tab(app, session):
    tab = app.settings_tab("  Demo & integration  ")
    intro = ttk.LabelFrame(tab, text=" Live demonstration with the CNCBridge engine ", padding=12)
    intro.pack(fill="x", pady=(0, 10))
    ttk.Label(intro, text="1. Monitoring starts automatically.  2. Drop a board.  3. Open the MES inbox.",
              style="Go.TLabel", wraplength=app.px(900)).pack(anchor="w")
    ttk.Label(intro, text="Machine CSV → your selected ranges + two manual inputs → JSON / CSV / TXT → "
              "shared folder and HTTP API → original in back-up.",
              wraplength=app.px(900)).pack(anchor="w", pady=(6, 0))
    ttk.Label(intro, text="This demo uses invented data in its own workspace. "
              "History, queue counters and the MES inbox show the actual processing results.",
              style="Small.TLabel", wraplength=app.px(900)).pack(anchor="w", pady=(6, 0))
    feedback = ttk.Label(tab, text="Ready. Click Drop one board now.", wraplength=app.px(900))
    feedback.pack(fill="x", pady=6)

    def action(work):
        def invoke():
            try:
                result = work()
                feedback.configure(text=str(result) if result else "Done. Check History and the MES inbox.")
            except Exception as exc:
                messagebox.showerror("Demo", str(exc), parent=app)
        return invoke

    groups = [
        ("Machine simulator", [
            ("Start machine (one board every 5 seconds)", lambda: session.start_machine()),
            ("Stop machine", lambda: session.stop_machine()),
            ("Drop one board now", session.drop_board),
            ("Send a duplicate", session.duplicate),
            ("Drop a broken file", session.broken),
            ("Write a file slowly", session.slow),
        ]),
        ("Customer MES", [
            ("Open MES inbox", lambda: webbrowser.open(session.mes.page)),
            ("Simulate outage", lambda: setattr(session.mes, "online", False)),
            ("End outage", lambda: setattr(session.mes, "online", True)),
            ("Refuse next file (HTTP 422)", lambda: setattr(session.mes, "refuse_next", True)),
            ("Open shared folder", lambda: app._open_folder(session.workspace / "shared")),
            ("Open back-up folder", lambda: app._open_folder(session.workspace / "backup")),
        ]),
    ]
    for title, commands in groups:
        box = ttk.LabelFrame(tab, text=f" {title} ", padding=10)
        box.pack(fill="x", pady=(0, 10))
        for i, (label, work) in enumerate(commands):
            box.columnconfigure(i % 3, weight=1)
            ttk.Button(box, text=label, command=action(work)).grid(
                row=i // 3, column=i % 3, sticky="ew", padx=4, pady=4)
    box = ttk.LabelFrame(tab, text=" Fixed test cases and proof ", padding=10)
    box.pack(fill="x")
    selected = ttk.Combobox(box, state="readonly", values=[case.name for case in testdata.CASES], width=40)
    selected.current(0)
    selected.grid(row=0, column=0, sticky="ew", padx=4)
    box.columnconfigure(0, weight=1)
    ttk.Button(box, text="Copy one", command=action(
        lambda: shutil.copyfile(session.test_files / selected.get(), session.machine / selected.get()))
    ).grid(row=0, column=1, padx=4)

    def copy_all():
        for i, case in enumerate(testdata.CASES):
            target = session.machine / case.name
            shutil.copyfile(session.test_files / case.name, target)
            stamp = time.time() - 100 + i
            os.utime(target, (stamp, stamp))
        return "Eight cases copied in order. Check History for six processed, one duplicate and one failed."

    ttk.Button(box, text="Copy all test files", command=action(copy_all)).grid(row=0, column=2, padx=4)
    ttk.Button(box, text="Open test script", command=action(
        lambda: open_path(session.workspace / "TEST_SCRIPT.md"))).grid(row=1, column=0, sticky="w", pady=8)
    ttk.Button(box, text="Verify the complete pipeline", command=app.verify_demo).grid(
        row=1, column=1, columnspan=2, sticky="ew", padx=4)
    ttk.Label(box, style="Small.TLabel", wraplength=app.px(900),
              text="Verification runs a separate isolated workspace. A pass requires actual HTTP "
              "acceptance, matching shared-folder bytes, archived originals, retry after restart, "
              "and duplicate / bad-file checks. Open CSV → Load demo sample previews the same ranges."
              ).grid(row=2, column=0, columnspan=3, sticky="w")
    return tab.notebook_page


def main(argv=None):
    parser = argparse.ArgumentParser(description="Launch CNCBridge with integrated demo controls")
    parser.add_argument("--workspace", help="separate workspace for invented demo data")
    args = parser.parse_args(argv)
    from cncbridge.__main__ import main as app_main
    return app_main(["--demo"] + (["--data", args.workspace] if args.workspace else []))


if __name__ == "__main__":
    raise SystemExit(main())
