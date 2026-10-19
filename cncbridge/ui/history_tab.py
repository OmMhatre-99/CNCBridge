"""The History tab and its Send again / Process again actions."""
from tkinter import messagebox, ttk

from .. import engine, theme
from ..config import APP_NAME


class HistoryTabMixin:
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
