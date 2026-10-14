"""Regression checks for data preservation, malformed settings, and file IO."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cncbridge import config, engine, storage
from cncbridge.appearance import normalize_preferences
from demo.testdata import demo_settings, make_board


class Hardening(unittest.TestCase):
    def test_colliding_headers_keep_every_value(self):
        names = engine._header(["A", "A", "A_2", "", "col4"])
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(dict(zip(names, range(5)))), 5)

    def test_malformed_settings_and_nonfinite_intervals(self):
        cfg = config.normalize_config({"selections": None, "manual_inputs": 42,
            "api_enabled": "false", "autostart": "true", "poll_seconds": "inf",
            "stable_seconds": "nan"})
        self.assertEqual(len(cfg["selections"]), 10)
        self.assertEqual(len(cfg["manual_inputs"]), 2)
        self.assertFalse(cfg["api_enabled"])
        self.assertTrue(cfg["autostart"])
        self.assertEqual(cfg["poll_seconds"], 3)
        self.assertEqual(cfg["stable_seconds"], 2)
        with self.assertRaises(ValueError):
            config.normalize_config([])
        self.assertEqual(normalize_preferences({"theme": [], "text_size": {}}),
                         {"theme": "Light", "text_size": "Medium"})

    def test_failed_publication_preserves_previous_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "payload.json"
            target.write_bytes(b"original")
            with patch.object(storage.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    storage.atomic_write(target, b"replacement")
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_secrets_saved_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            config.save_config({"api_token": "test-secret"}, target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.load_config(target)["api_token"], "test-secret")
            target.write_text(json.dumps({"api_token": "dpapi:AAAA"}))
            self.assertEqual(config.load_config(target)["api_token"], "")

    def test_reused_source_filename_does_not_overwrite_delivered_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.normalize_config(dict(demo_settings(tmp, ""), api_enabled=False, stable_seconds=0))
            machine = Path(cfg["source_folder"])
            machine.mkdir()
            worker = engine.Engine(cfg, Path(tmp) / "data")
            for number in (1, 2):
                (machine / "same.csv").write_text(make_board(number), encoding="utf-8")
                self.assertEqual(worker.scan_once(), 1)
            self.assertEqual(len(list(Path(cfg["shared_folder"]).glob("*.json"))), 2)
            self.assertEqual(worker.ledger.stats(), {"ok": 2})

    def test_changing_source_is_left_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.normalize_config(dict(demo_settings(tmp, ""), api_enabled=False, stable_seconds=0))
            machine = Path(cfg["source_folder"])
            machine.mkdir()
            src = machine / "changing.csv"
            src.write_text(make_board(1), encoding="utf-8")
            worker = engine.Engine(cfg, Path(tmp) / "data")
            original = engine.build_output
            def changing(*args, **kwargs):
                result = original(*args, **kwargs)
                src.write_text(make_board(2, placements=20), encoding="utf-8")
                return result
            with patch.object(engine, "build_output", side_effect=changing):
                self.assertEqual(worker.scan_once(), 0)
            self.assertTrue(src.exists())
            self.assertEqual(worker.ledger.stats(), {})
            self.assertEqual(sum(engine.count_queued(worker.data).values()), 0)

    def test_invalid_api_and_duplicate_manual_names_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.normalize_config(demo_settings(tmp, "http://user:secret@example.test:bad"))
            cfg["manual_inputs"] = [{"name": "operator", "value": "A"}, {"name": "operator", "value": "B"}]
            errors = engine.validate(cfg)
            self.assertTrue(any("API link" in error for error in errors))
            self.assertTrue(any("Manual input" in error for error in errors))

    def test_queued_files_cannot_be_rerouted_by_settings_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.normalize_config(dict(demo_settings(tmp, ""), api_enabled=False, stable_seconds=0))
            machine = Path(cfg["source_folder"])
            machine.mkdir()
            source = machine / "board.csv"
            source.write_text(make_board(1), encoding="utf-8")
            worker = engine.Engine(cfg, Path(tmp) / "data")
            worker.process_file(source)
            original = worker.cfg["shared_folder"]
            worker.cfg["shared_folder"] = str(Path(tmp) / "different-customer")
            worker.drain_outbox()
            self.assertFalse(Path(worker.cfg["shared_folder"]).exists())
            self.assertEqual(engine.count_queued(worker.data)["shared"], 1)
            worker.cfg["shared_folder"] = original
            worker.drain_outbox()
            self.assertEqual(engine.count_queued(worker.data)["shared"], 0)
            self.assertEqual(len(list(Path(original).glob("*.json"))), 1)
