"""The set-up window: choose the folder and options, watch the steps, finish. Also the uninstall question."""
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import core


class Wizard(tk.Tk):
    """
    parts: what to install and where: payload, program, data, menu, desktop, registry, setup_exe, running_exe,
    remove_tasks (see core.Installer). With sync=True the work runs in the calling thread (for tests).
    """

    def __init__(self, parts, uninstall=False, sync=False):
        super().__init__()
        self.parts, self.sync, self.mode = dict(parts), sync, "uninstall" if uninstall else "install"
        self.messages = queue.Queue()
        self.finished = False
        self.title("CNCBridge setup")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        installed = core.installed_location(self.parts["registry"])
        self.folder = tk.StringVar(self, str(installed[0] if installed else self.parts["program"]))
        self.start_menu, self.desktop = tk.BooleanVar(self, True), tk.BooleanVar(self, True)
        self.keep_data, self.start_now = tk.BooleanVar(self, True), tk.BooleanVar(self, True)
        if self.mode == "uninstall":
            self._uninstall_page(frame, installed)
        else:
            self._install_page(frame, installed)
        self.log = tk.Text(frame, height=9, width=72, state="disabled", relief="solid", borderwidth=1,
                           font=("Consolas", 9))
        self.log.pack(fill="x", pady=(12, 0))
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(12, 0))
        self.close_button = ttk.Button(bar, text="Cancel", command=self.close)
        self.close_button.pack(side="right")
        self.go_button = ttk.Button(bar, text="Uninstall" if self.mode == "uninstall" else "Install",
                                    command=self.go)
        self.go_button.pack(side="right", padx=8)
        self.after(150, self._pump)

    # ------------------------------------------------------------ pages
    def _install_page(self, frame, installed):
        ttk.Label(frame, text="CNCBridge", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Takes the CSV files a machine writes and delivers the chosen cells to a shared "
                              "folder, a web link or an FTP server.", wraplength=520, justify="left").pack(
            anchor="w", pady=(2, 10))
        if installed:
            ttk.Label(frame, text=f"CNCBridge {installed[1]} is installed in {installed[0]}. Installing again "
                                  "updates it; your settings and history are kept.", wraplength=520,
                      justify="left", foreground="#1a66d9").pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="Install in").pack(side="left")
        ttk.Entry(row, textvariable=self.folder, width=52).pack(side="left", padx=8)
        ttk.Button(row, text="Browse...", command=self._browse).pack(side="left")
        ttk.Checkbutton(frame, text="Add CNCBridge to the Start menu", variable=self.start_menu).pack(anchor="w",
                                                                                                    pady=(10, 0))
        ttk.Checkbutton(frame, text="Put a shortcut on the desktop", variable=self.desktop).pack(anchor="w")
        ttk.Label(frame, text=f"Settings, history and the send queue are kept in {self.parts['data']}, where "
                              "ordinary users may write, so replacing the program never removes them.",
                  wraplength=520, justify="left", foreground="#6b6b6b").pack(anchor="w", pady=(10, 0))

    def _uninstall_page(self, frame, installed):
        ttk.Label(frame, text="Remove CNCBridge", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        where = installed[0] if installed else self.parts["program"]
        ttk.Label(frame, text=f"CNCBridge will be removed from {where}, together with its shortcuts and its "
                              "background task.", wraplength=520, justify="left").pack(anchor="w", pady=(2, 10))
        ttk.Checkbutton(frame, text=f"Keep my settings and history ({self.parts['data']})",
                        variable=self.keep_data).pack(anchor="w")

    def _browse(self):
        chosen = filedialog.askdirectory(parent=self, title="Choose the install folder")
        if chosen:
            self.folder.set(str(Path(chosen) / "CNCBridge") if Path(chosen).name != "CNCBridge" else chosen)

    # ------------------------------------------------------------ doing it
    def say(self, text):
        self.messages.put(("say", text))

    def _pump(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "say":
                    self.log.configure(state="normal")
                    self.log.insert("end", value + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "ask":
                    value["answer"] = messagebox.askyesno("CNCBridge", value["text"], parent=self)
                    value["done"].set()
                elif kind == "done":
                    self._done(value)
        except queue.Empty:
            pass
        self.after(150, self._pump)

    def installer(self):
        parts = dict(self.parts)
        parts["program_dir"] = Path(self.folder.get())
        return core.Installer(parts["payload"], parts["program_dir"], parts["data"], parts["menu"], parts["desktop"],
                              setup_exe=parts.get("setup_exe"), registry=parts["registry"], report=self.say,
                              remove_tasks=parts.get("remove_tasks"), running_exe=parts.get("running_exe"))

    def go(self):
        self.go_button.configure(state="disabled")
        self.close_button.configure(state="disabled")
        if self.sync:
            self._work()
            self._pump()
        else:
            threading.Thread(target=self._work, daemon=True).start()

    def _ask(self, text):
        """Ask the person (from the working thread) and wait for the answer."""
        if self.sync:
            return messagebox.askyesno("CNCBridge", text, parent=self)
        box = {"text": text, "done": threading.Event(), "answer": False}
        self.messages.put(("ask", box))
        box["done"].wait()
        return box["answer"]

    def _work(self):
        installer = self.installer()
        for attempt in range(3):
            try:
                if self.mode == "uninstall":
                    installer.uninstall(keep_data=self.keep_data.get())
                    result = None
                else:
                    result = installer.install(self.start_menu.get(), self.desktop.get())
                self.messages.put(("done", ("ok", result)))
                return
            except core.InUse as e:
                if attempt < 2 and self._ask(f"{e}\n\nClose CNCBridge now and continue?"):
                    closed = core.stop_running(installer.program)
                    self.say(f"Closed {closed} running program(s).")
                    continue
                self.messages.put(("done", ("error", str(e))))
                return
            except core.InstallError as e:
                self.messages.put(("done", ("error", str(e))))
                return
            except Exception as e:  # never leave the window hanging
                self.messages.put(("done", ("error", f"Unexpected problem: {e}")))
                return

    def _done(self, outcome):
        status, value = outcome
        self.finished = True
        self.outcome = outcome
        if status == "error":
            self.say(f"Stopped: {value}")
            self.close_button.configure(text="Close", state="normal")
            self.go_button.configure(text="Try again", state="normal")
            return
        self.close_button.configure(text="Finish", state="normal")
        if self.mode == "install":
            self.exe = value
            ttk.Checkbutton(self.log.master, text="Start CNCBridge now", variable=self.start_now).pack(anchor="w")

    def close(self):
        if self.finished and getattr(self, "outcome", ("", None))[0] == "ok" and self.mode == "install" \
                and self.start_now.get() and getattr(self, "exe", None) and not self.sync:
            subprocess.Popen(["explorer.exe", str(self.exe)])  # started by the person, not with the set-up's rights
        self.destroy()
