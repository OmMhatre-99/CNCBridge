"""The operator view, the setup password and the shift-change confirmation."""
from tkinter import messagebox, simpledialog, ttk

from .. import config, permissions, shifts
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
        box = ttk.LabelFrame(tab, text=tr(" Details added to every file "), padding=self.L.box_pad)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self.operator_labels = []
        for i, (_, value) in enumerate(self.man_vars):
            label = ttk.Label(box, text=tr("Input {number}", number=i + 1), style="Card.TLabel", width=22)
            label.grid(row=i, column=0, sticky="w", pady=6)
            ttk.Entry(box, textvariable=value).grid(row=i, column=1, sticky="ew", padx=8, pady=6)
            self.operator_labels.append(label)
        apply = ttk.Button(box, text=tr("Apply"), style="Go.TButton", command=self.apply_operator_values)
        apply.grid(row=len(self.man_vars), column=1, sticky="w", padx=8, pady=(8, 2))
        self.field_widgets["operator_apply"] = apply

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
        self.save_btn.configure(state="disabled" if locked else "normal")
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
        self.tabs.select(self.operator_tab if locked else self.setup_tab)

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
