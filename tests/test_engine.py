"""Run:  python -m unittest discover -s tests -v"""
import csv
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cncbridge import config, engine  # noqa: E402

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)
SAMPLE = ROOT / "samples" / "pnp_L1M8_board_00001.csv"
OVEN = ROOT / "samples" / "reflow_oven_log.csv"


def sel(rng, name="", header=True):
    return {"name": name, "range": rng, "header": header}


class Receiver:
    """A stand-in for the customer's API. `status` is a number or a function of the file name."""

    def __init__(self):
        self.status, self.got = 200, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                name = self.headers.get("X-Filename", "")
                code = outer.status(name) if callable(outer.status) else outer.status
                if code == 200:
                    outer.got.append({"path": self.path, "headers": dict(self.headers), "body": body})
                self.send_response(code)
                if code == 302:
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/upload"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Ranges(unittest.TestCase):
    def test_forms(self):
        for text in ("L1:M8", "l1 x m8", "L1xM8", "L1×M8", "L1 to M8", "L1-M8", "M8:L1"):
            self.assertEqual(engine.normalize_range(text), "L1:M8", text)
        self.assertEqual(engine.normalize_range(" b6 "), "B6")
        self.assertEqual(engine.normalize_range("a12 : i*"), "A12:I*")
        self.assertEqual(engine.parse_range("AA3:AB4"), (26, 2, 27, 3))

    def test_invalid(self):
        for text in ("", "hello", "L0:M8", "1A", "L1:M", "L1:M8:N9"):
            with self.assertRaises(ValueError, msg=text):
                engine.parse_range(text)


class Extraction(unittest.TestCase):
    def setUp(self):
        self.rows = engine.read_rows(SAMPLE)

    def test_table_with_header(self):
        block, filled = engine.extract(self.rows, sel("A12:I24", "Placements"))
        self.assertTrue(filled)
        self.assertEqual(block["columns"][:3], ["Timestamp", "Ref", "Part_No"])
        self.assertEqual(len(block["rows"]), 12)
        self.assertEqual(block["rows"][0][1], "R101")

    def test_single_cell(self):
        block, _ = engine.extract(self.rows, sel("B6", "Serial"))
        self.assertEqual(block, {"name": "Serial", "range": "B6", "value": "B240001"})

    def test_open_range_stops_at_first_empty_row(self):
        block, _ = engine.extract(self.rows, sel("A12:I*"))
        self.assertEqual(block["name"], "A12:I*")
        self.assertEqual(len(block["rows"]), 12)  # not the FEEDER_STATUS table further down

    def test_no_header(self):
        block, _ = engine.extract(self.rows, sel("A3:B4", header=False))
        self.assertNotIn("columns", block)
        self.assertEqual(block["rows"], [["Machine", "PNP-M8"], ["Line", "L1"]])

    def test_past_end_of_file(self):
        cfg = config.normalize_config({"selections": [sel("A49:D99")]})
        _, blocks, warnings = engine.build_output("x.csv", self.rows, cfg)
        self.assertEqual(len(blocks[0]["rows"]), 1)
        self.assertTrue(any("past the last row" in w for w in warnings))

    def test_nothing_selected_has_data(self):
        cfg = config.normalize_config({"selections": [sel("X1:Z5"), sel("A300")]})
        with self.assertRaises(engine.FileError):
            engine.build_output("x.csv", self.rows, cfg)

    def test_semicolon_file(self):
        rows = engine.read_rows(OVEN)
        block, _ = engine.extract(rows, sel("B1 x D*"))
        self.assertEqual(block["columns"], ["Zone", "Setpoint_C", "Actual_C"])
        self.assertEqual(len(block["rows"]), 6)


class Reading(unittest.TestCase):
    def test_decimal_commas_do_not_fool_the_delimiter(self):
        rows = engine.read_rows(b"Zone;Temp;Speed\r\nZ1;149,6;12,0\r\nZ2;181,2;12,0\r\n")
        self.assertEqual(rows[1], ["Z1", "149,6", "12,0"])

    def test_forced_delimiter(self):
        self.assertEqual(engine.read_rows(b"a|b,c\n", delimiter="|"), [["a", "b,c"]])
        self.assertEqual(engine.read_rows(b"a\tb\n"), [["a", "b"]])

    def test_encodings(self):
        self.assertEqual(engine.read_rows("Ref,Temp\nR1,25°C\n".encode("cp1252"))[1][1], "25°C")
        self.assertEqual(engine.read_rows("﻿Ref,Temp\nR1,25°C\n".encode("utf-8"))[0][0], "Ref")
        self.assertEqual(engine.read_rows("Ref,Temp\nR1,25°C\n".encode("utf-16"))[1][1], "25°C")
        self.assertEqual(engine.read_rows("品番,数\n".encode("shift_jis"), encoding="shift_jis")[0][0], "品番")


class Rendering(unittest.TestCase):
    def setUp(self):
        self.rows = engine.read_rows(SAMPLE)
        self.cfg = config.normalize_config({
            "station_name": "SMT Line 1",
            "selections": [sel("A12:I*", "Placements"), sel("B6", "Serial"), sel("A3:B4", "Machine", header=False)],
            "manual_inputs": [{"name": "Operator", "value": "OP-17"}, {"name": "", "value": "WO-1187"}]})
        self.now = datetime(2026, 10, 10, 9, 30, 0)

    def out(self, fmt):
        self.cfg["output_format"] = fmt
        return engine.build_output("board.csv", self.rows, self.cfg, self.now)[0]

    def test_json(self):
        doc = json.loads(self.out("json"))
        self.assertEqual(doc["source_file"], "board.csv")
        self.assertEqual(doc["processed_at"], "2026-10-10T09:30:00")
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-17", "Manual input 2": "WO-1187"})
        placements, serial, machine = doc["selections"]
        self.assertEqual(placements["rows"][1], dict(placements["rows"][1], Ref="R102", Result="PICKUP_ERR"))
        self.assertEqual(serial, {"name": "Serial", "range": "B6", "value": "B240001"})
        self.assertEqual(machine["rows"], [["Machine", "PNP-M8"], ["Line", "L1"]])

    def test_csv(self):
        rows = list(csv.reader(io.StringIO(self.out("csv"), newline="")))
        self.assertEqual(rows[0], ["[INFO]"])
        self.assertIn(["station", "SMT Line 1"], rows)
        self.assertIn(["Operator", "OP-17"], rows)
        i = rows.index(["[Placements]", "A12:I*"])
        self.assertEqual(rows[i + 1][:2], ["Timestamp", "Ref"])
        self.assertEqual(rows[rows.index(["[Serial]", "B6"]) + 1], ["B240001"])

    def test_txt(self):
        text = self.out("txt")
        self.assertIn("Operator      : OP-17", text)
        self.assertIn("=== Placements  [A12:I*] ===", text)
        self.assertIn("Machine | PNP-M8", text)


class Settings(unittest.TestCase):
    def test_token_is_encrypted_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.save_config({"api_token": "s3cret-token"}, path)
            self.assertNotIn("s3cret-token", path.read_text(encoding="utf-8"))
            self.assertEqual(config.load_config(path)["api_token"], "s3cret-token")

    def test_validate(self):
        with tempfile.TemporaryDirectory() as d:
            problems = engine.validate(config.normalize_config({}))
            self.assertEqual(len(problems), 4)  # source, back-up, range, delivery
            cfg = config.normalize_config({
                "source_folder": d, "backup_folder": d, "selections": [sel("nonsense")],
                "shared_enabled": True, "shared_folder": d, "api_enabled": True, "api_url": "ftp://x",
                "api_headers": "no colon here", "encoding": "klingon"})
            self.assertEqual(len(engine.validate(cfg)), 6)
            cfg = config.normalize_config({
                "source_folder": d, "backup_folder": d + "/bak", "selections": [sel("L1 x M8")],
                "api_enabled": True, "api_url": "https://mes.example/api", "api_headers": "X-Api-Key: abc"})
            self.assertEqual(engine.validate(cfg), [])


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src, self.bak, self.shared = self.tmp / "machine", self.tmp / "backup", self.tmp / "shared"
        self.src.mkdir()
        self.api = Receiver()
        self.cfg = {
            "station_name": "SMT Line 1", "source_folder": str(self.src), "backup_folder": str(self.bak),
            "selections": [sel("A12:I*", "Placements"), sel("B6", "Serial")],
            "manual_inputs": [{"name": "Operator", "value": "OP-17"}, {"name": "Work order", "value": "WO-1"}],
            "shared_enabled": True, "shared_folder": str(self.shared), "stable_seconds": 0}

    def tearDown(self):
        self.api.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **overrides):
        return engine.Engine(dict(self.cfg, **overrides), self.tmp / "data")

    def drop(self, name="board1.csv", serial="B240001"):
        (self.src / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def today(self):
        return self.bak / f"{datetime.now():%Y-%m-%d}"

    def test_shared_folder_and_backup(self):
        self.drop()
        eng = self.engine()
        self.assertEqual(eng.scan_once(), 1)
        out = list(self.shared.glob("board1_*.json"))
        self.assertEqual(len(out), 1)
        doc = json.loads(out[0].read_text(encoding="utf-8"))
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-17", "Work order": "WO-1"})
        self.assertEqual(doc["selections"][1]["value"], "B240001")
        self.assertEqual(list(self.src.iterdir()), [])
        self.assertTrue((self.today() / "board1.csv").exists())
        row = eng.ledger.recent()[0]
        self.assertEqual((row["status"], row["shared"], row["api"]), ("ok", "ok", "off"))
        self.assertEqual(engine.count_queued(self.tmp / "data"), {"shared": 0, "api": 0})

    def test_each_format(self):
        for fmt in ("csv", "txt"):
            self.drop(f"{fmt}.csv", serial=fmt)
            self.engine(output_format=fmt).scan_once()
            self.assertEqual(len(list(self.shared.glob(f"{fmt}_*.{fmt}"))), 1)

    def test_manual_inputs_can_change_while_running(self):
        eng = self.engine()
        self.drop("a.csv", "A")
        eng.scan_once()
        eng.cfg = config.normalize_config(dict(self.cfg, manual_inputs=[{"name": "Operator", "value": "OP-99"}]))
        self.drop("b.csv", "B")
        eng.scan_once()
        doc = json.loads(next(self.shared.glob("b_*.json")).read_text(encoding="utf-8"))
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-99"})

    def test_duplicate_is_not_sent_twice(self):
        eng = self.engine()
        self.drop("a.csv")
        eng.scan_once()
        self.drop("a_copy.csv")
        self.assertEqual(eng.scan_once(), 0)
        self.assertEqual(len(list(self.shared.iterdir())), 1)
        self.assertTrue((self.bak / "_duplicates" / "a_copy.csv").exists())
        self.assertEqual(eng.ledger.recent()[0]["status"], "duplicate")

    def test_unusable_file_is_quarantined_and_the_rest_continue(self):
        (self.src / "junk.csv").write_text("only,one,line\n")
        os.utime(self.src / "junk.csv", (time.time() - 60,) * 2)
        self.drop("good.csv")
        eng = self.engine()
        self.assertEqual(eng.scan_once(), 1)
        self.assertTrue((self.bak / "_failed" / "junk.csv").exists())
        self.assertEqual([r["status"] for r in eng.ledger.recent()], ["ok", "failed"])

    def test_file_still_being_written_is_left_alone(self):
        self.drop()
        eng = self.engine(stable_seconds=30)
        self.assertEqual(eng.pending_files(), [])
        eng = self.engine()
        with open(self.src / "board1.csv", "ab"):  # the machine still has it open
            self.assertEqual(eng.pending_files(), [])
        self.assertEqual(len(eng.pending_files()), 1)

    def test_backup_drive_away_sends_nothing(self):
        (self.tmp / "not_a_folder").write_text("x")
        self.drop()
        eng = self.engine(backup_folder=str(self.tmp / "not_a_folder"))
        self.assertEqual(eng.scan_once(), 0)
        self.assertTrue((self.src / "board1.csv").exists())
        self.assertFalse(self.shared.exists())
        self.assertEqual(eng.ledger.recent(), [])

    def test_api_raw(self):
        self.drop()
        self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url, api_token="tok",
                    api_headers="X-Api-Key: abc\nX-Line: 1").scan_once()
        got = self.api.got[0]
        self.assertEqual(json.loads(got["body"])["selections"][1]["value"], "B240001")
        h = got["headers"]
        self.assertEqual((h["Authorization"], h["X-Api-Key"], h["X-Line"]), ("Bearer tok", "abc", "1"))
        self.assertEqual(h["Content-Type"], "application/json")
        self.assertTrue(h["X-Filename"].startswith("board1_"))

    def test_api_multipart(self):
        self.drop()
        self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url, api_body="multipart",
                    output_format="csv").scan_once()
        got = self.api.got[0]
        self.assertIn("multipart/form-data; boundary=", got["headers"]["Content-Type"])
        self.assertIn(b'name="file"; filename="board1_', got["body"])
        self.assertIn(b'name="station"\r\n\r\nSMT Line 1', got["body"])
        self.assertIn(b"[Serial],B6", got["body"])

    def test_api_outage_queues_then_delivers_in_order(self):
        eng = self.engine(api_enabled=True, api_url=self.api.url)
        self.api.status = 503
        for i in (1, 2):
            self.drop(f"b{i}.csv", f"S{i}")
            os.utime(self.src / f"b{i}.csv", (time.time() - 60 + i,) * 2)
        self.assertEqual(eng.scan_once(), 2)
        self.assertEqual(len(list(self.shared.iterdir())), 2)  # the shared folder is not held up by the API
        self.assertEqual(engine.count_queued(self.tmp / "data"), {"shared": 0, "api": 2})
        self.assertEqual({r["api"] for r in eng.ledger.recent()}, {"queued"})
        self.api.status = 200
        eng.scan_once()
        self.assertEqual([g["headers"]["X-Filename"][:2] for g in self.api.got], ["b1", "b2"])
        self.assertEqual({r["api"] for r in eng.ledger.recent()}, {"ok"})
        self.assertEqual(engine.count_queued(self.tmp / "data")["api"], 0)

    def test_refused_file_does_not_block_the_queue(self):
        eng = self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url)
        self.api.status = lambda name: 422 if name.startswith("bad") else 200
        self.drop("bad.csv", "S1")
        os.utime(self.src / "bad.csv", (time.time() - 60,) * 2)
        self.drop("good.csv", "S2")
        eng.scan_once()
        self.assertEqual(len(self.api.got), 1)
        self.assertEqual(engine.count_rejected(self.tmp / "data"), 1)
        self.assertEqual(engine.count_queued(self.tmp / "data")["api"], 0)
        by_source = {r["source"]: r["api"] for r in eng.ledger.recent()}
        self.assertEqual(by_source["good.csv"], "ok")
        self.assertTrue(by_source["bad.csv"].startswith("rejected (HTTP 422"))

    def test_wrong_token_keeps_files_queued(self):
        eng = self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url)
        self.api.status = 401
        self.drop()
        eng.scan_once()
        self.assertEqual(engine.count_queued(self.tmp / "data")["api"], 1)
        self.assertEqual(engine.count_rejected(self.tmp / "data"), 0)

    def test_redirect_is_not_followed(self):
        eng = self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url)
        self.api.status = 302
        self.drop()
        eng.scan_once()
        self.assertEqual(self.api.got, [])
        self.assertEqual(engine.count_queued(self.tmp / "data")["api"], 1)
        with self.assertRaisesRegex(RuntimeError, "redirects to /elsewhere"):
            engine.test_api(eng.cfg)

    def test_connection_tests(self):
        cfg = config.normalize_config(dict(self.cfg, api_url=self.api.url))
        self.assertEqual(engine.test_api(cfg), 200)
        self.assertTrue(Path(engine.test_shared(cfg)).is_dir())
        self.assertEqual(list(self.shared.iterdir()), [])


class DataFolder(unittest.TestCase):
    """Settings and history live outside the program folder, and an old 'data' folder is moved there once."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old, self.new = self.tmp / "program" / "data", self.tmp / "ProgramData" / "CNCBridge"
        (self.old / "outbox" / "shared").mkdir(parents=True)
        (self.old / "logs").mkdir()
        config.save_config({"station_name": "Line 7", "api_token": "tok"}, self.old / "config.json")
        engine.Ledger(self.old / "ledger.db").add(source="a.csv", sha256="abc", status="ok", output="a.json")
        (self.old / "outbox" / "shared" / "a.json").write_text('{"queued": true}')
        (self.old / "instance.lock").write_text("")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **values):
        saved = {k: os.environ.get(k) for k in values}
        os.environ.update({k: str(v) for k, v in values.items()})
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])

    def test_default_is_programdata(self):
        self.env(PROGRAMDATA=self.tmp / "ProgramData", LOCALAPPDATA=self.tmp / "Local")
        self.assertEqual(config.default_data_dir(), self.tmp / "ProgramData" / "CNCBridge")

    def test_falls_back_to_localappdata(self):
        (self.tmp / "not_a_folder").write_text("x")  # ProgramData cannot be used
        self.env(PROGRAMDATA=self.tmp / "not_a_folder", LOCALAPPDATA=self.tmp / "Local")
        self.assertEqual(config.default_data_dir(), self.tmp / "Local" / "CNCBridge")

    def test_old_folder_is_moved_once(self):
        message = config.move_data(self.old, self.new, today=datetime(2026, 10, 17))
        self.assertIn("moved", message)
        self.assertEqual(config.load_config(self.new / "config.json")["station_name"], "Line 7")
        self.assertEqual(config.load_config(self.new / "config.json")["api_token"], "tok")
        self.assertEqual(engine.Ledger(self.new / "ledger.db").recent()[0]["source"], "a.csv")
        self.assertTrue((self.new / "outbox" / "shared" / "a.json").exists())
        self.assertFalse((self.new / "instance.lock").exists())
        self.assertFalse(self.old.exists())
        self.assertTrue((self.tmp / "program" / "data.moved-20261017" / "config.json").exists())
        self.assertIsNone(config.move_data(self.old, self.new))  # nothing left to move

    def test_existing_settings_are_never_overwritten(self):
        config.save_config({"station_name": "New PC"}, self.new / "config.json")
        self.assertIsNone(config.move_data(self.old, self.new))
        self.assertEqual(config.load_config(self.new / "config.json")["station_name"], "New PC")
        self.assertTrue(self.old.exists())

    def test_history_alone_is_moved_too(self):
        (self.old / "config.json").unlink()  # settings lost, history still there
        self.assertIn("moved", config.move_data(self.old, self.new, today=datetime(2026, 10, 17)))
        self.assertEqual(engine.Ledger(self.new / "ledger.db").recent()[0]["source"], "a.csv")

    def test_existing_history_is_never_overwritten(self):
        engine.Ledger(self.new / "ledger.db").add(source="new.csv", status="ok")
        self.assertIsNone(config.move_data(self.old, self.new))
        self.assertEqual(engine.Ledger(self.new / "ledger.db").recent()[0]["source"], "new.csv")

    def test_second_move_on_the_same_day_gets_its_own_name(self):
        (self.tmp / "program" / "data.moved-20261017").mkdir()
        config.move_data(self.old, self.new, today=datetime(2026, 10, 17))
        self.assertTrue((self.tmp / "program" / "data.moved-20261017-2" / "config.json").exists())

    def test_not_moved_while_an_old_copy_runs(self):
        from cncbridge import __main__ as entry
        held = entry.acquire_lock(self.old)
        try:
            folder, message = entry.choose_data_dir(old=self.old, new=self.new)
        finally:
            held.close()
        self.assertEqual((folder, message), (self.old, None))  # the lock then reports "already running"
        self.assertFalse((self.new / "config.json").exists())
        self.assertTrue((self.old / "config.json").exists())

    def test_second_copy_keeps_its_own_folder(self):
        from cncbridge import __main__ as entry
        config.save_config({"station_name": "First copy"}, self.new / "config.json")
        folder, message = entry.choose_data_dir(old=self.old, new=self.new)
        self.assertEqual((folder, message), (self.old, None))
        self.assertEqual(config.load_config(self.new / "config.json")["station_name"], "First copy")
        self.assertEqual(config.load_config(self.old / "config.json")["station_name"], "Line 7")

    def test_fresh_install_and_explicit_folder(self):
        from cncbridge import __main__ as entry
        empty = self.tmp / "empty_program" / "data"
        self.assertEqual(entry.choose_data_dir(old=empty, new=self.new), (self.new, None))
        self.assertEqual(entry.choose_data_dir(requested=self.tmp / "mine", old=self.old, new=self.new),
                         ((self.tmp / "mine").resolve(), None))
        self.assertTrue(self.old.exists())  # an explicit --data never moves anything

    def test_queued_file_is_delivered_after_the_move(self):
        from cncbridge import __main__ as entry
        folder, message = entry.choose_data_dir(old=self.old, new=self.new)
        self.assertEqual(folder, self.new)
        self.assertIn("moved", message)
        shared = self.tmp / "shared"
        eng = engine.Engine(dict(shared_enabled=True, shared_folder=str(shared)), self.new)
        eng.drain_outbox()
        self.assertEqual((shared / "a.json").read_text(), '{"queued": true}')
        self.assertEqual(eng.ledger.recent()[0]["shared"], "ok")


if __name__ == "__main__":
    unittest.main()
