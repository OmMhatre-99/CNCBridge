"""CNCBridge main window (Tkinter). The engine runs on a worker thread; the window only reads snapshots."""
import ctypes
import logging
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import engine
from .config import APP_NAME, FORMATS, MAX_MANUAL, MAX_SELECTIONS, VERSION, load_config, normalize_config, save_config
from .font_registration import register_bundled_fonts
from .tooltip import NotebookTabTooltip, Tooltip
from .grid import MARK_COLOURS, Grid
from .theme import (ACCENT, ACCENT_HOVER, ACCENT_TINT, ACTIVE_HEADER, BACKGROUND, BORDER,
                    DISABLED_ACCENT, DISABLED_SURFACE, DISABLED_TEXT, ERROR, FOCUS, FOCUS_SURFACE,
                    HOVER_SURFACE, IDLE, MUTED, SUCCESS, SURFACE, SURFACE_ALT, TEXT, WARNING,
                    WHITE, apply_tk_palette, resolve_font_families)

log = logging.getLogger("cncbridge")

BODY_LABELS = {"raw": "File as request body", "multipart": "Form upload (multipart)"}
ENCODINGS = ("auto", "utf-8", "cp1252", "utf-16", "shift_jis", "gbk", "big5", "euc_kr")
DELIMITER_CHOICES = ("auto", ",", ";", "tab", "|")
PREVIEW_LIMIT = 200_000


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put((record.levelname, self.format(record)))


class App(tk.Tk):
    def __init__(self, data_dir):
        self.bundled_fonts = register_bundled_fonts()
        super().__init__()
        self.data_dir = Path(data_dir)
        self.cfg_path = self.data_dir / "config.json"
        self.cfg = load_config(self.cfg_path)
        self.ledger = engine.Ledger(self.data_dir / "ledger.db")
        self.engine = self.thread = None
        self.busy = False            # a one-off "Process now" run is in progress
        self.closing = threading.Event()
        self.snapshot = None         # latest counters + history, produced by the stats thread
        self.shown_history = None
        self.sample_rows, self.sample_name = [], ""
        self.ui_q, self.log_q = queue.Queue(), queue.Queue()

        handler = QueueHandler(self.log_q)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)

        self.title(f"{APP_NAME} {VERSION}")
        self.scale = self.winfo_fpixels("1i") / 96  # fonts grow with the Windows display scale; sizes must follow
        # never taller or wider than the screen (minus taskbar and title bar), or the Save button ends up off-screen
        width = min(self.px(1240), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(790), self.winfo_screenheight() - self.px(100))
        self.geometry(f"{width}x{height}+{(self.winfo_screenwidth() - width) // 2}+{self.px(8)}")
        self.minsize(min(self.px(1020), width), min(self.px(660), height))
        self.font_families = resolve_font_families(self)
        body = self.font_families["body"]
        heading = self.font_families["heading"]
        mono = self.font_families["mono"]
        apply_tk_palette(self, self.font_families)
        self.configure(background=BACKGROUND)
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=(body, 11), foreground=TEXT)
        style.configure("TFrame", background=BACKGROUND)
        style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
        style.configure("Small.TLabel", background=BACKGROUND, foreground=MUTED)
        style.configure("PageTitle.TLabel", background=BACKGROUND, foreground=TEXT,
                        font=(heading, 19, "bold"))
        style.configure("PageHint.TLabel", background=BACKGROUND, foreground=MUTED, font=(body, 11))
        style.configure("EmptyState.TLabel", background=SURFACE, foreground=MUTED,
                        font=(body, 12), justify="center", padding=(20, 14))
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("Card.TLabel", background=BACKGROUND, foreground=TEXT)
        style.configure("CardSmall.TLabel", background=BACKGROUND, foreground=MUTED)
        style.configure("MetricSmall.TLabel", background=SURFACE, foreground=MUTED)
        style.configure("MetricValue.TLabel", background=SURFACE, foreground=TEXT,
                        font=(body, 16, "bold"))
        style.configure("TLabelframe", background=BACKGROUND, bordercolor=BORDER, relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=BACKGROUND, foreground=TEXT,
                        font=(heading, 12, "bold"))
        style.configure("Count.TLabel", background=BACKGROUND, foreground=TEXT, font=(body, 16, "bold"))
        style.configure("Metric.TFrame", background=SURFACE, relief="solid", borderwidth=1)
        style.configure("TButton", padding=(self.px(11), self.px(6)), background=SURFACE,
                        bordercolor=BORDER, focuscolor=FOCUS)
        style.map("TButton", background=[("active", HOVER_SURFACE), ("disabled", DISABLED_SURFACE)],
                  foreground=[("disabled", DISABLED_TEXT)], bordercolor=[("focus", FOCUS), ("active", ACCENT)])
        style.configure("Go.TButton", font=(body, 11, "bold"), background=ACCENT,
                        foreground=WHITE, bordercolor=ACCENT)
        style.map("Go.TButton", background=[("active", ACCENT_HOVER), ("disabled", DISABLED_ACCENT)],
                  foreground=[("disabled", WHITE)], bordercolor=[("focus", FOCUS)])
        style.configure("Toolbutton", padding=(self.px(10), self.px(6)), background=SURFACE,
                        bordercolor=BORDER, focuscolor=FOCUS)
        style.map("Toolbutton", background=[("active", HOVER_SURFACE)], bordercolor=[("focus", FOCUS),
                                                                                   ("active", ACCENT)])
        style.configure("Go.Toolbutton", font=(body, 11, "bold"), background=ACCENT,
                        foreground=WHITE, bordercolor=ACCENT)
        style.map("Go.Toolbutton", background=[("active", ACCENT_HOVER), ("disabled", DISABLED_ACCENT)],
                  foreground=[("disabled", WHITE)], bordercolor=[("focus", FOCUS)])
        style.configure("TEntry", padding=(self.px(7), self.px(5)), fieldbackground=SURFACE,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, insertcolor=TEXT)
        style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)], fieldbackground=[("disabled", DISABLED_SURFACE)],
                  foreground=[("disabled", DISABLED_TEXT)])
        style.configure("TCombobox", padding=(self.px(7), self.px(5)), fieldbackground=SURFACE,
                        bordercolor=BORDER, arrowcolor=TEXT, focuscolor=FOCUS)
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE), ("disabled", SURFACE_ALT)],
                  bordercolor=[("focus", ACCENT), ("active", ACCENT)],
                  foreground=[("disabled", DISABLED_TEXT)], arrowcolor=[("disabled", MUTED)])
        style.configure("TCheckbutton", background=BACKGROUND, foreground=TEXT, padding=(0, self.px(3)),
                        focuscolor=FOCUS)
        style.map("TCheckbutton", background=[("active", BACKGROUND), ("disabled", BACKGROUND)],
                  foreground=[("disabled", MUTED)])
        style.configure("TRadiobutton", background=BACKGROUND, foreground=TEXT, padding=(0, self.px(4)),
                        focuscolor=FOCUS)
        style.map("TRadiobutton", background=[("active", BACKGROUND), ("disabled", BACKGROUND)],
                  foreground=[("disabled", MUTED)])
        style.configure("TScrollbar", background=SURFACE_ALT, troughcolor=BACKGROUND,
                        bordercolor=BORDER, arrowcolor=MUTED, lightcolor=SURFACE_ALT, darkcolor=BORDER)
        style.map("TScrollbar", background=[("active", ACCENT_TINT), ("pressed", ACCENT_TINT)],
                  arrowcolor=[("active", ACCENT)])
        style.configure("TPanedwindow", background=BACKGROUND)
        style.configure("Sash", background=BORDER, sashthickness=self.px(5))
        style.configure("TNotebook", background=BACKGROUND, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=BACKGROUND, foreground=MUTED,
                        padding=(self.px(15), self.px(9)), font=(body, 11, "bold"))
        style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", SURFACE_ALT)],
                  foreground=[("selected", ACCENT), ("active", TEXT)], bordercolor=[("focus", FOCUS)])
        style.configure("Treeview", rowheight=self.px(30), background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, bordercolor=BORDER, font=(body, 11))
        style.configure("History.Treeview", rowheight=self.px(30), background=SURFACE,
                        fieldbackground=SURFACE, foreground=TEXT, bordercolor=BORDER, font=(mono, 11))
        style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=TEXT,
                        font=(body, 10, "bold"), padding=(self.px(8), self.px(8)))
        style.configure("History.Treeview.Heading", background=SURFACE_ALT, foreground=TEXT,
                        font=(body, 10, "bold"), padding=(self.px(8), self.px(8)))
        style.map("Treeview", background=[("selected", ACCENT_TINT), ("focus", FOCUS_SURFACE)],
                  foreground=[("selected", TEXT)])
        style.map("Treeview.Heading", background=[("active", ACTIVE_HEADER)],
                  foreground=[("active", TEXT)])
        style.configure("TScrollbar", arrowsize=self.px(15))
        self._font_body, self._font_heading, self._font_mono = body, heading, mono

        self._make_vars()
        self._build_toolbar()
        self._build_header()
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(4, 0))
        self._build_setup()
        self._build_delivery()
        self._build_pick()
        self._build_history()
        self._build_log()
        self._build_footer()
        self.fill(self.cfg)
        self._add_tooltips()
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        threading.Thread(target=self._stats_loop, daemon=True).start()
        self.after(200, self._tick)
        if self.cfg["autostart"]:
            self.after(400, lambda: self.start(quiet=True))

    def px(self, n):
        return int(n * self.scale)

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

        self.field_widgets = {}


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
    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x")

        def button(text, command, style="Toolbutton"):
            b = ttk.Button(bar, text=text, style=style, command=command)
            b.pack(side="left", padx=(0, 6))
            return b

        def divider():
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=3)

        self.start_btn = button("▶  Start monitoring", self.start, "Go.Toolbutton")
        self.stop_btn = button("■  Stop", self.stop)
        self.once_btn = button("↻  Process now", self.process_now)
        divider()
        self.save_btn = button("✔  Save settings", self.save)
        divider()
        self.open_csv_btn = button("▦  Open CSV file...", self.open_csv)
        folders = ttk.Menubutton(bar, text="Open folder  ▾", style="Toolbutton")
        self.open_folders_btn = folders
        menu = tk.Menu(folders, tearoff=False, background=SURFACE, foreground=TEXT,
                       activebackground=ACCENT, activeforeground=WHITE, borderwidth=1,
                       relief="solid", selectcolor=ACCENT)
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
        bar = ttk.Frame(self, padding=(18, 10, 18, 10))
        bar.pack(fill="x")
        self.state_dot = tk.Label(bar, text="●", fg=IDLE, bg=BACKGROUND,
                                  font=(self._font_body, 15, "bold"))
        self.state_dot.pack(side="left")
        self.state_label = ttk.Label(bar, text="Stopped", font=(self._font_body, 12, "bold"), width=11)
        self.state_label.pack(side="left", padx=(4, 10))

        self.counts = {}
        for key, caption in reversed((("ok", "Processed"), ("waiting", "Waiting in folder"),
                                      ("queued", "Queued to send"), ("failed", "Failed files"),
                                      ("rejected", "Refused by API"))):
            box = ttk.Frame(bar, style="Metric.TFrame", padding=(12, 5))
            box.pack(side="right", padx=(8, 0))
            self.counts[key] = ttk.Label(box, text="0", style="MetricValue.TLabel", anchor="e")
            self.counts[key].pack(anchor="e")
            ttk.Label(box, text=caption, style="CardSmall.TLabel").pack(anchor="e")
        ttk.Separator(self).pack(fill="x")

    def _page_intro(self, parent, title, hint):
        intro = ttk.Frame(parent)
        intro.pack(fill="x", pady=(0, 14))
        ttk.Label(intro, text=title, style="PageTitle.TLabel").pack(anchor="w")
        hint_label = ttk.Label(intro, text=hint, style="PageHint.TLabel", wraplength=self.px(1100),
                               justify="left")
        hint_label.pack(anchor="w", pady=(3, 0), fill="x")
        self._fit_wrap(hint_label, self.px(1100))
        return intro

    def _fit_wrap(self, label, maximum):
        def resize(_event=None):
            width = label.winfo_width()
            if width > self.px(120):
                label.configure(wraplength=min(maximum, width - self.px(24)))

        label.bind("<Configure>", resize, add="+")
        self.after_idle(resize)

    def _folder_row(self, parent, row, label, key):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(parent, textvariable=self.v[key], font=(self._font_mono, 11))
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=5)
        self.field_widgets[key] = entry
        browse = ttk.Button(parent, text="Browse...", width=9,
                            command=lambda: self._browse(self.v[key]))
        browse.grid(row=row, column=2, padx=(2, 0), pady=5)
        self.field_widgets[f"{key}_browse"] = browse

    def _browse(self, var):
        start = var.get() if Path(var.get() or "?").is_dir() else None
        folder = filedialog.askdirectory(parent=self, initialdir=start, title="Choose a folder")
        if folder:
            var.set(os.path.normpath(folder))

    def _build_setup(self):
        tab = ttk.Frame(self.tabs, padding=(20, 18))
        self.tabs.add(tab, text="  Setup  ")
        self._page_intro(tab, "Set up your machine", "Choose where machine files arrive, what to include, and how outputs should look.")
        content = ttk.Frame(tab)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=4, uniform="setup")
        content.columnconfigure(1, weight=6, uniform="setup")
        content.rowconfigure(0, weight=1)
        left, right = ttk.Frame(content), ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        box = ttk.LabelFrame(left, text=" 1  Machine folders ", padding=(14, 10))
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Station name", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=5)
        station_entry = ttk.Entry(box, textvariable=self.v["station_name"])
        station_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=5)
        self.field_widgets["station_name"] = station_entry
        self._folder_row(box, 1, "Machine CSV folder", "source_folder")
        ttk.Label(box, text="File pattern", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=5)
        pattern_entry = ttk.Entry(box, textvariable=self.v["file_pattern"], width=14,
                                  font=(self._font_mono, 11))
        pattern_entry.grid(row=2, column=1, sticky="w", padx=8, pady=5)
        self.field_widgets["file_pattern"] = pattern_entry
        self._folder_row(box, 3, "Back-up folder", "backup_folder")
        folder_help = ttk.Label(box, style="Small.TLabel", wraplength=self.px(470), justify="left",
                                text="Each file is taken from the machine folder as soon as the machine has finished writing it. "
                                     "After processing it is moved into the back-up folder (one sub-folder per day).")
        folder_help.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(9, 2))
        self._fit_wrap(folder_help, self.px(470))

        box = ttk.LabelFrame(left, text=" 2  Output format ", padding=(14, 9))
        box.pack(fill="x", pady=(14, 0))
        for fmt in FORMATS:
            choice = ttk.Radiobutton(box, text=fmt.upper(), value=fmt, variable=self.v["output_format"])
            choice.pack(side="left", padx=(0, 28), pady=2)
            self.field_widgets[f"output_format_{fmt}"] = choice

        box = ttk.LabelFrame(left, text=" 4  Details added to every file ", padding=(14, 10))
        box.pack(fill="x", pady=(14, 0))
        box.columnconfigure(1, weight=1)
        box.columnconfigure(2, weight=2)
        ttk.Label(box, text="Field name", style="CardSmall.TLabel").grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(box, text="Value", style="CardSmall.TLabel").grid(row=0, column=2, sticky="w")
        for i, (name, value) in enumerate(self.man_vars, 1):
            ttk.Label(box, text=f"Input {i}", style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=5)
            ttk.Entry(box, textvariable=name).grid(row=i, column=1, sticky="ew", padx=8, pady=5)
            ttk.Entry(box, textvariable=value).grid(row=i, column=2, sticky="ew", pady=5)
        manual_help = ttk.Label(box, style="Small.TLabel", wraplength=self.px(470), justify="left",
                                 text="For example Operator and Work order. Change a value and press Save settings; "
                                      "it applies from the next file, without stopping.")
        manual_help.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 2))
        self._fit_wrap(manual_help, self.px(470))

        box = ttk.LabelFrame(right, text=" 3  Choose data to include (up to 10 ranges) ", padding=(14, 10))
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=3)
        box.columnconfigure(2, weight=2)
        for col, text in ((1, "Name in the output"), (2, "Cell range"), (3, "First row is header")):
            ttk.Label(box, text=text, style="CardSmall.TLabel").grid(row=0, column=col, sticky="w", padx=6, pady=(0, 6))
        for i, (name, rng, header) in enumerate(self.sel_vars):
            tk.Label(box, text=f" {i + 1} ", bg=MARK_COLOURS[i][1], fg="white",
                     font=(self._font_body, 9, "bold")).grid(row=i + 1, column=0, pady=3)
            name_entry = ttk.Entry(box, textvariable=name)
            name_entry.grid(row=i + 1, column=1, sticky="ew", padx=6, pady=4)
            range_entry = ttk.Entry(box, textvariable=rng, width=12, font=(self._font_mono, 11))
            range_entry.grid(row=i + 1, column=2, sticky="ew", padx=6, pady=4)
            header_check = ttk.Checkbutton(box, text="Yes", variable=header)
            header_check.grid(row=i + 1, column=3, pady=4)
            pick_button = ttk.Button(box, text="Pick...", width=7,
                                     command=lambda i=i: self.open_picker(i))
            pick_button.grid(row=i + 1, column=4, padx=(6, 0), pady=4)
            self.field_widgets[f"selection_name_{i}"] = name_entry
            self.field_widgets[f"selection_range_{i}"] = range_entry
            self.field_widgets[f"selection_header_{i}"] = header_check
            self.field_widgets[f"selection_pick_{i}"] = pick_button
        range_help = ttk.Label(box, style="CardSmall.TLabel", wraplength=self.px(650), justify="left",
                               text="Type a range like in a spreadsheet, or press Pick... and drag over a real file.\n"
                                    "L1:M8  (also L1 x M8)  = from cell L1 to cell M8\n"
                                    "B6  = one cell          A12:I*  = from A12 down to the first empty row")
        range_help.grid(row=MAX_SELECTIONS + 1, column=0, columnspan=5, sticky="ew", pady=(11, 2))
        self._fit_wrap(range_help, self.px(650))

    def _build_delivery(self):
        tab = ttk.Frame(self.tabs, padding=(20, 18))
        self.tabs.add(tab, text="  Delivery  ")
        self._page_intro(tab, "Choose where files are delivered",
                         "Enable one or both destinations. If a destination is unavailable, files wait safely and retry automatically.")
        content = ttk.Frame(tab)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=6, uniform="delivery")
        content.columnconfigure(1, weight=5, uniform="delivery")
        content.rowconfigure(0, weight=1)
        left, right = ttk.Frame(content), ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        box = ttk.LabelFrame(left, text=" 5  Shared folder ", padding=(14, 10))
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        shared_chk = ttk.Checkbutton(box, text="Save every output file into a shared folder",
                                     variable=self.b["shared_enabled"])
        shared_chk.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 5))
        self.field_widgets["shared_enabled"] = shared_chk
        self._folder_row(box, 1, "Folder / UNC path", "shared_folder")
        test_shared = ttk.Button(box, text="Test folder", command=self.test_shared)
        test_shared.grid(row=2, column=1, sticky="w", padx=8, pady=(5, 2))
        self.field_widgets["test_shared"] = test_shared

        box = ttk.LabelFrame(left, text=" 6  API connection ", padding=(14, 10))
        box.pack(fill="both", expand=True, pady=(14, 0))
        box.columnconfigure(1, weight=1)
        api_enabled = ttk.Checkbutton(box, text="Send every output file to the end user's site",
                                      variable=self.b["api_enabled"])
        api_enabled.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.field_widgets["api_enabled"] = api_enabled
        ttk.Label(box, text="API link (URL)", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=5)
        api_url = ttk.Entry(box, textvariable=self.v["api_url"], font=(self._font_mono, 11))
        api_url.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=5)
        self.field_widgets["api_url"] = api_url
        ttk.Label(box, text="Token (optional)", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=5)
        api_token = ttk.Entry(box, textvariable=self.v["api_token"], show="●")
        api_token.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=5)
        self.field_widgets["api_token"] = api_token
        ttk.Label(box, text="Send as", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=5)
        api_body = ttk.Combobox(box, textvariable=self.v["api_body"], values=list(BODY_LABELS.values()),
                                state="readonly", width=26)
        api_body.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=5)
        self.field_widgets["api_body"] = api_body
        ttk.Label(box, text="Extra headers", style="Card.TLabel").grid(row=4, column=0, sticky="nw", pady=5)
        self.headers_text = tk.Text(box, height=4, width=30, font=(self._font_mono, 11), relief="solid", borderwidth=1,
                                    background=SURFACE, foreground=TEXT, insertbackground=TEXT,
                                    highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                                    padx=7, pady=5, wrap="word", undo=True)
        self.headers_text.grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=5)
        self.field_widgets["api_headers"] = self.headers_text
        ttk.Label(box, style="CardSmall.TLabel", wraplength=self.px(420), justify="left",
                  text="One per line, for example  X-Api-Key: abc123. The token is sent as "
                       "Authorization: Bearer <token> and is stored encrypted.").grid(row=5, column=1, sticky="w",
                                                                                      padx=(8, 0), pady=(0, 3))
        test_api = ttk.Button(box, text="Test connection", command=self.test_api)
        test_api.grid(row=6, column=1, sticky="w", padx=8, pady=(7, 2))
        self.field_widgets["test_api"] = test_api

        box = ttk.LabelFrame(right, text=" Monitoring & file options ", padding=(14, 10))
        box.pack(fill="x")
        autostart = ttk.Checkbutton(box, text="Start monitoring when the program opens",
                                    variable=self.b["autostart"])
        autostart.grid(row=0, column=0, columnspan=2, sticky="w", pady=4)
        self.field_widgets["autostart"] = autostart
        skip_duplicates = ttk.Checkbutton(box, text="Never send the same file content twice",
                                          variable=self.b["skip_duplicates"])
        skip_duplicates.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.field_widgets["skip_duplicates"] = skip_duplicates
        ttk.Label(box, text="Check the folder every", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=5)
        row = ttk.Frame(box)
        row.grid(row=2, column=1, sticky="w", padx=8)
        poll_seconds = ttk.Entry(row, textvariable=self.v["poll_seconds"], width=5,
                                 font=(self._font_mono, 11))
        poll_seconds.pack(side="left")
        self.field_widgets["poll_seconds"] = poll_seconds
        ttk.Label(row, text=" seconds", style="Card.TLabel").pack(side="left", padx=(5, 0))
        ttk.Label(box, text="Machine file encoding", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=5)
        encoding = ttk.Combobox(box, textvariable=self.v["encoding"], values=ENCODINGS, width=12)
        encoding.grid(row=3, column=1, sticky="w", padx=8)
        self.field_widgets["encoding"] = encoding
        ttk.Label(box, text="Column separator", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=5)
        delimiter = ttk.Combobox(box, textvariable=self.v["delimiter"], values=DELIMITER_CHOICES,
                                 state="readonly", width=12)
        delimiter.grid(row=4, column=1, sticky="w", padx=8)
        self.field_widgets["delimiter"] = delimiter

        box = ttk.LabelFrame(right, text=" What happens to your files ", padding=(14, 10))
        box.pack(fill="both", expand=True, pady=(14, 0))
        ttk.Label(box, style="Card.TLabel", wraplength=self.px(420), justify="left", text=(
            "1. CNCBridge safely queues each output on this PC.\n\n"
            "2. It sends the file to the destinations you enable. If a destination is unavailable, delivery retries "
            "in order when it returns. Machine files continue to be processed and backed up.\n\n"
            "3. A file refused by the receiving site is set aside in data\\rejected so other files can continue."
        )).pack(anchor="nw", fill="x", pady=3)

    def _build_pick(self):
        tab = ttk.Frame(self.tabs, padding=(20, 18))
        self.pick_tab = tab
        self.tabs.add(tab, text="  Pick ranges & preview  ")
        self._page_intro(tab, "Pick data from a machine file", "Open a sample file, select the cells to include, then assign them to a range slot.")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 10))
        self.sample_open_btn = ttk.Button(top, text="Open CSV file...", style="Go.TButton",
                                          command=self.open_sample_dialog)
        self.sample_open_btn.pack(side="left")
        self.sample_newest_btn = ttk.Button(top, text="Newest machine file", command=self.open_newest)
        self.sample_newest_btn.pack(side="left", padx=8)
        self.sample_label = ttk.Label(top, text="No sample file open", style="PageHint.TLabel",
                                      font=(self._font_mono, 11))
        self.sample_label.pack(side="left", padx=12)
        for fmt in reversed(FORMATS):
            ttk.Radiobutton(top, text=fmt.upper(), value=fmt, variable=self.v["output_format"],
                            command=self.refresh_preview).pack(side="right", padx=(10, 0))
        ttk.Label(top, text="Output preview as").pack(side="right")

        panes = ttk.PanedWindow(tab, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(0, 4))
        left = ttk.Frame(panes)
        self.grid_view = Grid(left, on_select=self._grid_selected)
        self.grid_view.pack(fill="both", expand=True)
        bar = ttk.Frame(left, padding=(10, 8))
        bar.pack(fill="x", pady=(8, 0))
        ttk.Label(bar, text="Selected range", style="Card.TLabel").pack(side="left")
        pick_range_entry = ttk.Entry(bar, textvariable=self.pick_range, width=11, font=(self._font_mono, 11),
                                     state="readonly")
        pick_range_entry.pack(side="left", padx=(4, 10))
        self.field_widgets["pick_range"] = pick_range_entry
        pick_open = ttk.Checkbutton(bar, text="Down to first empty row", variable=self.pick_open,
                                    command=self._update_pick_range)
        pick_open.pack(side="left")
        self.field_widgets["pick_open"] = pick_open
        ttk.Label(bar, text="Slot", style="Card.TLabel").pack(side="left", padx=(12, 5))
        self.slot_box = ttk.Combobox(bar, state="readonly", width=22)
        self.slot_box.pack(side="left")
        self.slot_box.bind("<<ComboboxSelected>>", self._slot_chosen)
        ttk.Label(bar, text="Name", style="Card.TLabel").pack(side="left", padx=(12, 5))
        pick_name_entry = ttk.Entry(bar, textvariable=self.pick_name, width=16)
        pick_name_entry.pack(side="left")
        self.field_widgets["pick_name"] = pick_name_entry
        assign_button = ttk.Button(bar, text="Assign", style="Go.TButton", width=8, command=self.assign)
        assign_button.pack(side="left", padx=(8, 4))
        self.field_widgets["assign"] = assign_button
        clear_button = ttk.Button(bar, text="Clear slot", command=self.clear_slot)
        clear_button.pack(side="left")
        self.field_widgets["clear_slot"] = clear_button
        panes.add(left, weight=3)

        right = ttk.LabelFrame(panes, text=" Output preview ", padding=(12, 10))
        self.preview_note = ttk.Label(right, text="", foreground=WARNING, wraplength=self.px(360),
                                      justify="left", background=BACKGROUND)
        self.preview_note.pack(fill="x", pady=(0, 8))
        self.preview = tk.Text(right, wrap="none", font=(self._font_mono, 11), width=44, state="normal",
                               relief="solid", borderwidth=1, background=SURFACE, foreground=TEXT,
                               insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
                               highlightcolor=ACCENT, padx=8, pady=8, takefocus=True)
        ys = ttk.Scrollbar(right, orient="vertical", command=self.preview.yview)
        xs = ttk.Scrollbar(right, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        xs.pack(side="bottom", fill="x")
        ys.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)
        self.preview.insert("1.0", "Your output preview will appear here.\n\nOpen a sample machine file to get started.")
        self.preview.configure(state="disabled")
        panes.add(right, weight=2)

    def _build_history(self):
        tab = ttk.Frame(self.tabs, padding=(20, 18))
        self.tabs.add(tab, text="  History  ")
        self._page_intro(tab, "File history", "Review processed machine files and check whether each output reached its destination.")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Button(bar, text="Open back-up folder",
                   command=lambda: self._open_folder(self.cfg["backup_folder"])).pack(side="left")
        ttk.Button(bar, text="Open data folder (queue, rejected, log file)",
                   command=lambda: self._open_folder(self.data_dir)).pack(side="left", padx=6)
        ttk.Label(bar, text="Most recent 200 files", style="PageHint.TLabel").pack(side="right")
        cols = (("processed_at", "Time", 140), ("source", "Machine file", 200), ("status", "Result", 80),
                ("output", "Output file", 250), ("shared", "Shared folder", 100), ("api", "API", 110),
                ("message", "Note", 300))
        history_area = ttk.Frame(tab, style="Card.TFrame", padding=1)
        history_area.pack(fill="both", expand=True)
        self.history = ttk.Treeview(history_area, columns=[c[0] for c in cols], show="headings",
                                    style="History.Treeview", takefocus=True)
        for key, title, width in cols:
            self.history.heading(key, text=title, anchor="w")
            self.history.column(key, width=self.px(width), anchor="w", stretch=key == "message")
        self.history.tag_configure("failed", foreground=ERROR)
        self.history.tag_configure("duplicate", foreground=MUTED)
        self.history.tag_configure("pending", foreground=WARNING)
        ys = ttk.Scrollbar(history_area, orient="vertical", command=self.history.yview)
        xs = ttk.Scrollbar(history_area, orient="horizontal", command=self.history.xview)
        self.history.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.history.pack(fill="both", expand=True)
        self.history_empty = ttk.Label(history_area, style="EmptyState.TLabel",
                                       text="No files processed yet\n\nRecent file activity will appear here.")
        self.history_empty.place(relx=0.5, rely=0.5, anchor="center")

    def _build_log(self):
        tab = ttk.Frame(self.tabs, padding=(20, 18))
        self.tabs.add(tab, text="  Log  ")
        self._page_intro(tab, "Activity log", "Technical processing and delivery messages. Most day-to-day status is shown in File history.")
        log_area = ttk.Frame(tab, style="Card.TFrame", padding=1)
        log_area.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_area, wrap="none", font=(self._font_mono, 11), state="disabled",
                                relief="flat", borderwidth=0, background=SURFACE, foreground=TEXT,
                                insertbackground=TEXT, highlightthickness=0, padx=9, pady=9, takefocus=True)
        self.log_text.tag_configure("WARNING", foreground=WARNING)
        self.log_text.tag_configure("ERROR", foreground=ERROR)
        ys = ttk.Scrollbar(log_area, orient="vertical", command=self.log_text.yview)
        xs = ttk.Scrollbar(log_area, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.log_text.pack(fill="both", expand=True)
        self.log_empty = ttk.Label(log_area, style="EmptyState.TLabel",
                                   text="No activity messages yet\n\nMessages will appear as CNCBridge works.")
        self.log_empty.place(relx=0.5, rely=0.5, anchor="center")

    def _build_footer(self):
        bar = ttk.Frame(self, padding=(18, 10))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_msg, style="Small.TLabel").pack(side="left")

    # ================================================================ run control
    def _add_tooltips(self):
        """Attach concise help to the controls that benefit from extra explanation."""
        Tooltip(self.start_btn, "Start monitoring the source folder for new CNC files.")
        Tooltip(self.stop_btn, "Stop monitoring and processing.")
        Tooltip(self.once_btn, "Process the current files once without continuous monitoring.")
        Tooltip(self.save_btn, "Save all current settings to configuration.")
        Tooltip(self.open_csv_btn, "Open a machine CSV file in the range picker.")
        Tooltip(self.open_folders_btn, "Open a configured folder or CNCBridge's data folder.")
        Tooltip(self.sample_open_btn, "Choose a sample machine file to inspect and select cell ranges.")
        Tooltip(self.sample_newest_btn, "Open the newest file waiting in the machine folder or most recent back-up.")
        Tooltip(self.grid_view.canvas, "Drag across cells to select them. Arrow keys move; hold Shift to extend a range.")

        help_text = {
            "station_name": "Name used to identify this machine in output files.",
            "source_folder": "Folder where the machine places completed CSV files.",
            "source_folder_browse": "Choose the machine's CSV folder.",
            "file_pattern": "File names to watch; the default matches all CSV files.",
            "backup_folder": "Processed machine files are moved here, grouped by date.",
            "backup_folder_browse": "Choose where processed machine files are archived.",
            "shared_enabled": "Turn on delivery to a local or network shared folder.",
            "shared_folder": "Optional destination folder, such as a network share.",
            "shared_folder_browse": "Choose the shared destination folder.",
            "test_shared": "Check that CNCBridge can write to the shared folder.",
            "api_enabled": "Turn on delivery to the receiving site's API.",
            "api_url": "Address that receives the output files.",
            "api_token": "Optional authentication token; CNCBridge stores it encrypted.",
            "api_body": "Choose whether the file is sent as the request body or as a form upload.",
            "api_headers": "Optional HTTP headers, one per line in Name: value format.",
            "test_api": "Send a small test file to check the API connection.",
            "autostart": "Begin monitoring automatically when CNCBridge opens.",
            "skip_duplicates": "Ignore files whose content has already been processed.",
            "poll_seconds": "How often CNCBridge checks the machine folder for new files.",
            "encoding": "Choose a character encoding if CSV text looks incorrect; Auto is recommended.",
            "delimiter": "Choose the CSV column separator; Auto is recommended.",
            "pick_range": "Shows the cell range selected in the grid.",
            "pick_open": "Extend the selected range down to the first empty row.",
            "pick_name": "Name shown for this data in the output file.",
            "assign": "Save the selected range into the chosen extraction slot.",
            "clear_slot": "Remove the range and name from the chosen slot.",
        }
        for key, text in help_text.items():
            Tooltip(self.field_widgets[key], text)

        Tooltip(self.slot_box, "Choose which extraction slot receives the selected cells.")
        for fmt in FORMATS:
            Tooltip(self.field_widgets[f"output_format_{fmt}"], f"Create the output as {fmt.upper()}.")
        for i in range(MAX_SELECTIONS):
            Tooltip(self.field_widgets[f"selection_name_{i}"], "Optional label used for this extracted data.")
            Tooltip(self.field_widgets[f"selection_range_{i}"], "Spreadsheet-style cells to include, e.g. A1:C8.")
            Tooltip(self.field_widgets[f"selection_header_{i}"], "Use the first selected row as column names.")
            Tooltip(self.field_widgets[f"selection_pick_{i}"], "Choose this range by selecting cells in a sample file.")

        self.tab_tooltip = NotebookTabTooltip(self.tabs, {
            0: "Machine folders, output format, data ranges and manual inputs.",
            1: "Shared-folder and API destinations, plus file-handling options.",
            2: "Select cells from a sample file and preview the resulting output.",
            3: "Review recent machine files and their delivery results.",
            4: "Technical event log for processing and delivery details.",
        })
        Tooltip(self.slot_box, "Select which slot (range) you are editing.")

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
        if self.engine:
            self.engine.stop()
        self.destroy()

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
            os.startfile(folder)
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
        marks, note, text = [], "", "Your output preview will appear here.\n\nOpen a sample machine file to get started."
        if self.sample_rows:
            text = ""
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
                text = f"Preview unavailable\n\n{note}\n\nChoose a range that contains data in the sample file."
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
            self.log_empty.place_forget()
            if at_end:
                self.log_text.see("end")

        running = self.running
        text, colour = ("Monitoring", SUCCESS) if running else ("Working...", WARNING) if self.busy else ("Stopped", IDLE)
        self.state_label.configure(text=text)
        self.state_label.configure(foreground=colour)
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
                colour = (ERROR if snap[key] else TEXT) if key in ("failed", "rejected") else (
                    WARNING if snap[key] else TEXT) if key == "queued" else (
                    SUCCESS if snap[key] else TEXT) if key == "ok" else ACCENT if snap[key] else TEXT
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
                if self.shown_history:
                    self.history_empty.place_forget()
                else:
                    self.history_empty.place(relx=0.5, rely=0.5, anchor="center")
        self.after(300, self._tick)


def run(data_dir):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass
    App(data_dir).mainloop()
