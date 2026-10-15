"""Native Tk smoke test. Linux CI runs this under Xvfb, never against machine folders."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
                        for mode in ("Guided", "Full workspace"):
                            app.workspace.set_mode(mode)
                            for tab in app.tabs.tabs():
                                app.tabs.select(tab)
                                app.update()
                        app.workspace.set_mode("Guided")
                self.assertFalse(errors, errors)
                self.assertEqual(json.loads((Path(tmp) / "appearance.json").read_text()),
                                 {"theme": theme, "text_size": size, "interface": "Guided"})
                self.assertEqual(len(app.sel_vars), 10)
                self.assertEqual(len(app.man_vars), 2)
                self.assertGreater(len(app.help_popups), 30)
            finally:
                app.on_close()

    def test_keyboard_ranges_preserve_anchor_and_survive_theme_change(self):
        from cncbridge.app import App
        with tempfile.TemporaryDirectory() as tmp:
            app = App(tmp)
            try:
                grid = app.grid_view
                grid.set_rows([["a", "b", "c"], ["1", "2", "3"]])
                grid._key_move(SimpleNamespace(keysym="Right", state=1))
                self.assertEqual(grid.sel, (0, 0, 1, 0))
                grid._key_move(SimpleNamespace(keysym="Down", state=1))
                self.assertEqual(grid.sel, (0, 0, 1, 1))
                app.theme_choice.set("Industrial")
                app.change_appearance()
                self.assertEqual(grid.sel, (0, 0, 1, 1))
                self.assertEqual(grid.active_cell, (1, 1))
                grid._key_move(SimpleNamespace(keysym="Right", state=0))
                self.assertEqual(grid.sel, (2, 1, 2, 1))
                grid._key_move(SimpleNamespace(keysym="Right", state=0))
                self.assertEqual(grid.sel, (2, 1, 2, 1))
                grid.set_rows([])
                self.assertEqual(grid._key_move(SimpleNamespace(keysym="Down", state=1)), "break")
                self.assertIsNone(grid.sel)
            finally:
                app.on_close()

    def test_tooltip_theme_and_destroy_cancel_pending_callbacks(self):
        from cncbridge.app import App
        from cncbridge.tooltip import Tooltip
        from tkinter import ttk
        with tempfile.TemporaryDirectory() as tmp:
            app = App(tmp)
            errors = []
            app.report_callback_exception = lambda *args: errors.append(args)
            try:
                button = ttk.Button(app, text="Help test")
                button.pack()
                app.update()
                tip = Tooltip(button, "Help text", delay=0)
                tip._enter()
                app.update()
                self.assertIsNotNone(tip.tipwindow)
                label = tip.tipwindow.winfo_children()[0]
                self.assertEqual(label.cget("background"), app.appearance.colors["panel"])
                tip._leave()
                tip.delay = 5000
                tip._enter()
                button.destroy()
                app.update()
                self.assertIsNone(tip.id)
                self.assertIsNone(tip.tipwindow)
                self.assertFalse(errors, errors)
            finally:
                app.on_close()
