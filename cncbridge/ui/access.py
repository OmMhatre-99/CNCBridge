"""The operator view, the setup password and the shift-change confirmation."""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import config, firstrun, permissions, shifts, theme
from ..config import APP_NAME, SettingsTooNew, normalize_config, save_config
from .common import log
from .dialogs import ShiftDialog
from ..i18n import tr


class AccessMixin:
    def _build_operator(self):
        """What an operator sees while the setup is locked: the typed values and nothing else to change."""
        tab = self.operator_tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.tabs.add(tab, text=tr("  Operator  "))
        self._page_intro(tab, tr("Typed values"),
                         tr("These values are written into every file. Change them when the operator or the "
                         "work order changes; they apply from the next file, without stopping."))
        self.operator_counts = {}
        if self.L.page_headers:  # a screen of its own, see _operator_mode
            self._build_operator_status(tab)
        box = ttk.LabelFrame(tab, text=tr(" Typed values "), padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap if self.L.page_headers else 0, 0))
        box.columnconfigure(1, weight=1)
        self.operator_labels = []
        big = {"font": (self._font_body, 16)} if self.L.page_headers else {}  # read and typed at arm's length
        for i, (_, value) in enumerate(self.man_vars):
            label = ttk.Label(box, text=tr("Typed value {number}", number=i + 1), style="Card.TLabel", width=22,
                              **big)
            label.grid(row=i, column=0, sticky="w", pady=6)
            entry = ttk.Entry(box, textvariable=value, **big)
            entry.grid(row=i, column=1, sticky="ew", padx=8, pady=6, ipady=self.px(6) if big else 0)
            self.field_widgets[f"operator_value_{i}"] = entry
            self.operator_labels.append(label)
        apply = ttk.Button(box, text=tr("Apply"), style="Go.TButton", command=self.apply_operator_values)
        apply.grid(row=len(self.man_vars), column=1, sticky="w", padx=8, pady=(8, 2))
        self.field_widgets["operator_apply"] = apply

    def _build_operator_status(self, tab):
        """The state, Start / Stop / Process now, and three counters, at the top of the operator's page."""
        px = self.px
        card = ttk.LabelFrame(tab, text=tr(" Status "), padding=self.L.box_pad)
        card.pack(fill="x")
        row = ttk.Frame(card)
        row.pack(fill="x")
        self.operator_state = ttk.Label(row, text=tr("Stopped"), font=self.strong(20))
        self.operator_state.pack(side="left")
        self.operator_stop = ttk.Button(row, text="■  " + tr("Stop"), style="Dark.TButton", command=self.stop)
        self.operator_stop.pack(side="right")
        self.operator_start = ttk.Button(row, text="▶  " + tr("Start monitoring"), style="Go.TButton",
                                         command=self.start)
        self.operator_start.pack(side="right", padx=(0, px(8)))
        self.operator_once = ttk.Button(row, text="↻  " + tr("Process now"), command=self.process_now)
        self.operator_once.pack(side="right", padx=(0, px(8)))
        counters = ttk.Frame(card)
        counters.pack(fill="x", pady=(px(14), 0))
        for column, (key, caption) in enumerate((("ok", tr("Done")), ("queued", tr("Waiting to be sent")),
                                                 ("failed", tr("Could not be read")))):
            counters.columnconfigure(column, weight=1, uniform="operator")
            box = ttk.Frame(counters, style="Metric.TFrame", padding=(px(14), px(10)))
            box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else px(10), 0))
            ttk.Label(box, text=caption, style="MetricSmall.TLabel").pack(anchor="w")
            self.operator_counts[key] = ttk.Label(box, text="0", style="MetricValue.TLabel")
            self.operator_counts[key].pack(anchor="w")

    def _build_operator_bar(self):
        """The dark bar of the operator view: whose station, the three pages an operator has, and the way back."""
        px = self.px
        bar = self.operator_bar = tk.Frame(self.content, bg=theme.SIDEBAR)
        self.operator_title = tk.Label(bar, text="", bg=theme.SIDEBAR, fg="#ffffff", font=self.strong(13))
        self.operator_title.pack(side="left", padx=(px(24), px(20)), pady=px(12))
        self.operator_nav = {}
        for key, label, attribute in (("operator", tr("Operator"), "operator_tab"), ("monitor", tr("Monitor"), "monitor_tab"),
                                      ("history", tr("History"), "history_tab")):
            if not hasattr(self, attribute):
                continue
            button = tk.Button(bar, text=label, bg=theme.SIDEBAR, fg=theme.SIDEBAR_TEXT, relief="flat", bd=0,
                               activebackground=theme.SIDEBAR_HOVER, activeforeground="#ffffff", cursor="hand2",
                               font=(self._font_body, 11), padx=px(12), pady=px(6), highlightthickness=2,
                               highlightbackground=theme.SIDEBAR, highlightcolor=theme.FOCUS,
                               command=lambda a=attribute: self._show_tab(getattr(self, a)))
            button.pack(side="left")
            self.operator_nav[key] = button
        unlock = tk.Button(bar, text=tr("Unlock setup..."), bg=theme.SIDEBAR_HOVER, fg="#ffffff", relief="flat", bd=0,
                           activebackground=theme.SIDEBAR_ACTIVE, activeforeground="#ffffff", cursor="hand2",
                           font=(self._font_body, 10), padx=px(14), pady=px(6), highlightthickness=2,
                           highlightbackground=theme.SIDEBAR, highlightcolor=theme.FOCUS, command=self.toggle_lock)
        unlock.pack(side="right", padx=px(20))
        self.field_widgets["operator_unlock"] = unlock
        self.tabs.bind("<<NotebookTabChanged>>", lambda event: self._mark_operator_nav(), add="+")

    def _mark_operator_nav(self):
        if not getattr(self, "operator_nav", None):
            return
        current = self.tabs.select()
        for key, button in self.operator_nav.items():
            on = current == str(getattr(self, f"{key}_tab"))
            button.configure(bg=theme.SIDEBAR_ACTIVE if on else theme.SIDEBAR, fg="#ffffff" if on else theme.SIDEBAR_TEXT)

    def _operator_mode(self, on):
        """A theme with page headers: while locked, the operator's own screen instead of the sidebar and header."""
        if getattr(self, "operator_bar", None) is None:
            self._build_operator_bar()
        station = self.cfg["station_name"] or tr("This station")
        self.operator_title.configure(text=tr("{station} · operator view", station=station))
        if on:
            if self.sidebar is not None:
                self.sidebar.pack_forget()
            self.page_header.pack_forget()
            if not self.operator_bar.winfo_manager():
                self.operator_bar.pack(fill="x", before=self.header_sep)
        else:
            self.operator_bar.pack_forget()
            if self.sidebar is not None and not self.sidebar.winfo_manager():
                self.sidebar.pack(side="left", fill="y", before=self.content)
            if not self.page_header.winfo_manager():
                self.page_header.pack(fill="x", before=self.header_sep)
        self._mark_operator_nav()

    def _update_operator_status(self, text, running, idle):
        """Called by _tick: the operator's copy of the state and the counters."""
        if not self.operator_counts:
            return
        if self.operator_state.cget("text") != text:
            self.operator_state.configure(text=text)
        self.operator_start.configure(state="normal" if idle else "disabled")
        self.operator_stop.configure(state="normal" if running else "disabled")
        self.operator_once.configure(state="normal" if idle and not self.connected else "disabled")
        snap = self.snapshot or {}
        for key, label in self.operator_counts.items():
            value = str(snap.get(key, 0))
            if label.cget("text") != value:
                label.configure(text=value)

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
        for button in self.save_buttons:
            button.configure(state="disabled" if locked else "normal")
        for key in self.LOCKED_ENTRIES:
            self._set_menu(key, not locked)
        lock_menu, lock_index = self.menu_items["lock"]
        lock_menu.entryconfigure(lock_index, label=tr("Unlock setup...") if locked else tr("Lock setup"),
                                 state="normal" if self.protected else "disabled")
        if locked:
            self.options_win.withdraw()
        for label, (name, _) in zip(self.operator_labels, self.man_vars):
            label.configure(text=name.get().strip() or label.cget("text"))
        self.password_state.set(tr("Password is set") if self.protected else tr("No password"))
        self.remove_pw_btn.configure(state="normal" if self.protected else "disabled")
        if self.L.page_headers:
            self._operator_mode(locked)
        self.tabs.select(self._home_tab(locked))
        self._sync_sidebar()
        self._schedule_checklist()

    def _home_tab(self, locked):
        """The page shown when the window opens, and after the lock changes."""
        if locked:
            return self.operator_tab
        if self.L.monitor_page and firstrun.is_done(self.data_dir):
            return self.monitor_tab  # set up and started before: the first thing wanted is to see how it is going
        return self.setup_tab

    def _ask_password(self, title, prompt):
        return simpledialog.askstring(title, prompt, show="●", parent=self)

    def toggle_lock(self):
        if self.protected and not self.unlocked:
            typed = self._ask_password(tr("Unlock setup"), tr("Setup password:"))
            if typed is None:
                return
            if not config.check_password(typed, self.cfg["setup_password"]):
                return messagebox.showerror(APP_NAME, tr("That password is not right."), parent=self)
            self.unlocked = True
        else:
            self.unlocked = False
        self._apply_lock()

    def set_password(self):
        first = self._ask_password(tr("Setup password"), tr("New password (at least {count} characters):", count=config.MIN_PASSWORD))
        if first is None:
            return
        if len(first) < config.MIN_PASSWORD:
            return messagebox.showerror(APP_NAME, tr("Use at least {count} characters.", count=config.MIN_PASSWORD), parent=self)
        if self._ask_password(tr("Setup password"), tr("Type it again:")) != first:
            return messagebox.showerror(APP_NAME, tr("The two passwords are not the same. Nothing was changed."),
                                        parent=self)
        self._store_password(config.hash_password(first))
        self.status_msg.set(tr("Password set. Press Lock setup when you are done; operators then see only the "
                            "Operator tab."))

    def remove_password(self):
        typed = self._ask_password(tr("Remove password"), tr("Current setup password:"))
        if typed is None:
            return
        if not config.check_password(typed, self.cfg["setup_password"]):
            return messagebox.showerror(APP_NAME, tr("That password is not right."), parent=self)
        self._store_password("")
        self.status_msg.set(tr("Password removed. Everyone can change the settings."))

    def _store_password(self, stored):
        """Save only the password; unsaved edits in the form stay unsaved."""
        cfg = dict(self.cfg, setup_password=stored)
        try:
            save_config(cfg, self.cfg_path, how="password")
        except PermissionError:
            return self._need_administrator()
        except SettingsTooNew as e:
            return messagebox.showerror(APP_NAME, tr("The password was not changed:\n{e}", e=e), parent=self)
        self.cfg = normalize_config(cfg)
        self.unlocked = True  # whoever just set or removed it is in setup
        self._apply_lock()

    # ================================================================ shift changes
    @staticmethod
    def _shift_problem(cfg):
        try:
            shifts.parse_times(cfg["shift_times"], strict=True)
        except ValueError as e:
            return tr("Shift times: {e}", e=e)
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
            self.shift_banner.configure(text="⏱  " + tr("Shift change at {due:%H:%M}: please confirm the typed values (click here)", due=due))
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
            if not self._save_typed_values():
                return False
        self.shift_confirmed = self._now()
        shifts.write_confirmed(self.data_dir, self.shift_confirmed)
        self._show_shift(None)
        self.status_msg.set(tr("Shift change confirmed."))
        return True

    def _save_typed_values(self):
        """
        Save only the typed values (operator.json). Operators may do this even when the settings are protected;
        nothing else in the form is saved. Returns False if it failed.
        """
        values = [var.get() for _, var in self.man_vars]
        try:
            cfg = config.save_operator_values(self.data_dir, values)
        except OSError as e:
            messagebox.showerror(APP_NAME, tr("The typed values could not be saved:\n{e}", e=e), parent=self)
            return False
        self.cfg = dict(self.cfg, manual_inputs=cfg["manual_inputs"])
        if self.running:
            self.engine.cfg = dict(self.engine.cfg, manual_inputs=cfg["manual_inputs"])
        log.info("Typed values saved")
        return True

    def apply_operator_values(self):
        """The Operator tab: the typed values are the only thing that can have changed."""
        if self._save_typed_values():
            self.status_msg.set(tr("Typed values applied. They are used from the next file."))
