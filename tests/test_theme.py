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

    def test_every_theme_has_a_complete_layout(self):
        fields = set(theme.Layout.__dataclass_fields__)
        for key, t in theme.THEMES.items():
            with self.subTest(theme=key):
                self.assertIsInstance(t.layout, theme.Layout)
                self.assertEqual(len(t.layout.window), 4)
                self.assertTrue(all(v is not None for v in (getattr(t.layout, f) for f in fields)))
                self.assertIn("strong", t.fonts)

    def test_strong_font_is_made_bold_unless_it_is_a_semibold_family(self):
        self.assertEqual(theme.strong_font({"strong": "Segoe UI Semibold"}, 12), ("Segoe UI Semibold", 12))
        self.assertEqual(theme.strong_font({"strong": "Segoe UI"}, 12), ("Segoe UI", 12, "bold"))

    def test_legacy_is_the_compact_version_1_layout(self):
        layout = theme.LEGACY.layout
        self.assertEqual(layout.window[:2], (1180, 740))  # the window size of version 1.0
        self.assertFalse(layout.page_intros)
        self.assertFalse(layout.metric_cards)
        self.assertEqual(layout.check_text, "")
        self.assertTrue(theme.MODERN.layout.page_intros)
        self.assertTrue(theme.MODERN.layout.metric_cards)

    def test_the_dashboard_has_a_sidebar_a_monitor_page_and_cards_and_the_others_do_not(self):
        self.assertIn("dashboard", theme.THEMES)
        layout = theme.DASHBOARD.layout
        self.assertTrue(layout.sidebar and layout.monitor_page and layout.cards)
        self.assertGreater(layout.sidebar_width, 150)
        for t in (theme.MODERN, theme.LEGACY):
            self.assertFalse(t.layout.sidebar or t.layout.monitor_page or t.layout.cards, t.key)
        self.assertEqual(theme.DEFAULT_THEME, "modern")  # the new look is a choice, not the default
        self.assertEqual(theme.DASHBOARD.ttk_base, "clam")

    def test_every_theme_names_the_sidebar_colours(self):
        for key, t in theme.THEMES.items():
            for name in ("SIDEBAR", "SIDEBAR_TEXT", "SIDEBAR_MUTED", "SIDEBAR_ACTIVE", "SIDEBAR_HOVER"):
                self.assertIn(name, t.colours, f"{key} {name}")

    def test_the_dashboards_sidebar_is_dark_in_light_mode_too(self):
        def brightness(colour):
            r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
            return (299 * r + 587 * g + 114 * b) / 1000
        for mode in ("light", "dark"):
            t = theme.use("dashboard", mode)
            self.assertLess(brightness(t.colours["SIDEBAR"]), 60, mode)
            self.assertGreater(brightness(t.colours["SIDEBAR_TEXT"]), 150, mode)
            self.assertGreater(brightness(t.colours["SIDEBAR_MUTED"]), 110, mode)

    def test_text_on_the_dashboards_cards_can_be_read(self):
        """WCAG contrast of the colours the Dashboard puts on its white cards and its grey page."""
        def luminance(colour):
            parts = []
            for i in (1, 3, 5):
                c = int(colour[i:i + 2], 16) / 255
                parts.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
            return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]

        def contrast(a, b):
            la, lb = sorted((luminance(a), luminance(b)), reverse=True)
            return (la + 0.05) / (lb + 0.05)
        for mode in ("light", "dark"):
            c = theme.use("dashboard", mode).colours
            for text in ("TEXT", "MUTED", "ACCENT", "SUCCESS", "WARNING", "ERROR"):
                for ground in ("SURFACE", "BACKGROUND"):
                    self.assertGreaterEqual(contrast(c[text], c[ground]), 4.5, f"{mode} {text} on {ground}")
            self.assertGreaterEqual(contrast(c["SIDEBAR_TEXT"], c["SIDEBAR"]), 4.5, mode)
            self.assertGreaterEqual(contrast(c["SIDEBAR_MUTED"], c["SIDEBAR"]), 4.5, mode)
            self.assertGreaterEqual(contrast(c["TEXT"], c["ACCENT_TINT"]), 4.5, mode)

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


class DarkMode(unittest.TestCase):
    def tearDown(self):
        theme.use(theme.DEFAULT_THEME)

    def test_modern_has_a_complete_dark_palette(self):
        self.assertEqual(set(theme.MODERN.dark_colours), set(theme.COLOUR_NAMES))
        self.assertEqual(len(theme.MODERN.dark_range_colours), 10)

    def test_the_dashboard_has_a_complete_dark_palette(self):
        self.assertEqual(set(theme.DASHBOARD.dark_colours), set(theme.COLOUR_NAMES))
        self.assertEqual(len(theme.DASHBOARD.dark_range_colours), 10)
        t = theme.use("dashboard", "dark")
        self.assertTrue(t.dark)
        self.assertEqual(t.ttk_base, "clam")
        self.assertEqual(theme.SIDEBAR, theme.DASHBOARD.dark_colours["SIDEBAR"])
        self.assertIs(theme.use("dashboard", "light"), theme.DASHBOARD)

    def test_dark_modern_is_in_force_after_use(self):
        t = theme.use("modern", "dark")
        self.assertTrue(t.dark)
        self.assertEqual(theme.BACKGROUND, theme.MODERN.dark_colours["BACKGROUND"])
        self.assertEqual(theme.RANGE_COLOURS, theme.MODERN.dark_range_colours)
        self.assertEqual(t.ttk_base, "clam")
        self.assertIs(theme.use("modern", "light"), theme.MODERN)  # light is the theme itself
        self.assertFalse(theme.MODERN.dark)
        self.assertEqual(theme.BACKGROUND, theme.MODERN.colours["BACKGROUND"])

    def test_invert_colour(self):
        self.assertEqual(theme.invert_colour("#000000"), "#ffffff")
        self.assertEqual(theme.invert_colour("#1a66d9"), "#e59926")
        for colour in ("#f0f0f0", "#c5221f", "#123456"):
            self.assertEqual(theme.invert_colour(theme.invert_colour(colour)), colour)

    def test_legacy_dark_is_the_inverse_of_every_legacy_colour(self):
        t = theme.use("legacy", "dark")
        self.assertTrue(t.dark)
        self.assertEqual(set(t.colours), set(theme.COLOUR_NAMES))
        for name, light in theme.LEGACY.colours.items():
            self.assertEqual(t.colours[name], theme.invert_colour(light), name)
            self.assertEqual(getattr(theme, name), theme.invert_colour(light), name)
        for (tint, edge), (dark_tint, dark_edge) in zip(theme.LEGACY.range_colours, t.range_colours):
            self.assertEqual((dark_tint, dark_edge), (theme.invert_colour(tint), theme.invert_colour(edge)))
        self.assertEqual(t.ttk_base, "alt")  # the native controls cannot be recoloured
        self.assertIsNot(t.configure_styles, theme.LEGACY.configure_styles)
        self.assertEqual(theme.LEGACY.ttk_base, "")  # light Legacy is untouched

    def test_follow_windows(self):
        original = theme.system_is_dark
        try:
            theme.system_is_dark = lambda: True
            self.assertTrue(theme.use("modern", "system").dark)
            theme.system_is_dark = lambda: False
            self.assertFalse(theme.use("modern", "system").dark)
            self.assertFalse(theme.use("modern", "light").dark)
        finally:
            theme.system_is_dark = original

    def test_a_theme_without_a_dark_version_stays_light(self):
        plain = theme.replace(theme.MODERN, key="plain", dark_colours=None)
        theme.THEMES["plain"] = plain
        try:
            self.assertFalse(theme.use("plain", "dark").dark)
        finally:
            del theme.THEMES["plain"]

    def test_every_layout_has_a_menu_size(self):
        for t in theme.THEMES.values():
            self.assertGreaterEqual(t.layout.menu_size, 10)


class Setting(unittest.TestCase):
    def test_default_and_cleaning(self):
        self.assertEqual(normalize_config({})["theme"], "modern")
        self.assertEqual(normalize_config({"theme": "  Legacy "})["theme"], "legacy")
        self.assertEqual(normalize_config({"theme": None})["theme"], "modern")
        self.assertEqual(normalize_config({"theme": "gone"})["theme"], "gone")  # kept; the window falls back


    def test_appearance_settings_are_checked(self):
        defaults = normalize_config({})
        self.assertEqual([defaults[k] for k in ("color_mode", "font_family", "font_size", "ui_scale")],
                         ["light", "", 100, 100])
        good = normalize_config({"color_mode": " Dark ", "font_family": " Arial ", "font_size": "115", "ui_scale": 125})
        self.assertEqual([good[k] for k in ("color_mode", "font_family", "font_size", "ui_scale")],
                         ["dark", "Arial", 115, 125])
        bad = normalize_config({"color_mode": "purple", "font_size": 7, "ui_scale": "huge"})
        self.assertEqual([bad[k] for k in ("color_mode", "font_size", "ui_scale")], ["light", 100, 100])
        self.assertEqual(normalize_config({"color_mode": None, "font_family": None})["color_mode"], "light")


if __name__ == "__main__":
    unittest.main()
