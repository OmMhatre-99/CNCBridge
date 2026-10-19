"""FTP / FTPS delivery, against a small FTP server written for the tests (control and passive data connections)."""
import ftplib
import os
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cncbridge import alerts, config, engine  # noqa: E402
from test_engine import SAMPLE, sel  # noqa: E402


class FakeFtpServer:
    """Just enough FTP for ftplib: USER PASS TYPE PWD CWD MKD PASV STOR NLST RNFR RNTO DELE QUIT."""

    def __init__(self, root, user="op", password="secret"):
        self.root, self.user, self.password = Path(root), user, password
        self.refuse_stor = {}       # file name -> reply line, e.g. {"x.json": "553 file name not allowed"}
        self.log = []               # every command received
        self.logins = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                outer._session(self)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ------------------------------------------------------------ one control connection
    def _session(self, h):
        cwd, user_ok, name, passive, rnfr = "/", False, None, None, None

        def send(line):
            h.wfile.write((line + "\r\n").encode("utf-8"))
            h.wfile.flush()

        def real(path):
            joined = (cwd.rstrip("/") + "/" + path) if not path.startswith("/") else path
            return self.root.joinpath(*[p for p in joined.split("/") if p])

        send("220 fake ftp ready")
        while True:
            line = h.rfile.readline().decode("utf-8", "replace").strip()
            if not line:
                return
            cmd, _, arg = line.partition(" ")
            cmd = cmd.upper()
            self.log.append(line if cmd != "PASS" else "PASS ****")
            if cmd == "USER":
                name = arg
                send("331 password please")
            elif cmd == "PASS":
                if name == self.user and arg == self.password:
                    user_ok = True
                    self.logins.append(name)
                    send("230 logged in")
                else:
                    send("530 login incorrect")
            elif not user_ok and cmd != "QUIT":
                send("530 please log in")
            elif cmd == "TYPE":
                send("200 type set")
            elif cmd == "PWD":
                send(f'257 "{cwd}" is the current directory')
            elif cmd == "CWD":
                target = real(arg)
                if target.is_dir():
                    cwd = ("/" + "/".join(target.relative_to(self.root).parts)) if target != self.root else "/"
                    send("250 ok")
                else:
                    send("550 no such folder")
            elif cmd == "MKD":
                try:
                    real(arg).mkdir()
                    send(f'257 "{arg}" created')
                except OSError:
                    send("550 cannot create")
            elif cmd == "PASV":
                if passive:
                    passive.close()
                passive = socket.socket()
                passive.bind(("127.0.0.1", 0))
                passive.listen(1)
                passive.settimeout(10)
                port = passive.getsockname()[1]
                send(f"227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255})")
            elif cmd == "STOR":
                reply = self.refuse_stor.get(arg) or self.refuse_stor.get(arg.lstrip(".").removesuffix(".part"))
                if reply:
                    send(reply)
                    continue
                send("150 send it")
                data, _ = passive.accept()
                with open(real(arg), "wb") as fh:
                    while True:
                        chunk = data.recv(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                data.close()
                send("226 stored")
            elif cmd == "NLST":
                send("150 listing")
                data, _ = passive.accept()
                folder = real(arg) if arg else real(".")
                data.sendall("".join(p.name + "\r\n" for p in sorted(folder.iterdir())).encode("utf-8"))
                data.close()
                send("226 done")
            elif cmd == "RNFR":
                rnfr = real(arg)
                send("350 ready" if rnfr.exists() else "550 no such file")
            elif cmd == "RNTO":
                rnfr.rename(real(arg))
                send("250 renamed")
            elif cmd == "DELE":
                try:
                    real(arg).unlink()
                    send("250 deleted")
                except OSError:
                    send("550 no such file")
            elif cmd == "QUIT":
                send("221 bye")
                return
            else:
                send("502 not implemented")


class FtpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "ftproot"
        self.root.mkdir()
        self.server = FakeFtpServer(self.root)
        self.addCleanup(self.server.close)
        (self.tmp / "machine").mkdir()
        self.cfg = config.normalize_config({
            "station_name": "Line 7", "source_folder": str(self.tmp / "machine"),
            "backup_folder": str(self.tmp / "backup"), "selections": [sel("A12:I*"), sel("B6")],
            "stable_seconds": 0, "ftp_enabled": True, "ftp_host": "127.0.0.1", "ftp_port": self.server.port,
            "ftp_user": "op", "ftp_password": "secret", "ftp_folder": "/incoming/line7", "api_timeout": 5})
        self.out = self.tmp / "out.json"
        self.out.write_text('{"hello": "world"}', encoding="utf-8")

    def uploaded(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())

    def drop(self, name, serial):
        (self.tmp / "machine" / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))


class Upload(FtpCase):
    def test_upload_creates_the_folder_and_leaves_only_the_finished_file(self):
        engine.send_ftp(self.out, self.cfg)
        self.assertEqual(self.uploaded(), ["incoming/line7/out.json"])
        self.assertEqual((self.root / "incoming" / "line7" / "out.json").read_text(), '{"hello": "world"}')
        self.assertIn("RNTO out.json", self.server.log)
        self.assertTrue(any(c.startswith("STOR .out.json.part") for c in self.server.log))  # uploaded under a . name
        self.assertEqual(self.server.logins, ["op"])

    def test_a_name_that_is_taken_gets_a_number(self):
        engine.send_ftp(self.out, self.cfg)
        engine.send_ftp(self.out, self.cfg)
        engine.send_ftp(self.out, self.cfg)
        self.assertEqual(self.uploaded(), ["incoming/line7/out.json", "incoming/line7/out_1.json",
                                           "incoming/line7/out_2.json"])

    def test_an_empty_folder_setting_uses_the_login_folder(self):
        engine.send_ftp(self.out, dict(self.cfg, ftp_folder=""))
        self.assertEqual(self.uploaded(), ["out.json"])

    def test_the_test_button_uploads_and_removes_a_probe(self):
        self.assertEqual(engine.test_ftp(self.cfg), "/incoming/line7")
        self.assertEqual(self.uploaded(), [])
        self.assertTrue(any(c.startswith("DELE .cncbridge_probe_") for c in self.server.log))

    def test_wrong_password_is_a_settings_problem_not_a_refused_file(self):
        with self.assertRaises(RuntimeError) as caught:
            engine.send_ftp(self.out, dict(self.cfg, ftp_password="wrong"))
        self.assertIn("refused the login", str(caught.exception))
        with self.assertRaises(RuntimeError):
            engine.test_ftp(dict(self.cfg, ftp_password="wrong"))

    def test_an_unreachable_server_is_retried_not_rejected(self):
        with self.assertRaises(RuntimeError) as caught:
            engine.send_ftp(self.out, dict(self.cfg, ftp_port=1, api_timeout=2))
        self.assertIn("cannot reach", str(caught.exception))

    def test_a_refused_file_name_is_a_rejected_file(self):
        self.server.refuse_stor["out.json"] = "553 file name not allowed"
        with self.assertRaises(engine.Rejected) as caught:
            engine.send_ftp(self.out, self.cfg)
        self.assertIn("553", str(caught.exception))
        self.assertEqual(self.uploaded(), [])

    def test_other_permanent_errors_keep_the_file_queued(self):
        self.server.refuse_stor["out.json"] = "550 permission denied"
        with self.assertRaises(RuntimeError):
            engine.send_ftp(self.out, self.cfg)  # a permissions problem is for the administrator, not this file


class Secure(FtpCase):
    def test_ftps_secures_the_data_channel_after_login(self):
        calls = []

        class FakeTls(ftplib.FTP):
            def login(self, *a, **k):
                calls.append("login")
                return super().login(*a, **k)

            def prot_p(self):
                calls.append("prot_p")
                return "200 protected"

        original = engine.ftplib.FTP_TLS
        self.addCleanup(setattr, engine.ftplib, "FTP_TLS", original)
        engine.ftplib.FTP_TLS = FakeTls
        engine.send_ftp(self.out, dict(self.cfg, ftp_tls=True))
        self.assertEqual(calls, ["login", "prot_p"])
        self.assertEqual(self.uploaded(), ["incoming/line7/out.json"])
        calls.clear()
        engine.send_ftp(self.out, dict(self.cfg, ftp_tls=False))  # plain FTP never asks for protection
        self.assertEqual(calls, [])


class Pipeline(FtpCase):
    """The queue, retries and the ledger treat FTP like the other destinations."""

    def setUp(self):
        super().setUp()
        self.sent = []
        for name in ("notify", "webhook", "email"):
            original = alerts.SENDERS[name]
            self.addCleanup(alerts.SENDERS.__setitem__, name, original)
            alerts.SENDERS[name] = lambda *a, _n=name, **k: self.sent.append((_n, a))

    def engine(self, **overrides):
        return engine.Engine(dict(self.cfg, **overrides), self.tmp / "data")

    def test_a_file_goes_through_the_queue_to_the_server(self):
        eng = self.engine()
        self.drop("a.csv", "S1")
        eng.scan_once()
        row = eng.ledger.recent()[0]
        self.assertEqual((row["status"], row["ftp"], row["shared"], row["api"]), ("ok", "ok", "off", "off"))
        self.assertEqual(len(self.uploaded()), 1)
        self.assertEqual(engine.count_queued(self.tmp / "data"), {"shared": 0, "api": 0, "ftp": 0})

    def test_a_server_that_is_down_keeps_the_files_queued_in_order_then_sends_them(self):
        eng = self.engine(alert_after_minutes=0)
        self.server.close()
        for i, serial in enumerate(("S1", "S2")):
            self.drop(f"{i}.csv", serial)
        eng.scan_once()
        self.assertEqual(engine.count_queued(self.tmp / "data")["ftp"], 2)
        self.assertEqual({r["ftp"] for r in eng.ledger.recent()}, {"queued"})
        self.assertIn("ftp", alerts.read_state(self.tmp / "data"))
        self.server = FakeFtpServer(self.root)  # back again, on another port
        self.addCleanup(self.server.close)
        eng.cfg = dict(eng.cfg, ftp_port=self.server.port)
        eng.scan_once()
        self.assertEqual(engine.count_queued(self.tmp / "data")["ftp"], 0)
        self.assertEqual({r["ftp"] for r in eng.ledger.recent()}, {"ok"})
        self.assertEqual(len(self.uploaded()), 2)
        self.assertEqual(alerts.read_state(self.tmp / "data"), {})

    def test_a_refused_file_is_set_aside_and_can_be_sent_again(self):
        eng = self.engine()
        self.server.refuse_stor["x"] = "553 no"  # placeholder; the real name is only known after processing
        self.drop("a.csv", "S1")
        eng.process_file(self.tmp / "machine" / "a.csv")
        queued = next((self.tmp / "data" / "outbox" / "ftp").glob("*"))
        self.server.refuse_stor[queued.name] = "553 file name not allowed"
        eng.drain_outbox()
        row = eng.ledger.recent()[0]
        self.assertTrue(row["ftp"].startswith("rejected"))
        self.assertEqual(engine.count_rejected(self.tmp / "data"), 1)
        self.assertEqual(len(list((self.tmp / "data" / "rejected" / "ftp").glob("*"))), 1)
        self.server.refuse_stor.clear()
        self.assertEqual(engine.resend_rejected(eng.data, row, eng.ledger), ["ftp"])
        eng.drain_outbox()
        self.assertEqual(eng.ledger.recent()[0]["ftp"], "ok")
        self.assertEqual(len(self.uploaded()), 1)

    def test_with_ftp_off_nothing_is_attempted_and_the_ledger_says_off(self):
        eng = self.engine(ftp_enabled=False, shared_enabled=True, shared_folder=str(self.tmp / "shared"))
        self.drop("a.csv", "S1")
        eng.scan_once()
        row = eng.ledger.recent()[0]
        self.assertEqual((row["shared"], row["ftp"]), ("ok", "off"))
        self.assertEqual(self.server.log, [])  # the server was never contacted

    def test_an_old_history_gains_the_ftp_column(self):
        import sqlite3
        db = self.tmp / "old" / "ledger.db"
        db.parent.mkdir()
        con = sqlite3.connect(db)
        con.execute("""CREATE TABLE files(id INTEGER PRIMARY KEY, processed_at TEXT, source TEXT, sha256 TEXT,
                       size INTEGER, status TEXT, output TEXT, shared TEXT, api TEXT, message TEXT)""")
        con.execute("INSERT INTO files(processed_at, source, status, shared, api) VALUES('2026-01-01T00:00:00', "
                    "'old.csv', 'ok', 'ok', 'off')")
        con.commit()
        con.close()
        ledger = engine.Ledger(db)
        self.assertEqual(ledger.recent()[0]["ftp"], "off")
        ledger.add(source="new.csv", status="ok", ftp="queued")
        self.assertEqual(ledger.recent()[0]["ftp"], "queued")


class Settings(unittest.TestCase):
    def test_validate(self):
        base = config.normalize_config({"source_folder": str(Path(tempfile.gettempdir())),
                                        "backup_folder": str(Path(tempfile.gettempdir()) / "cncbridge_bak_x"),
                                        "selections": [sel("A1:B2")]})
        self.assertIn("Turn on at least one delivery: shared folder, API link or FTP server.", engine.validate(base))
        on = dict(base, ftp_enabled=True)
        self.assertIn("Enter the FTP server address.", engine.validate(on))
        self.assertEqual([p for p in engine.validate(dict(on, ftp_host="ftp.example.com")) if "FTP" in p or "delivery" in p],
                         [])

    def test_password_is_encrypted_on_disk_and_never_exported(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = config.normalize_config({"ftp_host": "h", "ftp_password": "hunter2"})
        config.save_config(cfg, tmp / "config.json")
        self.assertNotIn("hunter2", (tmp / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config.load_config(tmp / "config.json")["ftp_password"], "hunter2")
        config.export_settings(cfg, tmp / "export.json")
        self.assertNotIn("hunter2", (tmp / "export.json").read_text(encoding="utf-8"))

    def test_values_are_cleaned(self):
        cfg = config.normalize_config({"ftp_port": "2121", "ftp_host": " ftp.x ", "ftp_tls": 1})
        self.assertEqual((cfg["ftp_port"], cfg["ftp_host"], cfg["ftp_tls"], cfg["ftp_passive"], cfg["ftp_enabled"]),
                         (2121, "ftp.x", True, True, False))
        self.assertEqual(config.normalize_config({"ftp_port": 99999})["ftp_port"], 21)
        self.assertEqual(config.normalize_config({"ftp_port": "x"})["ftp_port"], 21)


if __name__ == "__main__":
    unittest.main()
