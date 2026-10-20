"""The set-up program: install, update and uninstall into temporary folders and a test registry key."""
import contextlib
import ctypes
import io
import os
import subprocess
import sys
import tempfile
import unittest
import winreg
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from installer import cli, core  # noqa: E402

TEST_KEY = r"Software\CNCBridgeTest"
# Windows refuses to make a shortcut to a file that is not a real program, so the test programs are a real
# (tiny) Windows program with a few bytes of their own added to the end, which Windows ignores.
REAL_EXE = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe").read_bytes()


def fake_exe(label):
    return REAL_EXE + label.encode()


def delete_registry_tree(path=TEST_KEY):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                delete_registry_tree(path + "\\" + child)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def hold_open(path):
    """Open a file so that nobody else may replace or delete it, as Windows does for a running program."""
    kernel = ctypes.windll.kernel32
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)  # read, no sharing, open existing
    if not handle or handle == 0xFFFFFFFFFFFFFFFF:  # INVALID_HANDLE_VALUE
        raise OSError(f"could not hold {path}")
    return handle


def release(handle):
    ctypes.windll.kernel32.CloseHandle(handle)


def shortcut_target(path):
    script = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{path}').TargetPath"
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                            creationflags=core.NO_WINDOW)
    return result.stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self.cleanup)
        delete_registry_tree()
        self.where = core.locations(self.tmp / "machine")
        self.payload = self.tmp / "payload"
        self.payload.mkdir()
        (self.payload / "CNCBridge.exe").write_bytes(fake_exe("app"))
        (self.payload / "CNCBridgeDemo.exe").write_bytes(fake_exe("demo"))
        (self.payload / "README.md").write_text("# CNCBridge\n", encoding="utf-8")
        self.setup_exe = self.tmp / "CNCBridgeSetup.exe"
        self.setup_exe.write_bytes(fake_exe("setup"))
        self.said = []
        self.removed_tasks = []

    def cleanup(self):
        import shutil
        delete_registry_tree()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def installer(self, **overrides):
        parts = dict(payload=self.payload, program_dir=self.where["program"], data_dir=self.where["data"],
                     menu_dir=self.where["menu"], desktop_dir=self.where["desktop"], setup_exe=self.setup_exe,
                     registry=self.where["registry"], report=self.said.append,
                     remove_tasks=self.removed_tasks.append, eventlog=self.where["eventlog"])
        parts.update(overrides)
        return core.Installer(**parts)


class FullInstall(Base):
    """One real installation with shortcuts, looked at from every side (shortcuts take a few seconds)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp())
        delete_registry_tree()
        cls.where = core.locations(cls._tmp / "machine")
        cls.payload = cls._tmp / "payload"
        cls.payload.mkdir()
        (cls.payload / "CNCBridge.exe").write_bytes(fake_exe("app"))
        (cls.payload / "CNCBridgeDemo.exe").write_bytes(fake_exe("demo"))
        (cls.payload / "README.md").write_text("# CNCBridge\n", encoding="utf-8")
        cls.setup_exe = cls._tmp / "CNCBridgeSetup.exe"
        cls.setup_exe.write_bytes(fake_exe("setup"))
        cls.said = []
        cls.installer_ = core.Installer(cls.payload, cls.where["program"], cls.where["data"], cls.where["menu"],
                                        cls.where["desktop"], setup_exe=cls.setup_exe, registry=cls.where["registry"],
                                        report=cls.said.append, remove_tasks=lambda d: None, version="9.9.9",
                                        eventlog=cls.where["eventlog"])
        cls.exe = cls.installer_.install()

    @classmethod
    def tearDownClass(cls):
        import shutil
        delete_registry_tree()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):  # the class does the installing
        pass

    def test_the_files_are_in_place(self):
        program = self.where["program"]
        self.assertEqual(self.exe, program / "CNCBridge.exe")
        self.assertEqual(sorted(p.name for p in program.iterdir()),
                         ["CNCBridge.exe", "CNCBridgeDemo.exe", "CNCBridgeSetup.exe", "README.md"])
        self.assertEqual((program / "CNCBridge.exe").read_bytes(), (self.payload / "CNCBridge.exe").read_bytes())
        self.assertTrue(self.where["data"].is_dir())

    def test_shortcuts_point_at_the_installed_programs(self):
        program, menu = self.where["program"], self.where["menu"] / "CNCBridge"
        self.assertEqual(sorted(p.name for p in menu.iterdir()),
                         ["CNCBridge demo.lnk", "CNCBridge.lnk", "Uninstall CNCBridge.lnk"])
        self.assertEqual(shortcut_target(menu / "CNCBridge.lnk"), str(program / "CNCBridge.exe"))
        self.assertEqual(shortcut_target(menu / "Uninstall CNCBridge.lnk"), str(program / "CNCBridgeSetup.exe"))
        self.assertEqual(shortcut_target(self.where["desktop"] / "CNCBridge.lnk"), str(program / "CNCBridge.exe"))

    def test_windows_lists_it_among_the_installed_apps(self):
        program = self.where["program"]
        with winreg.OpenKey(*self.where["registry"]) as key:
            value = lambda name: winreg.QueryValueEx(key, name)[0]  # noqa: E731
            self.assertEqual(value("DisplayName"), "CNCBridge 9.9.9")
            self.assertEqual(value("DisplayVersion"), "9.9.9")
            self.assertEqual(value("InstallLocation"), str(program))
            self.assertEqual(value("UninstallString"), f'"{program / "CNCBridgeSetup.exe"}" --uninstall')
            self.assertEqual(value("QuietUninstallString"), f'"{program / "CNCBridgeSetup.exe"}" --uninstall /S')
            self.assertGreater(value("EstimatedSize"), 0)
            self.assertEqual((value("NoModify"), value("NoRepair")), (1, 1))
        self.assertEqual(core.installed_location(self.where["registry"]), (program, "9.9.9"))

    def test_the_windows_event_log_knows_the_program(self):
        with winreg.OpenKey(*self.where["eventlog"]) as key:
            self.assertEqual(winreg.QueryValueEx(key, "TypesSupported")[0], 7)
            message_file = winreg.QueryValueEx(key, "EventMessageFile")
        self.assertTrue(message_file[0].endswith("EventLogMessages.dll"))
        self.assertEqual(message_file[1], winreg.REG_EXPAND_SZ)  # %SystemRoot% is expanded by Windows
        self.assertTrue(Path(os.path.expandvars(message_file[0])).is_file())

    def test_ordinary_users_may_write_the_data_folder(self):
        listing = subprocess.run(["icacls", str(self.where["data"])], capture_output=True, text=True).stdout
        self.assertIn("(OI)(CI)(M)", listing)  # the Users group: modify, inherited by everything in it

    def test_the_steps_were_reported(self):
        text = "\n".join(self.said)
        for expected in ("Copying the program files", "CNCBridge.exe", "Creating shortcuts", "list of installed apps",
                         "CNCBridge 9.9.9 is installed"):
            self.assertIn(expected, text)


class Install(Base):
    def test_without_shortcuts_none_are_made(self):
        self.installer().install(menu_shortcuts=False, desktop_shortcut=False)
        self.assertFalse(self.where["menu"].exists())
        self.assertFalse(self.where["desktop"].exists())
        self.assertTrue((self.where["program"] / "CNCBridge.exe").exists())

    def test_the_demo_and_the_uninstaller_are_optional(self):
        (self.payload / "CNCBridgeDemo.exe").unlink()
        self.installer(setup_exe=None).install(menu_shortcuts=False, desktop_shortcut=False)
        self.assertEqual(sorted(p.name for p in self.where["program"].iterdir()), ["CNCBridge.exe", "README.md"])

    def test_a_payload_without_the_program_is_refused(self):
        (self.payload / "CNCBridge.exe").unlink()
        with self.assertRaises(core.InstallError):
            self.installer().install(False, False)
        self.assertFalse(self.where["program"].exists())

    def test_installing_again_updates_the_program_and_keeps_the_data(self):
        self.installer().install(False, False)
        (self.where["data"] / "config.json").write_text('{"station_name": "Line 7"}')
        (self.where["data"] / "ledger.db").write_bytes(b"history")
        (self.payload / "CNCBridge.exe").write_bytes(fake_exe("new version"))
        self.installer(version="2.0").install(False, False)
        self.assertEqual((self.where["program"] / "CNCBridge.exe").read_bytes(), fake_exe("new version"))
        self.assertEqual((self.where["data"] / "config.json").read_text(), '{"station_name": "Line 7"}')
        self.assertEqual((self.where["data"] / "ledger.db").read_bytes(), b"history")
        self.assertEqual(core.installed_location(self.where["registry"])[1], "2.0")

    def test_a_running_program_cannot_be_replaced_and_the_message_says_so(self):
        installer = self.installer()
        installer.install(False, False)
        handle = hold_open(self.where["program"] / "CNCBridge.exe")
        try:
            with self.assertRaises(core.InUse) as caught:
                installer.install(False, False)
            self.assertIn("close CNCBridge", str(caught.exception))
        finally:
            release(handle)
        installer.install(False, False)  # and it works again once the program is closed

    def test_stop_running_finds_nothing_in_an_empty_folder(self):
        self.assertEqual(core.stop_running(self.tmp / "nothing"), 0)

    def test_locations(self):
        real = core.locations()
        self.assertEqual(real["program"].name, "CNCBridge")
        self.assertEqual(real["registry"], (winreg.HKEY_LOCAL_MACHINE, core.UNINSTALL_KEY))
        self.assertTrue(str(real["menu"]).endswith(r"Start Menu\Programs"))
        test = core.locations(self.tmp)
        self.assertEqual(test["registry"][0], winreg.HKEY_CURRENT_USER)
        self.assertTrue(str(test["program"]).startswith(str(self.tmp)))


class Uninstall(Base):
    def install_all(self):
        installer = self.installer()
        installer.install()
        (self.where["data"] / "config.json").write_text("{}")
        return installer

    def test_everything_goes_and_the_data_stays(self):
        installer = self.install_all()
        installer.uninstall()
        self.assertFalse(self.where["program"].exists())
        self.assertFalse((self.where["menu"] / "CNCBridge").exists())
        self.assertFalse((self.where["desktop"] / "CNCBridge.lnk").exists())
        self.assertIsNone(core.installed_location(self.where["registry"]))
        self.assertEqual((self.where["data"] / "config.json").read_text(), "{}")
        self.assertEqual(self.removed_tasks, [self.where["data"]])  # the background task is removed first
        self.assertIn("kept", "\n".join(self.said))

    def test_the_event_log_entry_goes_with_the_program(self):
        installer = self.install_all()
        winreg.OpenKey(*self.where["eventlog"]).Close()  # there after the install
        installer.uninstall()
        with self.assertRaises(FileNotFoundError):
            winreg.OpenKey(*self.where["eventlog"])

    def test_a_set_up_without_an_event_log_key_still_installs(self):
        self.installer(eventlog=None).install(False, False)
        self.assertTrue((self.where["program"] / "CNCBridge.exe").exists())

    def test_the_data_can_go_too(self):
        installer = self.install_all()
        installer.uninstall(keep_data=False)
        self.assertFalse(self.where["data"].exists())

    def test_uninstalling_twice_is_harmless(self):
        installer = self.install_all()
        installer.uninstall()
        installer.uninstall()

    def test_a_running_program_blocks_the_uninstall_with_a_clear_message(self):
        installer = self.install_all()
        handle = hold_open(self.where["program"] / "CNCBridge.exe")
        try:
            with self.assertRaises(core.InUse):
                installer.uninstall()
        finally:
            release(handle)

    def test_the_running_uninstaller_is_removed_after_it_exits(self):
        later = []
        installer = self.installer(running_exe=self.where["program"] / "CNCBridgeSetup.exe")
        installer.install(False, False)
        installer._delete_later = later.append
        handle = hold_open(self.where["program"] / "CNCBridgeSetup.exe")  # as a running exe is held by Windows
        try:
            installer.uninstall()
        finally:
            release(handle)
        self.assertEqual([p.name for p in later[0]], ["CNCBridgeSetup.exe"])
        self.assertEqual(sorted(p.name for p in self.where["program"].iterdir()), ["CNCBridgeSetup.exe"])
        self.assertIsNone(core.installed_location(self.where["registry"]))


class DeferredDelete(Base):
    def test_the_cleanup_script_removes_the_files_then_the_folder_then_itself(self):
        installer = self.installer()
        installer.install(False, False)
        for name in ("CNCBridge.exe", "CNCBridgeDemo.exe", "README.md"):
            (self.where["program"] / name).unlink()  # only the uninstaller is left, as in a real uninstall
        script = installer.cleanup_script([self.where["program"] / "CNCBridgeSetup.exe"])
        self.assertIn("\r\n", script)
        self.assertIn(f'rmdir "{self.where["program"]}"', script)
        installer._delete_later([self.where["program"] / "CNCBridgeSetup.exe"])
        for _ in range(100):
            if not self.where["program"].exists():
                break
            import time
            time.sleep(0.1)
        self.assertFalse(self.where["program"].exists())
        leftovers = list(Path(tempfile.gettempdir()).glob("cncbridge_cleanup_*.bat"))
        import time
        time.sleep(1)
        self.assertEqual([p for p in leftovers if p.exists()], [])  # the batch file removed itself

    def test_it_waits_while_the_file_is_still_held(self):
        installer = self.installer()
        installer.install(False, False)
        for name in ("CNCBridge.exe", "CNCBridgeDemo.exe", "README.md"):
            (self.where["program"] / name).unlink()
        held = self.where["program"] / "CNCBridgeSetup.exe"
        handle = hold_open(held)
        installer._delete_later([held])
        import time
        time.sleep(2.5)
        self.assertTrue(held.exists())  # still there: it keeps trying
        release(handle)
        for _ in range(100):
            if not self.where["program"].exists():
                break
            time.sleep(0.1)
        self.assertFalse(self.where["program"].exists())


class CommandLine(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))  # the silent mode prints its steps

    def test_arguments(self):
        o = cli.parse(["/S", "/D=C:\\Program Files\\My CNC Bridge"])
        self.assertTrue(o["silent"])
        self.assertEqual(o["folder"], "C:\\Program Files\\My CNC Bridge")
        o = cli.parse(["/D=D:\\CNCBridge", "/S"])  # the folder is the rest of the line, as in NSIS
        self.assertEqual((o["silent"], o["folder"]), (False, "D:\\CNCBridge /S"))
        o = cli.parse(["--uninstall", "/s", "/DELETEDATA", "/NOMENU", "/NODESKTOP", "--test-root", "X:\\t"])
        self.assertEqual((o["uninstall"], o["silent"], o["delete_data"], o["menu"], o["desktop"], o["test_root"]),
                         (True, True, True, False, False, "X:\\t"))
        self.assertEqual(cli.parse([]), {"silent": False, "uninstall": False, "menu": True, "desktop": True,
                                        "delete_data": False, "folder": None, "test_root": None})

    def parts(self):
        return {"payload": self.payload, "program": self.where["program"], "data": self.where["data"],
                "menu": self.where["menu"], "desktop": self.where["desktop"], "registry": self.where["registry"],
                "eventlog": self.where["eventlog"], "setup_exe": self.setup_exe, "running_exe": None}

    def test_a_silent_install_and_uninstall(self):
        options = cli.parse(["/S", "/NOMENU", "/NODESKTOP"])
        self.assertEqual(cli.run_silent(options, self.parts()), 0)
        self.assertTrue((self.where["program"] / "CNCBridge.exe").exists())
        self.assertIsNotNone(core.installed_location(self.where["registry"]))
        self.assertEqual(cli.run_silent(cli.parse(["--uninstall", "/S"]), self.parts()), 0)
        self.assertFalse(self.where["program"].exists())

    def test_a_silent_install_into_a_chosen_folder(self):
        folder = self.tmp / "Chosen Folder"
        options = cli.parse(["/S", "/NOMENU", "/NODESKTOP", f"/D={folder}"])
        self.assertEqual(cli.run_silent(options, self.parts()), 0)
        self.assertTrue((folder / "CNCBridge.exe").exists())
        self.assertEqual(core.installed_location(self.where["registry"])[0], folder)
        self.assertEqual(cli.run_silent(cli.parse(["--uninstall", "/S"]), self.parts()), 0)  # found in the registry
        self.assertFalse(folder.exists())

    def test_exit_codes(self):
        parts = self.parts()
        (self.payload / "CNCBridge.exe").unlink()
        self.assertEqual(cli.run_silent(cli.parse(["/S"]), parts), 1)  # nothing to install
        (self.payload / "CNCBridge.exe").write_bytes(fake_exe("x"))
        self.assertEqual(cli.run_silent(cli.parse(["/S", "/NOMENU", "/NODESKTOP"]), parts), 0)
        handle = hold_open(self.where["program"] / "CNCBridge.exe")
        try:
            self.assertEqual(cli.run_silent(cli.parse(["/S", "/NOMENU", "/NODESKTOP"]), parts), 2)  # in use
        finally:
            release(handle)

    def test_the_installed_files_are_the_payload_byte_for_byte(self):
        cli.run_silent(cli.parse(["/S", "/NOMENU", "/NODESKTOP"]), self.parts())
        for name in ("CNCBridge.exe", "CNCBridgeDemo.exe", "README.md"):
            self.assertEqual((self.where["program"] / name).read_bytes(), (self.payload / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
