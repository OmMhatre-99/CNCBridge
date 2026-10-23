"""The output viewer: an output file that was sent, shown as tables (JSON, CSV), as text (TXT), or opened (PDF)."""
import os
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import engine, theme
from ..config import APP_NAME
from ..i18n import tr

ROW_LIMIT = 1000  # rows shown at once; the filter narrows them down


class OutputWindowMixin:
    def view_output(self, row):
        """Show the output file of a history row. Returns False when it is not kept any more."""
        path = engine.find_output(self.cfg, self.data_dir, row) if row.get("output") else None
        if path is None:
            messagebox.showinfo(APP_NAME, tr(
                "The output file of {file} is not kept any more.\n\nA copy of each output is kept in the back-up "
                "folder (a folder 'outputs' in each day folder) until the back-up clean-up deletes that day. Files "
                "processed before this version had no copy.", file=row.get("source") or ""), parent=self)
            return False
        try:
            content = engine.read_output(path)
        except (OSError, ValueError) as e:
            messagebox.showerror(APP_NAME, tr("Cannot read {path}:\n{e}", path=path, e=e), parent=self)
            return False
        if getattr(self, "output_win", None) is None:
            self._build_output_window()
        self.output_path, self.output_content = path, content
        self._show_output()
        win = self.output_win
        win.title(tr("{name} - {APP_NAME} output", name=path.name, APP_NAME=APP_NAME))
        win.deiconify()
        win.lift()
        if self.theme.dark:
            theme.dark_title_bar(win)
        return True

    def _history_row(self, iid):
        return next((r for r in self.shown_history or [] if str(r["id"]) == str(iid)), None)

    # ---------------------------------------------------------------- building it (once, when first needed)
    def _build_output_window(self):
        win = self.output_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        win.bind("<Escape>", lambda event: win.withdraw())
        width = min(self.px(1000 * self.text_scale), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(620 * self.text_scale), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(560), width), min(self.px(320), height))
        top = ttk.Frame(win, padding=(14, 12, 14, 6))
        top.pack(fill="x")
        self.output_title = ttk.Label(top, text="", font=self.strong(13))
        self.output_title.pack(anchor="w")
        self.output_where = ttk.Label(top, text="", style="PageHint.TLabel")
        self.output_where.pack(anchor="w", pady=(2, 0))
        self.output_info = ttk.Label(top, text="", style="PageHint.TLabel", wraplength=self.px(900), justify="left")
        self.output_info.pack(anchor="w", pady=(2, 0))

        bar = ttk.Frame(win, padding=(14, 10))
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text=tr("Close"), command=win.withdraw).pack(side="right")
        ttk.Button(bar, text=tr("Open folder"), command=lambda: self._open_folder(self.output_path.parent)).pack(
            side="right", padx=8)
        ttk.Button(bar, text=tr("Save a copy..."), command=self.save_output_copy).pack(side="right")

        body = ttk.Frame(win, padding=(14, 0, 14, 0))
        body.pack(fill="both", expand=True)
        # tables: a choice of selection, a filter, and the rows
        tables = self.output_tables = ttk.Frame(body)
        row = ttk.Frame(tables)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text=tr("Selection")).pack(side="left")
        self.output_block = ttk.Combobox(row, state="readonly", width=34)
        self.output_block.pack(side="left", padx=(6, 16))
        self.output_block.bind("<<ComboboxSelected>>", lambda event: self._fill_output_table())
        ttk.Label(row, text=tr("Filter")).pack(side="left")
        self.output_filter = tk.StringVar(self)
        self.output_filter.trace_add("write", lambda *_: self._fill_output_table())
        ttk.Entry(row, textvariable=self.output_filter, width=24).pack(side="left", padx=(6, 12))
        self.output_count = ttk.Label(row, text="", style="PageHint.TLabel")
        self.output_count.pack(side="left")
        area = ttk.Frame(tables, style="Card.TFrame", padding=1)
        area.pack(fill="both", expand=True)
        self.output_tree = ttk.Treeview(area, show="headings", selectmode="extended", takefocus=True)
        ys = ttk.Scrollbar(area, orient="vertical", command=self.output_tree.yview)
        xs = ttk.Scrollbar(area, orient="horizontal", command=self.output_tree.xview)
        self.output_tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.output_tree.pack(fill="both", expand=True)
        # text: a TXT report as it is
        self.output_text_area = ttk.Frame(body, style="Card.TFrame", padding=1)
        self.output_text = tk.Text(self.output_text_area, wrap="none", font=(self._font_mono, self.L.text_mono),
                                   relief="flat", borderwidth=0, background=theme.SURFACE, foreground=theme.TEXT,
                                   insertbackground=theme.TEXT, highlightthickness=0, padx=9, pady=9, takefocus=True)
        ys = ttk.Scrollbar(self.output_text_area, orient="vertical", command=self.output_text.yview)
        xs = ttk.Scrollbar(self.output_text_area, orient="horizontal", command=self.output_text.xview)
        self.output_text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.output_text.pack(fill="both", expand=True)
        # PDF: what it holds, and the PDF reader for the rest
        self.output_pdf = ttk.Frame(body, style="Card.TFrame", padding=(20, 18))
        self.output_pdf_note = ttk.Label(self.output_pdf, text="", style="Card.TLabel")
        self.output_pdf_note.pack(anchor="w")
        ttk.Button(self.output_pdf, text=tr("Open in PDF reader"), style="Go.TButton",
                   command=self.open_output_file).pack(anchor="w", pady=(12, 0))

    # ---------------------------------------------------------------- showing a file
    def _show_output(self):
        content, path = self.output_content, self.output_path
        self.output_title.configure(text=path.name)
        self.output_where.configure(text=tr("Kept in {folder}", folder=path.parent))
        words = {"source_file": tr("Machine file"), "processed_at": tr("Processed"), "station": tr("Station")}
        self.output_info.configure(text="   ·   ".join(f"{words.get(k, k)}: {v}" for k, v in content["info"] if v))
        for area in (self.output_tables, self.output_text_area, self.output_pdf):
            area.pack_forget()
        if content["format"] == "pdf":
            self.output_pdf_note.configure(text=tr("A PDF report of {pages} page(s), {size} KB.",
                                                   pages=content["pages"], size=max(1, path.stat().st_size // 1024)))
            self.output_pdf.pack(fill="both", expand=True)
        elif content["format"] == "txt":
            self.output_text.configure(state="normal")
            self.output_text.delete("1.0", "end")
            self.output_text.insert("1.0", content["text"])
            self.output_text.configure(state="disabled")
            self.output_text_area.pack(fill="both", expand=True)
        else:
            names = [f"{i}:  {b['name']}  ({len(b['rows'])})" for i, b in enumerate(content["blocks"], 1)]
            self.output_block.configure(values=names)
            if names:
                self.output_block.current(0)
            self.output_filter.set("")  # which fills the table
            self.output_tables.pack(fill="both", expand=True)

    def _fill_output_table(self):
        tree = self.output_tree
        tree.delete(*tree.get_children())
        blocks = getattr(self, "output_content", {}).get("blocks") or []
        if not blocks:
            tree.configure(columns=())
            self.output_count.configure(text=tr("No selections in this file"))
            return
        block = blocks[max(self.output_block.current(), 0)]
        columns = block["columns"]
        ids = [f"c{i}" for i in range(len(columns))]
        tree.configure(columns=ids)
        for cid, name in zip(ids, columns):
            longest = max([len(name)] + [len(r[ids.index(cid)]) for r in block["rows"][:200]])
            tree.heading(cid, text=name, anchor="w")
            tree.column(cid, width=self.px(min(max(longest * 8 + 18, 70), 320)), anchor="w", stretch=False)
        wanted = self.output_filter.get().strip().lower()
        rows = [r for r in block["rows"] if not wanted or any(wanted in cell.lower() for cell in r)]
        for r in rows[:ROW_LIMIT]:
            tree.insert("", "end", values=r)
        total = len(block["rows"])
        if len(rows) > ROW_LIMIT:
            note = tr("The first {shown} of {count} rows; type in the filter to narrow them down.",
                      shown=ROW_LIMIT, count=len(rows))
        elif wanted:
            note = tr("{count} of {total} rows", count=len(rows), total=total)
        else:
            note = tr("{total} row(s)", total=total)
        self.output_count.configure(text=note)

    # ---------------------------------------------------------------- the buttons
    def save_output_copy(self):
        path = self.output_path
        target = filedialog.asksaveasfilename(parent=self.output_win, title=tr("Save a copy"), initialfile=path.name,
                                              defaultextension=path.suffix,
                                              filetypes=[(path.suffix.lstrip(".").upper(), f"*{path.suffix}")])
        if not target:
            return
        try:
            shutil.copyfile(path, target)
        except OSError as e:
            return messagebox.showerror(APP_NAME, tr("Cannot save {path}:\n{e}", path=target, e=e),
                                        parent=self.output_win)
        self.status_msg.set(tr("Saved a copy as {path}", path=target))

    def open_output_file(self):
        try:
            os.startfile(Path(self.output_path))  # in the program Windows uses for this kind of file
        except OSError as e:
            messagebox.showerror(APP_NAME, tr("Cannot open {path}:\n{e}", path=self.output_path, e=e),
                                 parent=self.output_win)
