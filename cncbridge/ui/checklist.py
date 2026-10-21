"""The first-start checklist at the top of the Setup tab: folders, what to extract, where to send, start."""
import tkinter as tk
from tkinter import ttk

from .. import engine, firstrun, output, theme
from ..i18n import N_, tr
from ..output import DELIVERY, FOLDERS, RANGES


class ChecklistMixin:
    # N_ marks the texts for translation; they are translated where they are shown (this is read at import)
    CHECKLIST_STEPS = (
        ("folders", N_("Choose the folders"),
         N_("Where the machine writes its CSV files, and where the used files are kept."), N_("Choose...")),
        ("ranges", N_("Choose what to extract"),
         N_("Open a machine file and drag over the cells you want. Each range gets a name."), N_("Open a file")),
        ("delivery", N_("Choose where the files go"),
         N_("A shared folder, an API link or an FTP server. Use its Test button to check it."), N_("Delivery")),
        ("start", N_("Start monitoring"),
         N_("CNCBridge then picks up each new file, sends it on and keeps a copy."), N_("Start")),
    )

    def _build_checklist(self, parent, before):
        """The panel is built hidden; _refresh_checklist shows it while it is wanted. before: what it goes above."""
        if not firstrun.is_done(self.data_dir) and not engine.validate(self.cfg):
            firstrun.mark_done(self.data_dir)  # a copy that is already set up has no use for a first-start guide
        self._checklist_forced, self._checklist_job, self.checklist_shown = False, None, False
        self._destination_tested = False
        self._checklist_before = before
        box = self.checklist_box = ttk.LabelFrame(parent, text=tr(" Getting started "), padding=self.L.box_pad)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, style="CardSmall.TLabel", text=tr("Four steps until files are flowing. This guide goes away "
                                                           "after the first start; View > Setup checklist brings "
                                                           "it back.")).grid(row=0, column=0, columnspan=2,
                                                                              sticky="w", pady=(0, 6))
        self.checklist_rows = {}
        for index, (key, title, hint, button) in enumerate(self.CHECKLIST_STEPS, 1):
            badge = tk.Label(box, text=f" {index} ", bg=theme.IDLE, fg=theme.WHITE,
                             font=self.strong(self.L.grid_badge + 1))
            badge.grid(row=index, column=0, padx=(0, 10), pady=4)
            words = ttk.Frame(box)
            words.grid(row=index, column=1, sticky="ew", pady=4)
            name = ttk.Label(words, text=tr(title), style="Card.TLabel", font=self.strong(self.L.grid_badge + 2))
            name.pack(anchor="w")
            detail = ttk.Label(words, text=tr(hint), style="CardSmall.TLabel", justify="left", wraplength=self.px(560))
            detail.pack(anchor="w", fill="x")
            self._fit_wrap(detail, self.px(560))
            go = ttk.Button(box, text=tr(button), width=14, command=getattr(self, f"_checklist_go_{key}"))
            go.grid(row=index, column=2, padx=(10, 0), pady=4)
            self.checklist_rows[key] = {"badge": badge, "title": name, "hint": detail, "go": go}
            self.field_widgets[f"checklist_{key}"] = go
        hide = ttk.Button(box, text=tr("Close this guide"), command=self.hide_checklist)
        hide.grid(row=0, column=2, sticky="e", pady=(0, 6))
        self.field_widgets["checklist_hide"] = hide

    # ---------------------------------------------------------------- showing it
    def _checklist_wanted(self):
        if self.protected and not self.unlocked:
            return False  # the setup is hidden from operators, and so is its guide
        return self._checklist_forced or not firstrun.is_done(self.data_dir)

    def _watch_setup_changes(self):
        """Look again whenever something in the setup is typed or ticked."""
        variables = [*self.v.values(), *self.b.values(), *(var for slot in self.sel_vars for var in slot)]
        for var in variables:
            var.trace_add("write", lambda *_: self._schedule_checklist())

    def _schedule_checklist(self):
        if self._checklist_job is None:
            self._checklist_job = self.after(150, self._refresh_checklist)

    def _refresh_checklist(self):
        self._checklist_job = None
        wanted = self._checklist_wanted()
        if wanted != self.checklist_shown:
            if wanted:
                self.checklist_box.pack(fill="x", pady=(0, self.L.box_gap), before=self._checklist_before)
            else:
                self.checklist_box.pack_forget()
            self.checklist_shown = wanted
        if not wanted:
            return
        parts = {part for part, _ in output.problems(self.collect())}
        done = {"folders": FOLDERS not in parts, "ranges": RANGES not in parts, "delivery": DELIVERY not in parts}
        ready = all(done.values())
        done["start"] = ready and (firstrun.is_done(self.data_dir) or self.processing)
        current = next((key for key, *_ in self.CHECKLIST_STEPS if not done[key]), None)
        for index, (key, title, hint, button) in enumerate(self.CHECKLIST_STEPS, 1):
            row = self.checklist_rows[key]
            if done[key]:
                row["badge"].configure(text=" ✓ ", bg=theme.SUCCESS)
            else:
                row["badge"].configure(text=f" {index} ", bg=theme.ACCENT if key == current else theme.IDLE)
            row["hint"].configure(text=tr(self._checklist_hint(key, hint, done[key])))
            row["go"].configure(style="Go.TButton" if key == current else "TButton",
                                state="normal" if key != "start" or ready else "disabled")

    def _checklist_hint(self, key, hint, finished):
        if key == "delivery" and finished:
            return N_("Set. Use its Test button to check that it works.") if not self._destination_tested \
                else N_("Set, and the test worked.")
        return hint

    def _destination_was_tested(self):
        self._destination_tested = True
        self._schedule_checklist()

    # ---------------------------------------------------------------- the buttons
    def _checklist_go_folders(self):
        self._show_tab(self.setup_tab)
        wanted = "source_folder" if not self.v["source_folder"].get().strip() else "backup_folder"
        self.field_widgets[wanted].focus_set()

    def _checklist_go_ranges(self):
        self.tabs.select(self.pick_tab)
        if not self.open_newest(quiet=True):
            self.open_csv()  # no file in the folders yet: ask for one

    def _checklist_go_delivery(self):
        self._show_tab(self.delivery_tab)

    def _checklist_go_start(self):
        self.start()

    def show_checklist(self):
        """View > Setup checklist: show it again, also after it has done its job."""
        self._checklist_forced = True
        self._show_tab(self.setup_tab)
        self._refresh_checklist()

    def hide_checklist(self):
        self._checklist_forced = False
        firstrun.mark_done(self.data_dir)
        self._refresh_checklist()
