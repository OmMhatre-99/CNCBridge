"""Themes: every theme is complete, an unknown name falls back, and the setting is stored."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import theme  # noqa: E402
from cncbridge.config import normalize_config  # noqa: E402


class Registry(unittest.TestCase):
    def tearDown(self):
        theme.use(theme.DEFAULT_THEME)

    def test_modern_and_legacy_exist_and_modern_is_the_default(self):
        self.assertIn("modern", theme.THEMES)
        self.assertIn("legacy", theme.THEMES)
        self.assertEqual(theme.DEFAULT_THEME, "modern")

    def test_every_theme_is_complete(self):
        for key, t in theme.THEMES.items():
            with self.subTest(theme=key):
                self.assertEqual(t.key, key)
                self.assertTrue(t.label and t.description)
                self.assertEqual(set(t.colours), set(theme.COLOUR_NAMES))
                self.assertEqual(len(t.range_colours), 10)
                self.assertTrue(all(len(pair) == 2 for pair in t.range_colours))
                self.assertTrue({"body", "heading", "mono"} <= set(t.fonts))
                self.assertTrue(callable(t.configure_styles) and callable(t.apply_palette))

    def test_labels_are_unique(self):
        labels = [t.label for t in theme.THEMES.values()]
        self.assertEqual(len(labels), len(set(labels)))

    def test_use_sets_the_colours_other_modules_read(self):
        legacy = theme.use("legacy")
        self.assertIs(legacy, theme.LEGACY)
        self.assertEqual(theme.BACKGROUND, theme.LEGACY.colours["BACKGROUND"])
        self.assertEqual(theme.RANGE_COLOURS, theme.LEGACY.range_colours)
        theme.use("modern")
        self.assertEqual(theme.BACKGROUND, theme.MODERN.colours["BACKGROUND"])
        self.assertNotEqual(theme.MODERN.colours["BACKGROUND"], theme.LEGACY.colours["BACKGROUND"])

    def test_unknown_theme_falls_back_to_the_default(self):
        for name in ("nonsense", "", None):
            self.assertIs(theme.use(name), theme.MODERN)

    def test_legacy_uses_the_native_widgets_and_modern_the_clam_base(self):
        self.assertEqual(theme.LEGACY.ttk_base, "")
        self.assertEqual(theme.MODERN.ttk_base, "clam")


class Setting(unittest.TestCase):
    def test_default_and_cleaning(self):
        self.assertEqual(normalize_config({})["theme"], "modern")
        self.assertEqual(normalize_config({"theme": "  Legacy "})["theme"], "legacy")
        self.assertEqual(normalize_config({"theme": None})["theme"], "modern")
        self.assertEqual(normalize_config({"theme": "gone"})["theme"], "gone")  # kept; the window falls back


if __name__ == "__main__":
    unittest.main()
