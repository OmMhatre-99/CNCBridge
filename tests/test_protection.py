"""Typed values and passwords kept apart from the settings, and the files locked so that only administrators change them."""
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import audit, config, engine, permissions  # noqa: E402

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)
BACKSLASH = chr(92)


def full_settings(**values):
    base = {"station_name": "Line 7", "api_token": "tok-1", "ftp_password": "pw-1", "alert_email_password": "mail-1",
            "manual_inputs": [{"name": "Operator", "value": "OP-1"}, {"name": "Work order", "value": "WO-7"}]}
    base.update(values)
    return config.normalize_config(base)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self.clean)
        self.path = self.tmp / "config.json"

    def clean(self):
        permissions.unlock(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def raw(self, name):
        return json.loads((self.tmp / name).read_text(encoding="utf-8"))


class SeparateFiles(Base):
    def test_passwords_and_typed_values_live_in_their_own_files(self):
        config.save_config(full_settings(), self.path)
        text = self.path.read_text(encoding="utf-8")
        for secret in ("tok-1", "pw-1", "mail-1", "dpapi:"):
            self.assertNotIn(secret, text)
        self.assertEqual([m["name"] for m in self.raw("config.json")["manual_inputs"]], ["Operator", "Work order"])
        self.assertEqual([m["value"] for m in self.raw("config.json")["manual_inputs"]], ["", ""])
        secrets = self.raw("secrets.json")
        self.assertEqual(sorted(secrets), sorted(config.SECRET_KEYS))
        self.assertTrue(all(v.startswith("dpapi:") for v in secrets.values()))
        self.assertNotIn("tok-1", (self.tmp / "secrets.json").read_text(encoding="utf-8"))
        self.assertEqual(self.raw("operator.json"), {"manual_inputs": ["OP-1", "WO-7"]})
        cfg = config.load_config(self.path)
        self.assertEqual((cfg["api_token"], cfg["ftp_password"], cfg["alert_email_password"]), ("tok-1", "pw-1", "mail-1"))
        self.assertEqual([m["value"] for m in cfg["manual_inputs"]], ["OP-1", "WO-7"])

    def test_a_file_from_before_the_split_is_read_and_cleaned_up_by_the_next_save(self):
        self.path.write_text(json.dumps({
            "station_name": "Old", "api_token": config.protect("tok-old"),
            "manual_inputs": [{"name": "Operator", "value": "OP-9"}, {"name": "", "value": ""}]}), encoding="utf-8")
        cfg = config.load_config(self.path)
        self.assertEqual((cfg["api_token"], cfg["manual_inputs"][0]["value"]), ("tok-old", "OP-9"))
        config.save_config(cfg, self.path)
        self.assertNotIn("tok-old", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("dpapi", self.path.read_text(encoding="utf-8"))
        again = config.load_config(self.path)
        self.assertEqual((again["api_token"], again["manual_inputs"][0]["value"]), ("tok-old", "OP-9"))

    def test_typed_values_are_saved_alone(self):
        config.save_config(full_settings(), self.path)
        before = {n: (self.tmp / n).read_bytes() for n in ("config.json", "secrets.json")}
        cfg = config.save_operator_values(self.tmp, ["OP-2", "WO-8"])
        self.assertEqual([m["value"] for m in cfg["manual_inputs"]], ["OP-2", "WO-8"])
        self.assertEqual({n: (self.tmp / n).read_bytes() for n in before}, before)  # nothing else was touched
        self.assertEqual([m["value"] for m in config.load_config(self.path)["manual_inputs"]], ["OP-2", "WO-8"])
        entry = audit.read(self.tmp, 1)[0]
        self.assertEqual(entry["how"], "operator")
        self.assertEqual([c["setting"] for c in entry["changes"]], ["Typed value 1", "Typed value 2"])
        self.assertEqual(entry["changes"][0]["new"], "Operator = OP-2")

    def test_only_two_typed_values_are_kept(self):
        config.save_config(full_settings(), self.path)
        cfg = config.save_operator_values(self.tmp, ["a", "b", "c"])
        self.assertEqual(self.raw("operator.json"), {"manual_inputs": ["a", "b"]})
        self.assertEqual(len(cfg["manual_inputs"]), 2)

    def test_the_background_process_notices_typed_values_changed_by_an_operator(self):
        config.save_config(full_settings(), self.path)
        eng = engine.Engine(config.load_config(self.path), self.tmp, config_path=self.path)
        self.assertFalse(eng.reload_settings())
        config.save_operator_values(self.tmp, ["OP-2", "WO-8"])
        later = (self.tmp / "operator.json").stat().st_mtime + 5
        os.utime(self.tmp / "operator.json", (later, later))
        self.assertTrue(eng.reload_settings())
        self.assertEqual(eng.cfg["manual_inputs"][0]["value"], "OP-2")

    def test_a_refused_write_leaves_nothing_behind(self):
        config.save_config(full_settings(), self.path)
        before = sorted(p.name for p in self.tmp.iterdir())
        with mock.patch("cncbridge.config.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                config.save_config(full_settings(station_name="Other"), self.path)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)

    def test_importing_keeps_todays_typed_values_when_the_file_has_none(self):
        config.save_config(full_settings(), self.path)
        backup = next((self.tmp / "backups").glob("config-*.json"))
        cfg = config.import_settings(backup, config.load_config(self.path))
        self.assertEqual([m["value"] for m in cfg["manual_inputs"]], ["OP-1", "WO-7"])
        self.assertEqual(cfg["api_token"], "tok-1")


class SwitchingProtectionOnAndOff(Base):
    def test_saving_with_protection_on_locks_the_files_for_the_named_accounts(self):
        with mock.patch.object(permissions, "lock_down", return_value=[]) as lock, \
                mock.patch.object(permissions, "unlock", return_value=[]) as unlock:
            config.save_config(full_settings(protect_settings=True,
                                             password_accounts="DOM" + BACKSLASH + "op\n# a note\n\nline-pc"), self.path)
            lock.assert_called_once_with(self.tmp, ["DOM" + BACKSLASH + "op", "line-pc"])
            unlock.assert_not_called()

    def test_switching_it_off_gives_the_permissions_back_once(self):
        with mock.patch.object(permissions, "lock_down", return_value=[]), \
                mock.patch.object(permissions, "unlock", return_value=[]) as unlock:
            config.save_config(full_settings(protect_settings=True), self.path)
            config.save_config(full_settings(protect_settings=False), self.path)
            unlock.assert_called_once_with(self.tmp)
            config.save_config(full_settings(protect_settings=False), self.path)
            unlock.assert_called_once()

    def test_without_protection_the_permissions_are_not_touched(self):
        with mock.patch.object(permissions, "lock_down") as lock, mock.patch.object(permissions, "unlock") as unlock:
            config.save_config(full_settings(), self.path)
        lock.assert_not_called()
        unlock.assert_not_called()

    def test_a_lock_that_fails_does_not_stop_the_save(self):
        with mock.patch.object(permissions, "lock_down", return_value=["config.json: Access is denied."]):
            config.save_config(full_settings(protect_settings=True), self.path)
        self.assertTrue(config.load_config(self.path)["protect_settings"])


@unittest.skipUnless(os.name == "nt", "Windows permissions")
class RealPermissions(Base):
    """Real icacls on a temporary folder. This test session is not elevated, so it behaves like an ordinary operator."""

    def setUp(self):
        super().setUp()
        # the data folder as the set-up program leaves it: ordinary users may modify, but not delete other people's files
        result = permissions._icacls(self.tmp, "/inheritance:r", "/grant:r", f"{permissions.SYSTEM}:(OI)(CI)(F)",
                                     f"{permissions.ADMINS}:(OI)(CI)(F)", f"{permissions.USERS}:(OI)(CI)M")
        self.assertEqual(result.returncode, 0, result.stderr)
        config.save_config(full_settings(), self.path)

    def lock(self, readers=(), as_account=None):
        with mock.patch.object(permissions, "current_account", return_value=as_account or permissions.current_account()):
            return permissions.lock_down(self.tmp, readers, force=True)

    def test_nothing_is_protected_to_begin_with(self):
        self.assertIs(permissions.settings_protected(self.tmp), False)
        self.assertTrue(permissions.can_write(self.path))
        self.assertIsNone(permissions.settings_protected(self.tmp / "nowhere"))

    def test_locked_settings_can_be_read_but_not_changed_by_an_ordinary_user(self):
        self.assertEqual(self.lock(), [])
        self.assertIs(permissions.settings_protected(self.tmp), True)
        self.assertFalse(permissions.can_write(self.path))
        self.assertEqual(self.raw("config.json")["station_name"], "Line 7")  # still readable
        self.assertTrue(permissions.can_write(self.tmp / "operator.json"))  # the typed values stay open
        self.assertFalse(permissions.can_write(self.tmp / "secrets.json"))

    def test_an_operator_can_still_save_typed_values_but_not_the_settings(self):
        self.lock()
        before = self.path.read_bytes()
        config.save_operator_values(self.tmp, ["OP-2", "WO-8"])
        self.assertEqual(self.raw("operator.json"), {"manual_inputs": ["OP-2", "WO-8"]})
        before_files = sorted(p.name for p in self.tmp.iterdir())
        with self.assertRaises(PermissionError):
            config.save_config(full_settings(station_name="Changed"), self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before_files)  # no .part file left

    def test_the_saved_passwords_are_unreadable_to_accounts_that_are_not_named(self):
        self.lock(as_account="Administrators")  # not this user: only administrators may read them
        with self.assertRaises(PermissionError):
            (self.tmp / "secrets.json").read_text(encoding="utf-8")
        cfg = config.load_config(self.path)
        self.assertEqual((cfg["station_name"], cfg["api_token"]), ("Line 7", ""))  # settings load, passwords do not

    def test_a_named_account_can_read_them(self):
        self.lock()  # this user is always one of them
        self.assertEqual(config.load_config(self.path)["api_token"], "tok-1")

    def test_an_unknown_account_is_reported_and_the_rest_still_locked(self):
        problems = self.lock(readers=["nobody-by-this-name"])
        self.assertEqual(len(problems), 1)
        self.assertIn("nobody-by-this-name", problems[0])
        self.assertIs(permissions.settings_protected(self.tmp), True)

    def test_unlocking_gives_the_folders_permissions_back(self):
        self.lock()
        self.assertEqual(permissions.unlock(self.tmp), [])
        self.assertIs(permissions.settings_protected(self.tmp), False)
        self.assertTrue(permissions.can_write(self.path))
        self.assertTrue(permissions.can_write(next((self.tmp / "backups").glob("config-*.json"))))

    def test_a_session_without_administrator_rights_does_not_lock_itself_out(self):
        with mock.patch.object(permissions, "is_elevated", return_value=False):
            problems = permissions.lock_down(self.tmp)
        self.assertEqual(len(problems), 1)
        self.assertIn("administrator", problems[0])
        self.assertTrue(permissions.can_write(self.path))

    def test_the_account_of_this_user_has_a_sid_and_a_made_up_one_does_not(self):
        self.assertTrue(permissions.account_sid(permissions.current_account()).startswith("S-1-5-"))
        self.assertIsNone(permissions.account_sid("nobody-by-this-name"))


class ReadingPermissions(unittest.TestCase):
    def test_which_acls_let_ordinary_users_write(self):
        cases = {
            "D:PAI(A;;FR;;;BU)(A;;FA;;;BA)(A;;FA;;;SY)": False,                   # locked: users read
            "D:AI(A;ID;0x1301bf;;;BU)(A;ID;FA;;;BA)": True,                       # modify, inherited
            "D:(A;;FA;;;WD)": True,                                               # everyone, full control
            "D:P(A;;GR;;;AU)(A;;FA;;;SY)": False,                                 # authenticated users: read only
            "D:P(A;;GW;;;AU)": True,
            "D:P(A;;0x1200a9;;;BU)": False,                                       # read and execute
            "D:P(A;;0x100116;;;BU)": True,                                        # write data
            "D:P(A;;FA;;;S-1-5-21-1-2-3-1002)": False,                            # one named person
            "D:P(D;;FW;;;BU)(A;;FR;;;BU)": False,                                 # a deny rule is not a grant
        }
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertIs(permissions.broad_write(text), expected)

    def test_account_names_in_a_settings_field(self):
        text = "DOM" + BACKSLASH + "op1\n\n  # the line PC\n line-pc  \n"
        self.assertEqual(permissions.account_list(text), ["DOM" + BACKSLASH + "op1", "line-pc"])
        self.assertEqual(permissions.account_list(""), [])


class AskingWindowsForAdministratorRights(unittest.TestCase):
    def fake_shell(self, result):
        calls = []
        shell = mock.MagicMock()
        shell.ShellExecuteW.side_effect = lambda *args: calls.append(args) or result
        return shell, calls

    def test_the_program_is_started_again_with_runas_and_its_arguments(self):
        shell, calls = self.fake_shell(42)
        with mock.patch.object(permissions.ctypes, "WinDLL", return_value=shell):
            started = permissions.run_as_administrator(
                ["C:/Program Files/CNCBridge/CNCBridge.exe", "--data", "C:/Program Data/CNC Bridge", "--restarted"],
                "C:/Program Files/CNCBridge")
        self.assertTrue(started)
        _, verb, program, arguments, folder, _ = calls[0]
        self.assertEqual((verb, program), ("runas", "C:/Program Files/CNCBridge/CNCBridge.exe"))
        self.assertEqual(arguments, '--data "C:/Program Data/CNC Bridge" --restarted')
        self.assertEqual(folder, "C:/Program Files/CNCBridge")

    def test_a_refused_question_is_reported_as_not_started(self):
        shell, _ = self.fake_shell(5)  # Windows' answer when the person says no
        with mock.patch.object(permissions.ctypes, "WinDLL", return_value=shell):
            self.assertFalse(permissions.run_as_administrator(["x.exe"]))


if __name__ == "__main__":
    unittest.main()
