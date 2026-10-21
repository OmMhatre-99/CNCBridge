"""The zip for support: what is in it, that no password is, and that making it changes nothing."""
import csv
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import __main__ as entry, alerts, background, config, diagnostics, engine, stations  # noqa: E402

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)
SECRETS = ("tok-SECRET-1", "ftp-SECRET-2", "mail-SECRET-3", "hdr-SECRET-4", "KEY-SECRET-5", "HOOK-SECRET-6")
PASSWORD_HASH = config.hash_password("opensesame")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = self.tmp / "data"
        registry = self.tmp / "stations.json"
        patcher = mock.patch.dict(os.environ, {"CNCBRIDGE_STATIONS": str(registry)})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(background, "task_exists", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.zip = self.tmp / "out" / "diagnostics.zip"

    def seed(self):
        """A data folder with something in every part, and a known secret in every place one could leak from."""
        config.save_config(config.normalize_config({
            "station_name": "Line 7", "source_folder": str(self.tmp / "machine"), "backup_folder": str(self.tmp / "backup"),
            "selections": [{"name": "Summary", "range": "A12:I14", "header": True}], "shared_enabled": True,
            "shared_folder": str(self.tmp / "shared"), "api_enabled": True,
            "api_url": "https://mes.example/api/in?key=KEY-SECRET-5", "api_token": "tok-SECRET-1",
            "api_headers": "X-Api-Key: hdr-SECRET-4\nX-Plant: P1", "ftp_password": "ftp-SECRET-2",
            "alert_email_password": "mail-SECRET-3", "alert_webhook_url": "https://hooks.example/t?token=HOOK-SECRET-6",
            "setup_password": PASSWORD_HASH, "manual_inputs": [{"name": "Operator", "value": "OP-17"}]}),
            self.data / "config.json")
        (self.tmp / "machine").mkdir()
        ledger = engine.Ledger(self.data / "ledger.db")  # makes the tables; the many rows go in at once
        import sqlite3
        con = sqlite3.connect(self.data / "ledger.db")
        con.executemany("INSERT INTO files(id, processed_at, source, sha256, size, status, output, message) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        [(n + 1, f"2026-10-20T10:{n % 60:02d}:00", f"b{n}.csv", f"{n:064x}", 100 + n, "ok", f"b{n}.json", "")
                         for n in range(505)])
        con.executemany("INSERT INTO deliveries(file_id, kind, status, updated_at) VALUES(?,?,?,?)",
                        [(n + 1, kind, status, "2026-10-20T10:00:00") for n in range(505)
                         for kind, status in (("shared", "ok"), ("api", "ok" if n % 7 else "queued"), ("ftp", "off"))])
        con.commit()
        con.close()
        ledger.add(source="bad.csv", status="failed", message="not a readable CSV")
        logs = self.data / "logs"
        logs.mkdir()
        (logs / "cncbridge.log").write_text(
            "10:00:01 ERROR   POST https://mes.example/api/in?key=KEY-SECRET-5 failed: HTTP 503\n"
            "10:00:02 DEBUG   Authorization: Bearer tok-SECRET-1\n"
            "10:00:03 WARNING token tok-SECRET-1 was rejected; ftp login used ftp-SECRET-2 and mail-SECRET-3\n"
            f"10:00:04 INFO    header X-Api-Key is hdr-SECRET-4; the stored password hash is {PASSWORD_HASH}\n",
            encoding="utf-8")
        (logs / "window.log").write_text("11:00:00 INFO    Settings saved\n", encoding="utf-8")
        older = logs / "cncbridge.log.1"
        older.write_text("x" * 79 + "\n" * 1 + ("y" * 79 + "\n") * 80_000, encoding="utf-8")  # about 6.4 MB
        os.utime(older, (1_700_000_000, 1_700_000_000))
        (self.data / "alerts.json").write_text(json.dumps({"open": {"api": {
            "text": "Delivery to the API link is not possible: https://mes.example/api/in?key=KEY-SECRET-5",
            "since": "2026-10-21T09:00:00"}}}), encoding="utf-8")
        (self.data / "outbox" / "api").mkdir(parents=True)
        (self.data / "outbox" / "api" / "b1.json").write_text('{"marker": "CONTENT-MARKER-XYZ"}', encoding="utf-8")
        (self.data / "rejected" / "api").mkdir(parents=True)
        (self.data / "rejected" / "api" / "r1.json").write_text("CONTENT-MARKER-XYZ", encoding="utf-8")
        self.other = self.tmp / "otherline"
        (self.other).mkdir()
        (self.other / "config.json").write_text("{}", encoding="utf-8")
        stations.register(self.other)

    def build(self, **kwargs):
        return diagnostics.build(self.data, self.zip, now=datetime(2026, 10, 21, 10, 15), **kwargs)

    def read(self, name):
        with zipfile.ZipFile(self.zip) as z:
            return z.read(name).decode("utf-8")

    def everything(self):
        with zipfile.ZipFile(self.zip) as z:
            return {name: z.read(name).decode("utf-8", errors="replace") for name in z.namelist()}


class WhatIsInIt(Base):
    def test_every_part_is_there_with_the_summary_first(self):
        self.seed()
        self.build()
        with zipfile.ZipFile(self.zip) as z:
            names = z.namelist()
        self.assertEqual(names[0], "summary.txt")
        for expected in ("settings.json", "history.csv", "alerts.json", "settings_changes.jsonl", "files.txt",
                         "stations.txt", "logs/cncbridge.log", "logs/window.log", "logs/cncbridge.log.1"):
            self.assertIn(expected, names)

    def test_the_summary_says_what_support_asks_first(self):
        self.seed()
        self.build()
        text = self.read("summary.txt")
        for expected in ("Version:      1.2.0", "Station:      Line 7", f"Data folder:  {self.data}", "Running as:   stopped",
                         "Background task: not set up", "Destinations: shared folder, API link",
                         "Done: 505   Could not be read: 1", "Waiting to be sent: 1   Refused: 1",
                         "Created:      2026-10-21 10:15:00", "Open problems", "Delivery to the API link is not possible",
                         "Last settings change:", "Contents of this file", "history.csv"):
            self.assertIn(expected, text)

    def test_the_settings_are_there_without_the_passwords(self):
        self.seed()
        self.build()
        doc = json.loads(self.read("settings.json"))
        self.assertEqual(doc["settings"]["station_name"], "Line 7")
        self.assertEqual(doc["settings"]["api_enabled"], True)
        self.assertEqual(doc["secrets_set"], {"api_token": True, "alert_email_password": True, "ftp_password": True})
        self.assertTrue(doc["setup_password_set"])
        self.assertEqual(doc["settings"]["api_token"], "")
        self.assertEqual(doc["settings"]["api_headers"], "X-Api-Key: ***\nX-Plant: ***")
        self.assertEqual(doc["settings"]["api_url"], "https://mes.example/api/in?...")
        self.assertEqual(doc["settings"]["alert_webhook_url"], "https://hooks.example/t?...")
        self.assertEqual(doc["settings"]["setup_password"], "")

    def test_the_history_is_the_last_500_files_newest_first_with_every_destination(self):
        self.seed()
        self.build()
        rows = list(csv.DictReader(io.StringIO(self.read("history.csv"))))
        self.assertEqual(len(rows), 500)
        self.assertEqual(rows[0]["source"], "bad.csv")
        self.assertEqual((rows[0]["status"], rows[0]["shared"], rows[0]["api"]), ("failed", "off", "off"))
        self.assertEqual(rows[1]["source"], "b504.csv")
        self.assertEqual({r["api"] for r in rows if r["status"] == "ok"}, {"ok", "queued"})
        self.assertEqual(list(rows[0]), ["id", "processed_at", "source", "sha256", "size", "status", "output", "shared",
                                         "api", "ftp", "message"])

    def test_a_history_from_before_the_deliveries_table_is_read_too(self):
        import sqlite3
        self.data.mkdir(parents=True)
        con = sqlite3.connect(self.data / "ledger.db")
        con.execute("CREATE TABLE files(id INTEGER PRIMARY KEY, processed_at TEXT, source TEXT, sha256 TEXT, size INTEGER,"
                    " status TEXT, output TEXT, shared TEXT, api TEXT, message TEXT)")
        con.execute("INSERT INTO files(processed_at, source, status, shared, api) VALUES('2026-01-01T10:00:00', 'a.csv',"
                    " 'ok', 'ok', 'queued')")
        con.commit()
        con.close()
        self.build()
        row = next(csv.DictReader(io.StringIO(self.read("history.csv"))))
        self.assertEqual((row["source"], row["shared"], row["api"], row["ftp"]), ("a.csv", "ok", "queued", "off"))

    def test_the_queue_is_listed_by_name_and_never_by_content(self):
        self.seed()
        self.build()
        text = self.read("files.txt")
        self.assertIn("outbox\\api: 1 file(s)", text)
        self.assertIn("b1.json", text)
        self.assertIn("rejected\\api: 1 file(s)", text)
        self.assertIn("r1.json", text)
        self.assertNotIn("CONTENT-MARKER-XYZ", "".join(self.everything().values()))

    def test_the_other_copies_on_this_pc_are_listed(self):
        self.seed()
        self.build()
        self.assertIn(f"{self.other}  stopped", self.read("stations.txt"))

    def test_the_logs_are_capped_and_the_newest_come_first(self):
        self.seed()
        self.build()
        sizes = {n: len(t.encode("utf-8")) for n, t in self.everything().items() if n.startswith("logs/")}
        self.assertLessEqual(sum(sizes.values()), diagnostics.LOG_CAP + 200)
        self.assertGreater(sizes["logs/cncbridge.log.1"], 4_000_000)  # the old one is cut to what fits
        self.assertTrue(self.read("logs/cncbridge.log.1").startswith("(the older part of this file is left out)"))
        self.assertIn("Settings saved", self.read("logs/window.log"))  # the new ones are whole

    def test_a_name_for_the_file_that_tells_the_station_and_the_day(self):
        self.assertEqual(diagnostics.default_name({"station_name": "Line 7"}, datetime(2026, 10, 21)),
                         "CNCBridge diagnostics Line 7 2026-10-21.zip")
        self.assertEqual(diagnostics.default_name({"station_name": "A/B:1"}, datetime(2026, 10, 21)),
                         "CNCBridge diagnostics A_B_1 2026-10-21.zip")
        self.assertEqual(diagnostics.default_name({"station_name": ""}, datetime(2026, 10, 21)),
                         "CNCBridge diagnostics 2026-10-21.zip")


class NoPasswords(Base):
    def test_no_secret_is_anywhere_in_the_zip(self):
        self.seed()
        self.build()
        for name, text in self.everything().items():
            for secret in SECRETS:
                self.assertNotIn(secret, text, f"{secret} is in {name}")
            self.assertNotIn(PASSWORD_HASH, text, f"the password hash is in {name}")
            self.assertNotIn("dpapi:", text, f"an encrypted password is in {name}")

    def test_what_replaced_them_is_visible_in_the_log(self):
        self.seed()
        self.build()
        log = self.read("logs/cncbridge.log")
        self.assertIn("https://mes.example/api/in?... failed: HTTP 503", log)
        self.assertIn("Authorization: Bearer ***", log)
        self.assertIn("token *** was rejected; ftp login used *** and ***", log)
        self.assertIn("the stored password hash is ***", log)
        self.assertIn("https://mes.example/api/in?...", self.read("alerts.json"))

    def test_a_secret_is_found_even_when_the_settings_cannot_be_read(self):
        """Settings that are broken give no list of secrets, so the readable parts must still be scrubbed by form."""
        self.seed()
        (self.data / "config.json").write_text("not json at all", encoding="utf-8")
        self.build()
        for name, text in self.everything().items():
            self.assertNotIn("KEY-SECRET-5", text, name)  # a web address is scrubbed whatever the settings say
            self.assertNotIn("Bearer tok-SECRET-1", text, name)

    def test_redaction_rules(self):
        secrets = ["longer-secret-value", "abcd"]
        text = "token abcd and longer-secret-value; Authorization: Basic Zm9v ; GET http://h.example/p?a=1&b=2#frag ok"
        self.assertEqual(diagnostics.redact(text, secrets),
                         "token *** and ***; Authorization: Basic *** ; GET http://h.example/p?... ok")
        self.assertEqual(diagnostics.redact("no secrets here", secrets), "no secrets here")

    def test_which_values_count_as_secrets(self):
        cfg = config.normalize_config({"api_token": "abcdef", "ftp_password": "ab", "alert_email_password": "",
                                       "api_headers": "X-Key: longvalue\nbad line\nX-Short: ab", "setup_password": "hashvalue"})
        self.assertEqual(diagnostics.secret_values(cfg), ["hashvalue", "longvalue", "abcdef"])  # longest first (ties by name), short ones left out


class Harmless(Base):
    def snapshot(self):
        return {str(p.relative_to(self.tmp)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in self.tmp.rglob("*") if p.is_file() and p != self.zip and "out" not in p.parts}

    def test_making_it_changes_nothing_in_the_data_folder(self):
        self.seed()
        before = self.snapshot()
        self.build()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.data / "instance.lock").exists())  # the single-copy lock was not touched

    def test_an_empty_data_folder_still_gives_a_useful_file(self):
        self.data.mkdir()
        names = self.build()
        self.assertIn("summary.txt", names)
        self.assertEqual(self.read("history.csv"), "No history yet.\n")
        self.assertIn("Station:      (no name yet)", self.read("summary.txt"))
        self.assertFalse((self.data / "ledger.db").exists())  # looking did not create a history

    def test_one_part_that_fails_does_not_stop_the_others(self):
        self.seed()
        with mock.patch.object(diagnostics, "history_csv", side_effect=RuntimeError("boom")):
            names = self.build()
        self.assertNotIn("history.csv", names)
        self.assertIn("settings.json", names)
        self.assertIn("history.csv: boom", self.read("summary.txt"))
        self.assertIn("Parts that could not be collected", self.read("summary.txt"))

    def test_a_place_that_cannot_be_written_is_an_error_not_a_silent_failure(self):
        self.seed()
        blocker = self.tmp / "a file"
        blocker.write_text("x")
        with self.assertRaises(OSError):
            diagnostics.build(self.data, blocker / "inside.zip")


class FromTheCommandLine(Base):
    def test_the_zip_is_written_and_nothing_else_is(self):
        self.seed()
        before = sorted(p.name for p in self.data.iterdir())
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = entry.main(["--diagnostics", str(self.zip), "--data", str(self.data)])
        self.assertEqual(code, 0)
        self.assertIn("Diagnostics written to", out.getvalue())
        self.assertIn("summary.txt", zipfile.ZipFile(self.zip).namelist())
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), before)  # no log file, no lock, no station entry
        for name, text in self.everything().items():
            self.assertNotIn("tok-SECRET-1", text, name)

    def test_the_data_folder_is_found_without_creating_anything(self):
        explicit = self.tmp / "chosen"
        self.assertEqual(entry.existing_data_dir(explicit), explicit.resolve())
        shared, personal = self.tmp / "ProgramData", self.tmp / "Local"
        (personal / "CNCBridge").mkdir(parents=True)
        (personal / "CNCBridge" / "config.json").write_text("{}")
        with mock.patch.dict(os.environ, {"PROGRAMDATA": str(shared), "LOCALAPPDATA": str(personal)}):
            self.assertEqual(entry.existing_data_dir(), personal / "CNCBridge")  # the first place that has data
            (shared / "CNCBridge").mkdir(parents=True)
            (shared / "CNCBridge" / "ledger.db").write_bytes(b"")
            self.assertEqual(entry.existing_data_dir(), shared / "CNCBridge")
        self.assertEqual(sorted(p.name for p in shared.iterdir()), ["CNCBridge"])  # nothing new was made


if __name__ == "__main__":
    unittest.main()
