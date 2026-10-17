"""
End-to-end tests that drive the real CNCBridge window. They open windows on the screen, so they only run
when asked:

    set CNCBRIDGE_WINDOW_TESTS=1
    python -m unittest tests.test_window -v
"""
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

from cncbridge import alerts, app, engine  # noqa: E402
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
                stop(f"timed out waiting for: {getattr(item, '__doc__', None) or item}")
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


class Notice(unittest.TestCase):
    def test_one_time_message_is_shown(self):
        shown = []
        original = messagebox.showinfo
        messagebox.showinfo = lambda title, text, **k: shown.append(text)
        tmp = Path(tempfile.mkdtemp())
        try:
            window = app.App(tmp / "data", notice="Your settings were moved.")
            errors = drive(window, lambda: iter([1000]))
        finally:
            messagebox.showinfo = original
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(errors, [])
        self.assertEqual(shown, ["Your settings were moved."])


if __name__ == "__main__":
    unittest.main()
