"""Checks that the demo's test data, fixed test files, test script and stand-in MES agree with each other."""
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cncbridge import config, engine  # noqa: E402

try:
    from demo import testdata
    from demo.mock_mes import MockMES
except ImportError as error:  # branches without the demo/ folder
    raise unittest.SkipTest(f"the demo is not on this branch ({error})")

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)
START = datetime(2026, 10, 9, 10, 0, 0)


def blocks(data, **settings):
    """The four demo ranges cut out of one file, by name."""
    cfg = config.normalize_config(dict(testdata.demo_settings("x", "http://x"), **settings))
    return {b["name"]: b for b in engine.build_output("f.csv", engine.read_rows(data), cfg)[1]}


class Generator(unittest.TestCase):
    def test_same_arguments_same_file(self):
        self.assertEqual(testdata.make_board(7, start=START), testdata.make_board(7, start=START))
        self.assertNotEqual(testdata.make_board(7, start=START), testdata.make_board(8, start=START))

    def test_ranges_hold_for_every_table_length(self):
        for count in (1, 6, 12, 20):
            b = blocks(testdata.make_board(3, placements=count, fail=(0,), start=START).encode())
            self.assertEqual(b["Summary"]["columns"], ["Item", "Value"])
            summary = dict(b["Summary"]["rows"])
            self.assertEqual(len(summary), 7, count)
            self.assertEqual((summary["Placed"], summary["Errors"], summary["Result"]), (str(count - 1), "1", "FAIL"))
            self.assertEqual(b["Board serial"]["value"], "B240003")
            self.assertEqual(b["Program"]["value"], "ECU-MAIN-REV3")
            self.assertEqual(len(b["Placements"]["rows"]), count)
            self.assertEqual(b["Placements"]["columns"][0], "Timestamp")

    def test_random_boards_vary_in_length(self):
        lengths = {len(blocks(testdata.make_board(n, start=START).encode())["Placements"]["rows"]) for n in range(1, 30)}
        self.assertGreater(len(lengths), 5)
        self.assertTrue(lengths <= set(range(6, 21)))

    def test_files_on_disk_match_the_generator(self):
        for case in testdata.CASES:
            self.assertEqual((ROOT / "demo" / "test_files" / case.name).read_bytes(), case.data, case.name)
        self.assertEqual((ROOT / "demo" / "TEST_SCRIPT.md").read_text(encoding="utf-8"), testdata.test_script())

    def test_script_names_every_case(self):
        text = testdata.test_script()
        for case in testdata.CASES:
            self.assertIn(case.name, text)
            self.assertIn(case.expect, text)


class FixedCases(unittest.TestCase):
    """Every fixed test file must end up where the test script says."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cfg = dict(testdata.demo_settings(cls.tmp, ""), api_enabled=False, stable_seconds=0)
        (cls.tmp / "machine").mkdir()
        files = testdata.write_test_files(cls.tmp / "test_files")
        for i, case in enumerate(testdata.CASES):
            target = cls.tmp / "machine" / case.name
            shutil.copyfile(files / case.name, target)
            stamp = time.time() - 100 + i
            os.utime(target, (stamp, stamp))
        cls.engine = engine.Engine(cfg, cls.tmp / "data")
        cls.processed = cls.engine.scan_once()
        cls.rows = {r["source"]: r for r in cls.engine.ledger.recent()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def output(self, name):
        return json.loads((self.tmp / "shared" / self.rows[name]["output"]).read_text(encoding="utf-8"))

    def by_name(self, name):
        return {s["name"]: s for s in self.output(name)["selections"]}

    def test_every_case_has_its_expected_result(self):
        self.assertEqual({c.name: self.rows[c.name]["status"] for c in testdata.CASES},
                         {c.name: c.status for c in testdata.CASES})
        self.assertEqual(self.processed, sum(c.status == "ok" for c in testdata.CASES))
        self.assertEqual(list((self.tmp / "machine").iterdir()), [])
        self.assertTrue((self.tmp / "backup" / "_duplicates" / "07_copy_of_01.csv").exists())
        self.assertTrue((self.tmp / "backup" / "_failed" / "08_not_a_board_log.csv").exists())

    def test_01_normal(self):
        doc = self.output("01_normal_board.csv")
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-17", "Work order": "WO-1187"})
        b = self.by_name("01_normal_board.csv")
        self.assertEqual(b["Board serial"]["value"], "B240001")
        self.assertEqual(len(b["Placements"]["rows"]), 12)

    def test_02_long_and_04_single(self):
        self.assertEqual(len(self.by_name("02_long_board.csv")["Placements"]["rows"]), 20)
        self.assertEqual(len(self.by_name("02_long_board.csv")["Summary"]["rows"]), 7)
        self.assertEqual(len(self.by_name("04_single_placement.csv")["Placements"]["rows"]), 1)

    def test_03_errors(self):
        summary = {r["Item"]: r["Value"] for r in self.by_name("03_board_with_errors.csv")["Summary"]["rows"]}
        self.assertEqual((summary["Errors"], summary["Result"]), ("2", "FAIL"))

    def test_05_semicolon_decimal_comma(self):
        b = self.by_name("05_semicolon_decimal_comma.csv")
        self.assertEqual({r["Item"]: r["Value"] for r in b["Summary"]["rows"]}["Yield_pct"], "100,0")
        self.assertEqual(len(b["Placements"]["columns"]), 9)
        self.assertEqual(len(b["Placements"]["rows"]), 12)

    def test_06_windows_1252(self):
        b = self.by_name("06_windows_1252.csv")
        self.assertEqual(b["Program"]["value"], "ECU-CAFÉ-REV3")
        self.assertEqual(b["Placements"]["rows"][0]["Part_No"], "TEMP-SENS-85°C")


class StandInMES(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mes = MockMES(self.tmp / "inbox", "tok", port=8790).start()

    def tearDown(self):
        self.mes.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, token="tok", name="a.json", body=b'{"x": 1}'):
        req = urllib.request.Request(self.mes.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {token}", "X-Filename": name, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_accepts_and_stores(self):
        self.assertEqual(self.post(), 201)
        self.assertEqual(self.mes.items[0]["name"], "a.json")
        self.assertEqual(Path(self.mes.items[0]["path"]).read_bytes(), b'{"x": 1}')
        with urllib.request.urlopen(self.mes.page + "api/received", timeout=5) as r:
            self.assertEqual(len(json.load(r)["items"]), 1)

    def test_wrong_token_outage_and_refusal(self):
        self.assertEqual(self.post(token="nope"), 401)
        self.mes.online = False
        self.assertEqual(self.post(), 503)
        self.mes.online, self.mes.refuse_next = True, True
        self.assertEqual([self.post(), self.post()], [422, 201])
        self.assertEqual((self.mes.unavailable, self.mes.refused, len(self.mes.items)), (1, 1, 1))

    def test_second_receiver_takes_the_next_port(self):
        other = MockMES(self.tmp / "inbox2", port=self.mes.port)
        try:
            self.assertNotEqual(other.port, self.mes.port)
        finally:
            other.server.server_close()

    def test_engine_delivers_to_it(self):
        ws = self.tmp / "ws"
        (ws / "machine").mkdir(parents=True)
        (ws / "machine" / "b.csv").write_bytes(testdata.CASES[0].data)
        cfg = dict(testdata.demo_settings(ws, self.mes.url), api_token="tok", stable_seconds=0)
        self.assertEqual(engine.Engine(cfg, ws / "data").scan_once(), 1)
        self.assertEqual(len(self.mes.items), 1)
        self.assertEqual(len(list((ws / "shared").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
