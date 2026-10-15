"""Check the desktop preflight and demo verification independently of network availability."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cncbridge import engine
from cncbridge.checks import run_checks
from cncbridge.config import normalize_config
from demo import testdata
from demo.verify import run_verification


class Preflight(unittest.TestCase):
    def test_configured_routes_and_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary)
            (ws / "machine").mkdir()
            cfg = normalize_config(testdata.demo_settings(ws, ""))
            checks = run_checks(cfg, ws / "data", engine.read_rows(testdata.CASES[0].data), "sample.csv")
            self.assertNotIn("FAIL", [c["status"] for c in checks])
            self.assertEqual(next(c["status"] for c in checks if c["name"] == "Extraction preview"), "PASS")
            self.assertEqual(next(c["status"] for c in checks if c["name"] == "API connection"), "DISABLED")
            self.assertFalse(list((ws / "shared").iterdir()), "Checks must not create a production payload")

    def test_untested_api_is_explicit_and_does_not_send(self):
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary)
            (ws / "machine").mkdir()
            cfg = normalize_config(testdata.demo_settings(ws, "https://example.invalid/upload"))
            with patch.object(engine, "test_api") as send:
                checks = run_checks(cfg, ws / "data")
            send.assert_not_called()
            self.assertEqual(next(c["status"] for c in checks if c["name"] == "API connection"), "NOT TESTED")
            self.assertEqual(next(c["status"] for c in checks if c["name"] == "Extraction preview"), "NOT TESTED")

    def test_failed_api_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary)
            (ws / "machine").mkdir()
            cfg = normalize_config(testdata.demo_settings(ws, "http://127.0.0.1:1/api/upload"))
            with patch.object(engine, "test_api", side_effect=RuntimeError("HTTP 401")):
                checks = run_checks(cfg, ws / "data", check_api=True)
            self.assertEqual(next(c["status"] for c in checks if c["name"] == "API connection"), "FAIL")

    def test_invalid_settings_create_no_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary)
            checks = run_checks({}, ws / "unused")
            self.assertEqual(checks[0]["status"], "FAIL")
            self.assertFalse((ws / "unused").exists())


class Verification(unittest.TestCase):
    def test_complete_engine_workflow_with_explicit_transport_double(self):
        """Exercise the report's assertions with real disk/engine state and a simulated API."""
        receivers = []

        class MemoryMES:
            def __init__(self, store, port=0):
                self.store = Path(store)
                self.store.mkdir()
                self.url = "http://demo.test/api/upload"
                self.items = []
                self.online, self.refuse_next, self.unavailable = True, False, 0
                receivers.append(self)

            def start(self):
                return self

            def close(self):
                pass

        def simulated_send(file_path, cfg):
            receiver = receivers[-1]
            if not receiver.online:
                receiver.unavailable += 1
                raise RuntimeError("HTTP 503 (test transport)")
            if receiver.refuse_next:
                receiver.refuse_next = False
                raise engine.Rejected("HTTP 422 (test transport)")
            file_path = Path(file_path)
            target = receiver.store / f"{len(receiver.items)}_{file_path.name}"
            target.write_bytes(file_path.read_bytes())
            receiver.items.append({"name": file_path.name, "path": str(target)})

        with tempfile.TemporaryDirectory() as temporary:
            with patch("demo.verify.MockMES", MemoryMES), patch.dict(
                engine.DESTINATIONS, {"api": (simulated_send, lambda cfg: cfg["api_enabled"])}
            ):
                report = run_verification(temporary)
            self.assertTrue(report["ok"], report["checks"])
            self.assertEqual(len(report["checks"]), 9)
            self.assertTrue(all(check["ok"] for check in report["checks"]))
            saved = json.loads((Path(temporary) / "verification.json").read_text())
            self.assertEqual(saved, report)

    def test_no_false_pass_if_receiver_cannot_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("demo.verify.MockMES", side_effect=PermissionError("listeners forbidden")):
                report = run_verification(temporary)
            self.assertFalse(report["ok"])
            self.assertIn("listeners forbidden", report["checks"][-1]["detail"])

    def test_existing_workspace_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "keep.txt"
            evidence.write_text("keep")
            with self.assertRaisesRegex(ValueError, "empty"):
                run_verification(temporary)
            self.assertEqual(evidence.read_text(), "keep")
