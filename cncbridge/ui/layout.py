"""Window frame: scrolling tab area, toolbar, status header, footer and layout helpers."""
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .. import engine, theme
from ..config import FORMATS
from ..i18n import tr
from .widgets import Segmented, Switch


class LayoutMixin:
    def strong(self, size):
        """Font for emphasised text in the current theme."""
        return theme.strong_font(self.font_families, size)

    @property
    def code_font(self):
        """Options for short code-like entries (file pattern, minutes): monospaced only if the theme wants it."""
        return {"font": (self._font_mono, self.L.range_size)} if self.L.code_mono else {}

    def px(self, n):
        return int(n * self.scale)

    # ================================================================ layout
    def _build_tabs_scroller(self):
        """Keep the tab content usable when the main window is shorter than its ideal height."""
        host = ttk.Frame(self.content)
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

        self.tabs = ttk.Notebook(self.tabs_frame, style="Pages.TNotebook" if self.L.sidebar else "TNotebook")
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

    def _build_toolbar(self):
        """The buttons for the daily work. Everything else is in the menu bar."""
        if self.L.page_headers:  # a header bar with each page's own buttons (ui/pageheader.py)
            return self._build_page_header()
        bar = ttk.Frame(self.content, padding=(8, 4), style="Toolbar.TFrame")
        bar.pack(fill="x")

        def button(text, command, style="Toolbutton"):
            b = ttk.Button(bar, text=text, style=style, command=command)
            b.pack(side="left", padx=(0, 6))
            return b

        self.start_btn = button("▶  " + tr("Start monitoring"), self.start, "Go.Toolbutton")
        self.stop_btn = button("■  " + tr("Stop"), self.stop)
        self.once_btn = button("↻  " + tr("Process now"), self.process_now)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=3)
        self.save_btn = button("✔  " + tr("Save settings"), self.save)
        self.save_buttons = [self.save_btn]
        if self.L.sidebar:  # the state of the program is in the bar, since the header with the counters is a page
            self._build_state_pill(bar)
        ttk.Separator(self.content).pack(fill="x")

    def _build_state_pill(self, bar, side="right"):
        """A pill with a dot and the state (Stopped, Monitoring, ...)."""
        pill = self.state_pill = ttk.Frame(bar, style="Pill.TFrame", padding=(self.px(12), self.px(6)))
        pill.pack(side=side, padx=(8, 4))
        dot_size, state_size = self.L.state
        self.state_dot = tk.Label(pill, text="●", fg=theme.IDLE, bg=theme.SURFACE_ALT,
                                  font=(self._font_body, dot_size, "bold"))
        self.state_dot.pack(side="left")
        self.state_label = ttk.Label(pill, text=tr("Stopped"), style="Pill.TLabel", width=11)
        self.state_label.pack(side="left", padx=(6, 0))

    def _build_header(self):
        if self.L.monitor_page:  # the counters and the health line are on the Monitor page (see ui/monitor.py)
            self.header_sep = ttk.Separator(self.content)
            self.header_sep.pack(fill="x")
            self._build_banners()
            return
        bar = ttk.Frame(self.content, padding=(18, 10, 18, 10))
        bar.pack(fill="x")
        dot_size, state_size = self.L.state
        self.state_dot = tk.Label(bar, text="●", fg=theme.IDLE, bg=theme.BACKGROUND,
                                  font=(self._font_body, dot_size, "bold"))
        self.state_dot.pack(side="left")
        self.state_label = ttk.Label(bar, text=tr("Stopped"), font=self.strong(state_size), width=11)
        self.state_label.pack(side="left", padx=(4, 10))

        self.counts = {}
        self.count_boxes, self.count_captions = {}, {}
        for key, caption in reversed((("ok", tr("Done")), ("waiting", tr("Waiting to be read")),
                                      ("queued", tr("Waiting to be sent")), ("failed", tr("Could not be read")),
                                      ("rejected", tr("Refused")))):
            if self.L.metric_cards:
                box = ttk.Frame(bar, style="Metric.TFrame", padding=(12, 5))
                box.pack(side="right", padx=(8, 0))
            else:  # as in version 1.0: a bare number over its caption
                box = ttk.Frame(bar)
                box.pack(side="right", padx=(18, 0))
            self.counts[key] = ttk.Label(box, text="0", style="MetricValue.TLabel" if self.L.metric_cards
                                         else "Count.TLabel", anchor="e")
            self.counts[key].pack(anchor="e")
            self.count_captions[key] = ttk.Label(box, text=caption, style="CardSmall.TLabel")
            self.count_captions[key].pack(anchor="e")
            self.count_boxes[key] = box
        # a line of facts under the counters: last file, queue age, back-up drive (see _show_health)
        self.health_bar = ttk.Frame(self.content, padding=(18, 0, 18, 8))
        self.health_bar.pack(fill="x")
        self.health_labels = {key: ttk.Label(self.health_bar, text="", style="CardSmall.TLabel")
                              for key in ("last", "queue", "disk", "source")}
        self.header_sep = ttk.Separator(self.content)
        self.header_sep.pack(fill="x")
        self._build_banners()

    def _build_banners(self):
        # shown only while an alert is open (see _tick)
        if self.L.page_headers:
            self._build_problem_banner()
        else:
            self._build_alert_label()
        self._build_shift_banner()

    def _build_alert_label(self):
        self.alert_banner = tk.Label(self.content, text="", anchor="w", justify="left", bg=theme.ERROR_TINT, fg=theme.ERROR,
                                     font=self.strong(11), padx=18, pady=7)
        self.alert_banner_shown = False

    def _build_shift_banner(self):
        # shown while a shift change is waiting for the operator's confirmation; a click opens the dialog
        self.shift_banner = tk.Label(self.content, text="", anchor="w", justify="left", bg=theme.WARNING_TINT, fg=theme.WARNING,
                                     font=self.strong(11), padx=18, pady=7, cursor="hand2")
        self.shift_banner.bind("<Button-1>", lambda e: self.confirm_shift())
        self.shift_banner_shown = False

    def _page_intro(self, parent, title, hint):
        """The title and hint at the top of a tab; version 1.0 had none, and Legacy does not either."""
        intro = ttk.Frame(parent)
        if self.L.page_headers:  # shown in the header bar while this page is open
            self._remember_page_text(parent, title, hint)
            return intro
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
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="nw" if self.L.chips else "w",
                                                                pady=self.L.row)
        if self.L.chips:  # a line under the field says whether the folder is there (see _check_folders)
            cell = ttk.Frame(parent)
            cell.grid(row=row, column=1, sticky="ew", padx=8, pady=self.L.row)
            entry = ttk.Entry(cell, textvariable=self.v[key])
            entry.pack(fill="x")
            self.folder_notes[key] = ttk.Label(cell, text="", style="CardSmall.TLabel")
            self.folder_notes[key].pack(anchor="w", fill="x", pady=(self.px(3), 0))
            self._fit_wrap(self.folder_notes[key], self.px(420))
            self.v[key].trace_add("write", lambda *_: self._schedule_folder_check())
        else:
            entry = ttk.Entry(parent, textvariable=self.v[key])
            entry.grid(row=row, column=1, sticky="ew", padx=8, pady=self.L.row)
        self.field_widgets[key] = entry
        browse = ttk.Button(parent, text=tr("Browse..."), width=9,
                            command=lambda: self._browse(self.v[key]))
        browse.grid(row=row, column=2, padx=(2, 0), pady=self.L.row, sticky="n" if self.L.chips else "")
        self.field_widgets[f"{key}_browse"] = browse

    # ---------------------------------------------------------------- controls of a theme with chips
    def _toggle(self, parent, key, text):
        """A setting that is on or off: a switch in a theme with chips, a check box otherwise."""
        if self.L.chips:
            return Switch(parent, self.b[key], text, bg=theme.SURFACE, px=self.px,
                          font=ttk.Style(self).lookup("TCheckbutton", "font") or None)
        return ttk.Checkbutton(parent, text=text, variable=self.b[key])

    def _format_choice(self, parent, command=None, register=True):
        """JSON | CSV | TXT | PDF as one segmented control (a theme with chips), or as radio buttons."""
        if self.L.chips:
            choice = Segmented(parent, self.v["output_format"], [(f, f.upper()) for f in FORMATS], command=command,
                               font=ttk.Style(self).lookup("TButton", "font") or None)
            if register:
                for fmt, segment in choice.segments.items():
                    self.field_widgets[f"output_format_{fmt}"] = segment
            return choice
        return None

    def _schedule_folder_check(self):
        if getattr(self, "_folder_job", None):
            self.after_cancel(self._folder_job)
        self._folder_job = self.after(400, self._check_folders)

    def _check_folders(self):
        """The line under each folder: found (and how many machine files wait in it), will be created, not found."""
        self._folder_job = None
        if not self.folder_notes:
            return
        source, backup = self.v["source_folder"].get().strip(), self.v["backup_folder"].get().strip()
        for key, note in self.folder_notes.items():
            folder = self.v[key].get().strip()
            text, colour = "", theme.MUTED
            if not folder:
                pass
            elif folder.startswith(("\\\\", "//")):  # a network folder can take seconds to answer: not here
                text = tr("A network folder: use its Test button to check it.") if key == "shared_folder" else \
                    tr("A network folder: it is checked when monitoring starts.")
            elif key == "backup_folder" and source and engine._same_folder(source, folder):
                text, colour = tr("Must be a different folder from the machine folder."), theme.ERROR
            elif Path(folder).is_dir():
                if key == "source_folder":
                    n = len(engine.machine_files(folder, self.v["file_pattern"].get()))
                    text = tr("Found. {n} file(s) waiting.", n=n) if n else tr("Found. Nothing waiting.")
                else:
                    text = tr("Found.")
                colour = theme.SUCCESS
            elif key == "source_folder":
                text, colour = tr("Folder not found."), theme.ERROR
            else:
                text = tr("Not there yet; made with the first file.")
            if note.cget("text") != text:
                note.configure(text=text, foreground=colour)

    def _browse(self, var):
        start = var.get() if Path(var.get() or "?").is_dir() else None
        folder = filedialog.askdirectory(parent=self, initialdir=start, title=tr("Choose a folder"))
        if folder:
            var.set(os.path.normpath(folder))

    def _build_footer(self):
        bar = ttk.Frame(self.content, padding=(18, 10))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_msg, style="Small.TLabel").pack(side="left")
