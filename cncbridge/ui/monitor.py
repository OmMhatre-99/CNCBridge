"""The Monitor page of a theme that has one: how it is going, at a glance."""
import tkinter as tk
from tkinter import ttk

from .. import engine, theme
from ..i18n import N_, tr
from .widgets import Chip


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

    def _view_recent(self, index=0):
        if index < len(self.recent_shown):
            row = self._history_row(self.recent_shown[index][0])
            if row and row["output"]:
                self.view_output(row)

    def _build_recent_files(self, parent):
        """The latest files, a line each: the time, the machine file, its result and where it went, as chips."""
        box = ttk.LabelFrame(parent, text=tr(" Recent files "), padding=self.L.box_pad)
        area = self.recent_area = ttk.Frame(box)
        area.pack(fill="both", expand=True)
        area.columnconfigure(1, weight=1)
        for column, title in enumerate((tr("Time"), tr("Machine file"), tr("Result"), tr("Destinations"))):
            ttk.Label(area, text=title, style="CardSmall.TLabel").grid(row=0, column=column, sticky="w",
                                                                       padx=(0, self.px(14)), pady=(0, self.px(6)))
        ttk.Separator(area).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, self.px(4)))
        chip_font = (self._font_body, 9)
        self.recent_lines, self.recent_shown = [], []  # recent_shown: (history id, the texts of the line)
        for i in range(self.MONITOR_ROWS):
            time_label = ttk.Label(area, text="", style="Card.TLabel", font=(self._font_mono, 10))
            file_label = ttk.Label(area, text="", style="Card.TLabel")
            result = Chip(area, font=chip_font)
            sent = ttk.Frame(area)
            places = [Chip(sent, font=chip_font) for _ in engine.DESTINATIONS]
            for widget in (time_label, file_label, result, sent, *places):
                widget.bind("<Double-1>", lambda e, n=i: self._view_recent(n))
            self.recent_lines.append((time_label, file_label, result, sent, places))
        self.recent_empty = ttk.Label(area, style="EmptyState.TLabel", text=tr("No files processed yet"))
        self.recent_empty.grid(row=2, column=0, columnspan=4, pady=self.px(30))
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
            self.recent_shown = []
            for i, (time_label, file_label, result, sent, places) in enumerate(self.recent_lines):
                widgets = (time_label, file_label, result, sent)
                if i >= len(history):
                    for widget in widgets:
                        widget.grid_remove()
                    continue
                row = history[i]
                kinds = [k for k in engine.DESTINATIONS if (row.get(k) or "off") != "off"]
                status = self._result_text(row["status"])
                when = (row["processed_at"] or "")[11:19]
                time_label.configure(text=when)
                file_label.configure(text=row["source"], cursor="hand2" if row["output"] else "")
                result.set(status, {"ok": "ok", "failed": "error"}.get(row["status"], "idle"))
                labels = []
                for chip, kind in zip(places, list(kinds) + [None] * len(places)):
                    if kind is None:
                        chip.pack_forget()
                        continue
                    value = row.get(kind) or ""
                    mark = self._mark(value)
                    text = f"{engine.destination_name(kind, capital=True)} {mark}".strip()
                    chip.set(text, "ok" if value == "ok" else "error" if value.startswith("rejected") else "warn")
                    chip.pack(side="left", padx=(0, self.px(4)))
                    labels.append(text)
                for column, widget in enumerate(widgets):
                    widget.grid(row=i + 2, column=column, sticky="w", padx=(0, self.px(14)), pady=self.px(3))
                self.recent_shown.append((str(row["id"]), (when, row["source"], status, " · ".join(labels))))
            if history:
                self.recent_empty.grid_remove()
            else:
                self.recent_empty.grid()
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
