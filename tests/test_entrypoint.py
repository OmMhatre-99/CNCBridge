"""Real CLI subprocesses and instance locking, independent of the GUI."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from cncbridge.__main__ import acquire_lock
from cncbridge.engine import Ledger

ROOT = Path(__file__).resolve().parents[1]


class EntryPoint(unittest.TestCase):
    def invoke(self, *args, trace=False):
        prefix = [sys.executable, "-m", "cncbridge"]
        if trace:
            prefix = [sys.executable, "-c", "import faulthandler, runpy; faulthandler.dump_traceback_later(12, repeat=True); runpy.run_module('cncbridge', run_name='__main__')"]
        try:
            return subprocess.run([*prefix, *map(str, args)], cwd=ROOT,
                                  capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as error:
            self.fail(f"CLI exceeded 30 seconds. stdout: {error.stdout!r}\nstderr and thread traces: {error.stderr!r}")

    def test_lock_blocks_second_instance_and_is_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = acquire_lock(temporary)
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(acquire_lock(temporary))
                result = self.invoke("--headless", "--once", "--data", temporary)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("already running", result.stderr)
            finally:
                first.release()
            final = acquire_lock(temporary)
            self.assertIsNotNone(final)
            final.release()

    def test_incomplete_profile_is_a_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.invoke("--headless", "--once", "--data", temporary)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("Monitoring cannot start", result.stderr)

    def test_malformed_profile_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.json"
            config.write_text("{broken-json")
            result = self.invoke("--headless", "--once", "--data", temporary)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(config.read_text(), "{broken-json")

    def test_once_requires_headless(self):
        result = self.invoke("--once")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("requires --headless", result.stderr)

    def test_cli_demo_once_delivers_real_http_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.invoke("--demo", "--headless", "--once", "--data", root, trace=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            record = Ledger(root / "data/ledger.db").recent()[0]
            self.assertEqual(record["status"], "ok")
            self.assertEqual(record["shared"], "ok")
            self.assertEqual(record["api"], "ok")
            shared = root / "shared" / record["output"]
            receipts = list((root / "mes_received").iterdir())
            self.assertEqual(len(receipts), 1)
            self.assertEqual(shared.read_bytes(), receipts[0].read_bytes())
            self.assertTrue(json.loads(shared.read_text())["manual_inputs"])
            self.assertEqual(len(list((root / "backup").glob("*/*.csv"))), 1)
            self.assertEqual(list((root / "machine").glob("*.csv")), [])
