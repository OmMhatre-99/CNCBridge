"""
Translation of the window's text.

Every sentence the window shows goes through tr(): the English text is the key, so the code stays readable and a
missing translation simply shows English. A language is a JSON file in cncbridge/locales, named after its code
(de.json):

    {"language": "Deutsch", "strings": {"Save settings": "Einstellungen speichern", ...}}

tools/extract_strings.py writes locales/template.json, which lists every English text; a translator copies it to
<code>.json and fills in the values (an empty value means "not translated yet"). A text with placeholders, such as
"{n} file(s) queued", keeps them: {n} must appear in the translation too.

Not translated, on purpose: the log, the output files, what is sent to a receiving system (API, webhook, FTP) and
the messages the processing engine produces. Those must stay stable for the machines and systems that read them.
"""
import json
import re
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "en"
TEMPLATE = "template"

_table = {}
_language = DEFAULT_LANGUAGE


def N_(text):
    """Mark a text that is translated later (tr(variable)); the extraction tool finds these too."""
    return text


def tr(text, **values):
    """The text in the current language (English when it has no translation), with {placeholders} filled in."""
    shown = _table.get(text)
    if not shown:  # a text with padding (" 1  Folders ", "  Setup  ") is looked up without it and keeps it
        core = text.strip()
        translated = _table.get(core)
        shown = text.replace(core, translated, 1) if translated and core else text
    if not values:
        return shown
    try:
        return shown.format(**values)
    except (KeyError, IndexError, ValueError):  # a translation that lost or misspelt a placeholder
        return text.format(**values)


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        strings = data.get("strings", {})
        return str(data.get("language") or Path(path).stem), {k: v for k, v in strings.items()
                                                              if isinstance(k, str) and isinstance(v, str) and v}
    except (OSError, ValueError, AttributeError):
        return None


def available():
    """[(code, name)] of the languages that can be chosen: English, then every locale file."""
    found = [(DEFAULT_LANGUAGE, "English")]
    try:
        files = sorted(LOCALE_DIR.glob("*.json"))
    except OSError:
        files = []
    for path in files:
        if path.stem in (TEMPLATE, DEFAULT_LANGUAGE):
            continue
        loaded = _read(path)
        if loaded:
            found.append((path.stem, loaded[0]))
    return found


def use(language):
    """Switch the language. An unknown or unreadable one gives English. Returns the code in force."""
    global _table, _language
    code = str(language or DEFAULT_LANGUAGE).strip().lower()
    _table, _language = {}, DEFAULT_LANGUAGE
    if re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})?", code) and code != DEFAULT_LANGUAGE \
            and (LOCALE_DIR / f"{code}.json").is_file():
        loaded = _read(LOCALE_DIR / f"{code}.json")
        if loaded:
            _table, _language = loaded[1], code
    return _language


def current():
    return _language
