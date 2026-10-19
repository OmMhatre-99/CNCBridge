"""
End-to-end tests that drive the real CNCBridge window. They open windows on the screen, so they only run
when asked:

    set CNCBRIDGE_WINDOW_TESTS=1
    python -m unittest tests.test_window -v
"""
import gc
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
import unittest
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

if os.environ.get("CNCBRIDGE_WINDOW_TESTS") != "1":
    raise unittest.SkipTest("window tests open real windows; set CNCBRIDGE_WINDOW_TESTS=1 to run them")

from cncbridge import alerts, app, engine, shifts  # noqa: E402

# Tk may only free a window on the thread that made it. Python's cyclic collector can run on any thread, so
# a closed window could be freed on a background thread and crash the process (Tcl_AsyncDelete). The tests
# therefore switch it off and collect on the main thread, after each test.
gc.disable()
from test_engine import Receiver  # noqa: E402

SAMPLE = ROOT / "samples" / "pnp_L1M8_board_00001.csv"


def drive(window, steps):
    """
    Run a generator of steps inside the window's event loop. A step yields a number (milliseconds to wait)
    or a function (wait until it returns true, at most 30 s). Returns the errors, empty when all passed.
    """
    errors, gen = [], steps()

    def stop(message=None):
        if message:
            errors.append(message)
        window.closing.set()
        if window.engine:
            window.engine.stop()
        window.destroy()

    def advance():
        try:
            item = next(gen)
        except StopIteration:
            return stop()
        except Exception:
            return stop(traceback.format_exc())
        if not callable(item):
            return window.after(item, advance)
        deadline = time.time() + 30

        def poll():
            try:
                done = item()
            except Exception:
                return stop(traceback.format_exc())
            if done:
                window.after(10, advance)
            elif time.time() > deadline:
                code = getattr(item, "__code__", None)
                where = f"line {code.co_firstlineno} of {Path(code.co_filename).name}" if code else repr(item)
                stop(f"timed out waiting for the condition at {where}")
            else:
                window.after(200, poll)
        poll()

    window.after(600, advance)
    window.mainloop()
    return errors


def drag(window, c1, r1, c2, r2):
    """Drag over cells in the range picker, as a user would with the mouse."""
    g = window.grid_view
    g.see(c1, r1)
    window.update()
    canvas = g.canvas

    def at(col, row):
        return int(g.rw + g.colx[col] + 6 - canvas.canvasx(0)), int(g.rh * (row + 1) + 6 - canvas.canvasy(0))
    (x1, y1), (x2, y2) = at(c1, r1), at(c2, r2)
    canvas.event_generate("<Button-1>", x=x1, y=y1)
    canvas.event_generate("<B1-Motion>", x=(x1 + x2) // 2, y=(y1 + y2) // 2)
    canvas.event_generate("<B1-Motion>", x=x2, y=y2)
    canvas.event_generate("<ButtonRelease-1>", x=x2, y=y2)
    window.update()


class WindowTest(unittest.TestCase):
    def setUp(self):
        logging.getLogger("cncbridge").setLevel(logging.INFO)
        self.tmp = Path(tempfile.mkdtemp())
        self.machine, self.backup, self.shared = (self.tmp / n for n in ("machine", "backup", "shared"))
        self.machine.mkdir()
        self.dialogs = []
        for name in ("showinfo", "showerror", "showwarning"):
            self._patch(messagebox, name, lambda *a, _n=name, **k: self.dialogs.append((_n, a)))
        self._patch(messagebox, "askokcancel", lambda *a, **k: True)
        self.notified = []  # Windows notifications are recorded, not shown
        self._patch(alerts, "SENDERS", dict(alerts.SENDERS, notify=lambda *a, **k: self.notified.append(a)))
        self.window = self.make_window()

    def make_window(self):
        return app.App(self.tmp / "data")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.window = None
        gc.collect()

    def _patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def run_steps(self, steps):
        errors = drive(self.window, steps)
        self.assertEqual(errors, [], "\n".join(errors))

    def drop(self, name, serial):
        (self.machine / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def files(self, folder, pattern="*"):
        return sorted(p for p in Path(folder).glob(pattern) if p.is_file())


class RangePicker(WindowTest):
    def test_drag_assign_and_preview(self):
        w = self.window

        def steps():
            self.assertTrue(w.open_sample(SAMPLE))
            w.tabs.select(w.pick_tab)
            w._update_slots()
            yield 300
            self.assertEqual((w.sample_name, len(w.sample_rows)), (SAMPLE.name, 50))
            w.slot_box.current(0)
            w._slot_chosen()
            drag(w, 0, 11, 8, 23)
            self.assertEqual(w.pick_range.get(), "A12:I24")
            w.pick_open.set(True)
            w._update_pick_range()
            self.assertEqual(w.pick_range.get(), "A12:I*")
            w.pick_name.set("Placements")
            w.assign()
            w.slot_box.current(1)
            w._slot_chosen()
            w.pick_open.set(False)
            drag(w, 1, 5, 1, 5)
            self.assertEqual(w.pick_range.get(), "B6")
            w.pick_name.set("Board serial")
            w.assign()
            yield 300
            preview = w.preview.get("1.0", "end")
            self.assertIn('"Board serial"', preview)
            self.assertIn('"value": "B240001"', preview)
            self.assertEqual([r.get() for _, r, _ in w.sel_vars[:2]], ["A12:I*", "B6"])
        self.run_steps(steps)


class SetupLock(WindowTest):
    """A setup password hides the setup tabs; operators keep Start/Stop, History, Log and the typed values."""

    def state(self, tab):
        return str(self.window.tabs.tab(tab, "state"))

    def visible(self):
        w = self.window
        return [self.state(t) for t in (w.setup_tab, w.delivery_tab, w.pick_tab, w.operator_tab)]

    def answers(self, *typed):
        queue = list(typed)
        self._patch(app.simpledialog, "askstring", lambda *a, **k: queue.pop(0))

    def test_set_lock_unlock_and_remove(self):
        w = self.window

        def steps():
            self.assertEqual(self.visible(), ["normal", "normal", "normal", "hidden"])  # no password: as before
            self.assertNotIn(w.lock_btn.winfo_manager(), ("pack", "grid"))

            self.answers("abc")  # too short
            w.set_password()
            self.answers("secret1", "different")  # not the same twice
            w.set_password()
            self.assertFalse(w.protected)
            self.assertEqual(len(self.dialogs), 2)

            self.answers("secret1", "secret1")
            w.set_password()
            self.assertTrue(w.protected and w.unlocked)
            self.assertEqual(self.visible(), ["normal", "normal", "normal", "hidden"])  # the setter stays in setup
            text = (self.tmp / "data" / "config.json").read_text(encoding="utf-8")
            self.assertIn("pbkdf2$", text)
            self.assertNotIn("secret1", text)

            w.toggle_lock()  # lock
            self.assertEqual(self.visible(), ["hidden", "hidden", "hidden", "normal"])
            self.assertEqual(w.tabs.select(), str(w.operator_tab))
            self.assertEqual([str(b["state"]) for b in (w.save_btn, w.open_csv_btn, w.settings_btn)], ["disabled"] * 3)
            self.assertEqual(w.lock_btn["text"], "Unlock setup...")
            w.update()  # the run controls stay usable: monitoring is stopped, so Start and Process now are on
            self.assertEqual([str(b["state"]) for b in (w.start_btn, w.once_btn, w.stop_btn)],
                             ["normal", "normal", "disabled"])
            self.assertEqual(w.password_state.get(), "Password is set")

            self.dialogs.clear()
            self.answers("wrong")
            w.toggle_lock()
            self.assertEqual((self.dialogs[0][0], self.visible()), ("showerror", ["hidden", "hidden", "hidden", "normal"]))
            self.answers(None)  # cancelled
            w.toggle_lock()
            self.assertEqual(self.visible(), ["hidden", "hidden", "hidden", "normal"])
            self.answers("secret1")
            w.toggle_lock()
            self.assertEqual(self.visible(), ["normal", "normal", "normal", "hidden"])
            self.assertEqual(w.lock_btn["text"], "Lock setup")
            self.assertEqual(str(w.save_btn["state"]), "normal")

            self.dialogs.clear()
            self.answers("wrong")
            w.remove_password()
            self.assertTrue(w.protected)
            self.answers("secret1")
            w.remove_password()
            self.assertFalse(w.protected)
            self.assertEqual(self.visible(), ["normal", "normal", "normal", "hidden"])
            self.assertEqual(w.lock_btn.winfo_manager(), "")
            self.assertEqual(app.load_config(self.tmp / "data" / "config.json")["setup_password"], "")
            yield 100
        self.run_steps(steps)

    def test_a_restart_starts_locked(self):
        w = self.window

        def steps():
            w.v["station_name"].set("Line 7")
            self.assertTrue(w.save())
            self.answers("secret1", "secret1")
            w.set_password()
            yield 100
        self.run_steps(steps)
        second = app.App(self.tmp / "data")
        try:
            self.assertTrue(second.protected and not second.unlocked)
            self.assertEqual([str(second.tabs.tab(t, "state")) for t in (second.setup_tab, second.operator_tab)],
                             ["hidden", "normal"])
            self.assertEqual(second.tabs.select(), str(second.operator_tab))
            self.assertEqual(second.cfg["station_name"], "Line 7")  # the password did not cost the other settings
        finally:
            second.destroy()

    def test_operator_changes_the_typed_values_while_locked_and_running(self):
        w = self.window

        def operator_of(pattern):
            files = self.files(self.shared, pattern)
            return json.loads(files[0].read_text(encoding="utf-8"))["manual_inputs"] if files else None

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("B6")
            w.man_vars[0][0].set("Operator"), w.man_vars[0][1].set("OP-17")
            w.man_vars[1][0].set("Work order"), w.man_vars[1][1].set("WO-1")
            w.cfg["stable_seconds"] = 0.3
            self.assertTrue(w.save())
            self.answers("secret1", "secret1")
            w.set_password()
            w.toggle_lock()
            self.assertEqual([lbl["text"] for lbl in w.operator_labels], ["Operator", "Work order"])
            w.start()
            yield lambda: w.running
            self.drop("b1.csv", "S1")
            yield lambda: operator_of("b1_*.json") == {"Operator": "OP-17", "Work order": "WO-1"}

            w.man_vars[0][1].set("OP-99")  # typed on the Operator tab
            w.man_vars[1][1].set("WO-2")
            w.apply_operator_values()
            self.assertIn("Typed values applied", w.status_msg.get())
            self.assertEqual(str(w.tabs.tab(w.setup_tab, "state")), "hidden")  # still locked after applying
            self.drop("b2.csv", "S2")
            yield lambda: operator_of("b2_*.json") == {"Operator": "OP-99", "Work order": "WO-2"}
            saved = app.load_config(self.tmp / "data" / "config.json")
            self.assertEqual([m["value"] for m in saved["manual_inputs"]], ["OP-99", "WO-2"])
            self.assertTrue(saved["setup_password"].startswith("pbkdf2$"))  # saving kept the password
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class ShiftChange(WindowTest):
    """At each shift start the operator is asked to confirm the typed values; processing never waits."""

    def saved_values(self):
        saved = app.load_config(self.tmp / "data" / "config.json")
        return [m["value"] for m in saved["manual_inputs"]]

    def fill_form(self, w):
        w.man_vars[0][0].set("Operator"), w.man_vars[0][1].set("OP-17")
        w.man_vars[1][0].set("Work order"), w.man_vars[1][1].set("WO-1")
        w.v["shift_times"].set("06:00, 14:00, 22:00")

    def test_ask_once_later_click_confirm_and_next_shift(self):
        w = self.window
        clock = [datetime(2026, 10, 19, 13, 0)]
        w._now = lambda: clock[0]
        data = self.tmp / "data"

        def steps():
            self.fill_form(w)
            self.assertTrue(w.save())
            w._check_shift()
            self.assertEqual((w.shift_banner_shown, w.shift_dialog), (False, None))  # nothing is overdue yet

            clock[0] = datetime(2026, 10, 19, 14, 5)
            w._check_shift()
            self.assertTrue(w.shift_banner_shown)
            self.assertIn("14:00", w.shift_banner["text"])
            self.assertEqual([v.get() for v in w.shift_dialog.entries], ["OP-17", "WO-1"])

            w.shift_dialog.later()  # not now: the banner stays, and the operator is not asked again at once
            yield 100
            first_dialog = w.shift_dialog
            w._check_shift()
            self.assertTrue(w.shift_banner_shown)
            self.assertIs(w.shift_dialog, first_dialog)
            self.assertFalse(first_dialog.winfo_exists())

            w.shift_banner.event_generate("<Button-1>")  # a click on the banner opens it again
            w.update()
            self.assertTrue(w.shift_dialog.winfo_exists())
            w.shift_dialog.entries[0].set("OP-99")
            w.shift_dialog.confirm()
            yield 100
            self.assertFalse(w.shift_banner_shown)
            self.assertEqual(self.saved_values(), ["OP-99", "WO-1"])
            self.assertEqual(shifts.read_confirmed(data), datetime(2026, 10, 19, 14, 5))
            w._check_shift()
            self.assertFalse(w.shift_banner_shown)

            clock[0] = datetime(2026, 10, 19, 22, 0, 30)  # the next shift
            w._check_shift()
            self.assertTrue(w.shift_banner_shown and "22:00" in w.shift_banner["text"])
            w.shift_dialog.confirm()  # unchanged values: nothing to save
            yield 100
            self.assertFalse(w.shift_banner_shown)
            self.assertEqual(self.saved_values(), ["OP-99", "WO-1"])
            self.assertEqual(shifts.read_confirmed(data), datetime(2026, 10, 19, 22, 0, 30))
        self.run_steps(steps)

    def test_an_unanswered_shift_is_remembered_across_a_restart(self):
        w = self.window

        def steps():
            self.fill_form(w)
            self.assertTrue(w.save())
            yield 100
        self.run_steps(steps)
        shifts.write_confirmed(self.tmp / "data", datetime(2026, 10, 19, 13, 0))
        second = app.App(self.tmp / "data")
        try:
            second._now = lambda: datetime(2026, 10, 19, 14, 30)
            second._check_shift()
            self.assertTrue(second.shift_banner_shown)
            self.assertIn("14:00", second.shift_banner["text"])
            second.shift_dialog.later()
        finally:
            second.destroy()

    def test_bad_times_are_refused_and_changing_them_does_not_ask_for_the_past(self):
        w = self.window
        w._now = lambda: datetime(2026, 10, 19, 18, 0)

        def steps():
            self.fill_form(w)
            w.v["shift_times"].set("06:00, 25:00")
            self.assertFalse(w.save())
            self.assertIn("'25:00' is not a time", self.dialogs[-1][1][1])
            self.assertFalse((self.tmp / "data" / "config.json").exists())
            w.v["source_folder"].set(str(self.machine))
            self.assertIsNone(w._ready(quiet=True))  # starting is refused for the same reason
            w.v["shift_times"].set("06:00, 14:00")
            self.assertTrue(w.save())  # the 14:00 shift began before the times were set: nobody is asked
            self.assertFalse(w.shift_banner_shown)
            self.assertIsNone(w.shift_dialog)
            w.v["shift_times"].set("")  # switched off: never asks
            self.assertTrue(w.save())
            w._now = lambda: datetime(2026, 10, 20, 7, 0)
            w._check_shift()
            self.assertFalse(w.shift_banner_shown)
            yield 100
        self.run_steps(steps)


class MarkerInWindow(WindowTest):
    def test_file_waits_for_the_marker_and_the_banner_explains(self):
        w = self.window

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A1:B2")
            w.v["complete_marker"].set("End_Time")
            w.v["alert_after_minutes"].set("0")
            w.cfg["stable_seconds"] = 0.3
            self.assertTrue(w.save())
            w.start()
            yield lambda: w.running
            board = self.machine / "board.csv"
            board.write_text("Key,Value\nMachine,PNP-M8\n")
            yield lambda: w.alert_banner_shown and "does not contain 'End_Time'" in w.alert_banner["text"]
            self.assertTrue(board.exists())
            self.assertEqual(self.files(self.shared), [])
            with open(board, "a") as f:
                f.write("End_Time,2026-10-19 10:00:11" + chr(10))
            yield lambda: self.files(self.shared, "board_*.json") and not board.exists()
            yield lambda: not w.alert_banner_shown
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class OutputOptions(WindowTest):
    def test_switches_are_saved_and_shown_in_the_preview(self):
        w = self.window

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            self.assertTrue(w.open_sample(SAMPLE))
            w.tabs.select(w.pick_tab)
            yield 300
            self.assertIn('"X_mm": "40.53"', w.preview.get("1.0", "end"))  # text, as before

            w.b["json_numbers"].set(True)
            w.rename_text.insert("1.0", "Ref=Reference")
            w.refresh_preview()
            preview = w.preview.get("1.0", "end")
            self.assertIn('"X_mm": 40.53', preview)
            self.assertIn('"Reference"', preview)

            w.b["time_zone"].set(True)
            w.v["output_format"].set("csv")
            w.b["csv_flat"].set(True)
            w.refresh_preview()
            lines = w.preview.get("1.0", "end").splitlines()
            self.assertEqual(lines[0].split(",")[:3], ["source_file", "processed_at", "station"])
            self.assertRegex(lines[1], r"[+-]\d\d:\d\d,")

            self.assertTrue(w.save())
            saved = app.load_config(self.tmp / "data" / "config.json")
            self.assertEqual([saved[k] for k in ("json_numbers", "csv_flat", "time_zone", "rename_columns")],
                             [True, True, True, "Ref=Reference"])
            w.rename_text.insert("end", chr(10) + "no equals sign here")
            self.assertEqual(engine.validate(w.collect())[-1].split(":")[0], "Rename columns")
            yield 100
        self.run_steps(steps)


class HeadingPicker(WindowTest):
    def test_find_by_heading_in_the_picker(self):
        w = self.window

        def steps():
            self.assertTrue(w.open_sample(SAMPLE))
            w.tabs.select(w.pick_tab)
            w._update_slots()
            yield 300
            w.slot_box.current(0)
            w._slot_chosen()
            w.pick_heading.set(True)
            drag(w, 0, 47, 0, 47)  # the [ERRORS] title cell
            self.assertEqual(w.pick_range.get(), "@[ERRORS]")
            self.assertIn("found again by this text", w.pick_hint.get())
            w.pick_name.set("Errors")
            w.assign()
            self.assertEqual(w.sel_vars[0][1].get(), "@[ERRORS]")
            self.assertEqual(w.pick_hint.get(), "")
            yield 300
            self.assertIn('"cells": "A49:D50"', w.preview.get("1.0", "end"))
            self.assertIn('"Code"', w.preview.get("1.0", "end"))

            w.slot_box.current(1)
            w._slot_chosen()
            drag(w, 0, 11, 0, 11)  # Timestamp occurs in two header rows
            self.assertEqual(w.pick_range.get(), "@Timestamp")
            self.assertIn("appears 2 times", w.pick_hint.get())
            drag(w, 5, 30, 5, 30)  # an empty cell has no heading to find
            self.assertIn("is empty", w.pick_hint.get())
            w.pick_heading.set(False)
            w._update_pick_range()
            self.assertEqual(w.pick_range.get(), "F31")
            self.assertEqual(w.pick_hint.get(), "")

            cfg = w.collect()  # a heading range survives saving the settings
            self.assertEqual(cfg["selections"][0]["range"], "@[ERRORS]")
            self.assertTrue(w.save())
        self.run_steps(steps)


class Monitoring(WindowTest):
    def test_files_flow_and_faults_are_handled(self):
        w, api = self.window, Receiver()
        self.addCleanup(api.close)
        data = self.tmp / "data"

        def queued_api():
            return engine.count_queued(data)["api"]

        def steps():
            w.v["station_name"].set("SMT Line 1")
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.b["api_enabled"].set(True)
            w.v["api_url"].set(api.url)
            for i, (name, rng) in enumerate((("Placements", "A12:I*"), ("Board serial", "B6"))):
                w.sel_vars[i][0].set(name), w.sel_vars[i][1].set(rng)
            w.man_vars[0][0].set("Operator"), w.man_vars[0][1].set("OP-17")
            w.cfg["stable_seconds"] = 0.3  # no field in the window; keeps the test fast
            w.start()
            yield lambda: w.running

            self.drop("b1.csv", "S1")
            self.drop("b2.csv", "S2")
            yield lambda: len(self.files(self.shared, "*.json")) == 2 and len(api.got) == 2
            self.assertEqual(self.files(self.machine), [])
            self.assertEqual(len(self.files(self.backup / f"{datetime.now():%Y-%m-%d}")), 2)
            doc = json.loads(self.files(self.shared, "b1_*.json")[0].read_text(encoding="utf-8"))
            self.assertEqual(doc["manual_inputs"], {"Operator": "OP-17"})
            self.assertEqual(doc["selections"][1]["value"], "S1")

            api.status = 503  # the customer's system goes down
            self.drop("b3.csv", "S3")
            yield lambda: len(self.files(self.shared, "*.json")) == 3 and queued_api() == 1
            api.status = 200
            yield lambda: queued_api() == 0 and len(api.got) == 3

            self.drop("b1_again.csv", "S1")  # same content as b1
            yield lambda: (self.backup / "_duplicates" / "b1_again.csv").exists()
            (self.machine / "junk.csv").write_text("only,one,line\n")
            yield lambda: (self.backup / "_failed" / "junk.csv").exists()

            w.man_vars[0][1].set("OP-99")  # changed while running
            self.assertTrue(w.save())
            self.drop("b4.csv", "S4")
            yield lambda: self.files(self.shared, "b4_*.json")
            doc = json.loads(self.files(self.shared, "b4_*.json")[0].read_text(encoding="utf-8"))
            self.assertEqual(doc["manual_inputs"], {"Operator": "OP-99"})

            yield lambda: w.snapshot and w.snapshot["ok"] == 4 and w.snapshot["failed"] == 1
            yield lambda: len(w.history.get_children()) == 6
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class AlertsInWindow(WindowTest):
    def test_banner_follows_the_problem_and_test_alert_reports(self):
        w, api = self.window, Receiver()
        self.addCleanup(api.close)

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["api_enabled"].set(True)
            w.v["api_url"].set(api.url)
            w.sel_vars[0][1].set("A12:I*")
            w.v["alert_after_minutes"].set("0")
            w.cfg["stable_seconds"] = 0.3
            api.status = 503
            w.start()
            self.drop("b1.csv", "S1")
            yield lambda: w.alert_banner_shown and "api delivery not possible" in w.alert_banner["text"]
            yield lambda: any("needs attention" in title for title, *_ in self.notified)
            api.status = 200
            yield lambda: not w.alert_banner_shown
            yield lambda: any("working again" in title for title, *_ in self.notified)
            w.stop()
            yield lambda: not w.running

            self.dialogs.clear()
            w.test_alert()
            yield lambda: self.dialogs
            self.assertEqual(self.dialogs[0][1][1], "Windows notification: sent")
        self.run_steps(steps)


class ConnectedToBackground(WindowTest):
    """A real background process (CNCBridge --headless) and a window connected to it."""

    def make_window(self):
        import subprocess
        from cncbridge import background, config
        data = self.tmp / "data"
        config.save_config({
            "source_folder": str(self.machine), "backup_folder": str(self.backup), "shared_enabled": True,
            "shared_folder": str(self.shared), "selections": [{"name": "Board serial", "range": "B6"}],
            "manual_inputs": [{"name": "Operator", "value": "OP-17"}], "poll_seconds": 1, "stable_seconds": 0.3,
            "alert_notify": False}, data / "config.json")
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "CNCBridge.pyw"), "--headless", "--data", str(data)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        self.addCleanup(lambda: self.process.poll() is None and self.process.kill())
        deadline = time.time() + 20
        while background.holder(data) != "background" and time.time() < deadline:
            time.sleep(0.2)
        self.assertEqual(background.holder(data), "background")
        return app.App(data, connected=True)

    def operator_of(self, pattern):
        files = self.files(self.shared, pattern)
        return json.loads(files[0].read_text(encoding="utf-8"))["manual_inputs"]["Operator"] if files else None

    def test_window_shows_and_controls_the_background(self):
        w = self.window

        def steps():
            yield lambda: w.bg_running
            yield lambda: w.state_label["text"] == "In background"
            self.assertEqual(str(w.once_btn["state"]), "disabled")
            self.drop("b1.csv", "S1")
            yield lambda: self.operator_of("b1_*.json") == "OP-17"
            yield lambda: len(w.history.get_children()) == 1
            yield lambda: "[background]" in w.log_text.get("1.0", "end")

            w.man_vars[0][1].set("OP-99")  # saved by the window, picked up by the background
            self.assertTrue(w.save())
            yield 1500
            self.drop("b2.csv", "S2")
            yield lambda: self.operator_of("b2_*.json") == "OP-99"

            w.stop()
            yield lambda: self.process.poll() is not None
            yield lambda: not w.bg_running and w.state_label["text"] == "Background off"

            w.start()  # no background task is set up, so this window takes over
            yield lambda: w.running and not w.connected
            self.drop("b3.csv", "S3")
            yield lambda: self.operator_of("b3_*.json") == "OP-99"
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class HistoryActions(WindowTest):
    def test_send_again_and_process_again(self):
        w, api = self.window, Receiver()
        self.addCleanup(api.close)

        def row_id(source, status):
            return next(str(r["id"]) for r in w.shown_history if r["source"] == source and r["status"] == status)

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["api_enabled"].set(True)
            w.v["api_url"].set(api.url)
            w.sel_vars[0][1].set("B6")
            w.v["alert_after_minutes"].set("60")
            w.cfg["stable_seconds"] = 0.3
            api.status = 422
            w.start()
            self.drop("b1.csv", "S1")
            (self.machine / "junk.csv").write_text("only,one,line\n")
            yield lambda: w.snapshot and w.snapshot["rejected"] == 1 and w.snapshot["failed"] == 1
            yield lambda: len(w.history.get_children()) == 2

            w.history.selection_set([row_id("b1.csv", "ok")])
            w.update()
            self.assertEqual((str(w.resend_btn["state"]), str(w.reprocess_btn["state"])), ("normal", "disabled"))
            api.status = 200
            w.resend_selected()
            yield lambda: len(api.got) == 1 and w.snapshot["rejected"] == 0

            w.history.selection_set([row_id("junk.csv", "failed")])
            w.update()
            self.assertEqual((str(w.resend_btn["state"]), str(w.reprocess_btn["state"])), ("disabled", "normal"))
            w.reprocess_selected()
            self.assertIn("put back in the machine folder", w.status_msg.get())
            yield lambda: w.snapshot["failed"] == 2  # still not a board log, so it is set aside again
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class SettingsFiles(WindowTest):
    def test_export_import_and_restore(self):
        w = self.window
        exported = self.tmp / "exported.json"

        def steps():
            w.v["station_name"].set("Line 7")
            w.v["api_token"].set("secret-token")
            w.sel_vars[0][1].set("L1:M8")
            w.save()
            self.assertEqual(len(self.files(self.tmp / "data" / "backups", "config-*.json")), 1)
            self._patch(filedialog, "asksaveasfilename", lambda **k: str(exported))
            w.export_settings()
            self.assertNotIn("secret-token", exported.read_text(encoding="utf-8"))
            w.v["station_name"].set("CHANGED")
            w.sel_vars[0][1].set("")
            self._patch(filedialog, "askopenfilename", lambda **k: str(exported))
            w.import_settings()
            self.assertEqual((w.v["station_name"].get(), w.sel_vars[0][1].get(), w.v["api_token"].get()),
                             ("Line 7", "L1:M8", "secret-token"))
            self.assertIn("press Save settings", w.status_msg.get())
            yield 100
        self.run_steps(steps)


class Appearance(WindowTest):
    def catch_restarts(self, launched, fail=None):
        """Record the command that restarts the program instead of starting it; other programs run as usual."""
        real = app.subprocess.Popen

        def popen(command, *a, **k):
            if "--restarted" not in command:
                return real(command, *a, **k)
            if fail:
                raise fail
            launched.append((command, k))
        self._patch(app.subprocess, "Popen", popen)

    def test_choose_a_theme_save_and_restart(self):
        w = self.window
        launched = []
        self.catch_restarts(launched)

        def steps():
            self.assertEqual(w.theme.key, "modern")
            for key in app.theme.THEMES:  # one radio button per theme, found by the registry
                self.assertIn(f"theme_{key}", w.field_widgets)
            self.assertEqual(str(w.restart_btn.cget("state")), "disabled")
            self.assertIn("in use", w.theme_note.get())

            w.v["theme"].set("legacy")
            w._update_theme_note()
            self.assertEqual(str(w.restart_btn.cget("state")), "normal")
            self.assertIn("Save settings", w.theme_note.get())

            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            w.restart()  # saves first, then starts the program again and closes this window
            yield 100
        self.run_steps(steps)
        saved = app.load_config(self.tmp / "data" / "config.json")
        self.assertEqual(saved["theme"], "legacy")
        self.assertEqual(len(launched), 1)
        command = launched[0][0]
        self.assertIn("--restarted", command)
        self.assertNotIn("--resume", command)  # it was not monitoring
        self.assertEqual(command[command.index("--data") + 1], str(self.tmp / "data"))
        self.assertEqual(launched[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_a_restart_while_monitoring_resumes_monitoring(self):
        w = self.window
        launched = []
        self.catch_restarts(launched)

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            w.start()
            yield lambda: w.running
            w.restart()
            self.assertFalse(w.running)  # the engine stopped before the new window takes over
            yield 100
        self.run_steps(steps)
        self.assertIn("--resume", launched[0][0])

    def test_a_failed_restart_keeps_the_window_open(self):
        w = self.window

        self.catch_restarts([], fail=OSError("no such program"))

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            w.restart()
            self.assertTrue(w.winfo_exists())
            self.assertEqual(self.dialogs[-1][0], "showerror")
            yield 100
        self.run_steps(steps)


class LegacyWindow(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"theme": "legacy"}), encoding="utf-8")
        return app.App(data)

    def test_the_window_opens_with_the_legacy_look(self):
        w = self.window

        def steps():
            self.assertEqual(w.theme.key, "legacy")
            self.assertEqual(w.v["theme"].get(), "legacy")
            self.assertEqual(app.theme.BACKGROUND, app.theme.LEGACY.colours["BACKGROUND"])
            self.assertNotEqual(app.ttk.Style(w).theme_use(), "clam")
            self.assertEqual(str(w.restart_btn.cget("state")), "disabled")  # already in use
            for tab in (w.tabs.tabs()):  # every tab builds and shows in this theme
                w.tabs.select(tab)
                w.update()
            yield 100
        self.run_steps(steps)


def page_titles(widget):
    """The page-title labels (one at the top of each tab in themes that have them), found anywhere in the window."""
    found = [widget] if str(widget.winfo_class()) == "TLabel" and widget.cget("style") == "PageTitle.TLabel" else []
    for child in widget.winfo_children():
        found += page_titles(child)
    return found


class LegacyLayout(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"theme": "legacy"}), encoding="utf-8")
        return app.App(data)

    def test_version_1_layout(self):
        w = self.window

        def steps():
            self.assertEqual(page_titles(w), [])  # version 1.0 had no page titles
            self.assertEqual(w.counts["ok"].cget("style"), "Count.TLabel")  # a bare number, not a card
            self.assertEqual(str(w.field_widgets["selection_header_0"].cget("text")), "")
            self.assertEqual(w.code_font, {})  # short codes in the normal font
            self.assertEqual(w.grid_view.font.cget("size"), 10)  # Consolas 10, as in version 1.0
            self.assertEqual(w.L.window, (1180, 740, 980, 620))
            yield 100
        self.run_steps(steps)


class ModernLayout(WindowTest):
    def test_titles_cards_and_scrollbar(self):
        w = self.window

        def steps():
            self.assertEqual(len(page_titles(w)), 6)  # Setup, Delivery, Typed values, Pick, History, Log
            self.assertEqual(w.counts["ok"].cget("style"), "MetricValue.TLabel")
            self.assertEqual(str(w.field_widgets["selection_header_0"].cget("text")), "Yes")
            self.assertIn("font", w.code_font)
            # the counters really are larger than the captions: a style font is not overridden by the defaults
            self.assertGreater(w.counts["ok"].winfo_reqheight(), 30)
            # the scrollbar shows only while the tabs are taller than the window
            real = w.tabs_frame.winfo_reqheight
            w.tabs_frame.winfo_reqheight = lambda: 50
            w._tabs_content_changed()
            w.update()
            self.assertFalse(w.tabs_scrollbar.winfo_ismapped())
            w.tabs_frame.winfo_reqheight = real
            w._tabs_content_changed()
            w.update()
            self.assertTrue(w.tabs_scrollbar.winfo_ismapped())
            # and the tabs are never wider than the window, however wide a tab wants to be
            self.assertEqual(w.tabs_frame.winfo_width(), w.tabs_canvas.winfo_width())
            yield 100
        self.run_steps(steps)


class UnknownTheme(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"theme": "from-the-future"}), encoding="utf-8")
        return app.App(data)

    def test_falls_back_to_the_default_theme(self):
        def steps():
            self.assertEqual(self.window.theme.key, "modern")
            yield 100
        self.run_steps(steps)


class Notice(unittest.TestCase):
    def test_one_time_message_is_shown(self):
        shown = []
        original = messagebox.showinfo
        messagebox.showinfo = lambda title, text, **k: shown.append(text)
        tmp = Path(tempfile.mkdtemp())
        try:
            window = app.App(tmp / "data", notice="Your settings were moved.")
            errors = drive(window, lambda: iter([1000]))
            window = None
        finally:
            messagebox.showinfo = original
            shutil.rmtree(tmp, ignore_errors=True)
            gc.collect()
        self.assertEqual(errors, [])
        self.assertEqual(shown, ["Your settings were moved."])


if __name__ == "__main__":
    unittest.main()
