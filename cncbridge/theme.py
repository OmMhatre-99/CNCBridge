"""Shared light industrial palette for CNCBridge's Tk and ttk controls."""

from tkinter import font as tkfont

BACKGROUND = "#f4f5f2"
SURFACE = "#ffffff"
SURFACE_ALT = "#edf2f1"
TEXT = "#26343d"
MUTED = "#52636b"
BORDER = "#d3dcda"
DISABLED_SURFACE = "#ecefed"
DISABLED_TEXT = "#68777d"
DISABLED_ACCENT = "#5a7372"
HOVER_SURFACE = "#e8eeec"
FOCUS_SURFACE = "#e7efee"
ACTIVE_HEADER = "#e2eae8"

ACCENT = "#176b70"
ACCENT_HOVER = "#12575b"
ACCENT_TINT = "#dcefee"
FOCUS = "#a85b00"

SUCCESS = "#176b45"
WARNING = "#855600"
ERROR = "#a92720"
ERROR_TINT = "#fbe9e7"
WARNING_TINT = "#fff1cf"
IDLE = MUTED
WHITE = "#ffffff"

GRID_LINE = BORDER
GRID_HEADER = SURFACE_ALT
GRID_HEADER_TEXT = MUTED
GRID_SELECTION = ACCENT_TINT
GRID_SELECTION_EDGE = ACCENT
GRID_HEADER_SELECTION = "#c3dedb"
RANGE_COLOURS = (
    ("#e6f1ef", "#176b70"),
    ("#e8edf5", "#3f5f86"),
    ("#f3eee2", "#795a1c"),
    ("#e9f0e3", "#4f7040"),
    ("#f3e9e6", "#9a4d3e"),
    ("#ece8f2", "#65527f"),
    ("#e3efed", "#35766e"),
    ("#f1eee2", "#756628"),
    ("#e9ecec", "#56666a"),
    ("#f2e9ee", "#895169"),
)


def resolve_font_families(root):
    """Choose installed font families, falling back cleanly when Google Fonts are absent."""
    available = {family.casefold(): family for family in tkfont.families(root)}
    default = tkfont.nametofont("TkDefaultFont", root).actual("family")
    fixed = tkfont.nametofont("TkFixedFont", root).actual("family")

    def choose(candidates, fallback):
        return next((available[name.casefold()] for name in candidates if name.casefold() in available), fallback)

    return {
        "heading": choose(
            ("Josefin Sans", "Avenir Next", "Avenir", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
            default,
        ),
        "body": choose(
            ("Roboto", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
            default,
        ),
        "mono": choose(
            ("Roboto Mono", "Menlo", "Monaco", "Consolas", "Courier New", "DejaVu Sans Mono"),
            fixed,
        ),
    }


def apply_tk_palette(root, fonts):
    """Set defaults for classic Tk widgets and Tk-rendered dialogs."""
    root.tk_setPalette(
        background=BACKGROUND,
        foreground=TEXT,
        activeBackground=ACCENT_HOVER,
        activeForeground=WHITE,
        selectBackground=ACCENT,
        selectForeground=WHITE,
        insertBackground=TEXT,
        highlightColor=FOCUS,
    )
    root.option_add("*Menu.background", SURFACE)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*Menu.activeBackground", ACCENT)
    root.option_add("*Menu.activeForeground", WHITE)
    root.option_add("*Menu.borderColor", BORDER)
    root.option_add("*Menu.relief", "solid")
    root.option_add("*Dialog.background", BACKGROUND)
    root.option_add("*Dialog.msg.background", BACKGROUND)
    root.option_add("*Dialog.msg.foreground", TEXT)
    root.option_add("*Dialog.button.background", SURFACE)
    root.option_add("*Dialog.button.foreground", TEXT)
    root.option_add("*Font", (fonts["body"], 11))
    root.option_add("*Menu.font", (fonts["body"], 11))
    root.option_add("*Dialog.msg.font", (fonts["body"], 11))
    root.option_add("*Dialog.button.font", (fonts["body"], 11))
