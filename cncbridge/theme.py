"""
Themes for the CNCBridge window: colours, fonts and the styles of the ttk widgets.

To add a theme, write its colours (every name in COLOUR_NAMES) and a function that styles the ttk widgets,
create a Theme for it and add it to THEMES. It then appears in the Appearance box on the Setup tab.

The rest of the program reads colours as theme.ACCENT, theme.SURFACE and so on, at the moment a widget is
built, so whatever theme use() selected is what a new window gets. A theme is chosen once, when the window
starts; changing it in the settings takes effect after a restart.
"""
from dataclasses import dataclass
from tkinter import font as tkfont
from typing import Callable

COLOUR_NAMES = (
    "BACKGROUND", "SURFACE", "SURFACE_ALT", "TEXT", "MUTED", "BORDER", "DISABLED_SURFACE", "DISABLED_TEXT",
    "DISABLED_ACCENT", "HOVER_SURFACE", "FOCUS_SURFACE", "ACTIVE_HEADER", "ACCENT", "ACCENT_HOVER", "ACCENT_TINT",
    "FOCUS", "SUCCESS", "WARNING", "ERROR", "ERROR_TINT", "WARNING_TINT", "IDLE", "WHITE", "GRID_LINE",
    "GRID_HEADER", "GRID_HEADER_TEXT", "GRID_SELECTION", "GRID_SELECTION_EDGE", "GRID_HEADER_SELECTION")


@dataclass(frozen=True)
class Theme:
    key: str                  # stored in the settings
    label: str                # shown in the Appearance box
    description: str
    colours: dict             # every name in COLOUR_NAMES
    range_colours: tuple      # (tint, edge) for each of the 10 selection slots
    fonts: dict               # candidate font families, best first, for "heading", "body" and "mono"
    bundled_fonts: bool       # register the fonts shipped in assets/fonts before the window opens
    ttk_base: str             # ttk theme to start from; "" keeps the native Windows one
    configure_styles: Callable   # (style, px, fonts) -> None
    apply_palette: Callable      # (root, fonts) -> None, defaults for classic Tk widgets and dialogs


# ---------------------------------------------------------------- modern: light industrial
def _modern_palette(root, fonts):
    """Set defaults for classic Tk widgets and Tk-rendered dialogs."""
    c = MODERN.colours
    root.tk_setPalette(
        background=c["BACKGROUND"],
        foreground=c["TEXT"],
        activeBackground=c["ACCENT_HOVER"],
        activeForeground=c["WHITE"],
        selectBackground=c["ACCENT"],
        selectForeground=c["WHITE"],
        insertBackground=c["TEXT"],
        highlightColor=c["FOCUS"],
    )
    root.option_add("*Menu.background", c["SURFACE"])
    root.option_add("*Menu.foreground", c["TEXT"])
    root.option_add("*Menu.activeBackground", c["ACCENT"])
    root.option_add("*Menu.activeForeground", c["WHITE"])
    root.option_add("*Menu.borderColor", c["BORDER"])
    root.option_add("*Menu.relief", "solid")
    root.option_add("*Dialog.background", c["BACKGROUND"])
    root.option_add("*Dialog.msg.background", c["BACKGROUND"])
    root.option_add("*Dialog.msg.foreground", c["TEXT"])
    root.option_add("*Dialog.button.background", c["SURFACE"])
    root.option_add("*Dialog.button.foreground", c["TEXT"])
    root.option_add("*Font", (fonts["body"], 11))
    root.option_add("*Menu.font", (fonts["body"], 11))
    root.option_add("*Dialog.msg.font", (fonts["body"], 11))
    root.option_add("*Dialog.button.font", (fonts["body"], 11))


def _modern_styles(style, px, fonts):
    c = MODERN.colours
    BACKGROUND, SURFACE, SURFACE_ALT, TEXT, MUTED, BORDER = (c[k] for k in (
        "BACKGROUND", "SURFACE", "SURFACE_ALT", "TEXT", "MUTED", "BORDER"))
    DISABLED_SURFACE, DISABLED_TEXT, DISABLED_ACCENT, HOVER_SURFACE, FOCUS_SURFACE, ACTIVE_HEADER = (c[k] for k in (
        "DISABLED_SURFACE", "DISABLED_TEXT", "DISABLED_ACCENT", "HOVER_SURFACE", "FOCUS_SURFACE", "ACTIVE_HEADER"))
    ACCENT, ACCENT_HOVER, ACCENT_TINT, FOCUS, WHITE = (c[k] for k in (
        "ACCENT", "ACCENT_HOVER", "ACCENT_TINT", "FOCUS", "WHITE"))
    body, heading, mono = fonts["body"], fonts["heading"], fonts["mono"]
    style.configure(".", font=(body, 11), foreground=TEXT)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Small.TLabel", background=BACKGROUND, foreground=MUTED)
    style.configure("PageTitle.TLabel", background=BACKGROUND, foreground=TEXT,
                    font=(heading, 19, "bold"))
    style.configure("PageHint.TLabel", background=BACKGROUND, foreground=MUTED, font=(body, 11))
    style.configure("EmptyState.TLabel", background=SURFACE, foreground=MUTED,
                    font=(body, 12), justify="center", padding=(20, 14))
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("Card.TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("CardSmall.TLabel", background=BACKGROUND, foreground=MUTED)
    style.configure("MetricSmall.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("MetricValue.TLabel", background=SURFACE, foreground=TEXT,
                    font=(body, 16, "bold"))
    style.configure("TLabelframe", background=BACKGROUND, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BACKGROUND, foreground=TEXT,
                    font=(heading, 12, "bold"))
    style.configure("Count.TLabel", background=BACKGROUND, foreground=TEXT, font=(body, 16, "bold"))
    style.configure("Metric.TFrame", background=SURFACE, relief="solid", borderwidth=1)
    style.configure("TButton", padding=(px(11), px(6)), background=SURFACE,
                    bordercolor=BORDER, focuscolor=FOCUS)
    style.map("TButton", background=[("active", HOVER_SURFACE), ("disabled", DISABLED_SURFACE)],
              foreground=[("disabled", DISABLED_TEXT)], bordercolor=[("focus", FOCUS), ("active", ACCENT)])
    style.configure("Go.TButton", font=(body, 11, "bold"), background=ACCENT,
                    foreground=WHITE, bordercolor=ACCENT)
    style.map("Go.TButton", background=[("active", ACCENT_HOVER), ("disabled", DISABLED_ACCENT)],
              foreground=[("disabled", WHITE)], bordercolor=[("focus", FOCUS)])
    style.configure("Toolbutton", padding=(px(10), px(6)), background=SURFACE,
                    bordercolor=BORDER, focuscolor=FOCUS)
    style.map("Toolbutton", background=[("active", HOVER_SURFACE)], bordercolor=[("focus", FOCUS),
                                                                               ("active", ACCENT)])
    style.configure("Go.Toolbutton", font=(body, 11, "bold"), background=ACCENT,
                    foreground=WHITE, bordercolor=ACCENT)
    style.map("Go.Toolbutton", background=[("active", ACCENT_HOVER), ("disabled", DISABLED_ACCENT)],
              foreground=[("disabled", WHITE)], bordercolor=[("focus", FOCUS)])
    style.configure("TEntry", padding=(px(7), px(5)), fieldbackground=SURFACE,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, insertcolor=TEXT)
    style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)], fieldbackground=[("disabled", DISABLED_SURFACE)],
              foreground=[("disabled", DISABLED_TEXT)])
    style.configure("TCombobox", padding=(px(7), px(5)), fieldbackground=SURFACE,
                    bordercolor=BORDER, arrowcolor=TEXT, focuscolor=FOCUS)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE), ("disabled", SURFACE_ALT)],
              bordercolor=[("focus", ACCENT), ("active", ACCENT)],
              foreground=[("disabled", DISABLED_TEXT)], arrowcolor=[("disabled", MUTED)])
    style.configure("TCheckbutton", background=BACKGROUND, foreground=TEXT, padding=(0, px(3)),
                    focuscolor=FOCUS)
    style.map("TCheckbutton", background=[("active", BACKGROUND), ("disabled", BACKGROUND)],
              foreground=[("disabled", MUTED)])
    style.configure("TRadiobutton", background=BACKGROUND, foreground=TEXT, padding=(0, px(4)),
                    focuscolor=FOCUS)
    style.map("TRadiobutton", background=[("active", BACKGROUND), ("disabled", BACKGROUND)],
              foreground=[("disabled", MUTED)])
    style.configure("TScrollbar", background=SURFACE_ALT, troughcolor=BACKGROUND,
                    bordercolor=BORDER, arrowcolor=MUTED, lightcolor=SURFACE_ALT, darkcolor=BORDER)
    style.map("TScrollbar", background=[("active", ACCENT_TINT), ("pressed", ACCENT_TINT)],
              arrowcolor=[("active", ACCENT)])
    style.configure("TPanedwindow", background=BACKGROUND)
    style.configure("Sash", background=BORDER, sashthickness=px(5))
    style.configure("TNotebook", background=BACKGROUND, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=BACKGROUND, foreground=MUTED,
                    padding=(px(15), px(9)), font=(body, 11, "bold"))
    style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", SURFACE_ALT)],
              foreground=[("selected", ACCENT), ("active", TEXT)], bordercolor=[("focus", FOCUS)])
    style.configure("Treeview", rowheight=px(30), background=SURFACE, fieldbackground=SURFACE,
                    foreground=TEXT, bordercolor=BORDER, font=(body, 11))
    style.configure("History.Treeview", rowheight=px(30), background=SURFACE,
                    fieldbackground=SURFACE, foreground=TEXT, bordercolor=BORDER, font=(mono, 11))
    style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=TEXT,
                    font=(body, 10, "bold"), padding=(px(8), px(8)))
    style.configure("History.Treeview.Heading", background=SURFACE_ALT, foreground=TEXT,
                    font=(body, 10, "bold"), padding=(px(8), px(8)))
    style.map("Treeview", background=[("selected", ACCENT_TINT), ("focus", FOCUS_SURFACE)],
              foreground=[("selected", TEXT)])
    style.map("Treeview.Heading", background=[("active", ACTIVE_HEADER)],
              foreground=[("active", TEXT)])
    style.configure("TScrollbar", arrowsize=px(15))


MODERN = Theme(
    key="modern",
    label="Modern",
    description="Light industrial look with flat controls and the bundled Roboto fonts. The default.",
    colours=dict(
        BACKGROUND="#f4f5f2", SURFACE="#ffffff", SURFACE_ALT="#edf2f1", TEXT="#26343d", MUTED="#52636b",
        BORDER="#d3dcda", DISABLED_SURFACE="#ecefed", DISABLED_TEXT="#68777d", DISABLED_ACCENT="#5a7372",
        HOVER_SURFACE="#e8eeec", FOCUS_SURFACE="#e7efee", ACTIVE_HEADER="#e2eae8", ACCENT="#176b70",
        ACCENT_HOVER="#12575b", ACCENT_TINT="#dcefee", FOCUS="#a85b00", SUCCESS="#176b45", WARNING="#855600",
        ERROR="#a92720", ERROR_TINT="#fbe9e7", WARNING_TINT="#fff1cf", IDLE="#52636b", WHITE="#ffffff",
        GRID_LINE="#d3dcda", GRID_HEADER="#edf2f1", GRID_HEADER_TEXT="#52636b", GRID_SELECTION="#dcefee",
        GRID_SELECTION_EDGE="#176b70", GRID_HEADER_SELECTION="#c3dedb"),
    range_colours=(
        ("#e6f1ef", "#176b70"), ("#e8edf5", "#3f5f86"), ("#f3eee2", "#795a1c"), ("#e9f0e3", "#4f7040"),
        ("#f3e9e6", "#9a4d3e"), ("#ece8f2", "#65527f"), ("#e3efed", "#35766e"), ("#f1eee2", "#756628"),
        ("#e9ecec", "#56666a"), ("#f2e9ee", "#895169")),
    fonts=dict(
        heading=("Josefin Sans", "Avenir Next", "Avenir", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
        body=("Roboto", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
        mono=("Roboto Mono", "Menlo", "Monaco", "Consolas", "Courier New", "DejaVu Sans Mono")),
    bundled_fonts=True,
    ttk_base="clam",
    configure_styles=_modern_styles,
    apply_palette=_modern_palette,
)


# ---------------------------------------------------------------- legacy: the original look
def _legacy_palette(root, fonts):
    """The original look left Tk's own defaults alone (system colours, Windows fonts)."""


def _legacy_styles(style, px, fonts):
    """Native Windows controls with a few tweaks, as in version 1.0. Colours come from the Legacy palette."""
    c = LEGACY.colours
    body, heading, mono = fonts["body"], fonts["heading"], fonts["mono"]
    style.configure(".", font=(body, 10))
    style.configure("Small.TLabel", foreground=c["MUTED"])
    style.configure("CardSmall.TLabel", foreground=c["MUTED"])
    style.configure("MetricSmall.TLabel", foreground=c["MUTED"])
    style.configure("PageTitle.TLabel", font=(heading, 16, "bold"))
    style.configure("PageHint.TLabel", foreground=c["MUTED"])
    style.configure("EmptyState.TLabel", foreground=c["MUTED"], font=(body, 11), justify="center", padding=(20, 14))
    style.configure("MetricValue.TLabel", font=(body, 15, "bold"))
    style.configure("Count.TLabel", font=(body, 15, "bold"))
    style.configure("TLabelframe.Label", font=(body, 10, "bold"))
    style.configure("Metric.TFrame", relief="solid", borderwidth=1)
    style.configure("TButton", padding=(px(8), px(2)))
    style.configure("Go.TButton", font=(body, 10, "bold"))
    style.configure("Toolbutton", padding=(px(8), px(4)))
    style.configure("Go.Toolbutton", font=(body, 10, "bold"))
    style.configure("TNotebook.Tab", padding=(px(12), px(5)))
    style.configure("Treeview", rowheight=px(24), font=(body, 10))
    style.configure("History.Treeview", rowheight=px(24), font=(mono, 10))
    style.configure("Treeview.Heading", font=(body, 9, "bold"))
    style.configure("History.Treeview.Heading", font=(body, 9, "bold"))


LEGACY = Theme(
    key="legacy",
    label="Legacy",
    description="The original look: native Windows controls, Segoe UI and Consolas, grey and blue.",
    colours=dict(
        BACKGROUND="#f0f0f0", SURFACE="#ffffff", SURFACE_ALT="#f1f1f1", TEXT="#000000", MUTED="#6b6b6b",
        BORDER="#adadad", DISABLED_SURFACE="#f0f0f0", DISABLED_TEXT="#a0a0a0", DISABLED_ACCENT="#a0a0a0",
        HOVER_SURFACE="#e5f1fb", FOCUS_SURFACE="#e5f1fb", ACTIVE_HEADER="#e5e5e5", ACCENT="#1a66d9",
        ACCENT_HOVER="#1555b3", ACCENT_TINT="#cfe2ff", FOCUS="#1a66d9", SUCCESS="#1e8e3e", WARNING="#b06000",
        ERROR="#c5221f", ERROR_TINT="#fbe4e2", WARNING_TINT="#fbeccb", IDLE="#6b6b6b", WHITE="#ffffff",
        GRID_LINE="#dcdcdc", GRID_HEADER="#f1f1f1", GRID_HEADER_TEXT="#555555", GRID_SELECTION="#cfe2ff",
        GRID_SELECTION_EDGE="#1a66d9", GRID_HEADER_SELECTION="#c5d7f2"),
    range_colours=(
        ("#fde9c8", "#c77700"), ("#d9f2dc", "#2e8b3d"), ("#f6dcee", "#b0308a"), ("#dbeafe", "#3b6fd4"),
        ("#fbe0dc", "#c0392b"), ("#e6e0f8", "#6b4fc4"), ("#d8f3f1", "#11847c"), ("#f3efc9", "#8a7a00"),
        ("#e8e8e8", "#666666"), ("#fcdfe9", "#c2185b")),
    fonts=dict(
        heading=("Segoe UI", "Helvetica", "Arial"),
        body=("Segoe UI", "Helvetica", "Arial"),
        mono=("Consolas", "Courier New", "DejaVu Sans Mono")),
    bundled_fonts=False,
    ttk_base="",
    configure_styles=_legacy_styles,
    apply_palette=_legacy_palette,
)


# ---------------------------------------------------------------- the registry
THEMES = {t.key: t for t in (MODERN, LEGACY)}   # add new themes here
DEFAULT_THEME = "modern"
ACTIVE = MODERN
RANGE_COLOURS = MODERN.range_colours


def use(key):
    """
    Make this theme the one the window is built with, and return it. An unknown name gives the default
    theme, so a settings file from a newer version never stops the program from starting.
    """
    global ACTIVE, RANGE_COLOURS
    theme = THEMES.get(str(key).strip().lower(), THEMES[DEFAULT_THEME])
    globals().update(theme.colours)
    ACTIVE, RANGE_COLOURS = theme, theme.range_colours
    return theme


use(DEFAULT_THEME)


def resolve_font_families(root, theme=None):
    """Choose installed font families for a theme, falling back cleanly when its fonts are absent."""
    theme = theme or ACTIVE
    available = {family.casefold(): family for family in tkfont.families(root)}
    default = tkfont.nametofont("TkDefaultFont", root).actual("family")
    fixed = tkfont.nametofont("TkFixedFont", root).actual("family")

    def choose(candidates, fallback):
        return next((available[name.casefold()] for name in candidates if name.casefold() in available), fallback)

    return {"heading": choose(theme.fonts["heading"], default),
            "body": choose(theme.fonts["body"], default),
            "mono": choose(theme.fonts["mono"], fixed)}


def apply_tk_palette(root, fonts, theme=None):
    """Set defaults for classic Tk widgets and dialogs, as the theme wants them."""
    (theme or ACTIVE).apply_palette(root, fonts)
