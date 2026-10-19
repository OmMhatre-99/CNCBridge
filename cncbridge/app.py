"""CNCBridge main window (Tkinter). The engine runs on a worker thread; the window only reads snapshots."""
import ctypes
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import alerts, background, config, engine, shifts, theme
from . import help as help_text
from .config import (APP_NAME, FORMATS, MAX_MANUAL, MAX_SELECTIONS, VERSION, export_settings, import_settings,
                     load_config, normalize_config, save_config)
from .font_registration import register_bundled_fonts
from .tooltip import NotebookTabTooltip, Tooltip
from .grid import Grid

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


class ShiftDialog(tk.Toplevel):
    """
    Asks the operator, at a shift change, to confirm or change the typed values. It does not block anything:
    processing goes on, and closing it with Later leaves the banner up in the main window.
    """

    def __init__(self, app, boundary):
        super().__init__(app)
        self.app = app
        self.title("Shift change")
        self.transient(app)
        self.configure(background=theme.BACKGROUND)
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=f"A new shift started at {boundary:%H:%M}", font=(app._font_heading, 14, "bold")
                  ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, wraplength=app.px(420), justify="left", text=(
            "Are these values still right? Change them if the operator or the work order is different. "
            "Files keep being processed meanwhile.")).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))
        self.entries = []
        for i, (name, value) in enumerate(app.man_vars):
            ttk.Label(frame, text=name.get().strip() or f"Input {i + 1}", style="Card.TLabel").grid(
                row=2 + i, column=0, sticky="w", pady=5)
            var = tk.StringVar(self, value.get())
            ttk.Entry(frame, textvariable=var, width=34).grid(row=2 + i, column=1, sticky="ew", padx=(10, 0), pady=5)
            self.entries.append(var)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2 + len(self.entries), column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Later", command=self.later).pack(side="right")
        ttk.Button(buttons, text="Confirm", style="Go.TButton", command=self.confirm).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self.later)
        self.update_idletasks()  # centred over the main window
        x = app.winfo_rootx() + max((app.winfo_width() - self.winfo_reqwidth()) // 2, 0)
        y = app.winfo_rooty() + max((app.winfo_height() - self.winfo_reqheight()) // 3, 0)
        self.geometry(f"+{x}+{y}")
        self._raise_job = self.after(50, self.lift)

    def destroy(self):
        try:
            self.after_cancel(self._raise_job)  # no raise may run after the dialog is gone
        except (AttributeError, tk.TclError):
            pass
        super().destroy()

    def confirm(self):
        if self.app.finish_shift([v.get() for v in self.entries]):
            self.destroy()

    def later(self):
        self.destroy()


class App(tk.Tk):
    def __init__(self, data_dir, notice=None, lock=None, connected=False, resume=False):
        """
        resume: start monitoring at once (the window was restarted while monitoring).
        lock: the data folder's single-copy lock, held while this window processes files itself.
        connected: background processing is set up or running; this window shows and controls it.
        """
        self.data_dir = Path(data_dir)
        self.cfg_path = self.data_dir / "config.json"
        self.cfg = load_config(self.cfg_path)
        self.theme = theme.use(self.cfg["theme"])  # the look is chosen once, before any widget exists
        self.bundled_fonts = register_bundled_fonts() if self.theme.bundled_fonts else {}
        super().__init__()
        self.lock, self.connected = lock, connected
        self.bg_running = False          # connected: the background process is working (stats thread)
        self.take_over_when_stopped = False
        self._log_pos = None             # connected: how much of the background's log has been shown
        self.ledger = engine.Ledger(self.data_dir / "ledger.db")
        self.engine = self.thread = None
        self.simulation_process = None
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
        self.L = self.theme.layout
        wide, tall, min_wide, min_tall = self.L.window
        width = min(self.px(wide), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(tall), self.winfo_screenheight() - self.px(100))
        self.geometry(f"{width}x{height}+{(self.winfo_screenwidth() - width) // 2}+{self.px(8)}")
        self.minsize(min(self.px(min_wide), width), min(self.px(min_tall), height))
        self.font_families = theme.resolve_font_families(self, self.theme)
        body, heading, mono = (self.font_families[k] for k in ("body", "heading", "mono"))
        theme.apply_tk_palette(self, self.font_families, self.theme)
        self.configure(background=theme.BACKGROUND)
        style = ttk.Style(self)
        if self.theme.ttk_base and self.theme.ttk_base in style.theme_names():
            style.theme_use(self.theme.ttk_base)
        self.theme.configure_styles(style, self.px, self.font_families)
        self._font_body, self._font_heading, self._font_mono = body, heading, mono
        log.info("Theme: %s", self.theme.key)

        self._make_vars()
        self._build_menubar()
        self._build_toolbar()
        self._build_header()
        self._build_tabs_scroller()
        self._build_setup()
        self._build_delivery()
        self._build_pick()
        self._build_history()
        self._build_operator()
        self._build_footer()
        self._build_options()
        self._build_log()
        self._build_help()
        self.fill(self.cfg)
        self._add_tooltips()
        self._bind_shortcuts()
        self._apply_lock()
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        threading.Thread(target=self._stats_loop, daemon=True).start()
        self._tick_job = self.after(200, self._tick)
        if notice:  # e.g. the one-time move of the data folder
            self.after(600, lambda: messagebox.showinfo(APP_NAME, notice, parent=self))
        if self.cfg["autostart"] or resume:
            self.after(400, lambda: self.start(quiet=True))

    def strong(self, size):
        """Font for emphasised text in the current theme."""
        return theme.strong_font(self.font_families, size)

    @property
    def code_font(self):
        """Options for short code-like entries (file pattern, minutes): monospaced only if the theme wants it."""
        return {"font": (self._font_mono, self.L.range_size)} if self.L.code_mono else {}

    def px(self, n):
        return int(n * self.scale)

    # ================================================================ variables <-> settings
    def _make_vars(self):
        self.v = {k: tk.StringVar(self) for k in (
            "station_name", "source_folder", "file_pattern", "backup_folder", "output_format", "shared_folder",
            "api_url", "api_token", "api_body", "encoding", "delimiter", "poll_seconds",
            "complete_marker", "shift_times", "theme", "alert_after_minutes", "alert_webhook_url", "alert_email_server", "alert_email_port",
            "alert_email_user", "alert_email_password", "alert_email_from", "alert_email_to",
            "keep_backup_days", "keep_history_days")}
        self.b = {k: tk.BooleanVar(self) for k in ("shared_enabled", "api_enabled", "skip_duplicates", "autostart",
                                                    "json_numbers", "csv_flat", "time_zone",
                                                    "alert_notify", "alert_webhook_enabled", "alert_email_enabled",
                                                    "alert_email_starttls")}
        self.sel_vars = [(tk.StringVar(self), tk.StringVar(self), tk.BooleanVar(self)) for _ in range(MAX_SELECTIONS)]
        self.man_vars = [(tk.StringVar(self), tk.StringVar(self)) for _ in range(MAX_MANUAL)]
        self.pick_range, self.pick_name = tk.StringVar(self), tk.StringVar(self)
        self._now = datetime.now     # replaced in tests
        self.shift_confirmed = shifts.read_confirmed(self.data_dir)
        if self.shift_confirmed is None:  # first start: nothing is overdue yet
            self.shift_confirmed = self._now()
            shifts.write_confirmed(self.data_dir, self.shift_confirmed)
        self.shift_dialog, self._shift_prompted, self._shift_checked = None, None, 0.0
        self.unlocked = False        # the setup password was entered in this session
        self.password_state = tk.StringVar(self)
        self.theme_note = tk.StringVar(self)
        self.pick_open = tk.BooleanVar(self)
        self.pick_heading = tk.BooleanVar(self)
        self.pick_hint = tk.StringVar(self)
        self.status_msg = tk.StringVar(self)
        # set up on this PC (a scheduled task), not part of the settings file
        self.background_on = tk.BooleanVar(self, background.task_exists(self.data_dir))

        self.field_widgets = {}


    def fill(self, cfg):
        numbers = ("poll_seconds", "alert_after_minutes", "alert_email_port", "keep_backup_days",
                   "keep_history_days")
        for k, var in self.v.items():
            var.set(BODY_LABELS[cfg[k]] if k == "api_body" else f"{cfg[k]:g}" if k in numbers else cfg[k])
        for k, var in self.b.items():
            var.set(cfg[k])
        for (name, rng, header), s in zip(self.sel_vars, cfg["selections"]):
            name.set(s["name"]), rng.set(s["range"]), header.set(s["header"])
        for (name, value), m in zip(self.man_vars, cfg["manual_inputs"]):
            name.set(m["name"]), value.set(m["value"])
        self.headers_text.delete("1.0", "end")
        self.headers_text.insert("1.0", cfg["api_headers"])
        self.rename_text.delete("1.0", "end")
        self.rename_text.insert("1.0", cfg["rename_columns"])
        self._update_theme_note()

    def collect(self):
        """The settings as currently shown in the window (saved or not)."""
        cfg = dict(self.cfg)
        cfg.update({k: var.get() for k, var in self.v.items()})
        cfg.update({k: var.get() for k, var in self.b.items()})
        cfg["api_body"] = next((k for k, label in BODY_LABELS.items() if label == self.v["api_body"].get()), "raw")
        cfg["api_headers"] = self.headers_text.get("1.0", "end").strip()
        cfg["rename_columns"] = self.rename_text.get("1.0", "end").strip()
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
    def _build_tabs_scroller(self):
        """Keep the tab content usable when the main window is shorter than its ideal height."""
        host = ttk.Frame(self)
        host.pack(fill="both", expand=True, padx=16, pady=(4, 0))
        self.tabs_canvas = tk.Canvas(host, background=theme.BACKGROUND, highlightthickness=0, borderwidth=0)
        self.tabs_scrollbar = ttk.Scrollbar(host, orient="vertical", command=self.tabs_canvas.yview)
        self.tabs_canvas.configure(yscrollcommand=self.tabs_scrollbar.set)
        self.tabs_canvas.pack(side="left", fill="both", expand=True)

        self.tabs_frame = ttk.Frame(self.tabs_canvas)
        self.tabs_window = self.tabs_canvas.create_window((0, 0), window=self.tabs_frame, anchor="nw")
        self.tabs_frame.bind("<Configure>", self._tabs_content_changed, add="+")
        self.tabs_canvas.bind("<Configure>", self._tabs_viewport_changed, add="+")
        self.bind_all("<MouseWheel>", self._tabs_mousewheel, add="+")

        self.tabs = ttk.Notebook(self.tabs_frame)
        self.tabs.pack(fill="both", expand=True)

    def _tabs_content_changed(self, _event=None):
        """Fit the scrolling area; the scrollbar is there only while the content is taller than the window."""
        self._tabs_fit()
        needed = self.tabs_frame.winfo_reqheight() > self.tabs_canvas.winfo_height() + 1
        if needed != self.tabs_scrollbar.winfo_ismapped():
            if needed:
                self.tabs_scrollbar.pack(side="right", fill="y")
            else:
                self.tabs_scrollbar.pack_forget()
                self.tabs_canvas.yview_moveto(0)

    def _tabs_viewport_changed(self, event):
        self._tabs_content_changed()

    def _tabs_fit(self):
        """The tabs are as wide as the window and fill it; they are taller only when the content needs the room."""
        canvas = self.tabs_canvas
        canvas.itemconfigure(self.tabs_window, width=canvas.winfo_width(),
                             height=max(canvas.winfo_height(), self.tabs_frame.winfo_reqheight()))
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _tabs_mousewheel(self, event):
        try:
            inside = self.tabs_canvas.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            inside = None
        if inside is None:
            return
        widget = inside
        while widget is not None:
            if widget == self.tabs_canvas or widget == self.tabs_frame:
                delta = -1 if event.delta > 0 else 1
                self.tabs_canvas.yview_scroll(delta, "units")
                return
            try:
                widget = widget.master
            except AttributeError:
                return

    def _build_menubar(self):
        """The menu bar: everything that is not the daily work. Each entry calls the method the old button did."""
        bar = tk.Menu(self, tearoff=False)
        self.menu_items = {}

        def menu(label, parent=bar):
            sub = tk.Menu(parent, tearoff=False)
            parent.add_cascade(label=label, menu=sub, underline=0)
            return sub

        def item(parent, key, label, command, accelerator=None):
            parent.add_command(label=label, command=command, accelerator=accelerator)
            self.menu_items[key] = (parent, parent.index("end"))

        file_menu = menu("File")
        item(file_menu, "open_csv", "Open CSV file...", self.open_csv, "Ctrl+O")
        item(file_menu, "open_newest", "Open newest machine file", self.open_newest_file)
        file_menu.add_separator()
        item(file_menu, "save", "Save settings", self.save, "Ctrl+S")
        item(file_menu, "export", "Export settings...", self.export_settings)
        item(file_menu, "import", "Import settings...", self.import_settings)
        item(file_menu, "restore", "Restore an earlier version...",
             lambda: self.import_settings(self.data_dir / "backups"))
        file_menu.add_separator()
        folders = menu("Open folder", file_menu)
        for label, key in (("Machine CSV folder", "source_folder"), ("Back-up folder", "backup_folder"),
                           ("Shared folder", "shared_folder")):
            folders.add_command(label=label, command=lambda key=key: self._open_folder(self.v[key].get()))
        folders.add_separator()
        folders.add_command(label="Data folder (send queue, rejected files, log file)",
                            command=lambda: self._open_folder(self.data_dir))
        file_menu.add_separator()
        item(file_menu, "exit", "Exit", self.on_close)

        monitoring = menu("Monitoring")
        item(monitoring, "start", "Start monitoring", self.start, "F5")
        item(monitoring, "stop", "Stop", self.stop, "Shift+F5")
        item(monitoring, "process_now", "Process now", self.process_now, "F6")

        settings = menu("Settings")
        item(settings, "options", "Options...", self.open_options, "Ctrl+,")
        settings.add_separator()
        for key, page, label in (("opt_processing", "processing", "Processing..."),
                                 ("opt_output", "output", "Output..."),
                                 ("opt_alerts", "alerts", "Alerts..."),
                                 ("opt_access", "access", "Operators && access..."),
                                 ("opt_appearance", "appearance", "Appearance...")):
            item(settings, key, label.replace("&&", "&"), lambda page=page: self.open_options(page))
        settings.add_separator()
        item(settings, "lock", "Lock setup", self.toggle_lock)

        tools = menu("Tools")
        item(tools, "test_shared", "Test shared folder", self.test_shared)
        item(tools, "test_api", "Test API connection", self.test_api)
        item(tools, "test_alert", "Send test alert", self.test_alert)
        tools.add_separator()
        item(tools, "simulation", "Simulation...", self.open_simulation)

        view = menu("View")
        item(view, "log", "Activity log", self.open_log, "Ctrl+L")
        view.add_separator()
        for number, (key, attribute, label) in enumerate((("view_setup", "setup_tab", "Setup"),
                                                          ("view_delivery", "delivery_tab", "Delivery"),
                                                          ("view_pick", "pick_tab", "Pick ranges && preview"),
                                                          ("view_history", "history_tab", "History")), 1):
            item(view, key, label.replace("&&", "&"), lambda attribute=attribute: self._show_tab(getattr(self, attribute)),
                 f"Ctrl+{number}")

        help_menu = menu("Help")
        item(help_menu, "help", "Getting started", lambda: self.open_help("start"), "F1")
        for key, title, _ in help_text.TOPICS[1:]:
            item(help_menu, f"help_{key}", title, lambda key=key: self.open_help(key))
        help_menu.add_separator()
        item(help_menu, "about", "About CNCBridge", self.about)
        self.configure(menu=bar)

    # entries that need the setup rights: they are disabled while the setup is locked
    LOCKED_ENTRIES = ("open_csv", "open_newest", "save", "export", "import", "restore", "options", "opt_processing",
                      "opt_output", "opt_alerts", "opt_access", "opt_appearance", "test_shared", "test_api",
                      "test_alert", "simulation", "view_setup", "view_delivery", "view_pick")

    def _set_menu(self, key, enabled):
        menu, index = self.menu_items[key]
        menu.entryconfigure(index, state="normal" if enabled else "disabled")

    def _menu_enabled(self, key):
        menu, index = self.menu_items[key]
        return str(menu.entrycget(index, "state")) != "disabled"

    def _bind_shortcuts(self):
        """Keyboard shortcuts; each does what its menu entry does, and nothing while that entry is disabled."""
        def bind(sequence, key, handler):
            def run(_event=None):
                if self._menu_enabled(key):
                    handler()
                return "break"
            self.bind(sequence, run)

        bind("<Control-o>", "open_csv", self.open_csv)
        bind("<Control-s>", "save", self.save)
        bind("<F5>", "start", self.start)
        bind("<Shift-F5>", "stop", self.stop)
        bind("<F6>", "process_now", self.process_now)
        bind("<Control-comma>", "options", self.open_options)
        bind("<Control-l>", "log", self.open_log)
        bind("<F1>", "help", lambda: self.open_help("start"))
        for number, (key, attribute) in enumerate((("view_setup", "setup_tab"), ("view_delivery", "delivery_tab"),
                                                   ("view_pick", "pick_tab"), ("view_history", "history_tab")), 1):
            bind(f"<Control-Key-{number}>", key, lambda attribute=attribute: self._show_tab(getattr(self, attribute)))

    def _show_tab(self, tab):
        if str(self.tabs.tab(tab, "state")) == "normal":
            self.tabs.select(tab)

    def open_newest_file(self):
        self.tabs.select(self.pick_tab)
        self.open_newest()

    def _build_toolbar(self):
        """The buttons for the daily work. Everything else is in the menu bar."""
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x")

        def button(text, command, style="Toolbutton"):
            b = ttk.Button(bar, text=text, style=style, command=command)
            b.pack(side="left", padx=(0, 6))
            return b

        self.start_btn = button("▶  Start monitoring", self.start, "Go.Toolbutton")
        self.stop_btn = button("■  Stop", self.stop)
        self.once_btn = button("↻  Process now", self.process_now)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=3)
        self.save_btn = button("✔  Save settings", self.save)
        ttk.Separator(self).pack(fill="x")

    def _build_header(self):
        bar = ttk.Frame(self, padding=(18, 10, 18, 10))
        bar.pack(fill="x")
        dot_size, state_size = self.L.state
        self.state_dot = tk.Label(bar, text="●", fg=theme.IDLE, bg=theme.BACKGROUND,
                                  font=(self._font_body, dot_size, "bold"))
        self.state_dot.pack(side="left")
        self.state_label = ttk.Label(bar, text="Stopped", font=self.strong(state_size), width=11)
        self.state_label.pack(side="left", padx=(4, 10))

        self.counts = {}
        for key, caption in reversed((("ok", "Processed"), ("waiting", "Waiting in folder"),
                                      ("queued", "Queued to send"), ("failed", "Failed files"),
                                      ("rejected", "Refused by API"))):
            if self.L.metric_cards:
                box = ttk.Frame(bar, style="Metric.TFrame", padding=(12, 5))
                box.pack(side="right", padx=(8, 0))
            else:  # as in version 1.0: a bare number over its caption
                box = ttk.Frame(bar)
                box.pack(side="right", padx=(18, 0))
            self.counts[key] = ttk.Label(box, text="0", style="MetricValue.TLabel" if self.L.metric_cards
                                         else "Count.TLabel", anchor="e")
            self.counts[key].pack(anchor="e")
            ttk.Label(box, text=caption, style="CardSmall.TLabel").pack(anchor="e")
        self.header_sep = ttk.Separator(self)
        self.header_sep.pack(fill="x")
        # shown only while an alert is open (see _tick)
        self.alert_banner = tk.Label(self, text="", anchor="w", justify="left", bg=theme.ERROR_TINT, fg=theme.ERROR,
                                     font=self.strong(11), padx=18, pady=7)
        self.alert_banner_shown = False
        # shown while a shift change is waiting for the operator's confirmation; a click opens the dialog
        self.shift_banner = tk.Label(self, text="", anchor="w", justify="left", bg=theme.WARNING_TINT, fg=theme.WARNING,
                                     font=self.strong(11), padx=18, pady=7, cursor="hand2")
        self.shift_banner.bind("<Button-1>", lambda e: self.confirm_shift())
        self.shift_banner_shown = False

    def _page_intro(self, parent, title, hint):
        """The title and hint at the top of a tab; version 1.0 had none, and Legacy does not either."""
        intro = ttk.Frame(parent)
        if not self.L.page_intros:
            return intro
        intro.pack(fill="x", pady=(0, self.L.box_gap + 2))
        ttk.Label(intro, text=title, style="PageTitle.TLabel").pack(side="left" if self.L.intro_inline else "top",
                                                                    anchor="w")
        hint_label = ttk.Label(intro, text=hint, style="PageHint.TLabel", wraplength=self.px(1100), justify="left")
        if self.L.intro_inline:
            hint_label.pack(side="left", fill="x", expand=True, padx=(16, 0), pady=(5, 0))
        else:
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
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=self.L.row)
        entry = ttk.Entry(parent, textvariable=self.v[key])
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=self.L.row)
        self.field_widgets[key] = entry
        browse = ttk.Button(parent, text="Browse...", width=9,
                            command=lambda: self._browse(self.v[key]))
        browse.grid(row=row, column=2, padx=(2, 0), pady=self.L.row)
        self.field_widgets[f"{key}_browse"] = browse

    def _browse(self, var):
        start = var.get() if Path(var.get() or "?").is_dir() else None
        folder = filedialog.askdirectory(parent=self, initialdir=start, title="Choose a folder")
        if folder:
            var.set(os.path.normpath(folder))

    def _build_setup(self):
        tab = self.setup_tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
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

        box = ttk.LabelFrame(left, text=" 1  Machine folders ", padding=self.L.box_pad)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Station name", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=self.L.row)
        station_entry = ttk.Entry(box, textvariable=self.v["station_name"])
        station_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=self.L.row)
        self.field_widgets["station_name"] = station_entry
        self._folder_row(box, 1, "Machine CSV folder", "source_folder")
        ttk.Label(box, text="File pattern", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=self.L.row)
        pattern_entry = ttk.Entry(box, textvariable=self.v["file_pattern"], width=14,
                                  **self.code_font)
        pattern_entry.grid(row=2, column=1, sticky="w", padx=8, pady=self.L.row)
        self.field_widgets["file_pattern"] = pattern_entry
        self._folder_row(box, 3, "Back-up folder", "backup_folder")
        folder_help = ttk.Label(box, style="Small.TLabel", wraplength=self.px(470), justify="left",
                                text="Each file is taken from the machine folder as soon as the machine has finished writing it. "
                                     "After processing it is moved into the back-up folder (one sub-folder per day).")
        folder_help.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(9, 2))
        self._fit_wrap(folder_help, self.px(470))

        box = ttk.LabelFrame(left, text=" 2  Output format ", padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        formats = ttk.Frame(box)
        formats.pack(fill="x")
        for fmt in FORMATS:
            choice = ttk.Radiobutton(formats, text=fmt.upper(), value=fmt, variable=self.v["output_format"])
            choice.pack(side="left", padx=(0, 28), pady=2)
            self.field_widgets[f"output_format_{fmt}"] = choice

        box = ttk.LabelFrame(left, text=" 4  Details added to every file ", padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        box.columnconfigure(1, weight=1)
        box.columnconfigure(2, weight=2)
        ttk.Label(box, text="Field name", style="CardSmall.TLabel").grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(box, text="Value", style="CardSmall.TLabel").grid(row=0, column=2, sticky="w")
        for i, (name, value) in enumerate(self.man_vars, 1):
            ttk.Label(box, text=f"Input {i}", style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=self.L.row)
            ttk.Entry(box, textvariable=name).grid(row=i, column=1, sticky="ew", padx=8, pady=self.L.row)
            ttk.Entry(box, textvariable=value).grid(row=i, column=2, sticky="ew", pady=self.L.row)
        manual_help = ttk.Label(box, style="Small.TLabel", wraplength=self.px(470), justify="left",
                                 text="For example Operator and Work order. Change a value and press Save settings; "
                                      "it applies from the next file, without stopping.")
        manual_help.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 2))
        self._fit_wrap(manual_help, self.px(470))

        box = ttk.LabelFrame(right, text=" 3  Choose data to include (up to 10 ranges) ", padding=self.L.box_pad)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=3)
        box.columnconfigure(2, weight=2)
        for col, text in ((1, "Name in the output"), (2, "Cell range"), (3, "First row is header")):
            ttk.Label(box, text=text, style="CardSmall.TLabel").grid(row=0, column=col, sticky="w", padx=6, pady=(0, 6))
        for i, (name, rng, header) in enumerate(self.sel_vars):
            tk.Label(box, text=f" {i + 1} ", bg=theme.RANGE_COLOURS[i][1], fg="white",
                     font=self.strong(self.L.grid_badge + 1)).grid(row=i + 1, column=0, pady=3)
            name_entry = ttk.Entry(box, textvariable=name)
            name_entry.grid(row=i + 1, column=1, sticky="ew", padx=6, pady=self.L.row)
            range_entry = ttk.Entry(box, textvariable=rng, width=12, font=(self._font_mono, self.L.range_size))
            range_entry.grid(row=i + 1, column=2, sticky="ew", padx=6, pady=self.L.row)
            header_check = ttk.Checkbutton(box, text=self.L.check_text, variable=header)
            header_check.grid(row=i + 1, column=3, pady=self.L.row)
            pick_button = ttk.Button(box, text="Pick...", width=7,
                                     command=lambda i=i: self.open_picker(i))
            pick_button.grid(row=i + 1, column=4, padx=(6, 0), pady=self.L.row)
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
        tab = self.delivery_tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.tabs.add(tab, text="  Delivery  ")
        self._page_intro(tab, "Choose where files are delivered",
                         "Enable one or both destinations. If a destination is unavailable, files wait safely and retry automatically.")
        content = ttk.Frame(tab)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=5, uniform="delivery")
        content.columnconfigure(1, weight=6, uniform="delivery")
        content.rowconfigure(0, weight=1)
        left, right = ttk.Frame(content), ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        box = ttk.LabelFrame(left, text=" 5  Shared folder ", padding=self.L.box_pad)
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

        box = ttk.LabelFrame(left, text=" How delivery works ", padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        note = ttk.Label(box, style="Card.TLabel", wraplength=self.px(540), justify="left", text=(
            "Each output file is first queued on this PC and removed from the queue only after it has arrived. "
            "If a destination cannot be reached, the files wait and are sent in order when it is back; machine "
            "files are still processed and backed up.\n\n"
            "A file the receiving site refuses is set aside in 'rejected' in the data folder, and the others "
            "continue. More under Help."))
        note.pack(anchor="nw", fill="x")
        self._fit_wrap(note, self.px(540))

        box = ttk.LabelFrame(right, text=" 6  API connection ", padding=self.L.box_pad)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        api_enabled = ttk.Checkbutton(box, text="Send every output file to the end user's site",
                                      variable=self.b["api_enabled"])
        api_enabled.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.field_widgets["api_enabled"] = api_enabled
        ttk.Label(box, text="API link (URL)", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=self.L.row)
        api_url = ttk.Entry(box, textvariable=self.v["api_url"])
        api_url.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=self.L.row)
        self.field_widgets["api_url"] = api_url
        ttk.Label(box, text="Token (optional)", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=self.L.row)
        api_token = ttk.Entry(box, textvariable=self.v["api_token"], show="●")
        api_token.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=self.L.row)
        self.field_widgets["api_token"] = api_token
        ttk.Label(box, text="Send as", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=self.L.row)
        api_body = ttk.Combobox(box, textvariable=self.v["api_body"], values=list(BODY_LABELS.values()),
                                state="readonly", width=26)
        api_body.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=self.L.row)
        self.field_widgets["api_body"] = api_body
        ttk.Label(box, text="Extra headers", style="Card.TLabel").grid(row=4, column=0, sticky="nw", pady=self.L.row)
        self.headers_text = tk.Text(box, height=4, width=30, font=(self._font_mono, self.L.text_mono), relief="solid", borderwidth=1,
                                    background=theme.SURFACE, foreground=theme.TEXT, insertbackground=theme.TEXT,
                                    highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
                                    padx=7, pady=5, wrap="word", undo=True)
        self.headers_text.grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=self.L.row)
        self.field_widgets["api_headers"] = self.headers_text
        ttk.Label(box, style="CardSmall.TLabel", wraplength=self.px(420), justify="left",
                  text="One per line, for example  X-Api-Key: abc123. The token is sent as "
                       "Authorization: Bearer <token> and is stored encrypted.").grid(row=5, column=1, sticky="w",
                                                                                      padx=(8, 0), pady=(0, 3))
        test_api = ttk.Button(box, text="Test connection", command=self.test_api)
        test_api.grid(row=6, column=1, sticky="w", padx=8, pady=(7, 2))
        self.field_widgets["test_api"] = test_api

    def _build_options(self):
        """The Options window: the settings that are set once and rarely changed, on pages. Built once, then shown."""
        win = self.options_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        win.title(f"{APP_NAME} options")
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", self.close_options)
        width = min(self.px(800), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(690), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(660), width), min(self.px(520), height))
        notebook = self.options_tabs = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=14, pady=(14, 0))
        self.option_pages = {}
        for key, title, build in (("processing", "Processing", self._build_processing),
                                  ("output", "Output", self._build_output_options),
                                  ("alerts", "Alerts", self._build_alerts),
                                  ("access", "Operators & access", self._build_access),
                                  ("appearance", "Appearance", self._build_appearance)):
            page = ttk.Frame(notebook, padding=self.L.tab_pad)
            notebook.add(page, text=f"  {title}  ")
            self.option_pages[key] = page
            build(page)
        bar = ttk.Frame(win, padding=(14, 10))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_msg, style="Small.TLabel", wraplength=self.px(430),
                  justify="left").pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Close", command=self.close_options).pack(side="right")
        self.options_save_btn = ttk.Button(bar, text="Save settings", style="Go.TButton", command=self.save)
        self.options_save_btn.pack(side="right", padx=8)

    def open_options(self, page=None):
        if self.protected and not self.unlocked:
            return
        win = self.options_win
        if page:
            self.options_tabs.select(self.option_pages[page])
        if win.state() == "withdrawn":
            win.update_idletasks()
            x = self.winfo_rootx() + max((self.winfo_width() - win.winfo_reqwidth()) // 2, 0)
            y = self.winfo_rooty() + self.px(40)
            win.geometry(f"+{x}+{y}")
        self._update_theme_note()
        win.deiconify()
        win.lift()
        win.focus_set()

    def close_options(self):
        self.options_win.withdraw()
        self.focus_force()  # the shortcuts work again at once

    def _build_processing(self, parent):
        box = ttk.LabelFrame(parent, text=" Monitoring & file options ", padding=self.L.box_pad)
        box.pack(fill="x")
        autostart = ttk.Checkbutton(box, text="Start monitoring when the program opens",
                                    variable=self.b["autostart"])
        autostart.grid(row=0, column=0, columnspan=2, sticky="w", pady=4)
        self.field_widgets["autostart"] = autostart
        skip_duplicates = ttk.Checkbutton(box, text="Never send the same file content twice",
                                          variable=self.b["skip_duplicates"])
        skip_duplicates.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.field_widgets["skip_duplicates"] = skip_duplicates
        ttk.Label(box, text="Check the folder every", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=self.L.row)
        row = ttk.Frame(box)
        row.grid(row=2, column=1, sticky="w", padx=8)
        poll_seconds = ttk.Entry(row, textvariable=self.v["poll_seconds"], width=5,
                                 **self.code_font)
        poll_seconds.pack(side="left")
        self.field_widgets["poll_seconds"] = poll_seconds
        ttk.Label(row, text=" seconds", style="Card.TLabel").pack(side="left", padx=(5, 0))
        ttk.Label(box, text="Machine file encoding", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=self.L.row)
        encoding = ttk.Combobox(box, textvariable=self.v["encoding"], values=ENCODINGS, width=12)
        encoding.grid(row=3, column=1, sticky="w", padx=8)
        self.field_widgets["encoding"] = encoding
        ttk.Label(box, text="Column separator", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=self.L.row)
        delimiter = ttk.Combobox(box, textvariable=self.v["delimiter"], values=DELIMITER_CHOICES,
                                 state="readonly", width=12)
        delimiter.grid(row=4, column=1, sticky="w", padx=8)
        self.field_widgets["delimiter"] = delimiter
        ttk.Label(box, text="File is complete when it contains", style="Card.TLabel").grid(
            row=5, column=0, sticky="w", pady=self.L.row)
        marker = ttk.Entry(box, textvariable=self.v["complete_marker"], width=16, **self.code_font)
        marker.grid(row=5, column=1, sticky="w", padx=8)
        self.field_widgets["complete_marker"] = marker
        in_background = ttk.Checkbutton(box, text="Process in the background: keeps going after this window\n"
                                                  "closes, and starts when you log on to Windows",
                                        variable=self.background_on)
        in_background.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.field_widgets["run_in_background"] = in_background
        for row_no, (key, label) in enumerate((("keep_backup_days", "Delete back-up files after"),
                                                ("keep_history_days", "Delete history after")), start=7):
            ttk.Label(box, text=label, style="Card.TLabel").grid(row=row_no, column=0, sticky="w", pady=self.L.row)
            row = ttk.Frame(box)
            row.grid(row=row_no, column=1, sticky="w", padx=8)
            days = ttk.Entry(row, textvariable=self.v[key], width=5, **self.code_font)
            days.pack(side="left")
            self.field_widgets[key] = days
            ttk.Label(row, text=" days  (0 = keep everything)", style="Card.TLabel").pack(side="left")


    def _build_output_options(self, parent):
        box = ttk.LabelFrame(parent, text=" Output options ", padding=self.L.box_pad)
        box.pack(fill="x")
        note = ttk.Label(box, style="CardSmall.TLabel", justify="left", wraplength=self.px(620), text=(
            "All of these are off by default, and with all of them off the files are exactly what version 1.0 "
            "wrote. Turn one on only when the receiving system asks for it. The Pick ranges & preview tab shows "
            "the effect."))
        note.pack(anchor="w", fill="x", pady=(0, 8))
        self._fit_wrap(note, self.px(620))
        for key, text in (("json_numbers", "JSON: write numbers as numbers, not as text"),
                          ("csv_flat", "CSV: one plain table, a row per record"),
                          ("time_zone", "Add the time zone to the processing time (+05:30)")):
            check = ttk.Checkbutton(box, text=text, variable=self.b[key])
            check.pack(anchor="w")
            self.field_widgets[key] = check
        ttk.Label(box, text="Rename columns, one per line as  Old name=New name", style="CardSmall.TLabel"
                  ).pack(anchor="w", pady=(8, 3))
        self.rename_text = tk.Text(box, height=3, width=30, font=(self._font_mono, self.L.text_mono), relief="solid",
                                   borderwidth=1, background=theme.SURFACE, foreground=theme.TEXT, insertbackground=theme.TEXT,
                                   highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
                                   padx=7, pady=5, wrap="none", undo=True)
        self.rename_text.pack(fill="x")
        self.field_widgets["rename_columns"] = self.rename_text

    def _build_alerts(self, parent):
        box = ttk.LabelFrame(parent, text=" Alerts ", padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        box.columnconfigure(1, weight=1)

        def check(row, key, text):
            widget = ttk.Checkbutton(box, text=text, variable=self.b[key])
            widget.grid(row=row, column=0, columnspan=4, sticky="w", pady=(4, 2))
            self.field_widgets[key] = widget

        def entry(row, key, label, show=None, width=None):
            ttk.Label(box, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(22, 0), pady=3)
            widget = ttk.Entry(box, textvariable=self.v[key], show=show or "", width=width or 0)
            widget.grid(row=row, column=1, columnspan=3, sticky="ew" if not width else "w", padx=(8, 0), pady=3)
            self.field_widgets[key] = widget

        row = ttk.Frame(box)
        row.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(row, text="Alert when a problem lasts", style="Card.TLabel").pack(side="left")
        minutes = ttk.Entry(row, textvariable=self.v["alert_after_minutes"], width=4, **self.code_font)
        minutes.pack(side="left", padx=6)
        self.field_widgets["alert_after_minutes"] = minutes
        ttk.Label(row, text="minutes, or a file is set aside", style="Card.TLabel").pack(side="left")
        check(1, "alert_notify", "Show a Windows notification on this PC")
        check(2, "alert_webhook_enabled", "Post to a webhook (Teams, Slack or another system)")
        entry(3, "alert_webhook_url", "Webhook link")
        check(4, "alert_email_enabled", "Send an email")
        entry(5, "alert_email_server", "Mail server")
        ttk.Label(box, text="Port", style="Card.TLabel").grid(row=6, column=0, sticky="w", padx=(22, 0), pady=3)
        port_row = ttk.Frame(box)
        port_row.grid(row=6, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=3)
        port = ttk.Entry(port_row, textvariable=self.v["alert_email_port"], width=7)
        port.pack(side="left")
        self.field_widgets["alert_email_port"] = port
        starttls = ttk.Checkbutton(port_row, text="Encrypt (STARTTLS)", variable=self.b["alert_email_starttls"])
        starttls.pack(side="left", padx=(16, 0))
        self.field_widgets["alert_email_starttls"] = starttls
        entry(7, "alert_email_user", "User name")
        entry(8, "alert_email_password", "Password", show="●")
        entry(9, "alert_email_from", "From")
        entry(10, "alert_email_to", "To")
        test = ttk.Button(box, text="Send test alert", command=self.test_alert)
        test.grid(row=11, column=0, columnspan=4, sticky="w", pady=(8, 2))
        self.field_widgets["test_alert"] = test

    def _build_appearance(self, parent):
        """The look of the window. One radio button per theme in theme.THEMES, so a new theme appears by itself."""
        box = ttk.LabelFrame(parent, text=" Appearance ", padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        for key, option in theme.THEMES.items():
            choice = ttk.Radiobutton(box, text=option.label, value=key, variable=self.v["theme"],
                                     command=self._update_theme_note)
            choice.pack(anchor="w")
            self.field_widgets[f"theme_{key}"] = choice
            about = ttk.Label(box, text=option.description, style="CardSmall.TLabel", wraplength=self.px(440),
                              justify="left")
            about.pack(fill="x", padx=(24, 0), pady=(0, 6))
            self._fit_wrap(about, self.px(440))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(2, 0))
        ttk.Label(row, textvariable=self.theme_note, style="CardSmall.TLabel", wraplength=self.px(300),
                  justify="left").pack(side="left", fill="x", expand=True)
        self.restart_btn = ttk.Button(row, text="Restart now", command=self.restart)
        self.restart_btn.pack(side="right")
        self.field_widgets["restart_now"] = self.restart_btn

    def _update_theme_note(self):
        """Say what the chosen theme needs: saving, a restart, or nothing."""
        if not hasattr(self, "restart_btn"):
            return
        chosen = self.v["theme"].get()
        if chosen == self.theme.key:
            note, state = "This is the look in use.", "disabled"
        elif chosen == self.cfg["theme"]:
            note, state = "Saved. It takes effect when CNCBridge restarts.", "normal"
        else:
            note, state = "Press Save settings to keep this choice, then restart to see it.", "normal"
        self.theme_note.set(note)
        self.restart_btn.configure(state=state)

    def _restart_command(self, resume=False):
        """The command that starts this program again on the same data folder."""
        base = config.program_dir()
        if getattr(sys, "frozen", False):
            command = [sys.executable]
        else:
            python = Path(sys.executable)
            pythonw = python.with_name("pythonw.exe")
            command = [str(pythonw if pythonw.exists() else python), str(base / "CNCBridge.pyw")]
        command += ["--data", str(self.data_dir), "--restarted"]
        return command + ["--resume"] if resume else command

    def restart(self):
        """Save, then replace this window with a new one, so that a newly chosen theme is applied."""
        if self.busy:
            return messagebox.showinfo(APP_NAME, "Process now is running. Restart when it has finished.", parent=self)
        if not self.save():
            return
        resume = self.running
        try:  # the new program waits for this window to let go of the data folder
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")  # a one-file exe must unpack afresh
            subprocess.Popen(self._restart_command(resume), cwd=str(config.program_dir()), env=env)
        except OSError as e:
            return messagebox.showerror(APP_NAME, f"CNCBridge could not be restarted:\n{e}", parent=self)
        if self.running:
            self.engine.stop()
            self.thread.join(20)
        self.closing.set()
        self.destroy()

    def _build_access(self, parent):
        box = ttk.LabelFrame(parent, text=" Operators & access ", padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Confirm typed values at", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=self.L.row)
        row = ttk.Frame(box)
        row.grid(row=0, column=1, sticky="w", padx=8)
        shift = ttk.Entry(row, textvariable=self.v["shift_times"], width=24, **self.code_font)
        shift.pack(side="left")
        self.field_widgets["shift_times"] = shift
        ttk.Label(row, text="  shift starts, e.g. 06:00, 14:00, 22:00", style="CardSmall.TLabel").pack(side="left")
        ttk.Label(box, text="Setup password", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=self.L.row)
        row = ttk.Frame(box)
        row.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(row, textvariable=self.password_state, style="Card.TLabel", width=14).pack(side="left")
        set_pw = ttk.Button(row, text="Set password...", command=self.set_password)
        set_pw.pack(side="left", padx=(6, 0))
        self.remove_pw_btn = ttk.Button(row, text="Remove", command=self.remove_password)
        self.remove_pw_btn.pack(side="left", padx=6)
        self.field_widgets["set_password"] = set_pw
        self.field_widgets["remove_password"] = self.remove_pw_btn
        note = ttk.Label(box, style="CardSmall.TLabel", wraplength=self.px(420), justify="left",
                         text="With a password, operators see only Start/Stop, the counters, History, the "
                              "Log and the typed values. It keeps settings from being changed by accident; it "
                              "does not protect against someone who can open this PC's files.")
        note.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._fit_wrap(note, self.px(420))

    def _build_operator(self):
        """What an operator sees while the setup is locked: the typed values and nothing else to change."""
        tab = self.operator_tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.tabs.add(tab, text="  Operator  ")
        self._page_intro(tab, "Typed values",
                         "These values are written into every file. Change them when the operator or the "
                         "work order changes; they apply from the next file, without stopping.")
        box = ttk.LabelFrame(tab, text=" Details added to every file ", padding=self.L.box_pad)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self.operator_labels = []
        for i, (_, value) in enumerate(self.man_vars):
            label = ttk.Label(box, text=f"Input {i + 1}", style="Card.TLabel", width=22)
            label.grid(row=i, column=0, sticky="w", pady=6)
            ttk.Entry(box, textvariable=value).grid(row=i, column=1, sticky="ew", padx=8, pady=6)
            self.operator_labels.append(label)
        apply = ttk.Button(box, text="Apply", style="Go.TButton", command=self.apply_operator_values)
        apply.grid(row=len(self.man_vars), column=1, sticky="w", padx=8, pady=(8, 2))
        self.field_widgets["operator_apply"] = apply

    # ================================================================ setup password
    @property
    def protected(self):
        return bool(self.cfg["setup_password"])

    def _apply_lock(self):
        """Show the setup tabs, or only the operator's, depending on the password and whether it was entered."""
        locked = self.protected and not self.unlocked
        for tab in (self.setup_tab, self.delivery_tab, self.pick_tab):
            self.tabs.tab(tab, state="hidden" if locked else "normal")
        self.tabs.tab(self.operator_tab, state="normal" if locked else "hidden")
        self.save_btn.configure(state="disabled" if locked else "normal")
        for key in self.LOCKED_ENTRIES:
            self._set_menu(key, not locked)
        lock_menu, lock_index = self.menu_items["lock"]
        lock_menu.entryconfigure(lock_index, label="Unlock setup..." if locked else "Lock setup",
                                 state="normal" if self.protected else "disabled")
        if locked:
            self.options_win.withdraw()
        for label, (name, _) in zip(self.operator_labels, self.man_vars):
            label.configure(text=name.get().strip() or label.cget("text"))
        self.password_state.set("Password is set" if self.protected else "No password")
        self.remove_pw_btn.configure(state="normal" if self.protected else "disabled")
        self.tabs.select(self.operator_tab if locked else self.setup_tab)

    def _ask_password(self, title, prompt):
        return simpledialog.askstring(title, prompt, show="●", parent=self)

    def toggle_lock(self):
        if self.protected and not self.unlocked:
            typed = self._ask_password("Unlock setup", "Setup password:")
            if typed is None:
                return
            if not config.check_password(typed, self.cfg["setup_password"]):
                return messagebox.showerror(APP_NAME, "That password is not right.", parent=self)
            self.unlocked = True
        else:
            self.unlocked = False
        self._apply_lock()

    def set_password(self):
        first = self._ask_password("Setup password", f"New password (at least {config.MIN_PASSWORD} characters):")
        if first is None:
            return
        if len(first) < config.MIN_PASSWORD:
            return messagebox.showerror(APP_NAME, f"Use at least {config.MIN_PASSWORD} characters.", parent=self)
        if self._ask_password("Setup password", "Type it again:") != first:
            return messagebox.showerror(APP_NAME, "The two passwords are not the same. Nothing was changed.",
                                        parent=self)
        self._store_password(config.hash_password(first))
        self.status_msg.set("Password set. Press Lock setup when you are done; operators then see only the "
                            "Operator tab.")

    def remove_password(self):
        typed = self._ask_password("Remove password", "Current setup password:")
        if typed is None:
            return
        if not config.check_password(typed, self.cfg["setup_password"]):
            return messagebox.showerror(APP_NAME, "That password is not right.", parent=self)
        self._store_password("")
        self.status_msg.set("Password removed. Everyone can change the settings.")

    def _store_password(self, stored):
        """Save only the password; unsaved edits in the form stay unsaved."""
        cfg = dict(self.cfg, setup_password=stored)
        save_config(cfg, self.cfg_path)
        self.cfg = normalize_config(cfg)
        self.unlocked = True  # whoever just set or removed it is in setup
        self._apply_lock()

    # ================================================================ shift changes
    @staticmethod
    def _shift_problem(cfg):
        try:
            shifts.parse_times(cfg["shift_times"], strict=True)
        except ValueError as e:
            return f"Shift times: {e}"
        return None

    def _check_shift(self):
        """Ask for a confirmation when a shift has started that nobody has confirmed yet."""
        due = shifts.pending(shifts.parse_times(self.cfg["shift_times"]), self.shift_confirmed, self._now())
        self._show_shift(due)
        if due and due != self._shift_prompted:
            self._shift_prompted = due  # ask once per shift change; the banner stays for a later click
            self.confirm_shift(due)

    def _show_shift(self, due):
        if due:
            self.shift_banner.configure(text=f"⏱  Shift change at {due:%H:%M}: please confirm the typed values "
                                             f"(click here)")
            if not self.shift_banner_shown:
                self.shift_banner.pack(fill="x", after=self.header_sep)
                self.shift_banner_shown = True
        elif self.shift_banner_shown:
            self.shift_banner.pack_forget()
            self.shift_banner_shown = False

    def confirm_shift(self, due=None):
        due = due or shifts.latest_boundary(shifts.parse_times(self.cfg["shift_times"]), self._now())
        if due is None:
            return
        if self.shift_dialog is not None and self.shift_dialog.winfo_exists():
            self.shift_dialog.deiconify()
            self.shift_dialog.lift()
            return
        self.shift_dialog = ShiftDialog(self, due)

    def finish_shift(self, values=None):
        """The operator confirmed. New values, if they changed, are saved first. Returns False if that failed."""
        if values is not None and values != [var.get() for _, var in self.man_vars]:
            for (_, var), text in zip(self.man_vars, values):
                var.set(text)
            if not self.save():
                return False
        self.shift_confirmed = self._now()
        shifts.write_confirmed(self.data_dir, self.shift_confirmed)
        self._show_shift(None)
        self.status_msg.set("Shift change confirmed.")
        return True

    def apply_operator_values(self):
        """The Operator tab: the typed values are the only thing that can have changed, so Save is enough."""
        if self.save():
            self.status_msg.set("Typed values applied. They are used from the next file.")

    def test_alert(self):
        cfg = self.collect()
        self._in_background("Test alert", lambda: alerts.send_test(cfg))

    def _build_pick(self):
        tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
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
                                      **self.code_font)
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
        pick_range_entry = ttk.Entry(bar, textvariable=self.pick_range, width=11, font=(self._font_mono, self.L.range_size),
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
        by_heading = ttk.Frame(left, padding=(10, 0, 10, 4))
        by_heading.pack(fill="x")
        pick_heading = ttk.Checkbutton(by_heading, text="Find by heading", variable=self.pick_heading,
                                       command=self._update_pick_range)
        pick_heading.pack(side="left")
        self.field_widgets["pick_heading"] = pick_heading
        ttk.Label(by_heading, textvariable=self.pick_hint, style="CardSmall.TLabel").pack(side="left", padx=(10, 0))
        panes.add(left, weight=3)

        right = ttk.LabelFrame(panes, text=" Output preview ", padding=(12, 10))
        self.preview_note = ttk.Label(right, text="", foreground=theme.WARNING, wraplength=self.px(360),
                                      justify="left", background=theme.BACKGROUND)
        self.preview_note.pack(fill="x", pady=(0, 8))
        self.preview = tk.Text(right, wrap="none", font=(self._font_mono, self.L.text_mono), width=44, height=12,
                               state="normal", relief="solid", borderwidth=1, background=theme.SURFACE, foreground=theme.TEXT,
                               insertbackground=theme.TEXT, highlightthickness=1, highlightbackground=theme.BORDER,
                               highlightcolor=theme.ACCENT, padx=8, pady=8, takefocus=True)
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
        tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.history_tab = tab
        self.tabs.add(tab, text="  History  ")
        self._page_intro(tab, "File history", "Review processed machine files and check whether each output reached its destination.")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Button(bar, text="Open back-up folder",
                   command=lambda: self._open_folder(self.cfg["backup_folder"])).pack(side="left")
        ttk.Button(bar, text="Open data folder (queue, rejected, log file)",
                   command=lambda: self._open_folder(self.data_dir)).pack(side="left", padx=6)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8, pady=3)
        self.resend_btn = ttk.Button(bar, text="Send again", command=self.resend_selected, state="disabled")
        self.resend_btn.pack(side="left")
        self.reprocess_btn = ttk.Button(bar, text="Process again", command=self.reprocess_selected,
                                        state="disabled")
        self.reprocess_btn.pack(side="left", padx=6)
        ttk.Label(bar, text="Most recent 200 files", style="PageHint.TLabel").pack(side="right")
        cols = (("processed_at", "Time", 140), ("source", "Machine file", 200), ("status", "Result", 80),
                ("output", "Output file", 250), ("shared", "Shared folder", 100), ("api", "API", 110),
                ("message", "Note", 300))
        history_area = ttk.Frame(tab, style="Card.TFrame", padding=1)
        history_area.pack(fill="both", expand=True)
        self.history = ttk.Treeview(history_area, columns=[c[0] for c in cols], show="headings",
                                    style="History.Treeview", takefocus=True, selectmode="extended")
        self.history.bind("<<TreeviewSelect>>", lambda e: self._update_history_buttons())
        for key, title, width in cols:
            self.history.heading(key, text=title, anchor="w")
            self.history.column(key, width=self.px(width), anchor="w", stretch=key == "message")
        self.history.tag_configure("failed", foreground=theme.ERROR)
        self.history.tag_configure("duplicate", foreground=theme.MUTED)
        self.history.tag_configure("pending", foreground=theme.WARNING)
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
        """The activity log, in a window of its own (View > Activity log). It keeps filling while hidden."""
        win = self.log_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        win.title(f"{APP_NAME} activity log")
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        width = min(self.px(980), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(600), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(560), width), min(self.px(320), height))
        frame = ttk.Frame(win, padding=(14, 12, 14, 0))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Technical processing and delivery messages. Most day-to-day status is shown in "
                              "History.", style="PageHint.TLabel").pack(anchor="w", pady=(0, 8))
        log_area = ttk.Frame(frame, style="Card.TFrame", padding=1)
        log_area.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_area, wrap="none", font=(self._font_mono, self.L.text_mono), state="disabled",
                                relief="flat", borderwidth=0, background=theme.SURFACE, foreground=theme.TEXT,
                                insertbackground=theme.TEXT, highlightthickness=0, padx=9, pady=9, takefocus=True)
        self.log_text.tag_configure("WARNING", foreground=theme.WARNING)
        self.log_text.tag_configure("ERROR", foreground=theme.ERROR)
        ys = ttk.Scrollbar(log_area, orient="vertical", command=self.log_text.yview)
        xs = ttk.Scrollbar(log_area, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.log_text.pack(fill="both", expand=True)
        self.log_empty = ttk.Label(log_area, style="EmptyState.TLabel",
                                   text="No activity messages yet\n\nMessages will appear as CNCBridge works.")
        self.log_empty.place(relx=0.5, rely=0.5, anchor="center")
        bar = ttk.Frame(win, padding=(14, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="Close", command=win.withdraw).pack(side="right")
        ttk.Button(bar, text="Open data folder", command=lambda: self._open_folder(self.data_dir)).pack(side="right",
                                                                                                         padx=8)

    def open_log(self):
        self.log_win.deiconify()
        self.log_win.lift()
        self.log_text.see("end")

    # ================================================================ help
    def _build_help(self):
        win = self.help_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        win.title(f"{APP_NAME} help")
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        width = min(self.px(980), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(660), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(640), width), min(self.px(400), height))
        panes = ttk.PanedWindow(win, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=14, pady=14)
        topics = self.help_topics = ttk.Treeview(panes, show="tree", selectmode="browse")
        topics.column("#0", width=self.px(300))
        for key, title, _ in help_text.TOPICS:
            topics.insert("", "end", iid=key, text=title)
        topics.bind("<<TreeviewSelect>>", self._help_selected)
        panes.add(topics, weight=1)
        body = ttk.Frame(panes, style="Card.TFrame", padding=1)
        text = self.help_body = tk.Text(body, wrap="word", state="disabled", relief="flat", borderwidth=0,
                                        background=theme.SURFACE, foreground=theme.TEXT, highlightthickness=0,
                                        padx=18, pady=14, font=(self._font_body, 11), cursor="arrow",
                                        spacing3=3)
        ys = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.tag_configure("h1", font=self.strong(15), spacing3=6)
        text.tag_configure("h2", font=self.strong(12), spacing1=10, spacing3=4)
        text.tag_configure("bullet", lmargin1=self.px(12), lmargin2=self.px(28))
        text.tag_configure("gap", font=(self._font_body, 5))
        text.tag_configure("code", font=(self._font_mono, 10), background=theme.SURFACE_ALT, lmargin1=self.px(12),
                           lmargin2=self.px(12), spacing3=0)
        panes.add(body, weight=4)

    def _help_selected(self, _event=None):
        selection = self.help_topics.selection()
        if selection:
            self._render_help(selection[0])

    def _render_help(self, key):
        title, body = next((t, b) for k, t, b in help_text.TOPICS if k == key)
        if key == "keys":
            body = ("The shortcut is shown beside each menu entry. A shortcut does nothing while its entry is "
                    "disabled (for example while the setup is locked).\n\n"
                    + "\n".join(f"    {keys:<20}{what}" for keys, what in help_text.SHORTCUTS))
        text = self.help_body
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", title + "\n", "h1")
        for line in body.split("\n"):
            if line.startswith("## "):
                text.insert("end", line[3:] + "\n", "h2")
            elif line.startswith("- "):
                text.insert("end", "•  " + line[2:] + "\n", "bullet")
            elif line.startswith("    "):
                text.insert("end", line + "\n", "code")
            elif not line.strip():
                text.insert("end", "\n", "gap")
            else:
                text.insert("end", line + "\n")
        text.configure(state="disabled")
        text.yview_moveto(0)

    def open_help(self, key="start"):
        self.help_topics.selection_set(key)
        self.help_topics.see(key)
        self._render_help(key)
        self.help_win.deiconify()
        self.help_win.lift()

    def about(self):
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {VERSION}\n\nTakes the CSV file an assembly machine writes for every board, cuts out the "
            f"cell ranges you choose, and delivers the result as JSON, CSV or TXT to a shared folder and/or an API "
            f"link.\n\nData folder:\n{self.data_dir}\n\nThe bundled fonts Roboto and Roboto Mono are used under the "
            f"SIL Open Font License.", parent=self)

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
            "alert_after_minutes": "A problem such as an unreachable destination must last this long before an "
                                   "alert is sent. Files set aside are announced at once.",
            "alert_notify": "Show a notification in the corner of this PC's screen.",
            "alert_webhook_enabled": "Post each alert as JSON to a link, such as a Teams or Slack incoming webhook.",
            "alert_webhook_url": "The address that receives the alert.",
            "alert_email_enabled": "Send each alert by email through your mail server.",
            "alert_email_server": "Name or address of the mail server, for example smtp.example.com.",
            "alert_email_port": "Usually 587 (with STARTTLS), 465 (encrypted) or 25.",
            "alert_email_starttls": "Encrypt the connection to the mail server. Leave on unless told otherwise.",
            "alert_email_user": "Leave empty if the mail server does not ask for a login.",
            "alert_email_password": "Stored encrypted, like the API token, and never exported.",
            "alert_email_from": "Sender address; the user name is used when this is empty.",
            "alert_email_to": "One or more addresses, separated by commas.",
            "test_alert": "Send a test message through every alert channel that is switched on.",
            "keep_backup_days": "Back-up day folders and set-aside files older than this are deleted, checked "
                                "once an hour. 0 keeps everything. The send queue is never touched.",
            "keep_history_days": "History rows older than this are deleted. 0 keeps the whole history.",
            "run_in_background": "Applied with Save settings. Processing then runs as a Windows background task "
                                 "for this user; this window shows it and Start/Stop control it.",
            "pick_range": "Shows the cell range selected in the grid.",
            "json_numbers": "In JSON files, cells such as 12 or 40.53 become numbers. IDs with leading zeros, text and "
                            "numbers with a decimal comma stay text. Other formats are not affected.",
            "csv_flat": "CSV files become one plain table: a row per record, with the file, time, station, your "
                        "inputs and the selection name in front. Other formats are not affected.",
            "time_zone": "The processing time written in each file ends with this PC's time zone, such as +05:30. "
                         "Times copied from the machine file are not changed.",
            "rename_columns": "Change column names in every file, for example Ref=Reference. Applies to selections "
                              "that use a header row. Names are matched ignoring upper and lower case.",
            "complete_marker": "Optional. Text the machine writes last, such as End_Time. A file is taken only once "
                               "it contains this text (ignoring case), on top of the usual idle time. Leave empty "
                               "for machines that write a file in one go.",
            "pick_open": "Extend the selected range down to the first empty row.",
            "pick_heading": "Instead of fixed cells, find the block by the text in the top-left cell you marked. "
                            "It then still works when the table moves down the file.",
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
            5: "The typed values that go into every file.",
        })
        Tooltip(self.field_widgets["shift_times"], "Optional. At each of these times the operator is asked to confirm "
                                                   "or change the typed values. Files keep being processed meanwhile.")
        Tooltip(self.field_widgets["restart_now"], "Save, then close this window and open it again with the "
                                                   "chosen look. Monitoring resumes by itself.")
        Tooltip(self.field_widgets["set_password"], "Set or change the password that locks the setup tabs.")
        Tooltip(self.field_widgets["remove_password"], "Remove the password so everyone can change settings.")
        Tooltip(self.field_widgets["operator_apply"], "Save the typed values; they apply from the next file.")
        Tooltip(self.slot_box, "Select which slot (range) you are editing.")

    def open_simulation(self):
        """Open the optional simulator control window without opening a console."""
        if self.simulation_process is not None and self.simulation_process.poll() is None:
            self.status_msg.set("Simulation window is already open.")
            return

        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        if getattr(sys, "frozen", False):
            command = [str(base / "CNCBridgeDemo.exe")]
        else:
            python = Path(sys.executable)
            pythonw = python.with_name("pythonw.exe")
            command = [str(pythonw if pythonw.exists() else python), "-m", "demo.run_demo"]
        try:
            self.simulation_process = subprocess.Popen(command, cwd=str(base))
            self.status_msg.set("Simulation window opened.")
        except OSError as exc:
            self.simulation_process = None
            messagebox.showerror(APP_NAME, f"Could not open the simulation window:\n{exc}", parent=self)

    @property
    def running(self):
        """This window's own monitoring is running."""
        return bool(self.thread and self.thread.is_alive())

    @property
    def processing(self):
        """Files are being processed, by this window or by the background process it is connected to."""
        return self.running or (self.connected and self.bg_running)

    def _apply(self, cfg):
        save_config(cfg, self.cfg_path)
        self.cfg = cfg
        self.fill(cfg)

    def save(self):
        cfg = self.collect()
        problems = engine.validate(cfg)
        if self.processing and problems:
            messagebox.showerror(APP_NAME, "Monitoring is running, so the settings must stay complete.\n\n- "
                                 + "\n- ".join(problems), parent=self)
            return False
        shift_problem = self._shift_problem(cfg)
        if shift_problem:
            messagebox.showerror(APP_NAME, shift_problem, parent=self)
            return False
        times_changed = cfg["shift_times"] != self.cfg["shift_times"]
        self._apply(cfg)
        if times_changed and cfg["shift_times"]:  # do not ask for a shift that started before they were set
            self.shift_confirmed = self._now()
            shifts.write_confirmed(self.data_dir, self.shift_confirmed)
        self._check_shift()
        if self.running:
            self.engine.cfg = cfg
        log.info("Settings saved")
        self.status_msg.set("Settings saved." if not problems else "Settings saved. Before starting: " + problems[0])
        self._apply_background_choice()  # the background process reads the saved settings by itself
        return True

    # ================================================================ background processing
    def _apply_background_choice(self):
        """Set up or remove the background task, as ticked on the Delivery tab, and hand over the processing."""
        want, have = self.background_on.get(), background.task_exists(self.data_dir)
        if want == have:
            return
        try:
            if want:
                background.install_task(self.data_dir)
                log.info("Background processing set up: it also starts when you log on to Windows")
                self._hand_over_to_background()
            else:
                background.remove_task(self.data_dir)
                log.info("Background processing switched off")
                if self.connected and self.bg_running:
                    background.request_stop(self.data_dir)
                    self.take_over_when_stopped = True
                    self.status_msg.set("Background processing is stopping. This window takes over when it has.")
                elif self.connected:
                    self._become_normal()
        except OSError as e:
            self.background_on.set(have)
            messagebox.showerror(APP_NAME, str(e), parent=self)

    def _hand_over_to_background(self):
        if self.running:
            self.engine.stop()
            self.thread.join(15)
        self.engine = self.thread = None
        if self.lock:
            self.lock.close()
            self.lock = None
        self.connected = True
        self._log_to("window.log")
        if engine.validate(self.cfg):
            self.status_msg.set("Background processing is set up. Complete the settings, then press Start "
                                "monitoring.")
            return
        background.start_task(self.data_dir)
        self.status_msg.set("Processing now runs in the background. It goes on after this window closes and "
                            "starts when you log on to Windows.")

    def _become_normal(self):
        """The background is off and stopped: this window processes files itself again."""
        lock = background.acquire_lock(self.data_dir)
        if not lock:
            return False
        self.lock, self.connected, self.take_over_when_stopped, self.bg_running = lock, False, False, False
        background.mark_holder(self.data_dir, "window")
        self._log_to("cncbridge.log")
        self.status_msg.set("Background processing is off. Press Start monitoring to process in this window.")
        return True

    def _log_to(self, filename):
        """Write this window's log to another file (a connected window never shares the background's log)."""
        for handler in [h for h in log.handlers if isinstance(h, RotatingFileHandler)]:
            log.removeHandler(handler)
            handler.close()
            new = RotatingFileHandler(Path(handler.baseFilename).with_name(filename), maxBytes=1_000_000,
                                      backupCount=5, encoding="utf-8")
            new.setFormatter(handler.formatter)
            log.addHandler(new)
        self._log_pos = None

    def _ready(self, quiet=False):
        cfg = self.collect()
        problems = engine.validate(cfg) + [p for p in [self._shift_problem(cfg)] if p]
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
        if self.processing or self.busy:
            return
        if self.connected:
            if not self._ready(quiet):
                return
            if background.task_exists(self.data_dir):
                try:
                    background.start_task(self.data_dir)
                    self.status_msg.set("Starting the background processing...")
                except OSError as e:
                    messagebox.showerror(APP_NAME, str(e), parent=self)
            elif self._become_normal():  # it was started some other way and has stopped
                self.start(quiet)
            return
        cfg = self._ready(quiet)
        if cfg:
            self.engine = engine.Engine(cfg, self.data_dir)
            self.thread = threading.Thread(target=self.engine.run_forever, daemon=True)
            self.thread.start()
            self.status_msg.set("Monitoring started.")

    def stop(self):
        if self.connected and self.bg_running:
            background.request_stop(self.data_dir)
            self.status_msg.set("Stopping the background processing after the current file...")
        elif self.running:
            self.engine.stop()
            self.status_msg.set("Stopping after the current file...")

    def process_now(self):
        if self.processing or self.busy or self.connected:
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

    def destroy(self):
        if self.lock:  # let the data folder go, for a background task or the next window
            self.lock.close()
            self.lock = None
        try:
            self.after_cancel(self._tick_job)  # no refresh may run after the window is gone
        except (AttributeError, tk.TclError):
            pass
        try:
            super().destroy()
        except tk.TclError:  # already destroyed: a restart closes the window, then a close may follow
            pass

    def on_close(self):
        if self.connected:  # the background process goes on by itself
            self.closing.set()
            self.destroy()
            return
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

    # ================================================================ settings files
    def export_settings(self):
        name = self.v["station_name"].get().strip() or APP_NAME
        path = filedialog.asksaveasfilename(
            parent=self, title="Export settings", defaultextension=".json",
            initialfile=f"{name} settings {datetime.now():%Y-%m-%d}.json",
            filetypes=[("Settings files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            export_settings(self.collect(), path)
        except OSError as e:
            return messagebox.showerror(APP_NAME, f"The settings could not be exported:\n{e}", parent=self)
        log.info("Settings exported to %s", path)
        self.status_msg.set("Settings exported. The API token is not included; enter it again on the other PC.")

    def import_settings(self, folder=None):
        """Load settings from an exported file or a backup copy into the form. Save settings keeps them."""
        start = folder if folder and Path(folder).is_dir() else None
        path = filedialog.askopenfilename(
            parent=self, title="Import settings" if not folder else "Restore earlier settings",
            initialdir=start, filetypes=[("Settings files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            cfg = import_settings(path, self.collect())
        except (OSError, ValueError) as e:
            return messagebox.showerror(APP_NAME, f"These settings could not be read:\n{e}", parent=self)
        # settings without a field in the window would otherwise be lost on the next save
        self.cfg = dict(self.cfg, **{k: cfg[k] for k in ("stable_seconds", "api_timeout")})
        self.fill(cfg)
        log.info("Settings loaded from %s (not saved yet)", path)
        self.status_msg.set(f"Settings loaded from {Path(path).name}. Check them, then press Save settings "
                            f"to keep them.")

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
        self.pick_hint.set("")
        if not self.grid_view.sel:
            return
        c1, r1, c2, r2 = self.grid_view.sel
        if self.pick_heading.get():
            row = self.sample_rows[r1] if r1 < len(self.sample_rows) else []
            heading = row[c1] if c1 < len(row) else ""
            if not heading:
                self.pick_hint.set("The top-left cell is empty, so there is no heading to find. Mark a cell with text.")
            else:
                matches = sum(1 for cells in self.sample_rows for cell in cells if cell.lower() == heading.lower())
                self.pick_range.set("@" + heading)
                self.pick_hint.set("The block is found again by this text, wherever it moves in the file." if matches == 1
                                   else f"This text appears {matches} times in the sample; the first one is used.")
                return
        self.pick_range.set(engine.format_range(c1, r1, c2, None if self.pick_open.get() else r2))

    def assign(self):
        if not self.pick_range.get():
            return messagebox.showinfo(APP_NAME, "Drag over the cells you want in the grid first.", parent=self)
        name, rng, _ = self.sel_vars[self._slot()]
        rng.set(self.pick_range.get())
        name.set(self.pick_name.get().strip())
        self.grid_view.set_selection(None)
        self.pick_range.set("")
        self.pick_hint.set("")
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
                    "history": self.ledger.recent(200),
                    "alerts": alerts.read_state(self.data_dir)}
                if self.connected:
                    self.bg_running = background.is_running(self.data_dir)
                    self._read_background_log()
            except Exception as e:
                log.debug("status refresh failed: %s", e)
            self.closing.wait(1.5)

    _LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ (\w+)\s+(.*)$")

    def _read_background_log(self):
        """Show the background process's new log lines in the Log tab."""
        path = self.data_dir / "logs" / "cncbridge.log"
        try:
            size = path.stat().st_size
        except OSError:
            return
        if self._log_pos is None or size < self._log_pos:  # first look, or the log was rotated
            self._log_pos = size if self._log_pos is None else 0
            if self._log_pos:
                return
        with open(path, "rb") as f:
            f.seek(self._log_pos)
            chunk = f.read()
        self._log_pos += len(chunk)
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            m = self._LOG_LINE.match(line)
            if m:
                self.log_q.put((m.group(2), f"{m.group(1)}  {m.group(2):<7}  [background] {m.group(3)}"))

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

        running = self.processing
        if self.connected:
            text, colour = ("In background", theme.SUCCESS) if running else ("Background off", theme.IDLE)
            if self.take_over_when_stopped and not running:
                self._become_normal()
        else:
            text, colour = ("Monitoring", theme.SUCCESS) if running else ("Working...", theme.WARNING) if self.busy else (
                "Stopped", theme.IDLE)
        self.state_label.configure(text=text, width=max(11, len(text)))
        self.state_label.configure(foreground=colour)
        self.state_dot.configure(fg=colour)
        idle = not running and not self.busy
        self.start_btn.configure(state="normal" if idle else "disabled")
        self.once_btn.configure(state="normal" if idle and not self.connected else "disabled")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self._set_menu("start", idle)
        self._set_menu("process_now", idle and not self.connected)
        self._set_menu("stop", running)
        if not running and self.status_msg.get().startswith("Stopping"):
            self.status_msg.set("Monitoring stopped.")

        if time.monotonic() - self._shift_checked > 15:  # cheap, but once every few seconds is plenty
            self._shift_checked = time.monotonic()
            self._check_shift()

        snap = self.snapshot
        if snap:
            self._show_alerts(snap.get("alerts") or {})
            for key, label in self.counts.items():
                colour = (theme.ERROR if snap[key] else theme.TEXT) if key in ("failed", "rejected") else (
                    theme.WARNING if snap[key] else theme.TEXT) if key == "queued" else (
                    theme.SUCCESS if snap[key] else theme.TEXT) if key == "ok" else theme.ACCENT if snap[key] else theme.TEXT
                label.configure(text=str(snap[key]), foreground=colour)
            if snap["history"] != self.shown_history:
                self.shown_history = snap["history"]
                selected = set(self.history.selection())
                self.history.delete(*self.history.get_children())
                for row in self.shown_history:
                    pending = "queued" in (row["shared"], row["api"]) or "rejected" in (row["api"] or "")
                    tag = row["status"] if row["status"] != "ok" else "pending" if pending else ""
                    self.history.insert("", "end", iid=str(row["id"]), tags=(tag,), values=[
                        (row["processed_at"] or "").replace("T", "  "), row["source"], row["status"],
                        row["output"] or "", row["shared"] or "", row["api"] or "", row["message"] or ""])
                keep = [iid for iid in selected if self.history.exists(iid)]
                if keep:
                    self.history.selection_set(keep)
                self._update_history_buttons()
                if self.shown_history:
                    self.history_empty.place_forget()
                else:
                    self.history_empty.place(relx=0.5, rely=0.5, anchor="center")
        self._tick_job = self.after(300, self._tick)


    # ================================================================ second chances (History tab)
    def _selected_rows(self):
        by_id = {str(r["id"]): r for r in self.shown_history or []}
        return [by_id[i] for i in self.history.selection() if i in by_id]

    @staticmethod
    def _refused(row):
        return any((row[k] or "").startswith("rejected") for k in ("shared", "api"))

    def _update_history_buttons(self):
        rows = self._selected_rows()
        self.resend_btn.configure(state="normal" if any(self._refused(r) for r in rows) else "disabled")
        self.reprocess_btn.configure(
            state="normal" if any(r["status"] in ("failed", "duplicate") for r in rows) else "disabled")

    def resend_selected(self):
        rows = [r for r in self._selected_rows() if self._refused(r)]
        sent = [r for r in rows if engine.resend_rejected(self.data_dir, r, self.ledger)]
        self.status_msg.set(f"{len(sent)} file(s) queued to send again." if sent else
                            "The refused files were not found in the data folder any more.")

    def reprocess_selected(self):
        rows = [r for r in self._selected_rows() if r["status"] in ("failed", "duplicate")]
        if any(r["status"] == "duplicate" for r in rows) and not messagebox.askokcancel(
                APP_NAME, "A duplicate has the same content as a file that was already delivered.\n\n"
                          "Process it again and deliver it a second time?", parent=self):
            return
        done = []
        for row in rows:
            try:
                if engine.reprocess(self.cfg, self.data_dir, row, self.ledger):
                    done.append(row)
            except OSError as e:
                messagebox.showerror(APP_NAME, f"{row['source']} could not be moved back:\n{e}", parent=self)
        self.status_msg.set(f"{len(done)} file(s) put back in the machine folder; they are processed in the "
                            f"next cycle." if done else "Those files were not found in the back-up folder any more.")

    def _show_alerts(self, open_alerts):
        """A red banner under the counters while a problem is open; gone when it is resolved."""
        if open_alerts:
            lines = [f"⚠  {item['text']}  (since {item['since'][11:16]})" for item in open_alerts.values()]
            self.alert_banner.configure(text="\n".join(lines), wraplength=max(self.winfo_width() - 40, 400))
            if not self.alert_banner_shown:
                self.alert_banner.pack(fill="x", after=self.header_sep)
                self.alert_banner_shown = True
        elif self.alert_banner_shown:
            self.alert_banner.pack_forget()
            self.alert_banner_shown = False


def run(data_dir, notice=None, lock=None, resume=False):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass
    App(data_dir, notice, lock, connected=lock is None, resume=resume).mainloop()
