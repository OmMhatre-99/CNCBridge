"""The history database: one row per file, one row per file and destination."""
import logging
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import engine  # noqa: E402

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)

OLD_TABLE = """CREATE TABLE files(id INTEGER PRIMARY KEY, processed_at TEXT, source TEXT, sha256 TEXT, size INTEGER,
               status TEXT, output TEXT, shared TEXT, api TEXT, {extra}message TEXT)"""


class LedgerTable(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "ledger.db"

    def query(self, sql, *args):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def old_history(self, with_ftp):
        con = sqlite3.connect(self.db)
        con.execute(OLD_TABLE.format(extra="ftp TEXT DEFAULT 'off', " if with_ftp else ""))
        con.execute("INSERT INTO files(processed_at, source, output, status, shared, api) VALUES"
                    "('2026-01-01T10:00:00', 'a.csv', 'a.json', 'ok', 'ok', 'queued')")
        con.execute("INSERT INTO files(processed_at, source, output, status, shared, api) VALUES"
                    "('2026-01-02T10:00:00', 'b.csv', 'b.json', 'ok', 'off', 'rejected (HTTP 422)')")
        con.commit()
        con.close()

    def test_a_new_history_has_a_destination_row_per_file_and_kind(self):
        ledger = engine.Ledger(self.db)
        file_id = ledger.add(source="a.csv", output="a.json", status="ok", shared="queued", api="off", ftp="off")
        self.assertEqual(self.query("SELECT kind, status FROM deliveries WHERE file_id=? ORDER BY kind", file_id),
                         [("api", "off"), ("ftp", "off"), ("shared", "queued")])
        self.assertEqual(self.query("PRAGMA user_version"), [(engine.Ledger.SCHEMA,)])
        self.assertNotIn("shared", [r[1] for r in self.query("PRAGMA table_info(files)")])

    def test_recent_returns_a_key_per_destination(self):
        ledger = engine.Ledger(self.db)
        ledger.add(source="a.csv", output="a.json", status="ok", shared="ok")
        ledger.add(source="dup.csv", status="duplicate")  # no destinations at all
        newest, older = ledger.recent()
        self.assertEqual((newest["shared"], newest["api"], newest["ftp"]), ("off", "off", "off"))
        self.assertEqual((older["shared"], older["api"], older["ftp"]), ("ok", "off", "off"))

    def test_a_status_changes_and_reaches_the_row(self):
        ledger = engine.Ledger(self.db)
        ledger.add(source="a.csv", output="a.json", status="ok", api="queued")
        ledger.set_delivery("a.json", "api", "rejected (HTTP 422)")
        self.assertEqual(ledger.recent()[0]["api"], "rejected (HTTP 422)")
        ledger.set_delivery("a.json", "api", "queued")
        ledger.set_delivery("a.json", "api", "ok")
        self.assertEqual(ledger.recent()[0]["api"], "ok")
        self.assertEqual(self.query("SELECT COUNT(*) FROM deliveries"), [(1,)])

    def test_a_new_kind_of_destination_needs_no_new_column(self):
        ledger = engine.Ledger(self.db)
        ledger.add(source="a.csv", output="a.json", status="ok", shared="ok", sftp="queued")
        ledger.set_delivery("a.json", "sftp", "ok")
        row = ledger.recent()[0]
        self.assertEqual((row["sftp"], row["shared"], row["api"]), ("ok", "ok", "off"))

    def test_an_old_history_is_copied_once(self):
        for with_ftp in (True, False):
            with self.subTest(with_ftp=with_ftp):
                self.db.unlink(missing_ok=True)
                self.old_history(with_ftp)
                ledger = engine.Ledger(self.db)
                newest, older = ledger.recent()
                self.assertEqual((older["shared"], older["api"], older["ftp"]), ("ok", "queued", "off"))
                self.assertEqual((newest["shared"], newest["api"]), ("off", "rejected (HTTP 422)"))
                self.assertEqual(self.query("PRAGMA user_version"), [(engine.Ledger.SCHEMA,)])
                count = self.query("SELECT COUNT(*) FROM deliveries")
                engine.Ledger(self.db)  # opening it again changes nothing
                self.assertEqual(self.query("SELECT COUNT(*) FROM deliveries"), count)

    def test_after_the_copy_new_work_goes_to_the_new_table(self):
        self.old_history(with_ftp=True)
        ledger = engine.Ledger(self.db)
        ledger.set_delivery("a.json", "api", "ok")
        ledger.add(source="c.csv", output="c.json", status="ok", ftp="queued")
        rows = {r["source"]: r for r in ledger.recent()}
        self.assertEqual((rows["a.csv"]["api"], rows["c.csv"]["ftp"]), ("ok", "queued"))
        self.assertEqual(self.query("SELECT api FROM files WHERE source='a.csv'"), [("queued",)])  # old column untouched

    def test_forgetting_old_rows_removes_their_destinations_too(self):
        ledger = engine.Ledger(self.db)
        ledger.add(processed_at="2026-01-01T00:00:00", source="old.csv", output="old.json", status="ok", shared="ok")
        ledger.add(processed_at="2026-03-01T00:00:00", source="new.csv", output="new.json", status="ok", shared="ok")
        self.assertEqual(ledger.forget_before(datetime(2026, 2, 1)), 1)
        self.assertEqual(self.query("SELECT COUNT(*) FROM deliveries"), [(1,)])
        self.assertEqual([r["source"] for r in ledger.recent()], ["new.csv"])

    def test_an_empty_history_gives_an_empty_list(self):
        self.assertEqual(engine.Ledger(self.db).recent(), [])


if __name__ == "__main__":
    unittest.main()
