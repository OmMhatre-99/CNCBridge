"""One word for one thing: the terms the program used to mix up must not come back (see Help > Words used)."""
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import extract_strings  # noqa: E402
from cncbridge import alerts, config, delivery, engine, help as help_text  # noqa: E402

# old wording (a regular expression) -> what is said now
BANNED = (
    (r"machine CSV folder", "machine folder"),
    (r"source folder", "machine folder"),
    (r"Details added", "Typed values"),
    (r"[Mm]anual input", "typed value"),
    (r"Waiting in folder", "Waiting to be read"),
    (r"Queued to send", "Waiting to be sent"),
    (r"Failed files", "Could not be read"),
    (r"Refused by API", "Refused"),
    (r"API connection", "API link"),
    (r"Test connection", "Test API link"),
    (r"Test folder", "Test shared folder"),
    (r"Test FTP(?! server)", "Test FTP server"),
    (r"\bInput \{", "Typed value {"),
    (r"web link", "API link"),
)


def old_words_in(text):
    return [(pattern, now) for pattern, now in BANNED if re.search(pattern, text)]


class NoOldWords(unittest.TestCase):
    def check(self, where, texts):
        found = [f"{where}: '{pattern}' should be '{now}' in: {text[:90]!r}"
                 for text in texts for pattern, now in old_words_in(text)]
        self.assertEqual(found, [])

    def test_the_text_of_the_window_and_of_help(self):
        self.check("window or Help", extract_strings.extract())  # every text that goes through tr(), Help included

    def test_the_keyboard_shortcuts(self):
        self.check("shortcuts", [text for _, text in help_text.SHORTCUTS])

    def test_the_readme_and_the_demo_script(self):
        for name in ("README.md", "demo/TEST_SCRIPT.md"):
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            # the one old name that stays, in the output files, because receiving systems read it: `Manual input 1`
            self.check(name, [line for line in lines if "`Manual input " not in line])

    def test_the_banned_list_catches_what_it_should(self):
        self.assertTrue(old_words_in("Check the machine CSV folder"))
        self.assertTrue(old_words_in("Queued to send"))
        self.assertTrue(old_words_in("Test FTP"))
        self.assertFalse(old_words_in("Test FTP server"))
        self.assertFalse(old_words_in("Machine folder, Typed values, Waiting to be sent"))


class DestinationNames(unittest.TestCase):
    def test_each_destination_is_named_the_same_way_everywhere(self):
        self.assertEqual({k: delivery.destination_name(k) for k in delivery.DESTINATIONS},
                         {"shared": "shared folder", "api": "API link", "ftp": "FTP server"})
        self.assertEqual(delivery.destination_name("api", capital=True), "API link")
        self.assertEqual(delivery.destination_name("shared", capital=True), "Shared folder")
        self.assertEqual(delivery.destination_name("sftp"), "sftp")  # one that has no name yet is shown as it is
        self.assertIs(engine.destination_name, delivery.destination_name)

    def test_a_destination_that_cannot_be_reached_is_reported_by_its_name(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(alerts.SENDERS.update, dict(alerts.SENDERS))
        alerts.SENDERS["notify"] = lambda *a, **k: None
        (tmp / "machine").mkdir()
        shutil.copy(ROOT / "samples" / "pnp_L1M8_board_00001.csv", tmp / "machine" / "a.csv")
        cfg = config.normalize_config({
            "source_folder": str(tmp / "machine"), "backup_folder": str(tmp / "backup"), "stable_seconds": 0,
            "selections": [{"name": "", "range": "A12:I14", "header": True}], "api_enabled": True,
            "api_url": "http://127.0.0.1:9/nothing-listens-here", "api_timeout": 2})
        eng = engine.Engine(cfg, tmp / "data")
        eng.scan_once()
        self.assertTrue(eng._reported["api"].startswith("Delivery to the API link is not possible, 1 file(s) waiting"),
                        eng._reported["api"])


if __name__ == "__main__":
    unittest.main()
