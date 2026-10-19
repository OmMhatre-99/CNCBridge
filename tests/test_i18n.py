"""The translation framework: tr(), language files, the extraction tool, and that the window's text goes through it."""
import ast
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from cncbridge import i18n  # noqa: E402
import extract_strings  # noqa: E402


class Locales(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.original = i18n.LOCALE_DIR
        i18n.LOCALE_DIR = self.tmp
        self.addCleanup(setattr, i18n, "LOCALE_DIR", self.original)
        self.addCleanup(i18n.use, "en")

    def write(self, code, name, strings):
        (self.tmp / f"{code}.json").write_text(json.dumps({"language": name, "strings": strings}), encoding="utf-8")

    def test_english_is_the_default_and_unknown_languages_fall_back(self):
        self.assertEqual(i18n.use("en"), "en")
        self.assertEqual(i18n.tr("Save settings"), "Save settings")
        for code in ("xx", "", None, "../secret", "de/x", "DE-"):
            self.assertEqual(i18n.use(code), "en", code)

    def test_a_translation_is_used_and_missing_ones_show_english(self):
        self.write("de", "Deutsch", {"Save settings": "Einstellungen speichern", "Stop": ""})
        self.assertEqual(i18n.use("DE"), "de")
        self.assertEqual(i18n.tr("Save settings"), "Einstellungen speichern")
        self.assertEqual(i18n.tr("Stop"), "Stop")          # empty means not translated yet
        self.assertEqual(i18n.tr("Never translated"), "Never translated")
        self.assertEqual(i18n.current(), "de")

    def test_placeholders_are_filled_in_and_a_damaged_translation_cannot_break_the_window(self):
        self.write("de", "Deutsch", {"{n} file(s) queued": "{n} Datei(en) in der Warteschlange",
                                     "Hello {name}": "Hallo {nmae}", "Plain {braces}": "Schlicht {braces}"})
        i18n.use("de")
        self.assertEqual(i18n.tr("{n} file(s) queued", n=3), "3 Datei(en) in der Warteschlange")
        self.assertEqual(i18n.tr("Hello {name}", name="Om"), "Hello Om")  # the misspelt placeholder: English
        self.assertEqual(i18n.tr("Plain {braces}"), "Schlicht {braces}")  # no values: never formatted

    def test_padding_is_kept(self):
        self.write("de", "Deutsch", {"Setup": "Einrichtung", "1  Folders": "1  Ordner"})
        i18n.use("de")
        self.assertEqual(i18n.tr("  Setup  "), "  Einrichtung  ")
        self.assertEqual(i18n.tr(" 1  Folders "), " 1  Ordner ")

    def test_available_lists_the_files_and_not_the_template(self):
        self.write("de", "Deutsch", {"Stop": "Halt"})
        self.write("template", "", {})
        (self.tmp / "broken.json").write_text("{ nope", encoding="utf-8")
        self.write("fr", "Français", {})
        self.assertEqual(i18n.available(), [("en", "English"), ("de", "Deutsch"), ("fr", "Français")])

    def test_a_broken_language_file_gives_english(self):
        (self.tmp / "de.json").write_text("{ nope", encoding="utf-8")
        self.assertEqual(i18n.use("de"), "en")
        (self.tmp / "de.json").write_text(json.dumps({"language": "D", "strings": {"Stop": 5}}), encoding="utf-8")
        i18n.use("de")
        self.assertEqual(i18n.tr("Stop"), "Stop")  # nothing usable in it: English

    def test_n_marks_a_text_without_changing_it(self):
        self.assertEqual(i18n.N_("Window open"), "Window open")


class Extraction(unittest.TestCase):
    def test_the_template_is_up_to_date(self):
        self.assertEqual(extract_strings.render(extract_strings.extract()),
                         extract_strings.TEMPLATE.read_text(encoding="utf-8"),
                         "run: python tools/extract_strings.py")

    def test_the_template_has_the_window_texts_help_and_shortcuts(self):
        strings = json.loads(extract_strings.TEMPLATE.read_text(encoding="utf-8"))["strings"]
        self.assertGreater(len(strings), 300)
        for text in ("Save settings", "Start monitoring", "Pick ranges & preview", "Getting started",
                     "Open a machine CSV file in the range picker", "Window open", "Modern", "Stations..."):
            self.assertIn(text, strings, text)
        self.assertTrue(all(v == "" for v in strings.values()))
        self.assertFalse([k for k in strings if k != k.strip()])

    def test_every_help_topic_is_in_the_template(self):
        from cncbridge import help as help_text
        strings = json.loads(extract_strings.TEMPLATE.read_text(encoding="utf-8"))["strings"]
        for key, title, body in help_text.TOPICS:
            self.assertIn(title.strip(), strings, key)
            if body:
                self.assertIn(body.strip(), strings, key)
        for keys, what in help_text.SHORTCUTS:
            self.assertIn(what, strings)

    def test_placeholder_only_texts_are_left_out(self):
        self.assertFalse(extract_strings.worth_translating("  {title}  "))
        self.assertFalse(extract_strings.worth_translating("{a}:{b}"))
        self.assertTrue(extract_strings.worth_translating("{n} files"))


class Coverage(unittest.TestCase):
    """New window text must go through tr(): a literal given as text=, title= or label= has to be wrapped."""

    ALLOWED = {"Card.TLabel", "CardSmall.TLabel", "Small.TLabel", "PageHint.TLabel", "Toolbutton"}

    def test_no_user_visible_literal_in_the_window_modules_escapes_tr(self):
        bare = []
        for path in sorted((ROOT / "cncbridge" / "ui").glob("*.py")) + [ROOT / "cncbridge" / "app.py"]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    value = kw.value
                    if kw.arg in ("text", "title", "label") and isinstance(value, (ast.Constant, ast.JoinedStr)):
                        text = value.value if isinstance(value, ast.Constant) else "".join(
                            v.value if isinstance(v, ast.Constant) else "{}" for v in value.values)
                        if isinstance(text, str) and re.search(r"[A-Za-z]{2,}", text) and text not in self.ALLOWED:
                            bare.append(f"{path.name}:{node.lineno}: {kw.arg}={text[:40]!r}")
        # app.py sets the window title from the program name and version, which are not translated
        bare = [b for b in bare if not b.startswith("app.py")]
        self.assertEqual(bare, [], "wrap these in tr(...): " + "; ".join(bare))

    def test_messageboxes_status_lines_and_menu_entries_go_through_tr(self):
        offenders = []
        for path in sorted((ROOT / "cncbridge" / "ui").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                f = node.func
                shown = f.attr in ("showinfo", "showerror", "showwarning", "askokcancel") and \
                    isinstance(f.value, ast.Name) and f.value.id == "messagebox"
                status = f.attr == "set" and isinstance(f.value, ast.Attribute) and f.value.attr == "status_msg"
                if shown or status:
                    for arg in node.args[:2]:
                        if isinstance(arg, (ast.Constant, ast.JoinedStr)) and not (
                                isinstance(arg, ast.Constant) and not isinstance(arg.value, str)):
                            offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
