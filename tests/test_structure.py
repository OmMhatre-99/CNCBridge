"""The main window is put together from mixins in cncbridge/ui; these checks keep that arrangement honest."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import app, engine  # noqa: E402


def mixins():
    return [c for c in app.App.__mro__ if c.__module__.startswith("cncbridge.ui.") and c.__name__.endswith("Mixin")]


class Structure(unittest.TestCase):
    def test_every_piece_is_part_of_the_window(self):
        self.assertEqual(len(mixins()), 13)
        chain = list(app.App.__mro__)
        self.assertLess(chain.index(mixins()[-1]), chain.index(app.tk.Tk))  # Tk comes after every piece

    def test_no_method_is_defined_in_two_places(self):
        seen = {}
        for mixin in mixins():
            for name in vars(mixin):
                if name.startswith("__"):
                    continue
                self.assertNotIn(name, seen, f"{name} is in both {seen.get(name)} and {mixin.__name__}")
                seen[name] = mixin.__name__
        self.assertNotIn("__init__", [n for m in mixins() for n in vars(m)])  # the constructor stays in app.py

    def test_app_still_offers_the_names_the_tests_and_other_code_use(self):
        for name in ("App", "run", "ShiftDialog", "QueueHandler", "log", "messagebox", "simpledialog", "subprocess",
                     "ttk", "alerts", "config", "engine", "theme", "help_text", "load_config", "VERSION", "APP_NAME",
                     "FONT_SIZE_LABELS", "UI_SCALE_LABELS", "DEFAULT_FONT_LABEL", "APPEARANCE_KEYS", "BODY_LABELS"):
            self.assertTrue(hasattr(app, name), name)

    def test_the_main_module_stays_small(self):
        self.assertLess(len((Path(app.__file__)).read_text(encoding="utf-8").splitlines()), 250)


if __name__ == "__main__":
    unittest.main()


# Every name that engine.py offered before it was split into modules. The window, the demo, the tests and
# anything written against it use them as engine.<name>, so they all have to stay reachable there.
ENGINE_NAMES = (
    "ALLOW_ONCE", "APP_NAME", "Alerts", "CONTENT_TYPES", "CRITICAL_DISK_BYTES", "DAY_FOLDER", "DELIMITERS",
    "DESTINATIONS", "Engine", "FileError", "LOW_DISK_BYTES", "LOW_DISK_FRACTION", "Ledger", "NUMBER",
    "PERMANENT_FTP", "PERMANENT_HTTP", "Path", "RANGE_RE", "Rejected", "VERSION", "_NoRedirect", "_aligned",
    "_allowed", "_flat_csv", "_ftp_connect", "_ftp_enter", "_ftp_free_name", "_ftp_quit", "_header",
    "_header_safe", "_kept_copy", "_multipart", "_opener", "_resolve_heading", "_same_folder", "_unique",
    "_visible", "allow_once", "as_number", "atomic_write", "build_output", "check_range", "clean_up",
    "col_index", "col_letter", "copy_shared", "count_queued", "count_rejected", "count_waiting", "datetime",
    "decode", "disk_level", "disk_space", "extract", "find_heading", "format_range", "has_marker", "health",
    "is_heading", "is_ready", "load_config", "log", "manual_dict", "normalize_config", "normalize_range",
    "oldest_queued_age", "parse_headers", "parse_range", "parse_renames", "read_rows", "render", "reprocess",
    "resend_rejected", "resolve_range", "send_api", "send_ftp", "sha256_file", "sniff_delimiter",
    "stop_requested", "take_allowance", "test_api", "test_ftp", "test_shared", "timedelta", "validate",
)
MODULES = ("csvread", "output", "ledger", "delivery", "housekeeping")


class EngineModules(unittest.TestCase):
    def test_every_name_engine_used_to_have_is_still_there(self):
        self.assertEqual([n for n in ENGINE_NAMES if not hasattr(engine, n)], [])

    def test_the_pieces_do_not_import_the_engine(self):
        """They are the layers below it; an import of engine from one of them would make a loop."""
        for name in MODULES:
            text = (Path(engine.__file__).parent / f"{name}.py").read_text(encoding="utf-8")
            self.assertNotIn("from .engine", text, name)
            self.assertNotIn("import engine", text, name)

    def test_engine_py_stays_small(self):
        self.assertLess(len(Path(engine.__file__).read_text(encoding="utf-8").splitlines()), 400)

    def test_a_name_has_one_home(self):
        import importlib
        homes = {}
        for name in MODULES:
            module = importlib.import_module(f"cncbridge.{name}")
            for attribute, value in vars(module).items():
                if getattr(value, "__module__", None) == module.__name__ and not attribute.startswith("__"):
                    self.assertNotIn(attribute, homes, f"{attribute} is in {homes.get(attribute)} and {name}")
                    homes[attribute] = name


if __name__ == "__main__":
    unittest.main()
