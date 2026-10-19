"""The Stations window: every CNCBridge copy on this PC, side by side."""
import os
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import config, stations, theme
from ..config import APP_NAME


class StationsMixin:
    COLUMNS = (("name", "Station", 250), ("folder", "Data folder", 260), ("state", "State", 110),
               ("processed", "Processed", 80), ("failed", "Failed", 60), ("waiting", "Waiting", 70),
               ("queued", "Queued", 70), ("rejected", "Refused", 70), ("last", "Last file", 150),
               ("disk", "Back-up free", 100), ("problem", "Open problem", 300))
    STATE_TEXT = {"window": "Window open", "background": "In background", "stopped": "Stopped", "missing": "Missing"}

    def _build_stations(self):
        win = self.stations_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        win.title(f"{APP_NAME} stations")
        win.protocol("WM_DELETE_WINDOW", self.close_stations)
        width = min(self.px(1180 * self.text_scale), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(480 * self.text_scale), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(760), width), min(self.px(300), height))
        frame = ttk.Frame(win, padding=(14, 12, 14, 0))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Every CNCBridge copy on this PC. Each keeps its own machine folder, settings and "
                              "history; this is only a view of them.", style="PageHint.TLabel").pack(anchor="w",
                                                                                                    pady=(0, 8))
        area = ttk.Frame(frame, style="Card.TFrame", padding=1)
        area.pack(fill="both", expand=True)
        self.stations_tree = ttk.Treeview(area, columns=[c[0] for c in self.COLUMNS], show="headings",
                                          selectmode="browse")
        for key, title, column_width in self.COLUMNS:
            self.stations_tree.heading(key, text=title, anchor="w")
            self.stations_tree.column(key, width=self.px(column_width), anchor="w", stretch=key == "problem")
        self.stations_tree.tag_configure("problem", foreground=theme.ERROR)
        self.stations_tree.tag_configure("missing", foreground=theme.MUTED)
        self.stations_tree.tag_configure("this", foreground=theme.ACCENT)
        ys = ttk.Scrollbar(area, orient="vertical", command=self.stations_tree.yview)
        xs = ttk.Scrollbar(area, orient="horizontal", command=self.stations_tree.xview)
        self.stations_tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.stations_tree.pack(fill="both", expand=True)
        self.stations_tree.bind("<<TreeviewSelect>>", lambda event: self._update_station_buttons())
        self.stations_tree.bind("<Double-1>", lambda event: self.open_station())
        bar = ttk.Frame(win, padding=(14, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="Close", command=self.close_stations).pack(side="right")
        self.station_buttons = {}
        for key, text, command in (("open", "Open", self.open_station),
                                   ("data", "Open data folder", self.open_station_folder),
                                   ("remove", "Remove from list", self.remove_station),
                                   ("add", "Add...", self.add_station)):
            button = ttk.Button(bar, text=text, command=command)
            button.pack(side="left", padx=(0, 8))
            self.station_buttons[key] = button
        self.station_rows = {}
        self._stations_job = None
        self._update_station_buttons()

    def open_stations(self):
        if not hasattr(self, "stations_win"):
            self._build_stations()
        self.stations_win.deiconify()
        self.stations_win.lift()
        if self.theme.dark:
            theme.dark_title_bar(self.stations_win)
        self.refresh_stations()

    def close_stations(self):
        self.stations_win.withdraw()
        if self._stations_job is not None:
            self.after_cancel(self._stations_job)
            self._stations_job = None

    def refresh_stations(self):
        """Read every copy on a thread (a network folder can be slow) and show the result; again in 5 seconds."""
        if self._stations_job is not None:
            self.after_cancel(self._stations_job)
            self._stations_job = None
        if self.stations_win.state() == "withdrawn":
            return

        def work():
            try:
                rows = stations.snapshots(self.data_dir)
            except Exception:  # a damaged copy must never take the window down
                rows = []
            self.ui_q.put(lambda: self._fill_stations(rows))

        threading.Thread(target=work, daemon=True).start()
        self._stations_job = self.after(5000, self.refresh_stations)

    def _fill_stations(self, rows):
        tree = self.stations_tree
        selected = tree.selection()
        tree.delete(*tree.get_children())
        self.station_rows = {}
        for row in rows:
            name = (row["name"] or (Path(row["folder"]).name if row["state"] == "missing" else "(no name yet)")) + (
                "   ← this window" if row["this"] else "")
            free = f"{row['backup_free'] / 1024 ** 3:.1f} GB" if row["backup_free"] is not None else ""
            last = ""
            if row["last_file"]:
                last = f"{row['last_file'][11:16]} ({self._ago(row['last_file_age'])})" \
                    if row["last_file_age"] is not None else row["last_file"]
            tags = ["problem"] if row["problem"] else ["missing"] if row["state"] == "missing" else \
                ["this"] if row["this"] else []
            self.station_rows[row["folder"]] = row
            tree.insert("", "end", iid=row["folder"], tags=tags, values=[
                name, row["folder"], self.STATE_TEXT.get(row["state"], row["state"]), row["processed"], row["failed"],
                row["waiting"], row["queued"], row["rejected"], last, free, row["problem"] or ""])
        keep = [iid for iid in selected if tree.exists(iid)]
        if keep:
            tree.selection_set(keep)
        self._update_station_buttons()

    def _selected_station(self):
        selection = self.stations_tree.selection()
        return self.station_rows.get(selection[0]) if selection else None

    def _update_station_buttons(self):
        row = self._selected_station()
        usable = row is not None and row["state"] != "missing"
        can_open = usable and not row["this"] and row["state"] != "window"
        self.station_buttons["open"].configure(state="normal" if can_open else "disabled")
        self.station_buttons["data"].configure(state="normal" if usable else "disabled")
        self.station_buttons["remove"].configure(state="normal" if row is not None else "disabled")

    def open_station(self):
        """Open CNCBridge on the selected copy's data folder, in a window of its own."""
        row = self._selected_station()
        if row is None or row["this"] or row["state"] in ("window", "missing"):
            return
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")  # a one-file exe must unpack afresh
        try:
            subprocess.Popen(stations.launch_command(row["folder"]), cwd=str(config.program_dir()), env=env)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"CNCBridge could not be opened for that station:\n{e}",
                                 parent=self.stations_win)

    def open_station_folder(self):
        row = self._selected_station()
        if row is not None:
            self._open_folder(row["folder"])

    def add_station(self):
        folder = filedialog.askdirectory(parent=self.stations_win, title="Choose the data folder of a CNCBridge copy")
        if not folder:
            return
        if not config.has_data(folder):
            return messagebox.showinfo(APP_NAME, "That folder holds no CNCBridge settings or history. Choose the "
                                                 "data folder of a copy (the folder that contains config.json).",
                                       parent=self.stations_win)
        stations.register(folder)
        self.refresh_stations()

    def remove_station(self):
        row = self._selected_station()
        if row is None:
            return
        if not messagebox.askokcancel(APP_NAME, "Remove this station from the list?\n\nNothing is deleted; the copy "
                                                "and its data stay as they are, and it is listed again when it "
                                                "starts.", parent=self.stations_win):
            return
        stations.unregister(row["folder"])
        self.refresh_stations()
