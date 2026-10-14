"""
Demo control window.

Sets up a demo workspace, starts the stand-in MES, opens CNCBridge on that workspace, and gives
the presenter buttons for the pretend machine and for each fault case.
"""
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from cncbridge.__main__ import acquire_lock
from cncbridge.config import load_config, save_config
from cncbridge.tooltip import Tooltip

from . import machine_simulator, testdata
from .mock_mes import MockMES

FOLDERS = ("machine", "backup", "shared", "mes_received", "data", "test_files")
GREEN, RED, GREY = "#1e8e3e", "#c5221f", "#6b6b6b"


def base_dir():
    """Where the exe (or, from source, the project) lives."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


class Workspace:
    """The demo's own folders. CNCBridge's normal 'data' folder is never used by the demo."""

    def __init__(self, root):
        self.root = Path(root)
        for name in FOLDERS:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        testdata.write_test_files(self.root / "test_files")
        (self.root / "TEST_SCRIPT.txt").write_text(testdata.test_script(), encoding="utf-8")

    def __getattr__(self, name):
        if name in FOLDERS:
            return self.root / name
        raise AttributeError(name)

    def write_settings(self, api_url):
        """Fresh workspace: write the demo settings. Existing one: only point the API link at this session."""
        path = self.data / "config.json"
        cfg = load_config(path) if path.exists() else testdata.demo_settings(self.root, api_url)
        cfg["api_url"] = api_url
        save_config(cfg, path)

    def in_use(self):
        """True while a CNCBridge is running on this workspace."""
        lock = acquire_lock(self.data)
        if lock:
            lock.close()
        return lock is None

    def reset(self):
        for name in ("machine", "backup", "shared", "mes_received"):
            shutil.rmtree(self.root / name, ignore_errors=True)
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ("outbox", "rejected", "logs"):
            shutil.rmtree(self.data / name, ignore_errors=True)
        for name in ("ledger.db", "instance.lock"):
            (self.data / name).unlink(missing_ok=True)
        (self.root / "board_counter.txt").unlink(missing_ok=True)


class Machine:
    """The pretend machine: writes board logs into the machine folder."""

    def __init__(self, workspace, say):
        self.ws, self.say = workspace, say
        self.every = 5.0
        self.running = threading.Event()
        self.last = None      # (name, bytes) of the newest board, for the duplicate button
        self.written = 0
        self._counter = self.ws.root / "board_counter.txt"
        threading.Thread(target=self._loop, daemon=True).start()

    def _next_number(self):
        try:
            n = int(self._counter.read_text()) + 1
        except (OSError, ValueError):
            n = 1
        self._counter.write_text(str(n))
        return n

    def _write(self, name, data, pause=0.4):
        """Write in two parts with a pause, as real equipment does."""
        half = len(data) // 2
        with open(self.ws.machine / name, "wb") as f:
            f.write(data[:half])
            f.flush()
            time.sleep(pause)
            f.write(data[half:])

    def _loop(self):
        while True:
            self.running.wait()
            self.drop_board()
            waited = 0.0
            while waited < self.every and self.running.is_set():
                time.sleep(0.2)
                waited += 0.2

    def drop_board(self):
        n = self._next_number()
        # Use the LineBridge simulator for live boards.  It emits the same
        # multi-table machine log (placements, feeder status, nozzle stats and
        # errors) that a real SMT line would produce, including a two-part
        # write so CNCBridge exercises its stability check.
        name = f"L1M8_board_{n:05d}.csv"
        data = machine_simulator.make_log(n).encode("utf-8")
        self._write(name, data)
        self.last, self.written = (name, data), self.written + 1
        self.say(f"Machine wrote {name}")

    def drop_duplicate(self):
        if not self.last:
            return self.say("Drop a board first; there is nothing to repeat yet")
        name = self.last[0].replace(".csv", "_RESENT.csv")
        self._write(name, self.last[1], pause=0.1)
        self.say(f"Machine re-sent the same content as {name}")

    def drop_broken(self):
        name = f"alarm_log_{datetime.now():%H%M%S}.csv"
        self._write(name, testdata.not_a_board().encode("utf-8"), pause=0.1)
        self.say(f"Machine wrote {name}, which is not a board log")

    def drop_slowly(self, seconds=10):
        n = self._next_number()
        name = f"L1M8_board_{n:05d}.csv"
        data = machine_simulator.make_log(n).encode("utf-8")
        self.say(f"Machine started writing {name} and will take {seconds} s")
        self._write(name, data, pause=seconds)
        self.last, self.written = (name, data), self.written + 1
        self.say(f"Machine finished {name}")

    def copy_test_files(self, names):
        for name in names:
            shutil.copyfile(self.ws.test_files / name, self.ws.machine / name)
            time.sleep(0.05)  # distinct file times, so they are processed in this order
        self.say(f"Copied {len(names)} test file(s) into the machine folder")


class Control(tk.Tk):
    def __init__(self, root_folder=None, launch=True):
        super().__init__()
        self.title("CNCBridge demo control")
        self.scale = self.winfo_fpixels("1i") / 96
        self.messages = queue.Queue()
        self.ws = Workspace(root_folder or base_dir() / "CNCBridge_demo")
        self.mes = MockMES(self.ws.mes_received, testdata.TOKEN).start()
        self.ws.write_settings(self.mes.url)
        self.machine = Machine(self.ws, self.messages.put)
        self.bridge = None

        style = ttk.Style(self)
        style.configure("TButton", padding=(self.px(8), self.px(3)))
        style.configure("Small.TLabel", foreground=GREY)
        self.interval = tk.StringVar(self, "5")
        self.on_top = tk.BooleanVar(self, True)
        self.chosen = tk.StringVar(self, testdata.CASES[0].name)
        self._build()
        self.attributes("-topmost", True)
        self.resizable(True, True)
        self.minsize(self.px(620), self.px(420))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.say(f"Workspace: {self.ws.root}")
        self.say(f"Customer inbox: {self.mes.page}")
        if launch:
            self.after(300, self.open_bridge)
        self.after(200, self._tick)

    def px(self, n):
        return int(n * self.scale)

    # ---- layout
    def _build(self):
        gap = self.px(8)
        host = ttk.Frame(self)
        host.pack(fill="both", expand=True)
        self.scroll_canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(host, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.scroll_frame = ttk.Frame(self.scroll_canvas)
        self.scroll_window = self.scroll_canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        self.scroll_frame.bind("<Configure>", self._scroll_content_changed, add="+")
        self.scroll_canvas.bind("<Configure>", self._scroll_viewport_changed, add="+")
        self.bind_all("<MouseWheel>", self._mousewheel, add="+")

        columns = ttk.Frame(self.scroll_frame)
        columns.pack(fill="x", padx=gap, pady=(gap, 0))
        left, right = ttk.Frame(columns), ttk.Frame(columns)
        left.pack(side="left", fill="both", expand=True, padx=(0, gap))
        right.pack(side="left", fill="both", expand=True)

        box = ttk.LabelFrame(left, text=" Machine ", padding=gap)
        box.pack(fill="x")
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.run_btn = ttk.Button(row, text="Start machine", width=14, command=self.toggle_machine)
        self.run_btn.pack(side="left")
        ttk.Label(row, text="  one board every").pack(side="left")
        self.interval_spin = ttk.Spinbox(row, from_=2, to=60, width=4, textvariable=self.interval)
        self.interval_spin.pack(side="left", padx=4)
        ttk.Label(row, text="s").pack(side="left")
        self.drop_btn = ttk.Button(box, text="Drop one board now", command=lambda: self.work(self.machine.drop_board))
        self.drop_btn.pack(fill="x", pady=(6, 0))
        self.machine_state = ttk.Label(box, text="", style="Small.TLabel")
        self.machine_state.pack(anchor="w", pady=(4, 0))

        box = ttk.LabelFrame(left, text=" Fault cases ", padding=gap)
        box.pack(fill="x", pady=(gap, 0))
        self.duplicate_btn = ttk.Button(box, text="Send a duplicate",
                                        command=lambda: self.work(self.machine.drop_duplicate))
        self.duplicate_btn.pack(fill="x", pady=2)
        self.broken_btn = ttk.Button(box, text="Drop a broken file",
                                     command=lambda: self.work(self.machine.drop_broken))
        self.broken_btn.pack(fill="x", pady=2)
        self.slow_btn = ttk.Button(box, text="Write a file slowly (10 s)",
                                   command=lambda: self.work(self.machine.drop_slowly))
        self.slow_btn.pack(fill="x", pady=2)

        box = ttk.LabelFrame(right, text=" Customer MES ", padding=gap)
        box.pack(fill="x")
        self.outage_btn = ttk.Button(box, text="Simulate outage", command=self.toggle_outage)
        self.outage_btn.pack(fill="x", pady=2)
        self.refuse_btn = ttk.Button(box, text="Refuse next file", command=self.refuse_next)
        self.refuse_btn.pack(fill="x", pady=2)
        self.inbox_btn = ttk.Button(box, text="Open customer inbox", command=lambda: webbrowser.open(self.mes.page))
        self.inbox_btn.pack(fill="x", pady=2)
        self.mes_state = tk.Label(box, text="", anchor="w", fg=GREEN)
        self.mes_state.pack(fill="x", pady=(4, 0))

        box = ttk.LabelFrame(right, text=" Test files ", padding=gap)
        box.pack(fill="x", pady=(gap, 0))
        self.copy_all_btn = ttk.Button(box, text="Copy all test files", command=lambda: self.work(
            lambda: self.machine.copy_test_files([c.name for c in testdata.CASES])))
        self.copy_all_btn.pack(fill="x", pady=2)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        self.chosen_box = ttk.Combobox(row, textvariable=self.chosen, values=[c.name for c in testdata.CASES],
                                       state="readonly", width=30)
        self.chosen_box.pack(side="left", fill="x", expand=True)
        self.copy_one_btn = ttk.Button(row, text="Copy one", command=lambda: self.work(
            lambda: self.machine.copy_test_files([self.chosen.get()])))
        self.copy_one_btn.pack(side="left", padx=(6, 0))
        self.script_btn = ttk.Button(box, text="Open test script",
                                     command=lambda: os.startfile(self.ws.root / "TEST_SCRIPT.txt"))
        self.script_btn.pack(fill="x", pady=2)

        row = ttk.Frame(self.scroll_frame)
        row.pack(fill="x", padx=gap, pady=(gap, 0))
        self.open_bridge_btn = ttk.Button(row, text="Open CNCBridge", command=self.open_bridge)
        self.open_bridge_btn.pack(side="left")
        self.workspace_btn = ttk.Button(row, text="Open workspace", command=lambda: os.startfile(self.ws.root))
        self.workspace_btn.pack(side="left", padx=6)
        self.reset_btn = ttk.Button(row, text="Reset demo", command=self.reset)
        self.reset_btn.pack(side="left")
        self.top_check = ttk.Checkbutton(row, text="Keep on top", variable=self.on_top,
                                         command=lambda: self.attributes("-topmost", self.on_top.get()))
        self.top_check.pack(side="right")

        self.log = tk.Text(self.scroll_frame, height=5, width=80, font=("Consolas", 8), state="disabled", relief="solid",
                           borderwidth=1, wrap="word")
        self.log.pack(fill="both", padx=gap, pady=gap)

        self._add_tooltips()

        # bottom-right corner of the screen, clear of CNCBridge's toolbar and counters
        self.update_idletasks()
        x = self.winfo_screenwidth() - self.winfo_reqwidth() - self.px(16)
        y = self.winfo_screenheight() - self.winfo_reqheight() - self.px(90)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _scroll_content_changed(self, _event=None):
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _scroll_viewport_changed(self, event):
        self.scroll_canvas.itemconfigure(self.scroll_window, width=event.width)
        self._scroll_content_changed()

    def _mousewheel(self, event):
        try:
            inside = self.scroll_canvas.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            inside = None
        if inside is None:
            return
        widget = inside
        while widget is not None:
            if widget == self.scroll_canvas or widget == self.scroll_frame:
                self.scroll_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return
            try:
                widget = widget.master
            except AttributeError:
                return

    def _add_tooltips(self):
        """Explain demo controls on hover and keyboard focus, matching CNCBridge."""
        tips = {
            self.run_btn: "Start or stop the simulated machine. It writes realistic multi-table CSV logs.",
            self.interval_spin: "Seconds between automatically generated board files.",
            self.drop_btn: "Write one completed board log immediately into the simulated machine folder.",
            self.machine_state: "Shows whether the simulator is running and how many boards it has written.",
            self.duplicate_btn: "Re-send the latest board bytes under a new name to demonstrate duplicate detection.",
            self.broken_btn: "Write an invalid alarm CSV to demonstrate failed-file quarantine.",
            self.slow_btn: "Write a board in two chunks with a long pause to demonstrate stability checking.",
            self.outage_btn: "Toggle the mock customer MES offline or online. Offline files remain queued for retry.",
            self.refuse_btn: "Make the mock MES reject the next upload so CNCBridge records it as refused.",
            self.inbox_btn: "Open the local mock customer MES inbox in a browser.",
            self.mes_state: "Shows mock MES availability and counts of received or refused files.",
            self.copy_all_btn: "Copy all fixed scenario CSV files into the simulated machine folder.",
            self.chosen_box: "Choose one fixed scenario CSV file to copy.",
            self.copy_one_btn: "Copy the selected fixed scenario into the simulated machine folder.",
            self.script_btn: "Open the step-by-step demo and expected results.",
            self.open_bridge_btn: "Open CNCBridge using this demo's isolated settings and folders.",
            self.workspace_btn: "Open the demo workspace containing machine, backup, shared and MES folders.",
            self.reset_btn: "Clear generated files and history from the demo workspace.",
            self.top_check: "Keep this control window above other windows.",
            self.log: "Live activity messages from the simulator and mock MES.",
        }
        for widget, text in tips.items():
            Tooltip(widget, text)

    # ---- actions
    def say(self, text):
        self.messages.put(text)

    def work(self, job):
        """Run a machine action off the window thread, so the window never freezes."""
        def run():
            try:
                job()
            except Exception as e:
                self.say(f"Could not do that: {e}")
        threading.Thread(target=run, daemon=True).start()

    def toggle_machine(self):
        if self.machine.running.is_set():
            self.machine.running.clear()
            self.say("Machine stopped")
        else:
            self.machine.running.set()
            self.say("Machine started")

    def toggle_outage(self):
        self.mes.online = not self.mes.online
        self.say("Customer MES is back online" if self.mes.online else "Customer MES is down (answers 503)")

    def refuse_next(self):
        self.mes.refuse_next = True
        self.say("Customer MES will refuse the next file (answers 422)")

    def open_bridge(self):
        if self.ws.in_use():
            return self.say("CNCBridge is already open on the demo workspace")
        if getattr(sys, "frozen", False):
            exe = base_dir() / "CNCBridge.exe"
            if not exe.exists():
                return messagebox.showerror("CNCBridge demo", f"Put CNCBridge.exe in the same folder as this "
                                            f"program:\n{base_dir()}", parent=self)
            command = [str(exe)]
        else:
            command = [sys.executable, "-m", "cncbridge"]
        self.bridge = subprocess.Popen(command + ["--data", str(self.ws.data)], cwd=base_dir())
        self.say("Opened CNCBridge. Press Start monitoring there.")

    def reset(self):
        if self.ws.in_use():
            return messagebox.showinfo("CNCBridge demo", "Close CNCBridge first, then reset.", parent=self)
        if not messagebox.askokcancel("CNCBridge demo", "Empty the demo workspace?\n\nMachine, back-up, shared "
                                      "and inbox files and the history are deleted. Settings are kept.",
                                      parent=self):
            return
        self.machine.running.clear()
        self.ws.reset()
        self.mes.clear()
        self.machine.last, self.machine.written = None, 0
        self.say("Demo workspace emptied")

    def on_close(self):
        self.mes.close()
        self.destroy()

    # ---- status
    def _tick(self):
        lines = []
        while True:
            try:
                lines.append(self.messages.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log.configure(state="normal")
            for line in lines:
                self.log.insert("end", f"{datetime.now():%H:%M:%S}  {line}\n")
            self.log.configure(state="disabled")
            self.log.see("end")
        try:
            self.machine.every = max(2.0, float(self.interval.get()))
        except ValueError:
            pass
        running = self.machine.running.is_set()
        self.run_btn.configure(text="Stop machine" if running else "Start machine")
        self.machine_state.configure(text=f"{'Running' if running else 'Stopped'}, {self.machine.written} "
                                          f"board(s) written this session")
        self.outage_btn.configure(text="End outage" if not self.mes.online else "Simulate outage")
        waiting = ", will refuse the next file" if self.mes.refuse_next else ""
        self.mes_state.configure(
            fg=GREEN if self.mes.online else RED,
            text=f"{'Online' if self.mes.online else 'OUTAGE'}: {len(self.mes.items)} received, "
                 f"{self.mes.refused} refused{waiting}")
        self.after(300, self._tick)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    Control().mainloop()
