"""Native Tk smoke test. Linux CI runs this under Xvfb, never against machine folders."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from cncbridge.appearance import PALETTES, SIZES


@unittest.skipUnless(os.environ.get("CNCBRIDGE_GUI_TESTS") == "1", "Set CNCBRIDGE_GUI_TESTS=1 with a display")
class Desktop(unittest.TestCase):
    def test_startup_themes_sizes_and_persistence(self):
        from cncbridge.app import App
        with tempfile.TemporaryDirectory() as tmp:
            app = App(tmp)
            errors = []
            app.report_callback_exception = lambda *args: errors.append(args)
            try:
                for theme in PALETTES:
                    for size in SIZES:
                        app.theme_choice.set(theme)
                        app.size_choice.set(size)
                        app.change_appearance()
                        app.update()
                        self.assertEqual(app.cget("background"), PALETTES[theme]["background"])
                        for tab in app.tabs.tabs():
                            app.tabs.select(tab)
                            app.update()
                self.assertFalse(errors, errors)
                self.assertEqual(json.loads((Path(tmp) / "appearance.json").read_text()),
                                 {"theme": theme, "text_size": size})
                self.assertEqual(len(app.sel_vars), 10)
                self.assertEqual(len(app.man_vars), 2)
            finally:
                app.on_close()
