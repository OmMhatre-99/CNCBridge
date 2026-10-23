"""The Pick ranges & preview tab."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import engine, theme
from ..config import APP_NAME, FORMATS
from ..grid import Grid
from .common import PREVIEW_LIMIT
from ..i18n import tr


class PickTabMixin:
    def _build_pick(self):
        tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.pick_tab = tab
        self.tabs.add(tab, text=tr("  Pick ranges & preview  "))
        self._page_intro(tab, tr("Pick data from a machine file"), tr("Open a sample file, select the cells to include, then assign them to a range slot."))
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 10))
        self.sample_open_btn = ttk.Button(top, text=tr("Open CSV file..."), style="Go.TButton",
                                          command=self.open_sample_dialog)
        self.sample_open_btn.pack(side="left")
        self.sample_newest_btn = ttk.Button(top, text=tr("Newest machine file"), command=self.open_newest)
        self.sample_newest_btn.pack(side="left", padx=8)
        self.sample_label = ttk.Label(top, text=tr("No sample file open"), style="PageHint.TLabel",
                                      **self.code_font)
        self.sample_label.pack(side="left", padx=12)
        for fmt in reversed(FORMATS):
            ttk.Radiobutton(top, text=fmt.upper(), value=fmt, variable=self.v["output_format"],
                            command=self.refresh_preview).pack(side="right", padx=(10, 0))
        ttk.Label(top, text=tr("Output preview as")).pack(side="right")

        panes = ttk.PanedWindow(tab, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(0, 4))
        left = ttk.Frame(panes)
        self.grid_view = Grid(left, on_select=self._grid_selected)
        self.grid_view.pack(fill="both", expand=True)
        bar = ttk.Frame(left, padding=(10, 8))
        bar.pack(fill="x", pady=(8, 0))
        ttk.Label(bar, text=tr("Selected range"), style="Card.TLabel").pack(side="left")
        pick_range_entry = ttk.Entry(bar, textvariable=self.pick_range, width=11, font=(self._font_mono, self.L.range_size),
                                     state="readonly")
        pick_range_entry.pack(side="left", padx=(4, 10))
        self.field_widgets["pick_range"] = pick_range_entry
        pick_open = ttk.Checkbutton(bar, text=tr("Down to first empty row"), variable=self.pick_open,
                                    command=self._update_pick_range)
        pick_open.pack(side="left")
        self.field_widgets["pick_open"] = pick_open
        bar = ttk.Frame(left, padding=(10, 0, 10, 8))  # a second row: the slot controls need the room
        bar.pack(fill="x")
        ttk.Label(bar, text=tr("Slot"), style="Card.TLabel").pack(side="left", padx=(0, 5))
        self.slot_box = ttk.Combobox(bar, state="readonly", width=22)
        self.slot_box.pack(side="left")
        self.slot_box.bind("<<ComboboxSelected>>", self._slot_chosen)
        ttk.Label(bar, text=tr("Name"), style="Card.TLabel").pack(side="left", padx=(12, 5))
        pick_name_entry = ttk.Entry(bar, textvariable=self.pick_name, width=16)
        pick_name_entry.pack(side="left")
        self.field_widgets["pick_name"] = pick_name_entry
        assign_button = ttk.Button(bar, text=tr("Assign"), style="Go.TButton", width=8, command=self.assign)
        assign_button.pack(side="left", padx=(8, 4))
        self.field_widgets["assign"] = assign_button
        clear_button = ttk.Button(bar, text=tr("Clear slot"), command=self.clear_slot)
        clear_button.pack(side="left")
        self.field_widgets["clear_slot"] = clear_button
        by_heading = ttk.Frame(left, padding=(10, 0, 10, 4))
        by_heading.pack(fill="x")
        pick_heading = ttk.Checkbutton(by_heading, text=tr("Find by heading"), variable=self.pick_heading,
                                       command=self._update_pick_range)
        pick_heading.pack(side="left")
        self.field_widgets["pick_heading"] = pick_heading
        ttk.Label(by_heading, textvariable=self.pick_hint, style="CardSmall.TLabel").pack(side="left", padx=(10, 0))
        panes.add(left, weight=3)

        right = ttk.LabelFrame(panes, text=tr(" Output preview "), padding=(12, 10))
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
        self.preview.insert("1.0", tr("Your output preview will appear here.\n\nOpen a sample machine file to get started."))
        self.preview.configure(state="disabled")
        panes.add(right, weight=2)

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
                                          filetypes=[(tr("Machine files"), "*.csv *.txt *.log *.json"), (tr("All files"), "*.*")])
        if path:
            self.open_sample(path)

    def open_newest(self, quiet=False):
        """Load the most recent machine file: one still waiting in the folder, else the latest in the back-up."""
        cfg, found = self.collect(), []
        for folder, sub_folders in ((cfg["source_folder"], False), (cfg["backup_folder"], True)):
            try:
                found = engine.machine_files(folder, cfg["file_pattern"], sub_folders) if folder else []
            except OSError:
                found = []
            if found:
                break
        if found:
            return self.open_sample(max(found, key=lambda f: f.stat().st_mtime))
        if not quiet:
            messagebox.showinfo(APP_NAME, tr("No machine file was found in the machine folder or the back-up folder."),
                                parent=self)
        return False

    def open_sample(self, path):
        cfg = self.collect()
        try:
            rows = engine.read_rows(Path(path), cfg["encoding"], cfg["delimiter"])
        except Exception as e:
            messagebox.showerror(APP_NAME, tr("Cannot read {path}:\n{e}", path=path, e=e), parent=self)
            return False
        self.sample_rows, self.sample_name = rows, Path(path).name
        width = max((len(r) for r in rows), default=0)
        self.sample_label.configure(text=tr("{name}   ({rows} rows x {width} columns)", name=self.sample_name, rows=len(rows), width=width))
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
            labels.append(f"{i + 1}:  {name.get() or rng.get() or tr('(empty)')}")
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
                self.pick_hint.set(tr("The top-left cell is empty, so there is no heading to find. Mark a cell with text."))
            else:
                matches = sum(1 for cells in self.sample_rows for cell in cells if cell.lower() == heading.lower())
                self.pick_range.set("@" + heading)
                self.pick_hint.set(tr("The block is found again by this text, wherever it moves in the file.") if matches == 1
                                   else tr("This text appears {matches} times in the sample; the first one is used.", matches=matches))
                return
        self.pick_range.set(engine.format_range(c1, r1, c2, None if self.pick_open.get() else r2))

    def assign(self):
        if not self.pick_range.get():
            return messagebox.showinfo(APP_NAME, tr("Drag over the cells you want in the grid first."), parent=self)
        name, rng, _ = self.sel_vars[self._slot()]
        rng.set(self.pick_range.get())
        name.set(self.pick_name.get().strip())
        self.grid_view.set_selection(None)
        self.pick_range.set("")
        self.pick_hint.set("")
        self._update_slots()
        self.refresh_preview()
        self.status_msg.set(tr("Slot {slot} set to {range}. Press Save settings to keep it.", slot=self._slot() + 1, range=rng.get()))

    def clear_slot(self):
        name, rng, _ = self.sel_vars[self._slot()]
        name.set(""), rng.set(""), self.pick_name.set("")
        self._update_slots()
        self.refresh_preview()

    def refresh_preview(self):
        cfg = self.collect()
        marks, note, text = [], "", tr("Your output preview will appear here.\n\nOpen a sample machine file to get started.")
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
                note = tr("With these ranges this file would be rejected: {e}.", e=e)
                text = tr("Preview unavailable\n\n{note}\n\nChoose a range that contains data in the sample file.", note=note)
            except ValueError:
                text = tr("No cell range yet.\n\nDrag over cells in the grid, choose a slot and press Assign.")
        self.grid_view.set_marks(marks)
        self.preview_note.configure(text=note)
        if len(text) > PREVIEW_LIMIT:
            text = text[:PREVIEW_LIMIT] + tr("\n... (preview shortened)")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
