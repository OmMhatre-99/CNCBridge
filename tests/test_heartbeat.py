"""The heartbeat: a regular sign of life, a last one when monitoring stops, and a report when it cannot be sent."""
import json
import logging
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import alerts, config, engine, heartbeat  # noqa: E402

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)


def settings(tmp, **values):
    base = {"station_name": "Line 7", "source_folder": str(tmp / "machine"), "backup_folder": str(tmp / "backup"),
            "stable_seconds": 0, "poll_seconds": 0.5, "alert_after_minutes": 0, "shared_enabled": True,
            "shared_folder": str(tmp / "shared"), "selections": [{"name": "", "range": "A1:B2", "header": True}] + [{}] * 9,
            "heartbeat_enabled": True, "heartbeat_minutes": 5}
    base.update(values)
    return config.normalize_config(base)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "machine").mkdir()
        self.posts, self.notified, self.web_error = [], [], None
        self.addCleanup(alerts.SENDERS.update, dict(alerts.SENDERS))
        alerts.SENDERS["notify"] = lambda *a, **k: self.notified.append(a)
        alerts.SENDERS["webhook"] = self.receive

    def receive(self, url, payload, timeout=15):
        if self.web_error:
            raise OSError(self.web_error)
        self.posts.append((url, payload))
        return 200

    def beat(self, eng, state=None):
        """One heartbeat, and wait for the thread that sends it."""
        eng._heartbeat(state)
        if eng._beat_thread:
            eng._beat_thread.join(5)


class Message(Base):
    def test_the_message_says_who_how_and_what(self):
        cfg = settings(self.tmp)
        ledger = engine.Ledger(self.tmp / "data" / "ledger.db")
        ledger.add(source="a.csv", output="a.json", status="ok", shared="ok")
        ledger.add(source="b.csv", status="failed")
        payload = heartbeat.snapshot(cfg, self.tmp / "data", ledger, "ok")
        json.dumps(payload)  # everything in it can be sent
        self.assertEqual((payload["app"], payload["kind"], payload["state"], payload["station"]),
                         ("CNCBridge", "heartbeat", "ok", "Line 7"))
        self.assertEqual(payload["every_minutes"], 5)
        self.assertEqual((payload["counts"]["processed"], payload["counts"]["failed"]), (1, 1))
        self.assertIn("last_file", payload["health"])
        self.assertIn("backup_free", payload["health"])
        self.assertEqual(payload["problems"], [])

    def test_the_file_name_is_safe(self):
        self.assertEqual(heartbeat.station_file_name({"station_name": "Line 7 / A:1"}), "Line_7_A_1.status.json")
        self.assertEqual(heartbeat.station_file_name({"station_name": ""}), "CNCBridge.status.json")


class Sending(Base):
    def test_the_message_goes_to_the_address_and_into_the_folder(self):
        cfg = settings(self.tmp, heartbeat_url="https://monitor.example/beat?key=SECRETKEY",
                       heartbeat_folder=str(self.tmp / "status"))
        ledger = engine.Ledger(self.tmp / "data" / "ledger.db")
        payload = heartbeat.snapshot(cfg, self.tmp / "data", ledger, "ok")
        went = heartbeat.send(cfg, payload)
        self.assertEqual(self.posts[0][0], "https://monitor.example/beat?key=SECRETKEY")
        self.assertEqual(json.loads((self.tmp / "status" / "Line_7.status.json").read_text(encoding="utf-8")), payload)
        self.assertFalse(any("SECRETKEY" in w for w in went))  # what is reported never shows the key
        self.assertEqual([p.name for p in (self.tmp / "status").iterdir()], ["Line_7.status.json"])  # no half-written file left

    def test_one_failing_destination_does_not_stop_the_other_and_is_named(self):
        self.web_error = "connection refused"
        cfg = settings(self.tmp, heartbeat_url="https://monitor.example/beat?key=SECRETKEY",
                       heartbeat_folder=str(self.tmp / "status"))
        ledger = engine.Ledger(self.tmp / "data" / "ledger.db")
        with self.assertRaises(OSError) as caught:
            heartbeat.send(cfg, heartbeat.snapshot(cfg, self.tmp / "data", ledger, "ok"))
        self.assertIn("connection refused", str(caught.exception))
        self.assertNotIn("SECRETKEY", str(caught.exception))
        self.assertTrue((self.tmp / "status" / "Line_7.status.json").exists())

    def test_a_test_needs_a_target(self):
        cfg = settings(self.tmp)
        with self.assertRaises(ValueError):
            heartbeat.send_test(cfg, self.tmp / "data", engine.Ledger(self.tmp / "data" / "ledger.db"))


class Settings(Base):
    def test_the_heartbeat_needs_somewhere_to_go(self):
        self.assertIn("The heartbeat is on: enter its web address or its folder (Options > Alerts).",
                      engine.validate(settings(self.tmp)))
        self.assertNotIn("The heartbeat is on: enter its web address or its folder (Options > Alerts).",
                         engine.validate(settings(self.tmp, heartbeat_folder=str(self.tmp / "status"))))
        self.assertIn("The heartbeat address must start with http:// or https://.",
                      engine.validate(settings(self.tmp, heartbeat_url="monitor.example/beat")))
        self.assertFalse([p for p in engine.validate(settings(self.tmp, heartbeat_enabled=False))
                          if "eartbeat" in p])

    def test_the_interval_has_a_floor(self):
        self.assertEqual(settings(self.tmp, heartbeat_minutes=0)["heartbeat_minutes"], 1)
        self.assertEqual(settings(self.tmp, heartbeat_minutes="soon")["heartbeat_minutes"], 5)


class InTheEngine(Base):
    def make(self, **values):
        return engine.Engine(settings(self.tmp, heartbeat_url="https://monitor.example/beat", **values),
                             self.tmp / "data")

    def test_a_beat_is_sent_when_due_and_not_again_before_the_interval(self):
        eng = self.make()
        self.beat(eng)
        self.assertEqual([p[1]["state"] for p in self.posts], ["ok"])
        self.beat(eng)
        self.assertEqual(len(self.posts), 1)
        eng._last_beat -= 5 * 60 + 1
        self.beat(eng)
        self.assertEqual(len(self.posts), 2)

    def test_nothing_is_sent_while_it_is_switched_off(self):
        eng = self.make(heartbeat_enabled=False)
        self.beat(eng)
        self.beat(eng, "stopped")
        self.assertEqual(self.posts, [])

    def test_an_open_alert_makes_it_a_problem_beat(self):
        eng = self.make()
        eng.alerts.problem("api", "API link cannot be reached", eng.cfg)
        self.beat(eng)
        payload = self.posts[0][1]
        self.assertEqual(payload["state"], "problem")
        self.assertEqual(payload["problems"][0]["subject"], "api")
        self.assertIn("API link cannot be reached", payload["problems"][0]["text"])

    def test_stopping_sends_a_last_beat_at_once(self):
        eng = self.make()
        self.beat(eng)
        self.beat(eng, "stopped")
        self.assertEqual([p[1]["state"] for p in self.posts], ["ok", "stopped"])

    def test_a_beat_that_cannot_be_sent_is_reported_then_cleared(self):
        eng = self.make()
        self.web_error = "connection refused"
        self.beat(eng)
        self.assertIsNotNone(eng._beat_error)
        eng._last_beat -= 5 * 60 + 1
        self.beat(eng)  # reports the failure of the one before (alert_after_minutes is 0)
        self.assertIn("heartbeat", alerts.read_state(self.tmp / "data"))
        self.assertIn("connection refused", alerts.read_state(self.tmp / "data")["heartbeat"]["text"])
        self.web_error = None
        eng._last_beat -= 5 * 60 + 1
        self.beat(eng)
        self.assertIsNone(eng._beat_error)
        eng._heartbeat()
        self.assertNotIn("heartbeat", alerts.read_state(self.tmp / "data"))

    def test_switching_it_off_clears_a_standing_failure(self):
        eng = self.make()
        self.web_error = "connection refused"
        self.beat(eng)
        self.beat(eng)
        self.assertIn("heartbeat", alerts.read_state(self.tmp / "data"))
        eng.cfg = settings(self.tmp, heartbeat_enabled=False)
        eng._heartbeat()
        self.assertNotIn("heartbeat", alerts.read_state(self.tmp / "data"))

    def test_while_monitoring_runs_it_beats_and_a_stop_says_so(self):
        eng = self.make()
        worker = threading.Thread(target=eng.run_forever, daemon=True)
        worker.start()
        deadline = time.time() + 10
        while not self.posts and time.time() < deadline:
            time.sleep(0.05)
        eng.stop()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.posts[0][1]["state"], "ok")
        self.assertEqual(self.posts[-1][1]["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
