"""CNCBridge main window (Tkinter). The engine runs on a worker thread; the window only reads snapshots."""
import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import engine
from .appearance import Appearance, PALETTES, SIZES
from .config import APP_NAME, FORMATS, MAX_MANUAL, MAX_SELECTIONS, VERSION, load_config, normalize_config, save_config
from .grid import MARK_COLOURS, Grid

log = logging.getLogger("cncbridge")

BODY_LABELS = {"raw": "File as request body", "multipart": "Form upload (multipart)"}
ENCODINGS = ("auto", "utf-8", "cp1252", "utf-16", "shift_jis", "gbk", "big5", "euc_kr")
DELIMITER_CHOICES = ("auto", ",", ";", "tab", "|")
PREVIEW_LIMIT = 200_000
GREEN, RED, AMBER, GREY = "#1e8e3e", "#c5221f", "#b06000", "#6b6b6b"


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put((record.levelname, self.format(record)))


class App(tk.Tk):
    def __init__(self, data_dir, demo_session=None):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.cfg_path = self.data_dir / "config.json"
        self.cfg = load_config(self.cfg_path)
        self.demo_session = demo_session
        self.ledger = engine.Ledger(self.data_dir / "ledger.db")
        self.engine = self.thread = None
        self.busy = False            # a one-off "Process now" run is in progress
        self.closing = threading.Event()
        self.snapshot = None         # latest counters + history, produced by the stats thread
        self.shown_history = None
        self.sample_rows, self.sample_name = [], ""
        self.ui_q, self.log_q = queue.Queue(), queue.Queue()

        handler = QueueHandler(self.log_q)
        self.log_handler = handler
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)

        self.title(f"{APP_NAME} {VERSION}")
        self.scale = self.winfo_fpixels("1i") / 96  # fonts grow with the Windows display scale; sizes must follow
        # never taller or wider than the screen (minus taskbar and title bar), or the Save button ends up off-screen
        width = min(self.px(1180), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(740), self.winfo_screenheight() - self.px(100))
        self.geometry(f"{width}x{height}+{(self.winfo_screenwidth() - width) // 2}+{self.px(8)}")
        self.minsize(min(self.px(980), width), min(self.px(620), height))
        style = ttk.Style(self)
        style.configure("Count.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Small.TLabel", foreground=GREY)
        style.configure("TButton", padding=(self.px(8), self.px(2)))
        style.configure("Go.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("Toolbutton", padding=(self.px(8), self.px(4)))
        style.configure("Go.Toolbutton", font=("Segoe UI", 9, "bold"))
        style.configure("Treeview", rowheight=self.px(22))

        self.appearance = Appearance(self, self.data_dir)
        self._make_vars()
        self._build_toolbar()
        self._build_header()
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(4, 0))
        self._build_help()
        self._build_setup()
        self._build_delivery()
        self._build_pick()
        self._build_history()
        self._build_log()
        self._build_checks()
        self._build_preferences()
        if self.demo_session:
            from demo.control import build_demo_tab
            self.demo_tab = build_demo_tab(self, self.demo_session)
            self.tabs.select(self.demo_tab)
        self._build_footer()
        self.fill(self.cfg)
        self.apply_appearance()
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.stats_thread = threading.Thread(target=self._stats_loop, daemon=True)
        self.stats_thread.start()
        self.after(200, self._tick)
        if self.cfg["autostart"]:
            self.after(400, lambda: self.start(quiet=True))

    def px(self, n):
        return int(n * self.scale)

    def destroy(self):
        self.closing.set()
        if hasattr(self, "stats_thread"):
            self.stats_thread.join(timeout=0.2)
        log.removeHandler(self.log_handler)
        self.log_handler.close()
        super().destroy()

    # ================================================================ variables <-> settings
    def _make_vars(self):
        self.v = {k: tk.StringVar(self) for k in (
            "station_name", "source_folder", "file_pattern", "backup_folder", "output_format", "shared_folder",
            "api_url", "api_token", "api_body", "encoding", "delimiter", "poll_seconds")}
        self.b = {k: tk.BooleanVar(self) for k in ("shared_enabled", "api_enabled", "skip_duplicates", "autostart")}
        self.sel_vars = [(tk.StringVar(self), tk.StringVar(self), tk.BooleanVar(self)) for _ in range(MAX_SELECTIONS)]
        self.man_vars = [(tk.StringVar(self), tk.StringVar(self)) for _ in range(MAX_MANUAL)]
        self.pick_range, self.pick_name = tk.StringVar(self), tk.StringVar(self)
        self.pick_open = tk.BooleanVar(self)
        self.status_msg = tk.StringVar(self)

    def fill(self, cfg):
        for k, var in self.v.items():
            var.set(BODY_LABELS[cfg[k]] if k == "api_body" else f"{cfg[k]:g}" if k == "poll_seconds" else cfg[k])
        for k, var in self.b.items():
            var.set(cfg[k])
        for (name, rng, header), s in zip(self.sel_vars, cfg["selections"]):
            name.set(s["name"]), rng.set(s["range"]), header.set(s["header"])
        for (name, value), m in zip(self.man_vars, cfg["manual_inputs"]):
            name.set(m["name"]), value.set(m["value"])
        self.headers_text.delete("1.0", "end")
        self.headers_text.insert("1.0", cfg["api_headers"])

    def collect(self):
        """The settings as currently shown in the window (saved or not)."""
        cfg = dict(self.cfg)
        cfg.update({k: var.get() for k, var in self.v.items()})
        cfg.update({k: var.get() for k, var in self.b.items()})
        cfg["api_body"] = next((k for k, label in BODY_LABELS.items() if label == self.v["api_body"].get()), "raw")
        cfg["api_headers"] = self.headers_text.get("1.0", "end").strip()
        cfg["selections"] = []
        for name, rng, header in self.sel_vars:
            text = rng.get().strip()
            try:
                text = engine.normalize_range(text) if text else ""
            except ValueError:
                pass  # left as typed; validate() names the slot
            cfg["selections"].append({"name": name.get(), "range": text, "header": header.get()})
        cfg["manual_inputs"] = [{"name": n.get(), "value": val.get()} for n, val in self.man_vars]
        return normalize_config(cfg)

    # ================================================================ layout
    def settings_tab(self, title):
        """Keep every setting reachable on small screens and at larger font sizes."""
        outer = ttk.Frame(self.tabs)
        self.tabs.add(outer, text=title)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vertical = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        horizontal = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        body = ttk.Frame(canvas, padding=10)
        item = canvas.create_window((0, 0), window=body, anchor="nw")

        def resize(_event=None):
            canvas.itemconfigure(item, width=max(canvas.winfo_width(), body.winfo_reqwidth()))
            canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.bind("<Configure>", resize)
        body.bind("<Configure>", resize)
        return body

    def _build_help(self):
        tab = self.settings_tab("  Getting started  ")
        ttk.Label(tab, text="Connect your machine CSV files to the MES", style="Go.TLabel").pack(anchor="w")
        for title, detail in (
            ("1. Try the demo", "Open the integrated demo to generate invented board logs and see real "
             "shared-folder and API delivery. Use Simulate outage to see files queue and recover."),
            ("2. Choose your folders", "In Setup, choose the folder where the machine saves CSV files and "
             "a separate back-up folder. Used originals are moved into dated back-up folders."),
            ("3. Pick data and format", "Open a machine CSV in Pick ranges & preview. Drag over cells, choose "
             "one of ten slots and press Assign. Add Operator and Work order in the two manual input fields. "
             "Choose JSON, CSV or TXT and inspect the preview."),
            ("4. Choose where to send", "In Delivery, enable a shared folder, an API link, or both. "
             "Test each route. In Checks, inspect settings and the sample before starting."),
            ("5. Start and observe", "Press Save settings, then Start monitoring. History shows each "
             "destination's result. Queued files retry automatically; originals stay in back-up."),
        ):
            box = ttk.LabelFrame(tab, text=f" {title} ", padding=10)
            box.pack(fill="x", pady=6)
            ttk.Label(box, text=detail, wraplength=self.px(950), justify="left").pack(anchor="w")
        ttk.Button(tab, text="Open integrated demo", command=self.launch_demo).pack(anchor="w", pady=6)
        ttk.Label(tab, text="Use Appearance to change colors or enlarge all text.",
                  style="Small.TLabel").pack(anchor="w")

    def launch_demo(self):
        if self.demo_session:
            self.tabs.select(self.demo_tab)
            return
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--demo"]
        else:
            command = [sys.executable, "-m", "cncbridge", "--demo"]
        subprocess.Popen(command, cwd=str(Path(__file__).resolve().parent.parent))
        self.status_msg.set("Demo opened in a separate window and workspace.")

    def _build_preferences(self):
        tab = self.settings_tab("  Appearance  ")
        box = ttk.LabelFrame(tab, text=" Make the window comfortable to read ", padding=16)
        box.pack(fill="x")
        self.theme_choice = tk.StringVar(self, self.appearance.preferences["theme"])
        self.size_choice = tk.StringVar(self, self.appearance.preferences["text_size"])
        for row, (title, variable, choices) in enumerate((
            ("Theme", self.theme_choice, tuple(PALETTES)),
            ("Text size", self.size_choice, tuple(SIZES)),
        )):
            ttk.Label(box, text=title).grid(row=row, column=0, sticky="w", padx=6, pady=8)
            control = ttk.Combobox(box, textvariable=variable, values=choices, state="readonly", width=24)
            control.grid(row=row, column=1, sticky="w", padx=6, pady=8)
            control.bind("<<ComboboxSelected>>", lambda event: self.change_appearance())
        ttk.Label(box, text="Changes apply immediately to the window, spreadsheet, preview, history and log. "
                  "They are saved automatically on this PC. Scrollbars keep settings reachable at larger sizes.",
                  wraplength=self.px(900)).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=12)

    def change_appearance(self):
        self.appearance.preferences = {"theme": self.theme_choice.get(), "text_size": self.size_choice.get()}
        self.apply_appearance()
        self.appearance.save()
        self.status_msg.set("Appearance saved.")

    def apply_appearance(self):
        self.appearance.apply()
        colors = self.appearance.colors
        self.grid_view.set_appearance(colors, self.appearance.points)
        for tag, key in (("failed", "error"), ("duplicate", "muted"), ("pending", "warning")):
            self.history.tag_configure(tag, foreground=colors[key])
        self.preview_note.configure(foreground=colors["warning"])
        for tag, key in (("WARNING", "warning"), ("ERROR", "error")):
            self.log_text.tag_configure(tag, foreground=colors[key])
        self.shown_history = None

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x")

        def button(text, command, style="Toolbutton"):
            b = ttk.Button(bar, text=text, style=style, command=command, takefocus=False)
            b.pack(side="left", padx=1)
            return b

        def divider():
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=3)

        self.start_btn = button("▶  Start monitoring", self.start, "Go.Toolbutton")
        self.stop_btn = button("■  Stop", self.stop)
        self.once_btn = button("↻  Process now", self.process_now)
        divider()
        button("✔  Save settings", self.save)
        bar = ttk.Frame(self, padding=(8, 2))
        bar.pack(fill="x")
        button("▦  Open CSV file...", self.open_csv)
        folders = ttk.Menubutton(bar, text="Open folder  ▾", style="Toolbutton", takefocus=False)
        menu = tk.Menu(folders, tearoff=False)
        for label, key in (("Machine CSV folder", "source_folder"), ("Back-up folder", "backup_folder"),
                           ("Shared folder", "shared_folder")):
            menu.add_command(label=label, command=lambda key=key: self._open_folder(self.v[key].get()))
        menu.add_separator()
        menu.add_command(label="Data folder (send queue, rejected files, log file)",
                         command=lambda: self._open_folder(self.data_dir))
        folders["menu"] = menu
        folders.pack(side="left", padx=1)
        ttk.Separator(self).pack(fill="x")

    def _build_header(self):
        bar = ttk.Frame(self, padding=(12, 8, 12, 6))
        bar.pack(fill="x")
        self.state_dot = tk.Label(bar, text="●", fg=GREY, font=("Segoe UI", 14))
        self.state_dot.pack(side="left")
        self.state_label = ttk.Label(bar, text="Stopped", font=("Segoe UI", 11, "bold"), width=11)
        self.state_label.pack(side="left", padx=(4, 10))

        self.counts = {}
        for key, caption in reversed((("ok", "Processed"), ("waiting", "Waiting in folder"),
                                      ("queued", "Queued to send"), ("failed", "Failed files"),
                                      ("rejected", "Refused by API"))):
            box = ttk.Frame(bar)
            box.pack(side="right", padx=(18, 0))
            self.counts[key] = ttk.Label(box, text="0", style="Count.TLabel", anchor="e")
            self.counts[key].pack(anchor="e")
            ttk.Label(box, text=caption, style="Small.TLabel").pack(anchor="e")
        ttk.Separator(self).pack(fill="x")

    def _folder_row(self, parent, row, label, key):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=self.v[key]).grid(row=row, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(parent, text="Browse...", width=9,
                   command=lambda: self._browse(self.v[key])).grid(row=row, column=2, pady=3)

    def _browse(self, var):
        start = var.get() if Path(var.get() or "?").is_dir() else None
        folder = filedialog.askdirectory(parent=self, initialdir=start)
        if folder:
            var.set(os.path.normpath(folder))

    def _build_setup(self):
        tab = self.settings_tab("  Setup  ")
        tab.columnconfigure(0, weight=1, uniform="half")
        tab.columnconfigure(1, weight=1, uniform="half")
        tab.rowconfigure(0, weight=1)
        left, right = ttk.Frame(tab), ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        box = ttk.LabelFrame(left, text=" 1  Folders ", padding=10)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Station name").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(box, textvariable=self.v["station_name"]).grid(row=0, column=1, columnspan=2, sticky="ew",
                                                                 padx=(6, 0), pady=3)
        self._folder_row(box, 1, "Machine CSV folder", "source_folder")
        ttk.Label(box, text="File pattern").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(box, textvariable=self.v["file_pattern"], width=14).grid(row=2, column=1, sticky="w", padx=6, pady=3)
        self._folder_row(box, 3, "Back-up folder", "backup_folder")
        ttk.Label(box, style="Small.TLabel", wraplength=self.px(470), justify="left",
                  text="Each file is taken from the machine folder as soon as the machine has finished writing it. "
                       "After processing it is moved into the back-up folder (one sub-folder per day)."
                  ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        box = ttk.LabelFrame(left, text=" 2  Output format ", padding=10)
        box.pack(fill="x", pady=(10, 0))
        for fmt in FORMATS:
            ttk.Radiobutton(box, text=fmt.upper(), value=fmt, variable=self.v["output_format"]).pack(side="left",
                                                                                                    padx=(0, 22))

        box = ttk.LabelFrame(left, text=" 4  Manual inputs (written into every output file) ", padding=10)
        box.pack(fill="x", pady=(10, 0))
        box.columnconfigure(1, weight=1)
        box.columnconfigure(2, weight=2)
        ttk.Label(box, text="Name", style="Small.TLabel").grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(box, text="Value", style="Small.TLabel").grid(row=0, column=2, sticky="w")
        for i, (name, value) in enumerate(self.man_vars, 1):
            ttk.Label(box, text=f"Input {i}").grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(box, textvariable=name).grid(row=i, column=1, sticky="ew", padx=6, pady=3)
            ttk.Entry(box, textvariable=value).grid(row=i, column=2, sticky="ew", pady=3)
        ttk.Label(box, style="Small.TLabel", wraplength=self.px(470), justify="left",
                  text="For example Operator and Work order. Change a value and press Save settings; "
                       "it applies from the next file, without stopping.").grid(row=3, column=0, columnspan=3,
                                                                                sticky="w", pady=(6, 0))

        box = ttk.LabelFrame(right, text=" 3  Data to extract (up to 10 cell ranges) ", padding=10)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=3)
        box.columnconfigure(2, weight=2)
        for col, text in ((1, "Name in the output"), (2, "Cell range"), (3, "First row is header")):
            ttk.Label(box, text=text, style="Small.TLabel").grid(row=0, column=col, sticky="w", padx=4)
        for i, (name, rng, header) in enumerate(self.sel_vars):
            tk.Label(box, text=f" {i + 1} ", bg=MARK_COLOURS[i][1], fg="white",
                     font=("Segoe UI", 8, "bold")).grid(row=i + 1, column=0, pady=3)
            ttk.Entry(box, textvariable=name).grid(row=i + 1, column=1, sticky="ew", padx=4, pady=3)
            ttk.Entry(box, textvariable=rng, width=12, font=("Consolas", 10)).grid(row=i + 1, column=2, sticky="ew",
                                                                                   padx=4, pady=3)
            ttk.Checkbutton(box, variable=header).grid(row=i + 1, column=3, pady=3)
            ttk.Button(box, text="Pick...", width=7,
                       command=lambda i=i: self.open_picker(i)).grid(row=i + 1, column=4, padx=(4, 0), pady=3)
        ttk.Label(box, style="Small.TLabel", wraplength=self.px(500), justify="left",
                  text="Type a range like in a spreadsheet, or press Pick... and drag over a real file.\n"
                       "L1:M8  (also L1 x M8)  = from cell L1 to cell M8\n"
                       "B6  = one cell          A12:I*  = from A12 down to the first empty row"
                  ).grid(row=MAX_SELECTIONS + 1, column=0, columnspan=5, sticky="w", pady=(8, 0))

    def _build_delivery(self):
        tab = self.settings_tab("  Delivery  ")
        tab.columnconfigure(0, weight=1, uniform="half")
        tab.columnconfigure(1, weight=1, uniform="half")
        left, right = ttk.Frame(tab), ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        box = ttk.LabelFrame(left, text=" 5  Shared folder ", padding=10)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Checkbutton(box, text="Save every output file into a shared folder",
                        variable=self.b["shared_enabled"]).grid(row=0, column=0, columnspan=3, sticky="w")
        self._folder_row(box, 1, "Folder / UNC path", "shared_folder")
        ttk.Button(box, text="Test folder", command=self.test_shared).grid(row=2, column=1, sticky="w", padx=6,
                                                                           pady=(4, 0))

        box = ttk.LabelFrame(left, text=" 6  API link ", padding=10)
        box.pack(fill="x", pady=(10, 0))
        box.columnconfigure(1, weight=1)
        ttk.Checkbutton(box, text="Send every output file to the end user's site",
                        variable=self.b["api_enabled"]).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(box, text="API link (URL)").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(box, textvariable=self.v["api_url"]).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=3)
        ttk.Label(box, text="Token (optional)").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(box, textvariable=self.v["api_token"], show="●").grid(row=2, column=1, sticky="ew", padx=(6, 0),
                                                                        pady=3)
        ttk.Label(box, text="Send as").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(box, textvariable=self.v["api_body"], values=list(BODY_LABELS.values()), state="readonly",
                     width=26).grid(row=3, column=1, sticky="w", padx=(6, 0), pady=3)
        ttk.Label(box, text="Extra headers").grid(row=4, column=0, sticky="nw", pady=3)
        self.headers_text = tk.Text(box, height=3, width=30, font=("Consolas", 9), relief="solid", borderwidth=1)
        self.headers_text.grid(row=4, column=1, sticky="ew", padx=(6, 0), pady=3)
        ttk.Label(box, style="Small.TLabel", wraplength=self.px(380), justify="left",
                  text="One per line, for example  X-Api-Key: abc123. The token is sent as "
                       "Authorization: Bearer <token> and is stored encrypted.").grid(row=5, column=1, sticky="w",
                                                                                      padx=(6, 0))
        ttk.Button(box, text="Test connection", command=self.test_api).grid(row=6, column=1, sticky="w", padx=6,
                                                                           pady=(6, 0))

        box = ttk.LabelFrame(right, text=" Options ", padding=10)
        box.pack(fill="x")
        ttk.Checkbutton(box, text="Start monitoring when the program opens",
                        variable=self.b["autostart"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Never send the same file content twice",
                        variable=self.b["skip_duplicates"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(box, text="Check the folder every").grid(row=2, column=0, sticky="w", pady=3)
        row = ttk.Frame(box)
        row.grid(row=2, column=1, sticky="w", padx=6)
        ttk.Entry(row, textvariable=self.v["poll_seconds"], width=5).pack(side="left")
        ttk.Label(row, text=" seconds").pack(side="left")
        ttk.Label(box, text="Machine file encoding").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(box, textvariable=self.v["encoding"], values=ENCODINGS, width=12).grid(row=3, column=1,
                                                                                            sticky="w", padx=6)
        ttk.Label(box, text="Column separator").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Combobox(box, textvariable=self.v["delimiter"], values=DELIMITER_CHOICES, state="readonly",
                     width=12).grid(row=4, column=1, sticky="w", padx=6)

        box = ttk.LabelFrame(right, text=" How delivery works ", padding=10)
        box.pack(fill="x", pady=(10, 0))
        ttk.Label(box, wraplength=self.px(470), justify="left", text=(
            "Turn on one destination or both. Each output file is first placed in a queue on this PC and removed "
            "from it only after it has arrived.\n\n"
            "If the shared folder or the link cannot be reached, the files wait and are sent in their original "
            "order once it is back. The machine files are still processed and backed up meanwhile.\n\n"
            "If the receiving site refuses one particular file (for example HTTP 400 or 422), that file is put "
            "aside in data\\rejected and the others continue.")).pack(anchor="w")

    def _build_pick(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.pick_tab = tab
        self.tabs.add(tab, text="  Pick ranges & preview  ")
        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Button(top, text="Open CSV file...", command=self.open_sample_dialog).pack(side="left")
        ttk.Button(top, text="Newest machine file", command=self.open_newest).pack(side="left", padx=6)
        ttk.Button(top, text="Load demo sample", command=self.load_demo_sample).pack(side="left", padx=6)
        self.sample_label = ttk.Label(top, text="No file open", style="Small.TLabel")
        self.sample_label.pack(side="left", padx=8)
        for fmt in reversed(FORMATS):
            ttk.Radiobutton(top, text=fmt.upper(), value=fmt, variable=self.v["output_format"],
                            command=self.refresh_preview).pack(side="right", padx=(10, 0))
        ttk.Label(top, text="Output preview as").pack(side="right")

        panes = ttk.PanedWindow(tab, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=8)
        left = ttk.Frame(panes)
        self.grid_view = Grid(left, on_select=self._grid_selected)
        self.grid_view.pack(fill="both", expand=True)
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Label(bar, text="Marked").pack(side="left")
        ttk.Entry(bar, textvariable=self.pick_range, width=11, font=("Consolas", 10),
                  state="readonly").pack(side="left", padx=(4, 10))
        ttk.Checkbutton(bar, text="down to first empty row", variable=self.pick_open,
                        command=self._update_pick_range).pack(side="left")
        ttk.Label(bar, text="Slot").pack(side="left", padx=(12, 4))
        self.slot_box = ttk.Combobox(bar, state="readonly", width=20)
        self.slot_box.pack(side="left")
        self.slot_box.bind("<<ComboboxSelected>>", self._slot_chosen)
        ttk.Label(bar, text="Name").pack(side="left", padx=(10, 4))
        ttk.Entry(bar, textvariable=self.pick_name, width=14).pack(side="left")
        ttk.Button(bar, text="Assign", style="Go.TButton", width=8, command=self.assign).pack(side="left", padx=(8, 4))
        ttk.Button(bar, text="Clear slot", command=self.clear_slot).pack(side="left")
        panes.add(left, weight=3)

        right = ttk.Frame(panes)
        self.preview_note = ttk.Label(right, text="", foreground=AMBER, wraplength=self.px(360), justify="left")
        self.preview_note.pack(fill="x")
        self.preview = tk.Text(right, wrap="none", font=("Consolas", 9), width=44, state="disabled",
                               relief="solid", borderwidth=1)
        ys = ttk.Scrollbar(right, orient="vertical", command=self.preview.yview)
        xs = ttk.Scrollbar(right, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        xs.pack(side="bottom", fill="x")
        ys.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)
        panes.add(right, weight=2)

    def _build_history(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="  History  ")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="Open back-up folder",
                   command=lambda: self._open_folder(self.cfg["backup_folder"])).pack(side="left")
        ttk.Button(bar, text="Open data folder (queue, rejected, log file)",
                   command=lambda: self._open_folder(self.data_dir)).pack(side="left", padx=6)
        ttk.Label(bar, text="Latest 200 files", style="Small.TLabel").pack(side="right")
        cols = (("processed_at", "Time", 140), ("source", "Machine file", 200), ("status", "Result", 80),
                ("output", "Output file", 250), ("shared", "Shared folder", 100), ("api", "API", 110),
                ("message", "Note", 300))
        self.history = ttk.Treeview(tab, columns=[c[0] for c in cols], show="headings")
        for key, title, width in cols:
            self.history.heading(key, text=title, anchor="w")
            self.history.column(key, width=self.px(width), anchor="w", stretch=key == "message")
        self.history.tag_configure("failed", foreground=RED)
        self.history.tag_configure("duplicate", foreground=GREY)
        self.history.tag_configure("pending", foreground=AMBER)
        ys = ttk.Scrollbar(tab, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.history.pack(fill="both", expand=True)

    def _build_log(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="  Log  ")
        self.log_text = tk.Text(tab, wrap="none", font=("Consolas", 9), state="disabled", relief="solid",
                                borderwidth=1)
        self.log_text.tag_configure("WARNING", foreground=AMBER)
        self.log_text.tag_configure("ERROR", foreground=RED)
        ys = ttk.Scrollbar(tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    def _build_footer(self):
        bar = ttk.Frame(self, padding=(12, 8))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_msg, style="Small.TLabel").pack(side="left")

    def _build_checks(self):
        tab = self.settings_tab("  Checks  ")
        ttk.Label(tab, text="Check your current settings before starting", style="Go.TLabel").pack(anchor="w")
        ttk.Label(tab, wraplength=self.px(950), text="These checks inspect folders and the sample currently "
                  "open in the range picker. A successful connection test confirms that a test was accepted. "
                  "Use Verify demo pipeline for a full simulated machine-to-MES delivery test."
                  ).pack(anchor="w", pady=8)
        self.check_api_choice = tk.BooleanVar(self, False)
        ttk.Checkbutton(tab, text="Send a small API test file to the entered link",
                        variable=self.check_api_choice).pack(anchor="w")
        self.check_button = ttk.Button(tab, text="Run checks", command=self.run_checks)
        self.check_button.pack(anchor="w", pady=8)
        self.check_result = tk.Text(tab, height=16, width=95, wrap="word", state="disabled",
                                    font=("Consolas", 10))
        self.check_result.pack(fill="both", expand=True)
        ttk.Button(tab, text="Verify demo pipeline (invented data)", command=self.verify_demo).pack(
            anchor="w", pady=8)

    def run_checks(self):
        from .checks import run_checks
        cfg = self.collect()
        rows = self.sample_rows if self.sample_name else None
        sample_name = self.sample_name
        check_api = self.check_api_choice.get()
        self.check_button.configure(state="disabled")
        self.status_msg.set("Checking folders, extraction and enabled destinations...")

        def show(text):
            self.check_result.configure(state="normal")
            self.check_result.delete("1.0", "end")
            self.check_result.insert("1.0", text)
            self.check_result.configure(state="disabled")
            self.check_button.configure(state="normal")
            self.status_msg.set("Checks finished. Review results before starting.")

        def job():
            try:
                result = run_checks(cfg, self.data_dir, rows, sample_name, check_api)
                text = "\n\n".join(f"{c['status']} — {c['name']}\n{c['detail']}" for c in result)
            except Exception as exc:
                text = f"FAIL — Check could not complete\n{exc}"
            self.ui_q.put(lambda: show(text))
        threading.Thread(target=job, daemon=True).start()

    def verify_demo(self):
        from demo.verify import run_verification
        self.status_msg.set("Verifying an isolated demo with real HTTP delivery...")

        def work():
            result = run_verification()
            lines = [f"{'PASS' if c['ok'] else 'FAIL'}: {c['name']}\n{c['detail']}" for c in result["checks"]]
            summary = "\n\n".join(lines) + f"\n\nEvidence and report: {result['workspace']}"
            if not result["ok"]:
                raise RuntimeError(summary)
            return summary
        self._in_background("Demo pipeline verification", work)

    # ================================================================ run control
    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def _apply(self, cfg):
        save_config(cfg, self.cfg_path)
        self.cfg = cfg
        self.fill(cfg)

    def save(self):
        cfg = self.collect()
        problems = engine.validate(cfg)
        if self.running and problems:
            messagebox.showerror(APP_NAME, "Monitoring is running, so the settings must stay complete.\n\n- "
                                 + "\n- ".join(problems), parent=self)
            return False
        self._apply(cfg)
        if self.running:
            self.engine.cfg = cfg
        log.info("Settings saved")
        self.status_msg.set("Settings saved." if not problems else "Settings saved. Before starting: " + problems[0])
        return True

    def _ready(self, quiet=False):
        cfg = self.collect()
        problems = engine.validate(cfg)
        if problems:
            if quiet:
                log.error("Monitoring was not started: %s", " ".join(problems))
            else:
                messagebox.showerror(APP_NAME, "Please complete the settings first.\n\n- " + "\n- ".join(problems),
                                     parent=self)
            return None
        self._apply(cfg)
        return cfg

    def start(self, quiet=False):
        if self.running or self.busy:
            return
        cfg = self._ready(quiet)
        if cfg:
            self.engine = engine.Engine(cfg, self.data_dir)
            self.thread = threading.Thread(target=self.engine.run_forever, daemon=True)
            self.thread.start()
            self.status_msg.set("Monitoring started.")

    def stop(self):
        if self.running:
            self.engine.stop()
            self.status_msg.set("Stopping after the current file...")

    def process_now(self):
        if self.running or self.busy:
            return
        cfg = self._ready()
        if not cfg:
            return
        self.busy = True

        def job():
            try:
                n = engine.Engine(cfg, self.data_dir).scan_once()
                log.info("Process now finished: %d file(s) processed", n)
            except Exception as e:
                log.error("Process now failed: %s", e)
            finally:
                self.busy = False
        threading.Thread(target=job, daemon=True).start()

    def on_close(self):
        if self.running and not messagebox.askokcancel(
                APP_NAME, "Monitoring is running.\n\nClosing the window stops it; machine files will wait in the "
                          "folder until the program is started again.\n\nClose anyway?", parent=self):
            return
        self.closing.set()
        if self.demo_session:
            self.demo_session.stop_machine()
        if self.engine:
            self.engine.stop()

        def finish():
            if self.running or self.busy:
                self.status_msg.set("Finishing the current file before closing...")
                self.after(100, finish)
            else:
                self.destroy()
        finish()

    # ================================================================ tests for the two destinations
    def _in_background(self, title, work):
        def job():
            try:
                result = (True, work())
            except Exception as e:
                result = (False, str(e))
            show = messagebox.showinfo if result[0] else messagebox.showerror
            self.ui_q.put(lambda: show(title, result[1], parent=self))
        threading.Thread(target=job, daemon=True).start()

    def test_shared(self):
        cfg = self.collect()
        if not cfg["shared_folder"]:
            return messagebox.showerror("Shared folder", "Enter the shared folder first.", parent=self)
        self._in_background("Shared folder", lambda: f"The folder can be written to:\n{engine.test_shared(cfg)}")

    def test_api(self):
        cfg = self.collect()
        if not cfg["api_url"]:
            return messagebox.showerror("API link", "Enter the API link first.", parent=self)
        self._in_background("API link", lambda: f"A small test file was accepted (HTTP {engine.test_api(cfg)}).")

    def _open_folder(self, folder):
        if folder and Path(folder).is_dir():
            from demo.control import open_path
            open_path(folder)
        else:
            messagebox.showinfo(APP_NAME, "That folder does not exist yet.", parent=self)

    # ================================================================ pick ranges & preview
    def _tab_changed(self, _event=None):
        if self.tabs.select() == str(self.pick_tab):
            self._update_slots()
            self.refresh_preview()

    def open_picker(self, slot):
        self.tabs.select(self.pick_tab)
        self._update_slots()
        self.slot_box.current(slot)
        self._slot_chosen()
        if not self.sample_rows and not self.open_newest(quiet=True):
            self.open_sample_dialog()

    def open_csv(self):
        self.tabs.select(self.pick_tab)
        self.open_sample_dialog()

    def load_demo_sample(self):
        from demo.testdata import CASES
        self.sample_rows = engine.read_rows(CASES[0].data)
        self.sample_name = CASES[0].name
        self.sample_label.configure(text="Invented demo sample: " + self.sample_name)
        self.pick_range.set("")
        self.grid_view.set_rows(self.sample_rows)
        self.refresh_preview()

    def open_sample_dialog(self):
        start = self.v["source_folder"].get() if Path(self.v["source_folder"].get() or "?").is_dir() else None
        path = filedialog.askopenfilename(parent=self, initialdir=start,
                                          filetypes=[("Machine files", "*.csv *.txt *.log"), ("All files", "*.*")])
        if path:
            self.open_sample(path)

    def open_newest(self, quiet=False):
        """Load the most recent machine file: one still waiting in the folder, else the latest in the back-up."""
        cfg, found = self.collect(), []
        for folder, pattern in ((cfg["source_folder"], cfg["file_pattern"]),
                                (cfg["backup_folder"], "*/" + cfg["file_pattern"])):
            try:
                found = [f for f in Path(folder).glob(pattern) if f.is_file()] if folder else []
            except OSError:
                found = []
            if found:
                break
        if found:
            return self.open_sample(max(found, key=lambda f: f.stat().st_mtime))
        if not quiet:
            messagebox.showinfo(APP_NAME, "No machine file was found in the machine folder or the back-up folder.",
                                parent=self)
        return False

    def open_sample(self, path):
        cfg = self.collect()
        try:
            rows = engine.read_rows(Path(path), cfg["encoding"], cfg["delimiter"])
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Cannot read {path}:\n{e}", parent=self)
            return False
        self.sample_rows, self.sample_name = rows, Path(path).name
        width = max((len(r) for r in rows), default=0)
        self.sample_label.configure(text=f"{self.sample_name}   ({len(rows)} rows x {width} columns)")
        self.pick_range.set("")
        self.grid_view.set_rows(rows)
        self.refresh_preview()
        return True

    def _slot(self):
        return max(self.slot_box.current(), 0)

    def _update_slots(self):
        current = self.slot_box.current()
        labels = []
        for i, (name, rng, _) in enumerate(self.sel_vars):
            labels.append(f"{i + 1}:  {name.get() or rng.get() or '(empty)'}")
        self.slot_box.configure(values=labels)
        if current < 0:  # first visit: offer the first free slot
            current = next((i for i, (_, rng, _) in enumerate(self.sel_vars) if not rng.get().strip()), 0)
        self.slot_box.current(current)

    def _slot_chosen(self, _event=None):
        name, rng, _ = self.sel_vars[self._slot()]
        self.pick_name.set(name.get())
        if self.sample_rows and rng.get().strip():
            try:
                c1, r1, c2, r2 = engine.resolve_range(self.sample_rows, rng.get())
            except ValueError:
                return
            self.grid_view.see(c1, r1)

    def _grid_selected(self, _bounds):
        self._update_pick_range()

    def _update_pick_range(self):
        if self.grid_view.sel:
            c1, r1, c2, r2 = self.grid_view.sel
            self.pick_range.set(engine.format_range(c1, r1, c2, None if self.pick_open.get() else r2))

    def assign(self):
        if not self.pick_range.get():
            return messagebox.showinfo(APP_NAME, "Drag over the cells you want in the grid first.", parent=self)
        name, rng, _ = self.sel_vars[self._slot()]
        rng.set(self.pick_range.get())
        name.set(self.pick_name.get().strip())
        self.grid_view.set_selection(None)
        self.pick_range.set("")
        self._update_slots()
        self.refresh_preview()
        self.status_msg.set(f"Slot {self._slot() + 1} set to {rng.get()}. Press Save settings to keep it.")

    def clear_slot(self):
        name, rng, _ = self.sel_vars[self._slot()]
        name.set(""), rng.set(""), self.pick_name.set("")
        self._update_slots()
        self.refresh_preview()

    def refresh_preview(self):
        cfg = self.collect()
        marks, note, text = [], "", ""
        if self.sample_rows:
            for i, sel in enumerate(cfg["selections"]):
                try:
                    marks.append((i, engine.resolve_range(self.sample_rows, sel["range"])))
                except ValueError:
                    pass
            try:
                text, _, warnings = engine.build_output(self.sample_name, self.sample_rows, cfg)
                note = "\n".join(w[0].upper() + w[1:] for w in warnings)
            except engine.FileError as e:
                note = f"With these ranges this file would be rejected: {e}."
            except ValueError:
                text = "No cell range yet.\n\nDrag over cells in the grid, choose a slot and press Assign."
        self.grid_view.set_marks(marks)
        self.preview_note.configure(text=note)
        if len(text) > PREVIEW_LIMIT:
            text = text[:PREVIEW_LIMIT] + "\n... (preview shortened)"
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    # ================================================================ live status
    def _stats_loop(self):
        """Runs on its own thread so a slow network folder can never freeze the window."""
        while not self.closing.is_set():
            try:
                cfg = self.cfg
                stats = self.ledger.stats()
                self.snapshot = {
                    "ok": stats.get("ok", 0), "failed": stats.get("failed", 0),
                    "waiting": engine.count_waiting(cfg) if cfg["source_folder"] else 0,
                    "queued": sum(engine.count_queued(self.data_dir).values()),
                    "rejected": engine.count_rejected(self.data_dir),
                    "history": self.ledger.recent(200)}
            except Exception as e:
                log.debug("status refresh failed: %s", e)
            self.closing.wait(1.5)

    def _tick(self):
        while True:
            try:
                self.ui_q.get_nowait()()
            except queue.Empty:
                break
        lines = []
        while len(lines) < 500:
            try:
                lines.append(self.log_q.get_nowait())
            except queue.Empty:
                break
        if lines:
            at_end = self.log_text.yview()[1] > 0.999
            self.log_text.configure(state="normal")
            for level, line in lines:
                self.log_text.insert("end", line + "\n", level)
            if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
                self.log_text.delete("1.0", "1000.0")
            self.log_text.configure(state="disabled")
            if at_end:
                self.log_text.see("end")

        running = self.running
        colors = self.appearance.colors
        text, colour = ("Monitoring", colors["success"]) if running else (
            "Working...", colors["warning"]) if self.busy else ("Stopped", colors["muted"])
        self.state_label.configure(text=text)
        self.state_dot.configure(fg=colour)
        idle = not running and not self.busy
        self.start_btn.configure(state="normal" if idle else "disabled")
        self.once_btn.configure(state="normal" if idle else "disabled")
        self.stop_btn.configure(state="normal" if running else "disabled")
        if not running and self.status_msg.get().startswith("Stopping"):
            self.status_msg.set("Monitoring stopped.")

        snap = self.snapshot
        if snap:
            for key, label in self.counts.items():
                colour = colors["error"] if key in ("failed", "rejected") and snap[key] else colors[
                    "warning"] if key == "queued" and snap[key] else colors["text"]
                label.configure(text=str(snap[key]), foreground=colour)
            if snap["history"] != self.shown_history:
                self.shown_history = snap["history"]
                self.history.delete(*self.history.get_children())
                for row in self.shown_history:
                    pending = "queued" in (row["shared"], row["api"]) or "rejected" in (row["api"] or "")
                    tag = row["status"] if row["status"] != "ok" else "pending" if pending else ""
                    self.history.insert("", "end", tags=(tag,), values=[
                        (row["processed_at"] or "").replace("T", "  "), row["source"], row["status"],
                        row["output"] or "", row["shared"] or "", row["api"] or "", row["message"] or ""])
        self.after(300, self._tick)


def run(data_dir, demo_session=None):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass
    App(data_dir, demo_session=demo_session).mainloop()
