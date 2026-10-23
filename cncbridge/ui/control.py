"""Starting, stopping and saving; background processing; closing the window."""
import subprocess
import sys
import threading
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox

from .. import background, config, engine, firstrun, permissions, shifts
from ..config import APP_NAME, SettingsTooNew, save_config
from .common import log
from ..i18n import tr


class ControlMixin:
    def _stopping_notes(self):
        """The status messages shown while a stop is under way (the window clears them when it has stopped)."""
        return {tr("Stopping the background processing after the current file..."),
                tr("Stopping after the current file...")}

    def open_simulation(self):
        """Open the optional simulator control window without opening a console."""
        if self.simulation_process is not None and self.simulation_process.poll() is None:
            self.status_msg.set(tr("Simulation window is already open."))
            return

        base = config.program_dir()  # the same folder as before: beside the exe, or the project folder
        if getattr(sys, "frozen", False):
            command = [str(base / "CNCBridgeDemo.exe")]
        else:
            python = Path(sys.executable)
            pythonw = python.with_name("pythonw.exe")
            command = [str(pythonw if pythonw.exists() else python), "-m", "demo.run_demo"]
        try:
            self.simulation_process = subprocess.Popen(command, cwd=str(base))
            self.status_msg.set(tr("Simulation window opened."))
        except OSError as exc:
            self.simulation_process = None
            messagebox.showerror(APP_NAME, tr("Could not open the simulation window:\n{exc}", exc=exc), parent=self)

    @property
    def running(self):
        """This window's own monitoring is running."""
        return bool(self.thread and self.thread.is_alive())

    @property
    def processing(self):
        """Files are being processed, by this window or by the background process it is connected to."""
        return self.running or (self.connected and self.bg_running)

    def _apply(self, cfg, how=None):
        """Save the settings. how: what led to it (save, operator, ...); else what the window remembers (import, restore)."""
        reason = (how, "") if how else self._pending_how or ("save", "")
        save_config(cfg, self.cfg_path, *reason)
        self._pending_how = None
        self.cfg = cfg
        self.fill(cfg)

    def save(self, how=None):
        cfg = self.collect()
        problems = engine.validate(cfg)
        if self.processing and problems:
            messagebox.showerror(APP_NAME, tr("Monitoring is running, so the settings must stay complete.\n\n- ")
                                 + "\n- ".join(problems), parent=self)
            return False
        shift_problem = self._shift_problem(cfg)
        if shift_problem:
            messagebox.showerror(APP_NAME, shift_problem, parent=self)
            return False
        times_changed = cfg["shift_times"] != self.cfg["shift_times"]
        if not permissions.can_write(self.cfg_path):
            self._need_administrator()
            return False
        try:
            self._apply(cfg, how)
        except PermissionError:
            self._need_administrator()
            return False
        except SettingsTooNew as e:
            messagebox.showerror(APP_NAME, tr("The settings were not saved:\n{e}\n\nUse the newer CNCBridge, or restore an "
                                              "earlier version from the File menu.", e=e), parent=self)
            return False
        if times_changed and cfg["shift_times"]:  # do not ask for a shift that started before they were set
            self.shift_confirmed = self._now()
            shifts.write_confirmed(self.data_dir, self.shift_confirmed)
        self._check_shift()
        if self.running:
            self.engine.cfg = cfg
        log.info("Settings saved")
        self._update_protection_state()
        self._schedule_checklist()  # and with it 'Unsaved changes' goes
        self.status_msg.set(tr("Settings saved.") if not problems else tr("Settings saved. Before starting: ") + problems[0])
        self._apply_background_choice()  # the background process reads the saved settings by itself
        return True

    # ================================================================ background processing
    def _apply_background_choice(self):
        """Set up or remove the background task, as ticked on the Delivery tab, and hand over the processing."""
        want, have = self.background_on.get(), background.task_exists(self.data_dir)
        if want == have:
            return
        try:
            if want:
                background.install_task(self.data_dir)
                log.info("Background processing set up: it also starts when you log on to Windows")
                self._hand_over_to_background()
            else:
                background.remove_task(self.data_dir)
                log.info("Background processing switched off")
                if self.connected and self.bg_running:
                    background.request_stop(self.data_dir)
                    self.take_over_when_stopped = True
                    self.status_msg.set(tr("Background processing is stopping. This window takes over when it has."))
                elif self.connected:
                    self._become_normal()
        except OSError as e:
            self.background_on.set(have)
            messagebox.showerror(APP_NAME, str(e), parent=self)

    def _hand_over_to_background(self):
        if self.running:
            self.engine.stop()
            self.thread.join(15)
        self.engine = self.thread = None
        if self.lock:
            self.lock.close()
            self.lock = None
        self.connected = True
        self._log_to("window.log")
        if engine.validate(self.cfg):
            self.status_msg.set(tr("Background processing is set up. Complete the settings, then press Start "
                                "monitoring."))
            return
        background.start_task(self.data_dir)
        self.status_msg.set(tr("Processing now runs in the background. It goes on after this window closes and "
                            "starts when you log on to Windows."))

    def _become_normal(self):
        """The background is off and stopped: this window processes files itself again."""
        lock = background.acquire_lock(self.data_dir)
        if not lock:
            return False
        self.lock, self.connected, self.take_over_when_stopped, self.bg_running = lock, False, False, False
        background.mark_holder(self.data_dir, "window")
        self._log_to("cncbridge.log")
        self.status_msg.set(tr("Background processing is off. Press Start monitoring to process in this window."))
        return True

    def _log_to(self, filename):
        """Write this window's log to another file (a connected window never shares the background's log)."""
        for handler in [h for h in log.handlers if isinstance(h, RotatingFileHandler)]:
            log.removeHandler(handler)
            handler.close()
            new = RotatingFileHandler(Path(handler.baseFilename).with_name(filename), maxBytes=1_000_000,
                                      backupCount=5, encoding="utf-8")
            new.setFormatter(handler.formatter)
            log.addHandler(new)
        self._log_pos = None

    def _ready(self, quiet=False):
        cfg = self.collect()
        problems = engine.validate(cfg) + [p for p in [self._shift_problem(cfg)] if p]
        if problems:
            if quiet:
                log.error("Monitoring was not started: %s", " ".join(problems))
            else:
                messagebox.showerror(APP_NAME, tr("Please complete the settings first.\n\n- ") + "\n- ".join(problems),
                                     parent=self)
            return None
        if permissions.can_write(self.cfg_path):
            self._apply(cfg)
        else:  # protected settings: an operator starts with the saved ones
            log.info("The settings are protected; monitoring starts with the saved ones")
            cfg = self.cfg
            if engine.validate(cfg):
                messagebox.showerror(APP_NAME, tr("The saved settings are not complete, and changing them needs "
                                                  "administrator rights."), parent=self)
                return None
        return cfg

    def start(self, quiet=False):
        if self.processing or self.busy:
            return
        if self.connected:
            if not self._ready(quiet):
                return
            if background.task_exists(self.data_dir):
                try:
                    background.start_task(self.data_dir)
                    self.status_msg.set(tr("Starting the background processing..."))
                    firstrun.mark_done(self.data_dir)
                    self._schedule_checklist()
                except OSError as e:
                    messagebox.showerror(APP_NAME, str(e), parent=self)
            elif self._become_normal():  # it was started some other way and has stopped
                self.start(quiet)
            return
        cfg = self._ready(quiet)
        if cfg:
            self.engine = engine.Engine(cfg, self.data_dir)
            self.thread = threading.Thread(target=self.engine.run_forever, daemon=True)
            self.thread.start()
            self.status_msg.set(tr("Monitoring started."))
            firstrun.mark_done(self.data_dir)
            self._schedule_checklist()

    def stop(self):
        if self.connected and self.bg_running:
            background.request_stop(self.data_dir)
            self.status_msg.set(tr("Stopping the background processing after the current file..."))
        elif self.running:
            self.engine.stop()
            self.status_msg.set(tr("Stopping after the current file..."))

    def process_now(self):
        if self.processing or self.busy or self.connected:
            return
        cfg = self._ready()
        if not cfg:
            return
        self.busy = True

        def job():
            try:
                n = engine.Engine(cfg, self.data_dir).scan_once()
                log.info("Process now finished: %d file(s) processed", n)
            except Exception as e:
                log.error("Process now failed: %s", e)
            finally:
                self.busy = False
        threading.Thread(target=job, daemon=True).start()

    def destroy(self):
        if self.lock:  # let the data folder go, for a background task or the next window
            self.lock.close()
            self.lock = None
        for job in ("_tick_job", "_edit_check_job", "_checklist_job", "_folder_job"):
            try:
                pending = getattr(self, job, None)
                if pending is not None:
                    self.after_cancel(pending)  # no refresh may run after the window is gone
            except (tk.TclError, ValueError):
                pass
        try:
            super().destroy()
        except tk.TclError:  # already destroyed: a restart closes the window, then a close may follow
            pass

    def on_close(self):
        if self.connected:  # the background process goes on by itself
            self.closing.set()
            self.destroy()
            return
        if self.running and not messagebox.askokcancel(
                APP_NAME, tr("Monitoring is running.\n\nClosing the window stops it; machine files will wait in the "
                          "folder until the program is started again.\n\nClose anyway?"), parent=self):
            return
        self.closing.set()
        if self.engine:
            self.engine.stop()
        self.destroy()
