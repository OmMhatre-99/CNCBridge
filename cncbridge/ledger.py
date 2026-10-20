"""The history database: one row per file, one row per file and destination."""

import contextlib
import sqlite3
import threading
from datetime import datetime
from pathlib import Path


class Ledger:
    """
    SQLite record of every file: fingerprint, outcome, delivery status. History + duplicate guard.

    One row per file in `files`, and one row per file and destination in `deliveries`, so a new
    destination needs no new column. Histories made before that kept the status in the columns
    shared / api / ftp of `files`; they are copied over once (PRAGMA user_version tells whether that
    happened). The old columns are left where they are and are no longer written.
    """

    SCHEMA = 1
    LEGACY_KINDS = ("shared", "api", "ftp")  # always present in a row from recent(), "off" when unused
    FILE_COLUMNS = ("processed_at", "source", "sha256", "size", "status", "output", "message")

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS files(
                id INTEGER PRIMARY KEY, processed_at TEXT, source TEXT, sha256 TEXT, size INTEGER,
                status TEXT, output TEXT, message TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS deliveries(
                file_id INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT, updated_at TEXT,
                PRIMARY KEY(file_id, kind))""")
            c.execute("CREATE INDEX IF NOT EXISTS ix_sha ON files(sha256)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_output ON files(output)")
            if c.execute("PRAGMA user_version").fetchone()[0] < self.SCHEMA:
                self._copy_legacy_columns(c)
                c.execute(f"PRAGMA user_version = {int(self.SCHEMA)}")

    @staticmethod
    def _copy_legacy_columns(c):
        """Histories from before the deliveries table: copy shared / api / ftp over (safe to repeat)."""
        have = {row[1] for row in c.execute("PRAGMA table_info(files)")}
        for kind in Ledger.LEGACY_KINDS:
            if kind in have:
                c.execute(f"INSERT OR IGNORE INTO deliveries(file_id, kind, status, updated_at) "
                          f"SELECT id, '{kind}', {kind}, processed_at FROM files WHERE {kind} IS NOT NULL")

    @contextlib.contextmanager
    def _db(self):
        with self._lock:
            con = sqlite3.connect(self.path, timeout=10)
            try:
                yield con
                con.commit()
            finally:
                con.close()

    def seen(self, sha):
        with self._db() as c:
            return c.execute("SELECT 1 FROM files WHERE sha256=? AND status='ok' LIMIT 1", (sha,)).fetchone() is not None

    def add(self, **row):
        """Record a file. Keys that are not columns of `files` are destinations: add(shared="ok", api="off")."""
        row.setdefault("processed_at", datetime.now().isoformat(timespec="seconds"))
        columns = {k: v for k, v in row.items() if k in self.FILE_COLUMNS}
        kinds = {k: v for k, v in row.items() if k not in self.FILE_COLUMNS}
        with self._db() as c:
            cursor = c.execute(f"INSERT INTO files({','.join(columns)}) VALUES({','.join('?' * len(columns))})",
                               list(columns.values()))
            for kind, status in kinds.items():
                c.execute("INSERT OR REPLACE INTO deliveries(file_id, kind, status, updated_at) VALUES(?,?,?,?)",
                          (cursor.lastrowid, kind, status, row["processed_at"]))
            return cursor.lastrowid

    def set_delivery(self, output, kind, status):
        now = datetime.now().isoformat(timespec="seconds")
        with self._db() as c:
            for (file_id,) in c.execute("SELECT id FROM files WHERE output=?", (output,)).fetchall():
                c.execute("INSERT OR REPLACE INTO deliveries(file_id, kind, status, updated_at) VALUES(?,?,?,?)",
                          (file_id, kind, status, now))

    def note(self, row_id, text):
        """Add a remark to a file's row, e.g. that it was sent back for another try."""
        with self._db() as c:
            c.execute("UPDATE files SET message = CASE WHEN message IS NULL OR message = '' THEN ? "
                      "ELSE message || '; ' || ? END WHERE id=?", (text, text, row_id))

    def stats(self):
        with self._db() as c:
            return dict(c.execute("SELECT status, COUNT(*) FROM files GROUP BY status").fetchall())

    def forget_before(self, cutoff):
        """Delete history rows processed before this time. Returns how many."""
        stamp = cutoff.isoformat(timespec="seconds")
        with self._db() as c:
            c.execute("DELETE FROM deliveries WHERE file_id IN (SELECT id FROM files WHERE processed_at < ?)", (stamp,))
            return c.execute("DELETE FROM files WHERE processed_at < ?", (stamp,)).rowcount

    def last_processed(self):
        """When the latest file was handled (ISO text), or None."""
        with self._db() as c:
            row = c.execute("SELECT processed_at FROM files ORDER BY id DESC LIMIT 1").fetchone()
            return row[0] if row else None

    def recent(self, n=200):
        """The latest files, newest first. Each row has one key per destination: its status, or "off"."""
        with self._db() as c:
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute("SELECT * FROM files ORDER BY id DESC LIMIT ?", (n,))]
            if not rows:
                return rows
            by_file = {}
            for r in c.execute(f"SELECT file_id, kind, status FROM deliveries WHERE file_id IN "
                               f"({','.join('?' * len(rows))})", [row["id"] for row in rows]):
                by_file.setdefault(r["file_id"], {})[r["kind"]] = r["status"]
            kinds = set(self.LEGACY_KINDS).union(*(d.keys() for d in by_file.values()))
            for row in rows:
                saved = by_file.get(row["id"], {})
                for kind in kinds:  # a row copied from an older program may still hold its status in the old column
                    row[kind] = saved.get(kind) or row.get(kind) or "off"
            return rows
