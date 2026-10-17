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

from cncbridge import alerts, config, engine  # noqa: E402

alerts.SENDERS["notify"] = lambda *a, **k: None  # no Windows notifications on screen during tests

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

    def test_every_save_keeps_a_backup_copy(self):
        with tempfile.TemporaryDirectory() as d:
            backups = Path(d) / "backups"
            backups.mkdir()
            for i in range(25):  # older copies, oldest first by name
                (backups / f"config-2020010{i // 10}-0000{i % 10:02d}.json").write_text("{}")
            config.save_config({"station_name": "Line 7"}, Path(d) / "config.json")
            kept = sorted(p.name for p in backups.glob("config-*.json"))
            self.assertEqual(len(kept), config.BACKUPS_KEPT)
            newest = backups / kept[-1]
            self.assertEqual(config.load_config(newest)["station_name"], "Line 7")

    def test_export_leaves_out_the_token_and_import_keeps_the_current_one(self):
        with tempfile.TemporaryDirectory() as d:
            exported = Path(d) / "export.json"
            config.export_settings({"station_name": "Line 7", "api_token": "secret", "stable_seconds": 9,
                                    "selections": [sel("L1:M8", "Summary")]}, exported)
            self.assertNotIn("secret", exported.read_text(encoding="utf-8"))
            cfg = config.import_settings(exported, {"api_token": "current-token"})
            self.assertEqual((cfg["station_name"], cfg["api_token"], cfg["stable_seconds"]),
                             ("Line 7", "current-token", 9))
            self.assertEqual(cfg["selections"][0]["range"], "L1:M8")

    def test_restoring_a_backup_brings_back_its_token(self):
        with tempfile.TemporaryDirectory() as d:
            config.save_config({"station_name": "Line 7", "api_token": "old-token"}, Path(d) / "config.json")
            backup = next((Path(d) / "backups").glob("config-*.json"))
            cfg = config.import_settings(backup, {"api_token": "current-token"})
            self.assertEqual(cfg["api_token"], "old-token")

    def test_import_refuses_other_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("other.json", '{"name": "not settings"}'), ("list.json", "[1, 2]"),
                               ("broken.json", "{not json")):
                (Path(d) / name).write_text(text)
                with self.assertRaises(ValueError, msg=name):
                    config.import_settings(Path(d) / name, {})

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


class _MailServer:
    """A minimal SMTP server that keeps the messages it receives (no encryption, no login)."""

    def __init__(self):
        import socketserver
        self.messages = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                def say(line):
                    self.wfile.write(line.encode() + b"\r\n")
                say("220 test mail server")
                while True:
                    line = self.rfile.readline().decode(errors="replace").strip()
                    if not line or line.upper().startswith("QUIT"):
                        say("221 bye")
                        return
                    if line.upper().startswith("DATA"):
                        say("354 go on")
                        body = []
                        for raw in iter(self.rfile.readline, b""):
                            if raw in (b".\r\n", b".\n"):
                                break
                            body.append(raw.decode(errors="replace"))
                        outer.messages.append("".join(body))
                        say("250 stored")
                    else:
                        say("250 ok")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Alerting(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "machine").mkdir()
        self.sent = []
        for name in ("notify", "webhook", "email"):
            original = alerts.SENDERS[name]
            self.addCleanup(alerts.SENDERS.__setitem__, name, original)
            alerts.SENDERS[name] = lambda *a, _n=name, **k: self.sent.append((_n, a))
        self.api = Receiver()
        self.addCleanup(self.api.close)
        self.cfg = {"station_name": "Line 7", "source_folder": str(self.tmp / "machine"),
                    "backup_folder": str(self.tmp / "backup"), "selections": [sel("A12:I*"), sel("B6")],
                    "api_enabled": True, "api_url": self.api.url, "stable_seconds": 0,
                    "alert_after_minutes": 0}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **overrides):
        return engine.Engine(dict(self.cfg, **overrides), self.tmp / "data")

    def drop(self, name, serial):
        (self.tmp / "machine" / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def messages(self, eng):
        eng.alerts.flush()
        return [a for name, a in self.sent if name == "notify"]  # (title, text, kind)

    def test_lasting_problem_is_announced_once_and_its_end_too(self):
        eng = self.engine()
        self.api.status = 503
        self.drop("a.csv", "S1")
        eng.scan_once()
        eng.scan_once()  # still down: no second alert
        titles = [t for t, *_ in self.messages(eng)]
        self.assertEqual(titles, ["CNCBridge needs attention - Line 7"])
        self.assertIn("api", alerts.read_state(self.tmp / "data"))
        self.api.status = 200
        eng.scan_once()
        titles = [t for t, *_ in self.messages(eng)]
        self.assertEqual(titles[-1], "CNCBridge is working again - Line 7")
        self.assertEqual(alerts.read_state(self.tmp / "data"), {})

    def test_short_problem_does_not_alert(self):
        eng = self.engine(alert_after_minutes=10)
        self.api.status = 503
        self.drop("a.csv", "S1")
        eng.scan_once()
        self.assertEqual(self.messages(eng), [])

    def test_files_set_aside_are_announced_each_time(self):
        eng = self.engine(alert_after_minutes=10)
        self.api.status = 422
        self.drop("a.csv", "S1")
        (self.tmp / "machine" / "junk.csv").write_text("only,one,line\n")
        os.utime(self.tmp / "machine" / "junk.csv", (time.time() - 60,) * 2)
        eng.scan_once()
        texts = [text for _, text, _ in self.messages(eng)]
        self.assertTrue(any("junk.csv" in t and "_failed" in t for t in texts), texts)
        self.assertTrue(any("refused" in t and "a_" in t for t in texts), texts)

    def test_channels_that_are_off_send_nothing(self):
        eng = self.engine(alert_notify=False)
        self.api.status = 503
        self.drop("a.csv", "S1")
        eng.scan_once()
        eng.alerts.flush()
        self.assertEqual(self.sent, [])

    def test_stopping_clears_the_open_problems(self):
        eng = self.engine(poll_seconds=0.5)
        self.api.status = 503
        self.drop("a.csv", "S1")
        worker = threading.Thread(target=eng.run_forever, daemon=True)
        worker.start()
        deadline = time.time() + 15
        while not alerts.read_state(self.tmp / "data") and time.time() < deadline:
            time.sleep(0.1)
        self.assertIn("api", alerts.read_state(self.tmp / "data"))
        eng.stop()
        worker.join(10)
        self.assertEqual(alerts.read_state(self.tmp / "data"), {})

    def test_webhook_is_really_posted(self):
        alerts.SENDERS["webhook"] = alerts.post_webhook
        hook = Receiver()
        self.addCleanup(hook.close)
        cfg = config.normalize_config(dict(self.cfg, alert_notify=False, alert_webhook_enabled=True,
                                           alert_webhook_url=hook.url))
        self.assertEqual(alerts.send_test(cfg), "Webhook: sent")
        payload = json.loads(hook.got[0]["body"])
        self.assertEqual((payload["station"], payload["state"]), ("Line 7", "test"))
        self.assertIn("Line 7", payload["text"])

    def test_email_is_really_sent(self):
        alerts.SENDERS["email"] = alerts.send_email
        mail = _MailServer()
        self.addCleanup(mail.close)
        cfg = config.normalize_config(dict(self.cfg, alert_notify=False, alert_email_enabled=True,
                                           alert_email_server="127.0.0.1", alert_email_port=mail.port,
                                           alert_email_starttls=False, alert_email_to="qa@example.com"))
        self.assertEqual(alerts.send_test(cfg), "Email: sent")
        self.assertIn("Subject: CNCBridge test alert - Line 7", mail.messages[0])
        self.assertIn("To: qa@example.com", mail.messages[0])

    def test_a_failing_channel_is_reported_not_raised(self):
        alerts.SENDERS["email"] = alerts.send_email
        cfg = config.normalize_config(dict(self.cfg, alert_notify=False, alert_email_enabled=True))
        self.assertIn("Email: failed - enter the mail server", alerts.send_test(cfg))
        self.assertEqual(alerts.send_test(config.normalize_config(dict(self.cfg, alert_notify=False))),
                         "No alert channel is switched on.")

    def test_email_password_is_encrypted_and_never_exported(self):
        path, exported = self.tmp / "config.json", self.tmp / "export.json"
        config.save_config({"alert_email_password": "mail-secret"}, path)
        self.assertNotIn("mail-secret", path.read_text(encoding="utf-8"))
        self.assertEqual(config.load_config(path)["alert_email_password"], "mail-secret")
        config.export_settings(config.load_config(path), exported)
        self.assertNotIn("mail-secret", exported.read_text(encoding="utf-8"))


class Background(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        (self.tmp / "machine").mkdir()
        self.cfg = {"source_folder": str(self.tmp / "machine"), "backup_folder": str(self.tmp / "backup"),
                    "selections": [sel("A12:I*"), sel("B6")], "shared_enabled": True,
                    "shared_folder": str(self.tmp / "shared"), "stable_seconds": 0, "poll_seconds": 0.5,
                    "manual_inputs": [{"name": "Operator", "value": "OP-17"}]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drop(self, name, serial):
        (self.tmp / "machine" / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def test_stop_request_ends_the_run_and_an_old_one_is_ignored(self):
        from cncbridge import background
        background.request_stop(self.data)  # left over from earlier: must not stop the new run
        eng = engine.Engine(self.cfg, self.data)
        worker = threading.Thread(target=eng.run_forever, daemon=True)
        worker.start()
        time.sleep(1.5)
        self.assertTrue(worker.is_alive())
        background.request_stop(self.data)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse((self.data / background.STOP_FILE).exists())

    def test_saved_settings_are_picked_up_while_running(self):
        path = self.data / "config.json"
        config.save_config(self.cfg, path)
        eng = engine.Engine(config.load_config(path), self.data, config_path=path)
        self.assertFalse(eng.reload_settings())  # nothing changed yet
        config.save_config(dict(self.cfg, manual_inputs=[{"name": "Operator", "value": "OP-99"}]), path)
        later = time.time() + 5
        os.utime(path, (later, later))  # some file systems keep times in whole seconds
        self.assertTrue(eng.reload_settings())
        self.drop("a.csv", "S1")
        eng.scan_once()
        doc = json.loads(next((self.tmp / "shared").glob("a_*.json")).read_text(encoding="utf-8"))
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-99"})

    def test_who_holds_the_data_folder(self):
        from cncbridge import background
        self.assertIsNone(background.holder(self.data))
        lock = background.acquire_lock(self.data)
        try:
            background.mark_holder(self.data, "background")
            self.assertEqual(background.holder(self.data), "background")
            self.assertTrue(background.is_running(self.data))
            background.mark_holder(self.data, "window")
            self.assertFalse(background.is_running(self.data))
        finally:
            lock.close()
        self.assertIsNone(background.holder(self.data))

    def test_window_mode(self):
        from cncbridge import __main__ as entry, background
        original = background.task_exists
        self.addCleanup(setattr, background, "task_exists", original)
        background.task_exists = lambda d: True
        self.assertEqual(entry.window_mode(self.data), ("connected", None))
        background.task_exists = lambda d: False
        mode, lock = entry.window_mode(self.data)
        self.assertEqual(mode, "normal")
        background.mark_holder(self.data, "window")
        self.assertEqual(entry.window_mode(self.data), ("busy", None))
        background.mark_holder(self.data, "background")
        self.assertEqual(entry.window_mode(self.data), ("connected", None))
        lock.close()

    def test_task_definition(self):
        from cncbridge import background
        self.assertNotEqual(background.task_name(self.data), "CNCBridge background")  # not the default folder
        self.assertTrue(background.task_name(self.data).startswith("CNCBridge background "))
        program, arguments, workdir = background.task_command(self.data)
        self.assertTrue(program.lower().endswith(("pythonw.exe", "python.exe")))
        self.assertIn("CNCBridge.pyw", arguments)
        self.assertIn(f'--headless --data "{self.data.resolve()}"', arguments)
        xml = background.task_xml(self.data, user="PLANT\\op & co")
        self.assertIn("<UserId>PLANT\\op &amp; co</UserId>", xml)
        self.assertIn("<LogonTrigger>", xml)
        self.assertIn("--headless", xml)

    def test_install_and_remove_use_schtasks(self):
        import subprocess
        from cncbridge import background
        calls = []

        def fake(*args):
            calls.append(args)
            if args[0] == "/Create":
                calls.append(Path(args[args.index("/XML") + 1]).read_text(encoding="utf-16"))
            return subprocess.CompletedProcess(args, 0, "SUCCESS", "")
        original = background._schtasks
        self.addCleanup(setattr, background, "_schtasks", original)
        background._schtasks = fake
        background.install_task(self.data)
        self.assertEqual(calls[0][:2], ("/Create", "/TN"))
        self.assertIn("<Command>", calls[1])
        background.start_task(self.data)
        self.assertEqual(calls[2][0], "/Run")
        background.remove_task(self.data)
        self.assertEqual(calls[3][0], "/Delete")
        background._schtasks = lambda *a: subprocess.CompletedProcess(a, 1, "", "ERROR: Access is denied.")
        with self.assertRaisesRegex(OSError, "could not be created: Access is denied"):
            background.install_task(self.data)


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
