"""Bundle discovery and safe fallbacks do not require a desktop display."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cncbridge import font_registration as fonts


class BundledFonts(unittest.TestCase):
    def test_source_bundle_includes_fonts_and_licenses(self):
        for path in fonts.bundled_font_paths().values():
            self.assertTrue(path.is_file(), path)
            self.assertGreater(path.stat().st_size, 1000)
        folder = next(iter(fonts.bundled_font_paths().values())).parent
        for name in ("Roboto-OFL.txt", "RobotoMono-OFL.txt"):
            self.assertIn("SIL OPEN FONT LICENSE", (folder / name).read_text())

    def test_frozen_discovery_and_missing_fonts_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(fonts.sys, "_MEIPASS", tmp, create=True), patch.object(fonts, "_registration_result", None):
                self.assertEqual(fonts.bundled_font_paths()["Roboto"], Path(tmp) / "assets/fonts/Roboto.ttf")
                self.assertEqual(fonts.register_bundled_fonts(), {})

    def test_registration_failure_uses_system_fonts(self):
        with patch.object(fonts, "_registration_result", None), patch.object(fonts.sys, "platform", "linux"):
            with patch.object(fonts, "_register_linux", side_effect=OSError("font library unavailable")):
                self.assertEqual(fonts.register_bundled_fonts(), {})
