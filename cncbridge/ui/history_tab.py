"""The History tab and its Send again / Process again actions."""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import engine, theme
from ..config import APP_NAME
from ..i18n import N_, tr
from .widgets import Chip

# the filters of a theme with chips: key -> (label, which rows)
FILTERS = (("all", N_("All"), lambda r: True),
           ("ok", N_("Delivered"), lambda r: r["status"] == "ok" and not _waiting(r) and not _refused_row(r)),
           ("waiting", N_("Waiting to be sent"), lambda r: _waiting(r)),
           ("refused", N_("Refused"), lambda r: _refused_row(r)),
           ("failed", N_("Could not be read"), lambda r: r["status"] == "failed"),
           ("duplicate", N_("Duplicate"), lambda r: r["status"] == "duplicate"))


def _waiting(row):
    return any((row[k] or "") == "queued" for k in ("shared", "api", "ftp"))


def _refused_row(row):
    return any((row[k] or "").startswith("rejected") for k in ("shared", "api", "ftp"))


class HistoryTabMixin:
    def _build_history(self):
        tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.history_tab = tab
        self.tabs.add(tab, text=tr("  History  "))
        self._page_intro(tab, tr("File history"), tr("Review processed machine files and check whether each output reached its destination."))
        self.history_filter, self.history_search = "all", tk.StringVar(self)
        if self.L.chips:
            bar = self._build_history_filters(tab)
        else:
            bar = ttk.Frame(tab)
            bar.pack(fill="x", pady=(0, 10))
            ttk.Button(bar, text=tr("Open back-up folder"),
                       command=lambda: self._open_folder(self.cfg["backup_folder"])).pack(side="left")
            ttk.Button(bar, text=tr("Open data folder (queue, rejected, log file)"),
                       command=lambda: self._open_folder(self.data_dir)).pack(side="left", padx=6)
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8, pady=3)
        self.resend_btn = ttk.Button(bar, text=tr("Send again"), command=self.resend_selected, state="disabled")
        self.resend_btn.pack(side="left")
        self.reprocess_btn = ttk.Button(bar, text=tr("Process again"), command=self.reprocess_selected,
                                        state="disabled")
        self.reprocess_btn.pack(side="left", padx=6)
        self.view_output_btn = ttk.Button(bar, text=tr("View output"), command=self.view_selected_output,
                                          state="disabled")
        self.view_output_btn.pack(side="left", padx=(6, 0) if self.L.chips else 0)
        if not self.L.chips:
            ttk.Label(bar, text=tr("Most recent 200 files"), style="PageHint.TLabel").pack(side="right")
        cols = (("processed_at", tr("Time"), 165), ("source", tr("Machine file"), 200), ("status", tr("Result"), 130),
                ("output", tr("Output file"), 250), ("shared", tr("Shared folder"), 130), ("api", tr("API link"), 130),
                ("ftp", tr("FTP server"), 130), ("message", tr("Note"), 300))
        history_area = ttk.Frame(tab, style="Card.TFrame", padding=1)
        history_area.pack(fill="both", expand=True)
        self.history = ttk.Treeview(history_area, columns=[c[0] for c in cols], show="headings",
                                    style="History.Treeview", takefocus=True, selectmode="extended")
        self.history.bind("<<TreeviewSelect>>", lambda e: self._update_history_buttons())
        self.history.bind("<Double-1>", lambda e: self.view_selected_output())
        self.history.bind("<Return>", lambda e: self.view_selected_output())
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
                                       text=tr("No files processed yet\n\nRecent file activity will appear here."))
        self.history_empty.place(relx=0.5, rely=0.5, anchor="center")

    def _build_history_filters(self, tab):
        """A theme with chips: a search, a chip per kind of row with its count, and a bar for the selected rows."""
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, self.px(10)))
        ttk.Button(top, text=tr("Open data folder"), command=lambda: self._open_folder(self.data_dir)).pack(side="right")
        ttk.Button(top, text=tr("Open back-up folder"),
                   command=lambda: self._open_folder(self.cfg["backup_folder"])).pack(side="right", padx=6)
        search = ttk.Entry(top, textvariable=self.history_search, width=28)
        search.pack(side="left")
        self.field_widgets["history_search"] = search
        self.history_search.trace_add("write", lambda *_: self._fill_history())
        ttk.Label(top, text=tr("Search file names and notes"), style="PageHint.TLabel").pack(side="left", padx=(8, 0))
        chips = ttk.Frame(tab)
        chips.pack(fill="x", pady=(0, self.px(10)))
        self.history_chips = {}
        for key, label, _ in FILTERS:
            chip = Chip(chips, font=(self._font_body, 10), bg=theme.BACKGROUND,
                        command=lambda k=key: self._choose_history_filter(k))
            chip.pack(side="left", padx=(0, self.px(6)))
            self.history_chips[key] = chip
        self._count_history_chips()
        # the selection bar: shown while rows are selected (see _update_history_buttons)
        bar = self.history_selection_bar = ttk.Frame(tab, style="Metric.TFrame", padding=(self.px(12), self.px(8)))
        self.history_selected = ttk.Label(bar, text="", style="Card.TLabel", font=self.strong(10))
        self.history_selected.pack(side="left", padx=(0, self.px(14)))
        ttk.Button(bar, text=tr("Clear selection"), command=lambda: self.history.selection_set(())).pack(side="right")
        return bar

    def _choose_history_filter(self, key):
        self.history_filter = key
        self._fill_history()

    def _count_history_chips(self):
        rows = self.shown_history or []
        for key, label, keep in FILTERS:
            active = key == self.history_filter
            self.history_chips[key].set(f"{tr(label)}  {sum(1 for r in rows if keep(r))}",
                                        "accent" if active else "plain")

    def _history_rows_shown(self):
        """The rows that pass the filter chip and the search."""
        rows = self.shown_history or []
        if not self.L.chips:
            return rows
        keep = next(k for key, _, k in FILTERS if key == self.history_filter)
        wanted = self.history_search.get().strip().lower()
        return [r for r in rows if keep(r) and (not wanted or any(
            wanted in (r[k] or "").lower() for k in ("source", "output", "message")))]

    def _fill_history(self):
        """Fill the table from the history (and, with chips, the filter and the search); keep the selection."""
        selected = set(self.history.selection())
        self.history.delete(*self.history.get_children())
        for row in self._history_rows_shown():
            pending = "queued" in (row["shared"], row["api"], row["ftp"]) or any(
                "rejected" in (row[k] or "") for k in ("api", "ftp"))
            tag = row["status"] if row["status"] != "ok" else "pending" if pending else ""
            self.history.insert("", "end", iid=str(row["id"]), tags=(tag,), values=[
                (row["processed_at"] or "").replace("T", "  "), row["source"], self._result_text(row["status"]),
                row["output"] or "", self._delivery_text(row["shared"]), self._delivery_text(row["api"]),
                self._delivery_text(row["ftp"]), row["message"] or ""])
        keep = [iid for iid in selected if self.history.exists(iid)]
        if keep:
            self.history.selection_set(keep)
        if self.L.chips:
            self._count_history_chips()
        self._update_history_buttons()
        if self.history.get_children():
            self.history_empty.place_forget()
        else:
            self.history_empty.configure(text=tr("No files match") if self.shown_history else
                                         tr("No files processed yet\n\nRecent file activity will appear here."))
            self.history_empty.place(relx=0.5, rely=0.5, anchor="center")

    # ================================================================ second chances (History tab)
    def _selected_rows(self):
        by_id = {str(r["id"]): r for r in self.shown_history or []}
        return [by_id[i] for i in self.history.selection() if i in by_id]

    @staticmethod
    def _result_text(status):
        """The Result column in the words of the counters; what is stored stays ok / failed / duplicate."""
        return {"ok": tr("Done"), "failed": tr("Could not be read"), "duplicate": tr("Duplicate")}.get(status, status)

    @staticmethod
    def _delivery_text(value):
        """A destination's column in plain words; what is stored stays ok / queued / off / rejected (...)."""
        value = value or ""
        if value.startswith("rejected"):
            return tr("Refused") + value[len("rejected"):]
        return {"ok": tr("Delivered"), "queued": tr("Waiting to be sent"), "off": tr("Off")}.get(value, value)

    @staticmethod
    def _refused(row):
        return any((row[k] or "").startswith("rejected") for k in ("shared", "api", "ftp"))

    def _update_history_buttons(self):
        rows = self._selected_rows()
        if self.L.chips:  # the selection bar is there only while something is selected
            if rows:
                self.history_selected.configure(text=tr("{n} selected", n=len(rows)))
                if not self.history_selection_bar.winfo_manager():
                    self.history_selection_bar.pack(fill="x", pady=(0, self.px(10)), before=self.history.master)
            elif self.history_selection_bar.winfo_manager():
                self.history_selection_bar.pack_forget()
        self.view_output_btn.configure(state="normal" if len(rows) == 1 and rows[0]["output"] else "disabled")
        self.resend_btn.configure(state="normal" if any(self._refused(r) for r in rows) else "disabled")
        self.reprocess_btn.configure(
            state="normal" if any(r["status"] in ("failed", "duplicate") for r in rows) else "disabled")

    def view_selected_output(self):
        rows = [r for r in self._selected_rows() if r["output"]]
        if rows:
            self.view_output(rows[0])

    def resend_selected(self):
        rows = [r for r in self._selected_rows() if self._refused(r)]
        sent = [r for r in rows if engine.resend_rejected(self.data_dir, r, self.ledger)]
        self.status_msg.set(tr("{count} file(s) queued to send again.", count=len(sent)) if sent else
                            tr("The refused files were not found in the data folder any more."))

    def reprocess_selected(self):
        rows = [r for r in self._selected_rows() if r["status"] in ("failed", "duplicate")]
        if any(r["status"] == "duplicate" for r in rows) and not messagebox.askokcancel(
                APP_NAME, tr("A duplicate has the same content as a file that was already delivered.\n\n"
                          "Process it again and deliver it a second time?"), parent=self):
            return
        done = []
        for row in rows:
            try:
                if engine.reprocess(self.cfg, self.data_dir, row, self.ledger):
                    done.append(row)
            except OSError as e:
                messagebox.showerror(APP_NAME, tr("{file} could not be moved back:\n{error}", file=row['source'], error=e), parent=self)
        self.status_msg.set(tr("{count} file(s) put back in the machine folder; they are processed in the next cycle.", count=len(done)) if done else tr("Those files were not found in the back-up folder any more."))
