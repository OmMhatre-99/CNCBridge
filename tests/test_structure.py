"""The main window is put together from mixins in cncbridge/ui; these checks keep that arrangement honest."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import app  # noqa: E402


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
