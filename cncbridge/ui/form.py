"""The settings form: variables, filling and collecting, settings files, destination tests."""
import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from .. import alerts, background, engine, shifts
from ..config import APP_NAME, MAX_MANUAL, MAX_SELECTIONS, export_settings, import_settings, normalize_config
from .common import BODY_LABELS, DEFAULT_FONT_LABEL, FONT_SIZE_LABELS, UI_SCALE_LABELS, log


class FormMixin:
    # ================================================================ variables <-> settings
    def _make_vars(self):
        self.v = {k: tk.StringVar(self) for k in (
            "station_name", "source_folder", "file_pattern", "backup_folder", "output_format", "shared_folder",
            "api_url", "api_token", "api_body", "encoding", "delimiter", "poll_seconds",
            "complete_marker", "shift_times", "theme", "color_mode", "font_family", "font_size", "ui_scale",
            "alert_after_minutes", "alert_webhook_url", "alert_email_server", "alert_email_port",
            "alert_email_user", "alert_email_password", "alert_email_from", "alert_email_to",
            "keep_backup_days", "keep_history_days", "ftp_host", "ftp_port", "ftp_user", "ftp_password",
            "ftp_folder")}
        self.b = {k: tk.BooleanVar(self) for k in ("shared_enabled", "api_enabled", "skip_duplicates", "autostart",
                                                    "json_numbers", "csv_flat", "time_zone",
                                                    "alert_notify", "alert_webhook_enabled", "alert_email_enabled",
                                                    "alert_email_starttls", "ftp_enabled", "ftp_tls",
                                                    "ftp_passive")}
        self.sel_vars = [(tk.StringVar(self), tk.StringVar(self), tk.BooleanVar(self)) for _ in range(MAX_SELECTIONS)]
        self.man_vars = [(tk.StringVar(self), tk.StringVar(self)) for _ in range(MAX_MANUAL)]
        self.pick_range, self.pick_name = tk.StringVar(self), tk.StringVar(self)
        self._now = datetime.now     # replaced in tests
        self.shift_confirmed = shifts.read_confirmed(self.data_dir)
        if self.shift_confirmed is None:  # first start: nothing is overdue yet
            self.shift_confirmed = self._now()
            shifts.write_confirmed(self.data_dir, self.shift_confirmed)
        self.shift_dialog, self._shift_prompted, self._shift_checked = None, None, 0.0
        self.unlocked = False        # the setup password was entered in this session
        self.password_state = tk.StringVar(self)
        self.theme_note = tk.StringVar(self)
        self.pick_open = tk.BooleanVar(self)
        self.pick_heading = tk.BooleanVar(self)
        self.pick_hint = tk.StringVar(self)
        self.status_msg = tk.StringVar(self)
        # set up on this PC (a scheduled task), not part of the settings file
        self.background_on = tk.BooleanVar(self, background.task_exists(self.data_dir))

        self.field_widgets = {}

    def fill(self, cfg):
        numbers = ("poll_seconds", "alert_after_minutes", "alert_email_port", "keep_backup_days",
                   "keep_history_days", "ftp_port")
        for k, var in self.v.items():
            var.set(BODY_LABELS[cfg[k]] if k == "api_body" else f"{cfg[k]:g}" if k in numbers
                    else FONT_SIZE_LABELS[cfg[k]] if k == "font_size" else UI_SCALE_LABELS[cfg[k]] if k == "ui_scale"
                    else (cfg[k] or DEFAULT_FONT_LABEL) if k == "font_family" else cfg[k])
        for k, var in self.b.items():
            var.set(cfg[k])
        for (name, rng, header), s in zip(self.sel_vars, cfg["selections"]):
            name.set(s["name"]), rng.set(s["range"]), header.set(s["header"])
        for (name, value), m in zip(self.man_vars, cfg["manual_inputs"]):
            name.set(m["name"]), value.set(m["value"])
        self.headers_text.delete("1.0", "end")
        self.headers_text.insert("1.0", cfg["api_headers"])
        self.rename_text.delete("1.0", "end")
        self.rename_text.insert("1.0", cfg["rename_columns"])
        self._update_theme_note()

    def collect(self):
        """The settings as currently shown in the window (saved or not)."""
        cfg = dict(self.cfg)
        cfg.update({k: var.get() for k, var in self.v.items()})
        cfg.update({k: var.get() for k, var in self.b.items()})
        cfg["api_body"] = next((k for k, label in BODY_LABELS.items() if label == self.v["api_body"].get()), "raw")
        cfg.update(self._chosen_appearance())
        cfg["api_headers"] = self.headers_text.get("1.0", "end").strip()
        cfg["rename_columns"] = self.rename_text.get("1.0", "end").strip()
        cfg["selections"] = []
        for name, rng, header in self.sel_vars:
            text = rng.get().strip()
            try:
                text = engine.normalize_range(text) if text else ""
            except ValueError:
                pass  # left as typed; validate() names the slot
            cfg["selections"].append({"name": name.get(), "range": text, "header": header.get()})
        cfg["manual_inputs"] = [{"name": n.get(), "value": val.get()} for n, val in self.man_vars]
        return normalize_config(cfg)

    def test_alert(self):
        cfg = self.collect()
        self._in_background("Test alert", lambda: alerts.send_test(cfg))

    # ================================================================ tests for the two destinations
    def _in_background(self, title, work):
        def job():
            try:
                result = (True, work())
            except Exception as e:
                result = (False, str(e))
            show = messagebox.showinfo if result[0] else messagebox.showerror
            self.ui_q.put(lambda: show(title, result[1], parent=self))
        threading.Thread(target=job, daemon=True).start()

    def test_shared(self):
        cfg = self.collect()
        if not cfg["shared_folder"]:
            return messagebox.showerror("Shared folder", "Enter the shared folder first.", parent=self)
        self._in_background("Shared folder", lambda: f"The folder can be written to:\n{engine.test_shared(cfg)}")

    def test_api(self):
        cfg = self.collect()
        if not cfg["api_url"]:
            return messagebox.showerror("API link", "Enter the API link first.", parent=self)
        self._in_background("API link", lambda: f"A small test file was accepted (HTTP {engine.test_api(cfg)}).")

    def test_ftp(self):
        cfg = self.collect()
        if not cfg["ftp_host"]:
            return messagebox.showerror("FTP server", "Enter the FTP server address first.", parent=self)
        self._in_background("FTP server", lambda: "A test file was uploaded and removed again in:\n"
                                                  f"{engine.test_ftp(cfg)}")

    # ================================================================ settings files
    def export_settings(self):
        name = self.v["station_name"].get().strip() or APP_NAME
        path = filedialog.asksaveasfilename(
            parent=self, title="Export settings", defaultextension=".json",
            initialfile=f"{name} settings {datetime.now():%Y-%m-%d}.json",
            filetypes=[("Settings files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            export_settings(self.collect(), path)
        except OSError as e:
            return messagebox.showerror(APP_NAME, f"The settings could not be exported:\n{e}", parent=self)
        log.info("Settings exported to %s", path)
        self.status_msg.set("Settings exported. The API token is not included; enter it again on the other PC.")

    def import_settings(self, folder=None):
        """Load settings from an exported file or a backup copy into the form. Save settings keeps them."""
        start = folder if folder and Path(folder).is_dir() else None
        path = filedialog.askopenfilename(
            parent=self, title="Import settings" if not folder else "Restore earlier settings",
            initialdir=start, filetypes=[("Settings files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            cfg = import_settings(path, self.collect())
        except (OSError, ValueError) as e:
            return messagebox.showerror(APP_NAME, f"These settings could not be read:\n{e}", parent=self)
        # settings without a field in the window would otherwise be lost on the next save
        self.cfg = dict(self.cfg, **{k: cfg[k] for k in ("stable_seconds", "api_timeout")})
        self.fill(cfg)
        log.info("Settings loaded from %s (not saved yet)", path)
        self.status_msg.set(f"Settings loaded from {Path(path).name}. Check them, then press Save settings "
                            f"to keep them.")

    def _open_folder(self, folder):
        if folder and Path(folder).is_dir():
            os.startfile(folder)
        else:
            messagebox.showinfo(APP_NAME, "That folder does not exist yet.", parent=self)
