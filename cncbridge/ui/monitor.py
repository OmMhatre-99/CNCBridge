"""The Monitor page of a theme that has one: how it is going, at a glance."""
import tkinter as tk
from tkinter import font as tkfont, ttk

from .. import engine, theme
from ..i18n import N_, tr


class MonitorMixin:
    MONITOR_ROWS = 8
    # N_ marks the texts for translation; they are translated where they are shown (this is read at import)
    MONITOR_COUNTERS = (("ok", N_("Done"), N_("Machine files read and handled.")),
                        ("waiting", N_("Waiting to be read"), N_("In the machine folder, not taken yet.")),
                        ("queued", N_("Waiting to be sent"), N_("Output files waiting for a destination.")),
                        ("failed", N_("Could not be read"), N_("Set aside in the back-up folder.")),
                        ("rejected", N_("Refused"), N_("Turned down by a destination.")))

    def _build_monitor(self):
        """The counters, the health line, the latest files, where they went and the typed values."""
        tab = self.monitor_tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.tabs.add(tab, text=tr("  Monitor  "))
        self._page_intro(tab, tr("Monitor"), tr("How it is going: the counters, the latest files and where they went."))
        gap = self.L.box_gap

        cards = ttk.Frame(tab)
        cards.pack(fill="x")
        self.counts, self.count_boxes, self.count_captions = {}, {}, {}
        for column, (key, caption, hint) in enumerate(self.MONITOR_COUNTERS):
            cards.columnconfigure(column, weight=1, uniform="counter")
            box = ttk.Frame(cards, style="Metric.TFrame", padding=(self.px(16), self.px(12)))
            box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else gap // 2, 0))
            self.count_captions[key] = ttk.Label(box, text=tr(caption), style="MetricSmall.TLabel")
            self.count_captions[key].pack(anchor="w")
            self.counts[key] = ttk.Label(box, text="0", style="MetricValue.TLabel")
            self.counts[key].pack(anchor="w", pady=(self.px(2), self.px(2)))
            ttk.Label(box, text=tr(hint), style="CardSmall.TLabel", wraplength=self.px(170), justify="left").pack(anchor="w")
            self.count_boxes[key] = box

        # the facts under the counters: last file, queue age, back-up drive (see _show_health)
        self.health_bar = ttk.Frame(tab, style="Metric.TFrame", padding=(self.px(16), self.px(9)))
        self.health_bar.pack(fill="x", pady=(gap, 0))
        self.health_labels = {key: ttk.Label(self.health_bar, text="", style="CardSmall.TLabel")
                              for key in ("last", "queue", "disk", "source")}

        columns = ttk.Frame(tab)
        columns.pack(fill="both", expand=True, pady=(gap, 0))
        columns.columnconfigure(0, weight=3)
        columns.columnconfigure(1, weight=2, minsize=self.px(300))
        columns.rowconfigure(0, weight=1)
        self._build_recent_files(columns).grid(row=0, column=0, sticky="nsew", padx=(0, gap // 2))
        side = ttk.Frame(columns)
        side.grid(row=0, column=1, sticky="nsew", padx=(gap // 2, 0))
        self._build_destinations(side).pack(fill="x")
        self._build_typed_values(side).pack(fill="x", pady=(gap, 0))
        self._monitor_history, self._monitor_destinations = None, None

    def _view_recent(self):
        row = self._history_row(self.recent_tree.focus() or next(iter(self.recent_tree.selection()), ""))
        if row and row["output"]:
            self.view_output(row)

    def _build_recent_files(self, parent):
        box = ttk.LabelFrame(parent, text=tr(" Recent files "), padding=self.L.box_pad)
        area = ttk.Frame(box)
        area.pack(fill="both", expand=True)
        columns = (("time", tr("Time"), 80, False), ("file", tr("Machine file"), 210, True),
                   ("result", tr("Result"), 165, False), ("sent", tr("Destinations"), 290, False))
        self.recent_tree = ttk.Treeview(area, columns=[c[0] for c in columns], show="headings", selectmode="browse",
                                        height=self.MONITOR_ROWS)
        font = tkfont.Font(font=ttk.Style(self).lookup("Treeview", "font") or "TkDefaultFont")
        for key, title, width, stretch in columns:
            self.recent_tree.heading(key, text=title, anchor="w")
            if key == "time":  # wide enough for the time in the larger text sizes too
                width = max(self.px(width), font.measure("00:00:00") + self.px(20))
            else:
                width = self.px(width)
            self.recent_tree.column(key, width=width, anchor="w", stretch=stretch)
        self.recent_tree.tag_configure("failed", foreground=theme.ERROR)
        self.recent_tree.tag_configure("duplicate", foreground=theme.MUTED)
        self.recent_tree.tag_configure("pending", foreground=theme.WARNING)
        self.recent_tree.pack(fill="both", expand=True)
        self.recent_tree.bind("<Double-1>", lambda e: self._view_recent())
        self.recent_tree.bind("<Return>", lambda e: self._view_recent())
        self.recent_empty = ttk.Label(area, style="EmptyState.TLabel", text=tr("No files processed yet"))
        self.recent_empty.place(relx=0.5, rely=0.5, anchor="center")
        link = ttk.Button(box, text=tr("Open History"), command=lambda: self._show_tab(self.history_tab))
        link.pack(anchor="w", pady=(self.px(10), 0))
        self.field_widgets["monitor_history"] = link
        return box

    def _build_destinations(self, parent):
        box = ttk.LabelFrame(parent, text=tr(" Destinations "), padding=self.L.box_pad)
        self.destination_rows = {}
        for kind in engine.DESTINATIONS:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=self.px(4))
            dot = tk.Label(row, text="●", fg=theme.IDLE, bg=theme.SURFACE, font=(self._font_body, 11))
            dot.pack(side="left", anchor="n", padx=(0, self.px(10)))
            words = ttk.Frame(row)
            words.pack(side="left", fill="x", expand=True)
            ttk.Label(words, text=tr(engine.destination_name(kind, capital=True)), style="Card.TLabel",
                      font=self.strong(11)).pack(anchor="w")
            status = ttk.Label(words, text="", style="CardSmall.TLabel", wraplength=self.px(250), justify="left")
            status.pack(anchor="w", fill="x")
            self.destination_rows[kind] = (dot, status)
        link = ttk.Button(box, text=tr("Delivery settings"), command=lambda: self._show_tab(self.delivery_tab))
        link.pack(anchor="w", pady=(self.px(8), 0))
        self.field_widgets["monitor_delivery"] = link
        return box

    def _build_typed_values(self, parent):
        box = ttk.LabelFrame(parent, text=tr(" Typed values "), padding=self.L.box_pad)
        box.columnconfigure(1, weight=1)
        self.monitor_values = []
        for row, (name, value) in enumerate(self.man_vars):
            label, shown = tk.StringVar(self), tk.StringVar(self)
            ttk.Label(box, textvariable=label, style="CardSmall.TLabel").grid(row=row, column=0, sticky="w",
                                                                                pady=self.px(4), padx=(0, self.px(14)))
            ttk.Label(box, textvariable=shown, style="Card.TLabel", font=(self._font_mono, self.L.range_size)).grid(
                row=row, column=1, sticky="w", pady=self.px(4))
            self.monitor_values.append((label, shown))
            for var in (name, value):
                var.trace_add("write", lambda *_: self._refresh_monitor_values())
        self.monitor_shift = ttk.Label(box, text="", style="CardSmall.TLabel", wraplength=self.px(280), justify="left")
        self.monitor_shift.grid(row=len(self.man_vars), column=0, columnspan=2, sticky="w", pady=(self.px(8), 0))
        change = ttk.Button(box, text=tr("Change..."), command=self._monitor_change_values)
        change.grid(row=len(self.man_vars) + 1, column=0, columnspan=2, sticky="w", pady=(self.px(8), 0))
        self.field_widgets["monitor_values"] = change
        self._refresh_monitor_values()
        return box

    def _refresh_monitor_values(self):
        for i, ((name, value), (label, shown)) in enumerate(zip(self.man_vars, self.monitor_values), 1):
            label.set(name.get().strip() or tr("Typed value {i}", i=i))
            shown.set(value.get() or "-")
        times = self.cfg["shift_times"] if hasattr(self, "cfg") else ""
        self.monitor_shift.configure(text=tr("Confirmed at shift changes: {times}", times=times) if times
                                     else tr("Change them when the work order changes."))

    def _monitor_change_values(self):
        locked = self.protected and not self.unlocked
        self._show_tab(self.operator_tab if locked else self.setup_tab)

    # ---------------------------------------------------------------- what it shows
    def _show_monitor(self, snap):
        """Called with every new snapshot: the latest files and the state of each destination."""
        history = snap["history"]
        if history != self._monitor_history:
            self._monitor_history = history
            self.recent_tree.delete(*self.recent_tree.get_children())
            for row in history[:self.MONITOR_ROWS]:
                kinds = [k for k in engine.DESTINATIONS if (row.get(k) or "off") != "off"]
                sent = " · ".join(f"{engine.destination_name(k, capital=True)} {self._mark(row.get(k))}" for k in kinds)
                pending = any((row.get(k) or "") == "queued" or (row.get(k) or "").startswith("rejected") for k in kinds)
                tag = row["status"] if row["status"] != "ok" else "pending" if pending else ""
                self.recent_tree.insert("", "end", iid=str(row["id"]), tags=(tag,), values=[
                    (row["processed_at"] or "")[11:19], row["source"], self._result_text(row["status"]), sent])
            if history:
                self.recent_empty.place_forget()
            else:
                self.recent_empty.place(relx=0.5, rely=0.5, anchor="center")
        self._show_destinations(snap)

    def _mark(self, value):
        value = value or ""
        if value == "ok":
            return "✓"
        if value == "queued":
            return tr("waiting")
        return tr("refused") if value.startswith("rejected") else ""

    def _show_destinations(self, snap):
        """One line per destination: working, waiting to be sent, not answering, or off."""
        shown = []
        for kind, (fn, enabled) in engine.DESTINATIONS.items():
            if not enabled(self.cfg):
                shown.append((kind, tr("Off"), theme.IDLE))
                continue
            waiting = snap.get("queued_kinds", {}).get(kind, 0)
            problem = snap["alerts"].get(kind)
            if problem:
                shown.append((kind, tr("Not answering since {time} · {n} waiting to be sent", time=problem["since"][11:16],
                                       n=waiting), theme.WARNING))
            elif waiting:
                shown.append((kind, tr("{n} waiting to be sent", n=waiting), theme.WARNING))
            else:
                last = next((r for r in snap["history"] if (r.get(kind) or "") == "ok"), None)
                shown.append((kind, tr("Delivered · last at {time}", time=(last["processed_at"] or "")[11:16]) if last
                              else tr("Working · nothing delivered yet"), theme.SUCCESS))
        if shown == self._monitor_destinations:
            return
        self._monitor_destinations = shown
        for kind, text, colour in shown:
            dot, status = self.destination_rows[kind]
            dot.configure(fg=colour)
            status.configure(text=text)
