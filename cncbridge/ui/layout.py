"""Window frame: scrolling tab area, toolbar, status header, footer and layout helpers."""
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .. import theme


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
        # a line of facts under the counters: last file, queue age, back-up drive (see _show_health)
        self.health_bar = ttk.Frame(self, padding=(18, 0, 18, 8))
        self.health_bar.pack(fill="x")
        self.health_labels = {key: ttk.Label(self.health_bar, text="", style="CardSmall.TLabel")
                              for key in ("last", "queue", "disk", "source")}
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

    def _build_footer(self):
        bar = ttk.Frame(self, padding=(18, 10))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_msg, style="Small.TLabel").pack(side="left")
