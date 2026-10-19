"""The list of CNCBridge copies on this PC, and the read-only look at each."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import background, config, engine, stations  # noqa: E402


def make_copy(root, name, station="", processed=0, failed=0, queued=0, mode=None, pid=None, problem=None,
              backup=True):
    """A data folder as a copy of CNCBridge leaves it."""
    folder = Path(root) / name
    (folder / "outbox" / "api").mkdir(parents=True)
    (folder / "rejected" / "api").mkdir(parents=True)
    machine = Path(root) / f"{name}-machine"
    machine.mkdir()
    cfg = {"station_name": station, "source_folder": str(machine), "backup_folder": str(Path(root) / f"{name}-backup")}
    config.save_config(config.normalize_config(cfg), folder / "config.json")
    ledger = engine.Ledger(folder / "ledger.db")
    for i in range(processed):
        ledger.add(source=f"ok{i}.csv", status="ok", output=f"o{i}.json", shared="off", api="sent")
    for i in range(failed):
        ledger.add(source=f"bad{i}.csv", status="failed")
    for i in range(queued):
        (folder / "outbox" / "api" / f"q{i}.json").write_text("{}")
    (machine / "waiting.csv").write_text("a,b\n")
    if mode:
        (folder / background.INSTANCE_FILE).write_text(json.dumps({"mode": mode, "pid": pid or os.getpid()}))
    if problem:
        (folder / "alerts.json").write_text(json.dumps({"open": {"api": {"text": problem, "since": 1}}}))
    return folder


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "stations.json"
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_register_is_idempotent_and_ignores_case_and_form(self):
        a = self.tmp / "Line1"
        a.mkdir()
        self.assertTrue(stations.register(a, self.path))
        self.assertFalse(stations.register(a, self.path))
        self.assertFalse(stations.register(str(a).upper(), self.path))
        self.assertFalse(stations.register(a / ".." / "Line1", self.path))
        self.assertEqual(len(stations.load(self.path)), 1)

    def test_unregister_removes_only_that_one(self):
        a, b = self.tmp / "A", self.tmp / "B"
        for f in (a, b):
            f.mkdir()
            stations.register(f, self.path)
        self.assertTrue(stations.unregister(a, self.path))
        self.assertFalse(stations.unregister(a, self.path))
        self.assertEqual([Path(e["folder"]).name for e in stations.load(self.path)], ["B"])

    def test_a_damaged_or_missing_file_is_an_empty_list(self):
        self.assertEqual(stations.load(self.path), [])
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(stations.load(self.path), [])
        self.path.write_text(json.dumps({"stations": ["x", {"folder": ""}, {"folder": "ok"}]}), encoding="utf-8")
        self.assertEqual([e["folder"] for e in stations.load(self.path)], ["ok"])
        self.assertTrue(stations.register(self.tmp / "New", self.path))  # and it can be written over

    def test_copies_starting_together_do_not_lose_each_other(self):
        folders = [self.tmp / f"Copy{i}" for i in range(12)]
        for f in folders:
            f.mkdir()
        threads = [threading.Thread(target=stations.register, args=(f, self.path)) for f in folders]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(Path(e["folder"]).name for e in stations.load(self.path)),
                         sorted(f.name for f in folders))

    def test_the_path_can_be_overridden(self):
        os.environ["CNCBRIDGE_STATIONS"] = str(self.path)
        self.addCleanup(os.environ.pop, "CNCBRIDGE_STATIONS", None)
        self.assertEqual(stations.registry_path(), self.path)

    def test_launch_command_opens_the_data_folder(self):
        command = stations.launch_command(r"C:\data\line2")
        self.assertEqual(command[-2:], ["--data", r"C:\data\line2"])
        self.assertTrue(command[0].lower().endswith((".exe", "python", "pythonw")))


class Reading(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_copy_is_described_from_its_own_files(self):
        folder = make_copy(self.tmp, "Line1", station="Line 1", processed=3, failed=1, queued=2,
                           problem="The API cannot be reached")
        row = stations.snapshot(folder)
        self.assertEqual((row["name"], row["processed"], row["failed"], row["queued"], row["waiting"],
                          row["rejected"]), ("Line 1", 3, 1, 2, 1, 0))
        self.assertEqual(row["problem"], "The API cannot be reached")
        self.assertIsNotNone(row["last_file"])
        self.assertIsNotNone(row["backup_free"])
        self.assertEqual(row["state"], "stopped")  # no marker file
        self.assertFalse(row["this"])

    def test_the_state_comes_from_a_live_process_not_from_a_leftover_marker(self):
        live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(live.kill)
        a = make_copy(self.tmp, "Win", mode="window", pid=live.pid)
        b = make_copy(self.tmp, "Bg", mode="background", pid=live.pid)
        c = make_copy(self.tmp, "Dead", mode="window", pid=4_000_000)
        self.assertEqual([stations.snapshot(f)["state"] for f in (a, b, c)], ["window", "background", "stopped"])
        live.kill()
        live.wait()
        self.assertEqual(stations.snapshot(a)["state"], "stopped")

    def test_reading_never_takes_the_copys_lock(self):
        folder = make_copy(self.tmp, "Line1", mode="window")
        lock = background.acquire_lock(folder)  # the copy itself holds its data folder
        self.assertIsNotNone(lock)
        try:
            self.assertEqual(stations.snapshot(folder)["state"], "window")
            self.assertEqual(background.holder(folder), "window")  # and it still does
        finally:
            lock.close()

    def test_reading_does_not_change_the_copy(self):
        folder = make_copy(self.tmp, "Line1", processed=2)
        before = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file())
        stations.snapshot(folder)
        stations.read_ledger(folder)
        self.assertEqual(sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                                for p in folder.rglob("*") if p.is_file()), before)

    def test_missing_empty_and_damaged_copies_are_described_not_raised(self):
        self.assertEqual(stations.snapshot(self.tmp / "gone")["state"], "missing")
        empty = self.tmp / "empty"
        empty.mkdir()
        row = stations.snapshot(empty)
        self.assertEqual((row["state"], row["processed"], row["name"]), ("stopped", 0, ""))
        damaged = make_copy(self.tmp, "Bad", processed=1)
        (damaged / "ledger.db").write_bytes(b"this is not a database")
        (damaged / "config.json").write_text("{ nope", encoding="utf-8")
        self.assertEqual(stations.snapshot(damaged)["processed"], 0)

    def test_snapshots_list_the_registry_and_this_copy(self):
        a = make_copy(self.tmp, "A", station="A")
        b = make_copy(self.tmp, "B", station="B")
        path = self.tmp / "stations.json"
        stations.register(a, path)
        rows = stations.snapshots(this_folder=b, path=path)
        self.assertEqual([(r["name"], r["this"]) for r in rows], [("B", True), ("A", False)])  # this copy even unlisted
        stations.register(b, path)
        self.assertEqual([r["name"] for r in stations.snapshots(this_folder=b, path=path)], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
