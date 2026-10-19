"""The Options window and its pages, including Appearance and the restart."""
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .. import config, i18n, theme
from ..config import APP_NAME
from .common import ENCODINGS, DEFAULT_FONT_LABEL, FONT_SIZE_LABELS, UI_SCALE_LABELS, APPEARANCE_KEYS, DELIMITER_CHOICES
from ..i18n import tr


class OptionsMixin:
    def _build_options(self):
        """The Options window: the settings that are set once and rarely changed, on pages. Built once, then shown."""
        win = self.options_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        if self.theme.dark:
            theme.dark_title_bar(win)
        win.title(tr("{APP_NAME} options", APP_NAME=APP_NAME))
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", self.close_options)
        width = min(self.px(800 * self.text_scale), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(690 * self.text_scale), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(660), width), min(self.px(520), height))
        notebook = self.options_tabs = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=14, pady=(14, 0))
        self.option_pages = {}
        for key, title, build in (("processing", tr("Processing"), self._build_processing),
                                  ("output", tr("Output"), self._build_output_options),
                                  ("alerts", tr("Alerts"), self._build_alerts),
                                  ("access", tr("Operators & access"), self._build_access),
                                  ("appearance", tr("Appearance"), self._build_appearance)):
            page = ttk.Frame(notebook, padding=self.L.tab_pad)
            notebook.add(page, text=f"  {title}  ")
            self.option_pages[key] = page
            build(page)
        bar = ttk.Frame(win, padding=(14, 10))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_msg, style="Small.TLabel", wraplength=self.px(430),
                  justify="left").pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text=tr("Close"), command=self.close_options).pack(side="right")
        self.options_save_btn = ttk.Button(bar, text=tr("Save settings"), style="Go.TButton", command=self.save)
        self.options_save_btn.pack(side="right", padx=8)

    def open_options(self, page=None):
        if self.protected and not self.unlocked:
            return
        win = self.options_win
        if page:
            self.options_tabs.select(self.option_pages[page])
        if win.state() == "withdrawn":
            win.update_idletasks()
            x = self.winfo_rootx() + max((self.winfo_width() - win.winfo_reqwidth()) // 2, 0)
            y = self.winfo_rooty() + self.px(40)
            win.geometry(f"+{x}+{y}")
        self._update_theme_note()
        win.deiconify()
        win.lift()
        win.focus_set()
        if self.theme.dark:
            theme.dark_title_bar(win)

    def close_options(self):
        self.options_win.withdraw()
        self.focus_force()  # the shortcuts work again at once

    def _build_processing(self, parent):
        box = ttk.LabelFrame(parent, text=tr(" Monitoring & file options "), padding=self.L.box_pad)
        box.pack(fill="x")
        autostart = ttk.Checkbutton(box, text=tr("Start monitoring when the program opens"),
                                    variable=self.b["autostart"])
        autostart.grid(row=0, column=0, columnspan=2, sticky="w", pady=4)
        self.field_widgets["autostart"] = autostart
        skip_duplicates = ttk.Checkbutton(box, text=tr("Never send the same file content twice"),
                                          variable=self.b["skip_duplicates"])
        skip_duplicates.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.field_widgets["skip_duplicates"] = skip_duplicates
        ttk.Label(box, text=tr("Check the folder every"), style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=self.L.row)
        row = ttk.Frame(box)
        row.grid(row=2, column=1, sticky="w", padx=8)
        poll_seconds = ttk.Entry(row, textvariable=self.v["poll_seconds"], width=5,
                                 **self.code_font)
        poll_seconds.pack(side="left")
        self.field_widgets["poll_seconds"] = poll_seconds
        ttk.Label(row, text=tr(" seconds"), style="Card.TLabel").pack(side="left", padx=(5, 0))
        ttk.Label(box, text=tr("Machine file encoding"), style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=self.L.row)
        encoding = ttk.Combobox(box, textvariable=self.v["encoding"], values=ENCODINGS, width=12)
        encoding.grid(row=3, column=1, sticky="w", padx=8)
        self.field_widgets["encoding"] = encoding
        ttk.Label(box, text=tr("Column separator"), style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=self.L.row)
        delimiter = ttk.Combobox(box, textvariable=self.v["delimiter"], values=DELIMITER_CHOICES,
                                 state="readonly", width=12)
        delimiter.grid(row=4, column=1, sticky="w", padx=8)
        self.field_widgets["delimiter"] = delimiter
        ttk.Label(box, text=tr("File is complete when it contains"), style="Card.TLabel").grid(
            row=5, column=0, sticky="w", pady=self.L.row)
        marker = ttk.Entry(box, textvariable=self.v["complete_marker"], width=16, **self.code_font)
        marker.grid(row=5, column=1, sticky="w", padx=8)
        self.field_widgets["complete_marker"] = marker
        in_background = ttk.Checkbutton(box, text=tr("Process in the background: keeps going after this window\n"
                                                  "closes, and starts when you log on to Windows"),
                                        variable=self.background_on)
        in_background.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.field_widgets["run_in_background"] = in_background
        for row_no, (key, label) in enumerate((("keep_backup_days", tr("Delete back-up files after")),
                                                ("keep_history_days", tr("Delete history after"))), start=7):
            ttk.Label(box, text=label, style="Card.TLabel").grid(row=row_no, column=0, sticky="w", pady=self.L.row)
            row = ttk.Frame(box)
            row.grid(row=row_no, column=1, sticky="w", padx=8)
            days = ttk.Entry(row, textvariable=self.v[key], width=5, **self.code_font)
            days.pack(side="left")
            self.field_widgets[key] = days
            ttk.Label(row, text=tr(" days  (0 = keep everything)"), style="Card.TLabel").pack(side="left")

    def _build_output_options(self, parent):
        box = ttk.LabelFrame(parent, text=tr(" Output options "), padding=self.L.box_pad)
        box.pack(fill="x")
        note = ttk.Label(box, style="CardSmall.TLabel", justify="left", wraplength=self.px(620), text=(
            tr("All of these are off by default, and with all of them off the files are exactly what version 1.0 "
            "wrote. Turn one on only when the receiving system asks for it. The Pick ranges & preview tab shows "
            "the effect.")))
        note.pack(anchor="w", fill="x", pady=(0, 8))
        self._fit_wrap(note, self.px(620))
        for key, text in (("json_numbers", tr("JSON: write numbers as numbers, not as text")),
                          ("csv_flat", tr("CSV: one plain table, a row per record")),
                          ("time_zone", tr("Add the time zone to the processing time (+05:30)"))):
            check = ttk.Checkbutton(box, text=text, variable=self.b[key])
            check.pack(anchor="w")
            self.field_widgets[key] = check
        ttk.Label(box, text=tr("Rename columns, one per line as  Old name=New name"), style="CardSmall.TLabel"
                  ).pack(anchor="w", pady=(8, 3))
        self.rename_text = tk.Text(box, height=3, width=30, font=(self._font_mono, self.L.text_mono), relief="solid",
                                   borderwidth=1, background=theme.SURFACE, foreground=theme.TEXT, insertbackground=theme.TEXT,
                                   highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
                                   padx=7, pady=5, wrap="none", undo=True)
        self.rename_text.pack(fill="x")
        self.field_widgets["rename_columns"] = self.rename_text

    def _build_alerts(self, parent):
        box = ttk.LabelFrame(parent, text=tr(" Alerts "), padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        box.columnconfigure(1, weight=1)

        def check(row, key, text):
            widget = ttk.Checkbutton(box, text=text, variable=self.b[key])
            widget.grid(row=row, column=0, columnspan=4, sticky="w", pady=(4, 2))
            self.field_widgets[key] = widget

        def entry(row, key, label, show=None, width=None):
            ttk.Label(box, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(22, 0), pady=3)
            widget = ttk.Entry(box, textvariable=self.v[key], show=show or "", width=width or 0)
            widget.grid(row=row, column=1, columnspan=3, sticky="ew" if not width else "w", padx=(8, 0), pady=3)
            self.field_widgets[key] = widget

        row = ttk.Frame(box)
        row.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(row, text=tr("Alert when a problem lasts"), style="Card.TLabel").pack(side="left")
        minutes = ttk.Entry(row, textvariable=self.v["alert_after_minutes"], width=4, **self.code_font)
        minutes.pack(side="left", padx=6)
        self.field_widgets["alert_after_minutes"] = minutes
        ttk.Label(row, text=tr("minutes, or a file is set aside"), style="Card.TLabel").pack(side="left")
        check(1, "alert_notify", tr("Show a Windows notification on this PC"))
        check(2, "alert_webhook_enabled", tr("Post to a webhook (Teams, Slack or another system)"))
        entry(3, "alert_webhook_url", tr("Webhook link"))
        check(4, "alert_email_enabled", tr("Send an email"))
        entry(5, "alert_email_server", tr("Mail server"))
        ttk.Label(box, text=tr("Port"), style="Card.TLabel").grid(row=6, column=0, sticky="w", padx=(22, 0), pady=3)
        port_row = ttk.Frame(box)
        port_row.grid(row=6, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=3)
        port = ttk.Entry(port_row, textvariable=self.v["alert_email_port"], width=7)
        port.pack(side="left")
        self.field_widgets["alert_email_port"] = port
        starttls = ttk.Checkbutton(port_row, text=tr("Encrypt (STARTTLS)"), variable=self.b["alert_email_starttls"])
        starttls.pack(side="left", padx=(16, 0))
        self.field_widgets["alert_email_starttls"] = starttls
        entry(7, "alert_email_user", tr("User name"))
        entry(8, "alert_email_password", tr("Password"), show="●")
        entry(9, "alert_email_from", tr("From"))
        entry(10, "alert_email_to", "To")
        test = ttk.Button(box, text=tr("Send test alert"), command=self.test_alert)
        test.grid(row=11, column=0, columnspan=4, sticky="w", pady=(8, 2))
        self.field_widgets["test_alert"] = test

    def _build_appearance(self, parent):
        """How the window looks: theme, colour mode, font, text size, scale. They apply when the window restarts."""
        box = ttk.LabelFrame(parent, text=tr(" Appearance "), padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        for key, option in theme.THEMES.items():
            choice = ttk.Radiobutton(box, text=tr(option.label), value=key, variable=self.v["theme"],
                                     command=self._update_theme_note)
            choice.pack(anchor="w")
            self.field_widgets[f"theme_{key}"] = choice
            about = ttk.Label(box, text=tr(option.description), style="CardSmall.TLabel", wraplength=self.px(440),
                              justify="left")
            about.pack(fill="x", padx=(24, 0), pady=(0, 6))
            self._fit_wrap(about, self.px(440))
        ttk.Separator(box).pack(fill="x", pady=(2, 8))

        modes = ttk.Frame(box)
        modes.pack(fill="x")
        ttk.Label(modes, text=tr("Colours"), style="Card.TLabel").pack(side="left", padx=(0, 16))
        for value, label in (("light", tr("Light")), ("dark", tr("Dark")), ("system", tr("Follow Windows"))):
            radio = ttk.Radiobutton(modes, text=label, value=value, variable=self.v["color_mode"],
                                    command=self._update_theme_note)
            radio.pack(side="left", padx=(0, 16))
            self.field_widgets[f"color_mode_{value}"] = radio
        self.mode_note = tk.StringVar(self)
        ttk.Label(box, textvariable=self.mode_note, style="CardSmall.TLabel", wraplength=self.px(440),
                  justify="left").pack(anchor="w", pady=(2, 6))

        fields = ttk.Frame(box)
        fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        for row, (label, key, values) in enumerate((
                (tr("Font"), "font_family", [DEFAULT_FONT_LABEL] + theme.installed_font_choices(self)),
                (tr("Text size"), "font_size", list(FONT_SIZE_LABELS.values())),
                (tr("Window scale"), "ui_scale", list(UI_SCALE_LABELS.values())),
                (tr("Language"), "language", [name for _, name in i18n.available()]))):
            ttk.Label(fields, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=self.L.row)
            combo = ttk.Combobox(fields, textvariable=self.v[key], values=values, state="readonly", width=26)
            combo.grid(row=row, column=1, sticky="w", padx=8, pady=self.L.row)
            combo.bind("<<ComboboxSelected>>", lambda event: self._update_theme_note())
            self.field_widgets[key] = combo
        ttk.Label(box, style="CardSmall.TLabel", wraplength=self.px(440), justify="left",
                  text=tr("Text size changes only the text. Window scale makes everything larger, "
                       "including spacing, buttons and the menu bar.")).pack(anchor="w", pady=(2, 6))

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(2, 0))
        ttk.Label(row, textvariable=self.theme_note, style="CardSmall.TLabel", wraplength=self.px(300),
                  justify="left").pack(side="left", fill="x", expand=True)
        self.restart_btn = ttk.Button(row, text=tr("Restart now"), command=self.restart)
        self.restart_btn.pack(side="right")
        self.field_widgets["restart_now"] = self.restart_btn

    def _chosen_appearance(self):
        """The appearance settings as shown in the form."""
        family = self.v["font_family"].get()
        return {"theme": self.v["theme"].get(), "color_mode": self.v["color_mode"].get(),
                "font_family": "" if family == DEFAULT_FONT_LABEL else family,
                "font_size": next((n for n, label in FONT_SIZE_LABELS.items()
                                   if label == self.v["font_size"].get()), 100),
                "ui_scale": next((n for n, label in UI_SCALE_LABELS.items()
                                  if label == self.v["ui_scale"].get()), 100),
                "language": next((code for code, name in i18n.available() if name == self.v["language"].get()), "en")}

    def _update_theme_note(self):
        """Say what the chosen look needs: saving, a restart, or nothing."""
        if not hasattr(self, "restart_btn"):
            return
        chosen = self._chosen_appearance()
        if chosen == self.appearance_in_use:
            note, state = tr("This is the look in use."), "disabled"
        elif chosen == {k: self.cfg[k] for k in APPEARANCE_KEYS}:
            note, state = tr("Saved. It takes effect when CNCBridge restarts."), "normal"
        else:
            note, state = tr("Press Save settings to keep these choices, then restart to see them."), "normal"
        self.theme_note.set(note)
        self.mode_note.set(tr("Legacy's dark mode inverts its colours.") if chosen["theme"] == "legacy" else "")
        self.restart_btn.configure(state=state)

    def _restart_command(self, resume=False):
        """The command that starts this program again on the same data folder."""
        base = config.program_dir()
        if getattr(sys, "frozen", False):
            command = [sys.executable]
        else:
            python = Path(sys.executable)
            pythonw = python.with_name("pythonw.exe")
            command = [str(pythonw if pythonw.exists() else python), str(base / "CNCBridge.pyw")]
        command += ["--data", str(self.data_dir), "--restarted"]
        return command + ["--resume"] if resume else command

    def restart(self):
        """Save, then replace this window with a new one, so that a newly chosen theme is applied."""
        if self.busy:
            return messagebox.showinfo(APP_NAME, tr("Process now is running. Restart when it has finished."), parent=self)
        if not self.save():
            return
        resume = self.running
        try:  # the new program waits for this window to let go of the data folder
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")  # a one-file exe must unpack afresh
            subprocess.Popen(self._restart_command(resume), cwd=str(config.program_dir()), env=env)
        except OSError as e:
            return messagebox.showerror(APP_NAME, tr("CNCBridge could not be restarted:\n{e}", e=e), parent=self)
        if self.running:
            self.engine.stop()
            self.thread.join(20)
        self.closing.set()
        self.destroy()

    def _build_access(self, parent):
        box = ttk.LabelFrame(parent, text=tr(" Operators & access "), padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text=tr("Confirm typed values at"), style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=self.L.row)
        row = ttk.Frame(box)
        row.grid(row=0, column=1, sticky="w", padx=8)
        shift = ttk.Entry(row, textvariable=self.v["shift_times"], width=24, **self.code_font)
        shift.pack(side="left")
        self.field_widgets["shift_times"] = shift
        ttk.Label(row, text=tr("  shift starts, e.g. 06:00, 14:00, 22:00"), style="CardSmall.TLabel").pack(side="left")
        ttk.Label(box, text=tr("Setup password"), style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=self.L.row)
        row = ttk.Frame(box)
        row.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(row, textvariable=self.password_state, style="Card.TLabel", width=14).pack(side="left")
        set_pw = ttk.Button(row, text=tr("Set password..."), command=self.set_password)
        set_pw.pack(side="left", padx=(6, 0))
        self.remove_pw_btn = ttk.Button(row, text=tr("Remove"), command=self.remove_password)
        self.remove_pw_btn.pack(side="left", padx=6)
        self.field_widgets["set_password"] = set_pw
        self.field_widgets["remove_password"] = self.remove_pw_btn
        note = ttk.Label(box, style="CardSmall.TLabel", wraplength=self.px(420), justify="left",
                         text=tr("With a password, operators see only Start/Stop, the counters, History, the "
                              "Log and the typed values. It keeps settings from being changed by accident; it "
                              "does not protect against someone who can open this PC's files."))
        note.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._fit_wrap(note, self.px(420))
