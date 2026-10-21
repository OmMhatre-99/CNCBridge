"""
Themes for the CNCBridge window: colours, fonts and the styles of the ttk widgets.

To add a theme, write its colours (every name in COLOUR_NAMES) and a function that styles the ttk widgets,
create a Theme for it and add it to THEMES. It then appears in Settings > Appearance. A theme may also carry a
dark version of its colours (dark_colours); use(key, "dark") then returns the theme with those in place.

The rest of the program reads colours as theme.ACCENT, theme.SURFACE and so on, at the moment a widget is
built, so whatever theme use() selected is what a new window gets. A theme is chosen once, when the window
starts; changing it in the settings takes effect after a restart.
"""
import ctypes
import sys
from dataclasses import dataclass, replace
from tkinter import font as tkfont
from typing import Callable

from .i18n import N_

COLOUR_NAMES = (
    "BACKGROUND", "SURFACE", "SURFACE_ALT", "TEXT", "MUTED", "BORDER", "DISABLED_SURFACE", "DISABLED_TEXT",
    "DISABLED_ACCENT", "HOVER_SURFACE", "FOCUS_SURFACE", "ACTIVE_HEADER", "ACCENT", "ACCENT_HOVER", "ACCENT_TINT",
    "FOCUS", "SUCCESS", "WARNING", "ERROR", "ERROR_TINT", "WARNING_TINT", "IDLE", "WHITE", "GRID_LINE",
    "GRID_HEADER", "GRID_HEADER_TEXT", "GRID_SELECTION", "GRID_SELECTION_EDGE", "GRID_HEADER_SELECTION",
    "SIDEBAR", "SIDEBAR_TEXT", "SIDEBAR_MUTED", "SIDEBAR_ACTIVE", "SIDEBAR_HOVER")  # the last five: a theme with a sidebar


@dataclass(frozen=True)
class Layout:
    """Sizes and spacing of the window, so a theme can be compact or airy as well as coloured."""
    window: tuple             # width, height, minimum width, minimum height (in 96-dpi pixels)
    page_intros: bool         # a title and a hint at the top of every tab
    intro_inline: bool        # ... on one line instead of two
    metric_cards: bool        # counters in boxes (otherwise a bare number over its caption)
    tab_pad: tuple            # space around the content of a tab
    box_pad: tuple            # space inside a framed box
    box_gap: int              # space between boxes
    row: int                  # vertical space around a row of fields
    range_size: int           # font size of cell ranges
    text_mono: int            # font size of the preview, log and other text areas
    code_mono: bool           # short codes (file pattern, minutes, ...) in the monospaced font
    counter_size: int         # font size of the counters
    state: tuple              # size of the status dot and of the status text
    check_text: str           # text beside the "first row is header" tick
    grid_font: int           # font size in the cell grid
    grid_badge: int          # font size of the slot numbers on the grid
    grid_empty: tuple         # sizes of the two lines shown while the grid is empty
    menu_size: int            # font size of the menu bar and its menus
    sidebar: bool = False     # navigation in a sidebar on the left; the tab strip is hidden
    monitor_page: bool = False   # a Monitor page: the counters, the health line, the latest files, the destinations
    cards: bool = False       # framed boxes are white cards on the page colour
    sidebar_width: int = 232  # in 96-dpi pixels


@dataclass(frozen=True)
class Theme:
    key: str                  # stored in the settings
    label: str                # shown in the Appearance box
    description: str
    colours: dict             # every name in COLOUR_NAMES
    range_colours: tuple      # (tint, edge) for each of the 10 selection slots
    fonts: dict               # candidate font families, best first, for "heading", "body", "strong" and "mono"
    layout: Layout
    bundled_fonts: bool       # register the fonts shipped in assets/fonts before the window opens
    ttk_base: str             # ttk theme to start from; "" keeps the native Windows one
    configure_styles: Callable   # (style, px, fonts) -> None
    apply_palette: Callable      # (root, fonts) -> None, defaults for classic Tk widgets and dialogs
    # the dark version, if the theme has one; whatever is left None is taken from the light version
    dark_colours: dict = None
    dark_range_colours: tuple = None
    dark_ttk_base: str = None
    dark_configure_styles: Callable = None
    dark_apply_palette: Callable = None
    dark: bool = False        # set on the copy that use() returns when the dark version is in force


def strong_font(fonts, size):
    """
    A font tuple for emphasised text. The bundled Roboto is a variable font with no bold of its own on
    Windows, so bold text in it looks regular; a "Semibold" family is used as it is, any other is made bold.
    """
    family = fonts["strong"]
    return (family, size) if "semibold" in family.casefold() else (family, size, "bold")


def invert_colour(colour):
    """'#rrggbb' as on an inverted screen: every channel becomes 255 minus itself."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (255 - r, 255 - g, 255 - b)


def _row_height(px, font, minimum, pad=8):
    """Height of a table row: the theme's minimum, or the text's line height plus a little air when larger."""
    return max(px(minimum), tkfont.Font(font=font).metrics("linespace") + px(pad))


# ---------------------------------------------------------------- modern: light industrial
def _modern_palette(root, fonts):
    """Set defaults for classic Tk widgets and Tk-rendered dialogs."""
    c = ACTIVE.colours
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
    root.option_add("*Menu.disabledForeground", c["DISABLED_TEXT"])
    root.option_add("*Dialog.background", c["BACKGROUND"])
    root.option_add("*Dialog.msg.background", c["BACKGROUND"])
    root.option_add("*Dialog.msg.foreground", c["TEXT"])
    root.option_add("*Dialog.button.background", c["SURFACE"])
    root.option_add("*Dialog.button.foreground", c["TEXT"])
    # Not "*Font": that also reaches the ttk labels, whose own font option then beats the font a style gives
    # them, so page titles and counters would never get their large fonts.
    for widget in ("Label", "Button", "Checkbutton", "Radiobutton", "Entry", "Text", "Listbox", "Spinbox",
                   "TEntry", "TCombobox", "TSpinbox"):  # ttk fields have a font of their own, not the style's
        root.option_add(f"*{widget}.font", (fonts["body"], 11))
    root.option_add("*TCombobox*Listbox.font", (fonts["body"], 11))
    root.option_add("*Menu.font", (fonts["body"], 11))
    root.option_add("*Dialog.msg.font", (fonts["body"], 11))
    root.option_add("*Dialog.button.font", (fonts["body"], 11))


def _modern_styles(style, px, fonts):
    c = ACTIVE.colours
    BACKGROUND, SURFACE, SURFACE_ALT, TEXT, MUTED, BORDER = (c[k] for k in (
        "BACKGROUND", "SURFACE", "SURFACE_ALT", "TEXT", "MUTED", "BORDER"))
    DISABLED_SURFACE, DISABLED_TEXT, DISABLED_ACCENT, HOVER_SURFACE, FOCUS_SURFACE, ACTIVE_HEADER = (c[k] for k in (
        "DISABLED_SURFACE", "DISABLED_TEXT", "DISABLED_ACCENT", "HOVER_SURFACE", "FOCUS_SURFACE", "ACTIVE_HEADER"))
    ACCENT, ACCENT_HOVER, ACCENT_TINT, FOCUS, WHITE = (c[k] for k in (
        "ACCENT", "ACCENT_HOVER", "ACCENT_TINT", "FOCUS", "WHITE"))
    body, heading, mono = fonts["body"], fonts["heading"], fonts["mono"]
    layout = ACTIVE.layout
    style.configure(".", font=(body, 11), foreground=TEXT)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Small.TLabel", background=BACKGROUND, foreground=MUTED)
    style.configure("PageTitle.TLabel", background=BACKGROUND, foreground=TEXT,
                    font=(heading, 15, "bold"))
    style.configure("PageHint.TLabel", background=BACKGROUND, foreground=MUTED, font=(body, 11))
    style.configure("EmptyState.TLabel", background=SURFACE, foreground=MUTED,
                    font=(body, 12), justify="center", padding=(20, 14))
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("Card.TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("CardSmall.TLabel", background=BACKGROUND, foreground=MUTED)
    style.configure("MetricSmall.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("MetricValue.TLabel", background=SURFACE, foreground=TEXT,
                    font=strong_font(fonts, layout.counter_size))
    style.configure("TLabelframe", background=BACKGROUND, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BACKGROUND, foreground=TEXT,
                    font=(heading, 12, "bold"))
    style.configure("Count.TLabel", background=BACKGROUND, foreground=TEXT, font=strong_font(fonts, 16))
    style.configure("Metric.TFrame", background=SURFACE, relief="solid", borderwidth=1)
    style.configure("TButton", padding=(px(11), px(6)), background=SURFACE,
                    bordercolor=BORDER, focuscolor=FOCUS)
    style.map("TButton", background=[("active", HOVER_SURFACE), ("disabled", DISABLED_SURFACE)],
              foreground=[("disabled", DISABLED_TEXT)], bordercolor=[("focus", FOCUS), ("active", ACCENT)])
    style.configure("Go.TButton", font=strong_font(fonts, 11), background=ACCENT,
                    foreground=WHITE, bordercolor=ACCENT)
    style.map("Go.TButton", background=[("active", ACCENT_HOVER), ("disabled", DISABLED_ACCENT)],
              foreground=[("disabled", WHITE)], bordercolor=[("focus", FOCUS)])
    style.configure("Toolbutton", padding=(px(10), px(6)), background=SURFACE,
                    bordercolor=BORDER, focuscolor=FOCUS)
    style.map("Toolbutton", background=[("active", HOVER_SURFACE)], bordercolor=[("focus", FOCUS),
                                                                               ("active", ACCENT)])
    style.configure("Go.Toolbutton", font=strong_font(fonts, 11), background=ACCENT,
                    foreground=WHITE, bordercolor=ACCENT)
    style.map("Go.Toolbutton", background=[("active", ACCENT_HOVER), ("disabled", DISABLED_ACCENT)],
              foreground=[("disabled", WHITE)], bordercolor=[("focus", FOCUS)])
    style.configure("MenuBar.TFrame", background=SURFACE)
    style.configure("MenuBar.Toolbutton", font=(body, layout.menu_size), padding=(px(12), px(6)),
                    background=SURFACE, foreground=TEXT, bordercolor=SURFACE, borderwidth=0, relief="flat")
    style.map("MenuBar.Toolbutton", background=[("pressed", ACCENT_TINT), ("active", HOVER_SURFACE)],
              bordercolor=[("pressed", ACCENT_TINT), ("active", HOVER_SURFACE)])
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
                    focuscolor=FOCUS, indicatorsize=px(15), indicatorbackground=SURFACE, indicatorcolor=SURFACE,
                    indicatorforeground=ACCENT, upperbordercolor=MUTED, lowerbordercolor=MUTED)
    style.map("TCheckbutton", indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_TINT), ("disabled", SURFACE_ALT)])
    style.map("TCheckbutton", background=[("active", BACKGROUND), ("disabled", BACKGROUND)],
              foreground=[("disabled", MUTED)])
    style.configure("TRadiobutton", background=BACKGROUND, foreground=TEXT, padding=(0, px(4)),
                    focuscolor=FOCUS, indicatorsize=px(15), indicatorbackground=SURFACE, indicatorcolor=SURFACE,
                    indicatorforeground=ACCENT, upperbordercolor=MUTED, lowerbordercolor=MUTED)
    style.map("TRadiobutton", indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_TINT), ("disabled", SURFACE_ALT)])
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
                    padding=(px(15), px(9)), font=strong_font(fonts, 11))
    style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", SURFACE_ALT)],
              foreground=[("selected", ACCENT), ("active", TEXT)], bordercolor=[("focus", FOCUS)])
    style.configure("Treeview", rowheight=_row_height(px, (body, 11), 30), background=SURFACE,
                    fieldbackground=SURFACE, foreground=TEXT, bordercolor=BORDER, font=(body, 11))
    style.configure("History.Treeview", rowheight=_row_height(px, (mono, 11), 30), background=SURFACE,
                    fieldbackground=SURFACE, foreground=TEXT, bordercolor=BORDER, font=(mono, 11))
    style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=TEXT,
                    font=strong_font(fonts, 10), padding=(px(8), px(8)))
    style.configure("History.Treeview.Heading", background=SURFACE_ALT, foreground=TEXT,
                    font=strong_font(fonts, 10), padding=(px(8), px(8)))
    style.map("Treeview", background=[("selected", ACCENT_TINT), ("focus", FOCUS_SURFACE)],
              foreground=[("selected", TEXT)])
    style.map("Treeview.Heading", background=[("active", ACTIVE_HEADER)],
              foreground=[("active", TEXT)])
    style.configure("TScrollbar", arrowsize=px(15))


MODERN = Theme(
    key="modern",
    label=N_("Modern"),
    description=N_("Light industrial look with flat controls and the bundled Roboto fonts. The default."),
    colours=dict(
        BACKGROUND="#f4f5f2", SURFACE="#ffffff", SURFACE_ALT="#edf2f1", TEXT="#26343d", MUTED="#52636b",
        BORDER="#d3dcda", DISABLED_SURFACE="#ecefed", DISABLED_TEXT="#68777d", DISABLED_ACCENT="#5a7372",
        HOVER_SURFACE="#e8eeec", FOCUS_SURFACE="#e7efee", ACTIVE_HEADER="#e2eae8", ACCENT="#176b70",
        ACCENT_HOVER="#12575b", ACCENT_TINT="#dcefee", FOCUS="#a85b00", SUCCESS="#176b45", WARNING="#855600",
        ERROR="#a92720", ERROR_TINT="#fbe9e7", WARNING_TINT="#fff1cf", IDLE="#52636b", WHITE="#ffffff",
        GRID_LINE="#d3dcda", GRID_HEADER="#edf2f1", GRID_HEADER_TEXT="#52636b", GRID_SELECTION="#dcefee",
        GRID_SELECTION_EDGE="#176b70", GRID_HEADER_SELECTION="#c3dedb",
        SIDEBAR="#26343d", SIDEBAR_TEXT="#dbe3e6", SIDEBAR_MUTED="#9fb0b8", SIDEBAR_ACTIVE="#34464f",
        SIDEBAR_HOVER="#2e3d46"),
    range_colours=(
        ("#e6f1ef", "#176b70"), ("#e8edf5", "#3f5f86"), ("#f3eee2", "#795a1c"), ("#e9f0e3", "#4f7040"),
        ("#f3e9e6", "#9a4d3e"), ("#ece8f2", "#65527f"), ("#e3efed", "#35766e"), ("#f1eee2", "#756628"),
        ("#e9ecec", "#56666a"), ("#f2e9ee", "#895169")),
    fonts=dict(
        heading=("Josefin Sans", "Avenir Next", "Avenir", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
        body=("Roboto", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
        strong=("Segoe UI Semibold", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
        mono=("Roboto Mono", "Menlo", "Monaco", "Consolas", "Courier New", "DejaVu Sans Mono")),
    layout=Layout(
        window=(1240, 790, 1020, 660), page_intros=True, intro_inline=True, metric_cards=True,
        tab_pad=(16, 12), box_pad=(12, 8), box_gap=10, row=4, range_size=11, text_mono=11, code_mono=True,
        counter_size=16, state=(15, 12), check_text="Yes", grid_font=11, grid_badge=9, grid_empty=(13, 11),
        menu_size=11),
    bundled_fonts=True,
    ttk_base="clam",
    configure_styles=_modern_styles,
    apply_palette=_modern_palette,
)


# ---------------------------------------------------------------- legacy: the original look
def _legacy_palette(root, fonts):
    """The original look left Tk's own defaults alone (system colours, Windows fonts)."""


def _legacy_styles(style, px, fonts):
    """Version 1.0: native Windows controls, Segoe UI 9, a few tweaks. Colours come from the Legacy palette."""
    c = ACTIVE.colours
    body = fonts["body"]
    layout = ACTIVE.layout
    style.configure(".", font=(body, 9))
    style.configure("MenuBar.Toolbutton", font=(body, layout.menu_size), padding=(px(10), px(5)))
    style.configure("Small.TLabel", foreground=c["MUTED"])
    style.configure("CardSmall.TLabel", foreground=c["MUTED"])
    style.configure("MetricSmall.TLabel", foreground=c["MUTED"])
    style.configure("PageTitle.TLabel", font=(fonts["heading"], 14, "bold"))
    style.configure("PageHint.TLabel", foreground=c["MUTED"])
    style.configure("EmptyState.TLabel", foreground=c["MUTED"], font=(body, 10), justify="center", padding=(20, 14))
    style.configure("MetricValue.TLabel", font=strong_font(fonts, layout.counter_size))
    style.configure("Count.TLabel", font=strong_font(fonts, layout.counter_size))
    style.configure("TLabelframe.Label", font=(body, 9))
    style.configure("Metric.TFrame", relief="solid", borderwidth=1)
    style.configure("TButton", padding=(px(8), px(2)))
    style.configure("Go.TButton", font=strong_font(fonts, 9))
    style.configure("Toolbutton", padding=(px(8), px(4)))
    style.configure("Go.Toolbutton", font=strong_font(fonts, 9))
    style.configure("Treeview", rowheight=_row_height(px, (body, 9), 22, 6))
    style.configure("History.Treeview", rowheight=_row_height(px, (fonts["mono"], 9), 22, 6))


def _legacy_dark_styles(style, px, fonts):
    """
    Legacy with its colours inverted. The native Windows controls cannot be recoloured, so this starts from
    Tk's "alt" theme, which has the same classic shapes, and paints every widget with the inverted palette.
    """
    _legacy_styles(style, px, fonts)
    c = ACTIVE.colours
    BACKGROUND, SURFACE, SURFACE_ALT, TEXT, MUTED, BORDER = (c[k] for k in (
        "BACKGROUND", "SURFACE", "SURFACE_ALT", "TEXT", "MUTED", "BORDER"))
    HOVER, ACCENT, ACCENT_HOVER, ACCENT_TINT, DISABLED_TEXT, WHITE, FOCUS = (c[k] for k in (
        "HOVER_SURFACE", "ACCENT", "ACCENT_HOVER", "ACCENT_TINT", "DISABLED_TEXT", "WHITE", "FOCUS"))
    style.configure(".", background=BACKGROUND, foreground=TEXT, fieldbackground=SURFACE, troughcolor=BACKGROUND,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, focuscolor=FOCUS, insertcolor=TEXT,
                    selectbackground=ACCENT_TINT, selectforeground=TEXT)
    style.map(".", foreground=[("disabled", DISABLED_TEXT)])
    for name in ("Small.TLabel", "CardSmall.TLabel", "PageHint.TLabel", "EmptyState.TLabel"):
        style.configure(name, foreground=MUTED)
    style.configure("MetricSmall.TLabel", foreground=MUTED)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("Metric.TFrame", background=SURFACE, bordercolor=BORDER)
    style.configure("TButton", background=SURFACE_ALT, foreground=TEXT)
    style.configure("Toolbutton", background=BACKGROUND, foreground=TEXT)
    style.configure("MenuBar.Toolbutton", background=BACKGROUND, foreground=TEXT)
    style.configure("Go.TButton", background=ACCENT, foreground=WHITE)
    style.configure("Go.Toolbutton", background=ACCENT, foreground=WHITE)
    style.map("TButton", background=[("active", HOVER), ("disabled", BACKGROUND)])
    style.map("Toolbutton", background=[("active", HOVER), ("pressed", ACCENT_TINT)])
    style.map("MenuBar.Toolbutton", background=[("pressed", ACCENT_TINT), ("active", HOVER)])
    style.map("Go.TButton", background=[("active", ACCENT_HOVER), ("disabled", BACKGROUND)],
              foreground=[("disabled", DISABLED_TEXT)])
    style.map("Go.Toolbutton", background=[("active", ACCENT_HOVER), ("disabled", BACKGROUND)],
              foreground=[("disabled", DISABLED_TEXT)])
    style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT)
    style.map("TEntry", fieldbackground=[("readonly", SURFACE_ALT), ("disabled", BACKGROUND)],
              foreground=[("disabled", DISABLED_TEXT)])
    style.configure("TCombobox", fieldbackground=SURFACE, foreground=TEXT, background=SURFACE_ALT, arrowcolor=TEXT)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE), ("disabled", BACKGROUND)],
              foreground=[("disabled", DISABLED_TEXT)], background=[("active", HOVER)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=BACKGROUND, foreground=TEXT, indicatorbackground=SURFACE,
                        indicatorforeground=TEXT, indicatorcolor=SURFACE)
        style.map(name, background=[("active", BACKGROUND)], indicatorcolor=[("selected", ACCENT)])
    style.configure("TLabelframe", background=BACKGROUND, bordercolor=BORDER)
    style.configure("TScrollbar", background=SURFACE_ALT, troughcolor=BACKGROUND, arrowcolor=TEXT)
    style.map("TScrollbar", background=[("active", HOVER)])
    style.configure("TPanedwindow", background=BACKGROUND)
    style.configure("Sash", background=BORDER)
    style.configure("TSeparator", background=BORDER)
    style.configure("TNotebook", background=BACKGROUND)
    style.configure("TNotebook.Tab", background=SURFACE_ALT, foreground=TEXT)
    style.map("TNotebook.Tab", background=[("selected", BACKGROUND), ("active", HOVER)])
    for name in ("Treeview", "History.Treeview"):
        style.configure(name, background=SURFACE, fieldbackground=SURFACE, foreground=TEXT)
        style.map(name, background=[("selected", ACCENT_TINT)], foreground=[("selected", TEXT)])
    for name in ("Treeview.Heading", "History.Treeview.Heading"):
        style.configure(name, background=SURFACE_ALT, foreground=TEXT)
        style.map(name, background=[("active", HOVER)])


def _legacy_dark_palette(root, fonts):
    c = ACTIVE.colours
    root.tk_setPalette(background=c["BACKGROUND"], foreground=c["TEXT"], activeBackground=c["HOVER_SURFACE"],
                       activeForeground=c["TEXT"], selectBackground=c["ACCENT_TINT"], selectForeground=c["TEXT"],
                       insertBackground=c["TEXT"], highlightColor=c["FOCUS"], troughColor=c["SURFACE"])
    root.option_add("*Menu.background", c["SURFACE"])
    root.option_add("*Menu.foreground", c["TEXT"])
    root.option_add("*Menu.activeBackground", c["ACCENT_TINT"])
    root.option_add("*Menu.activeForeground", c["TEXT"])
    root.option_add("*Menu.disabledForeground", c["DISABLED_TEXT"])
    root.option_add("*Dialog.background", c["BACKGROUND"])
    root.option_add("*Dialog.msg.foreground", c["TEXT"])


LEGACY = Theme(
    key="legacy",
    label=N_("Legacy"),
    description=N_("The look of version 1.0: native Windows controls, Segoe UI and Consolas, grey and blue, "
                   "compact spacing and no page titles."),
    colours=dict(
        BACKGROUND="#f0f0f0", SURFACE="#ffffff", SURFACE_ALT="#f1f1f1", TEXT="#000000", MUTED="#6b6b6b",
        BORDER="#adadad", DISABLED_SURFACE="#f0f0f0", DISABLED_TEXT="#a0a0a0", DISABLED_ACCENT="#a0a0a0",
        HOVER_SURFACE="#e5f1fb", FOCUS_SURFACE="#e5f1fb", ACTIVE_HEADER="#e5e5e5", ACCENT="#1a66d9",
        ACCENT_HOVER="#1555b3", ACCENT_TINT="#cfe2ff", FOCUS="#1a66d9", SUCCESS="#1e8e3e", WARNING="#b06000",
        ERROR="#c5221f", ERROR_TINT="#fbe4e2", WARNING_TINT="#fbeccb", IDLE="#6b6b6b", WHITE="#ffffff",
        GRID_LINE="#dcdcdc", GRID_HEADER="#f1f1f1", GRID_HEADER_TEXT="#555555", GRID_SELECTION="#cfe2ff",
        GRID_SELECTION_EDGE="#1a66d9", GRID_HEADER_SELECTION="#c5d7f2",
        SIDEBAR="#f0f0f0", SIDEBAR_TEXT="#000000", SIDEBAR_MUTED="#6b6b6b", SIDEBAR_ACTIVE="#cfe2ff",
        SIDEBAR_HOVER="#e5f1fb"),
    range_colours=(
        ("#fde9c8", "#c77700"), ("#d9f2dc", "#2e8b3d"), ("#f6dcee", "#b0308a"), ("#dbeafe", "#3b6fd4"),
        ("#fbe0dc", "#c0392b"), ("#e6e0f8", "#6b4fc4"), ("#d8f3f1", "#11847c"), ("#f3efc9", "#8a7a00"),
        ("#e8e8e8", "#666666"), ("#fcdfe9", "#c2185b")),
    fonts=dict(
        heading=("Segoe UI", "Helvetica", "Arial"),
        body=("Segoe UI", "Helvetica", "Arial"),
        strong=("Segoe UI", "Helvetica", "Arial"),
        mono=("Consolas", "Courier New", "DejaVu Sans Mono")),
    layout=Layout(
        window=(1180, 740, 980, 620), page_intros=False, intro_inline=False, metric_cards=False,
        tab_pad=(10, 10), box_pad=(10, 10), box_gap=10, row=3, range_size=10, text_mono=9, code_mono=False,
        counter_size=15, state=(14, 11), check_text="", grid_font=10, grid_badge=7, grid_empty=(10, 10),
        menu_size=10),
    bundled_fonts=False,
    ttk_base="",
    configure_styles=_legacy_styles,
    apply_palette=_legacy_palette,
)


# ---------------------------------------------------------------- dashboard: sidebar, Monitor page and cards
def _dashboard_styles(style, px, fonts):
    """
    Modern's widgets restyled as white cards on a calm grey page. The tab strip is hidden: the sidebar (see
    ui/sidebar.py) is the navigation. Styles named Inner.* are what the plain widgets inside a card get, so that
    they take the card's colour (ui/cards.py).
    """
    _modern_styles(style, px, fonts)
    c = ACTIVE.colours
    BACKGROUND, SURFACE, SURFACE_ALT, TEXT, MUTED, BORDER = (c[k] for k in (
        "BACKGROUND", "SURFACE", "SURFACE_ALT", "TEXT", "MUTED", "BORDER"))
    ACCENT, ACCENT_TINT, FOCUS, HOVER, DISABLED_TEXT = (c[k] for k in (
        "ACCENT", "ACCENT_TINT", "FOCUS", "HOVER_SURFACE", "DISABLED_TEXT"))
    body, heading = fonts["body"], fonts["heading"]
    layout = ACTIVE.layout
    # no tab strip, and no frame around the pages
    style.layout("TNotebook.Tab", [])
    style.layout("TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    style.configure("TNotebook", background=BACKGROUND, bordercolor=BACKGROUND, lightcolor=BACKGROUND,
                    darkcolor=BACKGROUND, borderwidth=0, tabmargins=(0, 0, 0, 0))
    # cards
    style.configure("TLabelframe", background=SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=SURFACE, foreground=TEXT, font=strong_font(fonts, 12))
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("CardSmall.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Metric.TFrame", background=SURFACE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    relief="solid", borderwidth=1)
    style.configure("MetricSmall.TLabel", background=SURFACE, foreground=MUTED, font=strong_font(fonts, 10))
    style.configure("MetricValue.TLabel", background=SURFACE, foreground=TEXT,
                    font=strong_font(fonts, layout.counter_size))
    style.configure("Inner.TFrame", background=SURFACE)
    style.configure("Inner.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Inner.Small.TLabel", background=SURFACE, foreground=MUTED)
    for name in ("Inner.TCheckbutton", "Inner.TRadiobutton"):
        style.configure(name, background=SURFACE, foreground=TEXT, padding=(0, px(3)), focuscolor=FOCUS,
                        indicatorsize=px(16), indicatorbackground=SURFACE, indicatorcolor=SURFACE,
                        indicatorforeground=ACCENT, upperbordercolor=MUTED, lowerbordercolor=MUTED)
        style.map(name, indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_TINT), ("disabled", SURFACE_ALT)],
                  background=[("active", SURFACE), ("disabled", SURFACE)], foreground=[("disabled", MUTED)])
    # the page: a title in the heading font, a hint under it
    style.configure("PageTitle.TLabel", background=BACKGROUND, foreground=TEXT, font=strong_font(fonts, 20))
    style.configure("PageHint.TLabel", background=BACKGROUND, foreground=MUTED, font=(body, 11))
    # the bar with the Start button, and the status pill in it
    style.configure("Toolbar.TFrame", background=SURFACE)
    style.configure("Pill.TFrame", background=SURFACE_ALT)
    style.configure("Pill.TLabel", background=SURFACE_ALT, foreground=TEXT, font=strong_font(fonts, 11))
    # roomier controls: a fingertip on a touch screen, and the eye find them easily
    style.configure("TButton", padding=(px(14), px(8)))
    style.configure("Go.TButton", padding=(px(14), px(8)))
    style.configure("Toolbutton", padding=(px(14), px(8)))
    style.configure("Go.Toolbutton", padding=(px(16), px(8)))
    style.configure("TEntry", padding=(px(10), px(8)))
    style.configure("TCombobox", padding=(px(10), px(8)))
    style.configure("Treeview", rowheight=_row_height(px, (body, 11), 36))
    style.configure("History.Treeview", rowheight=_row_height(px, (body, 11), 36), font=(body, 11))
    for name in ("Treeview.Heading", "History.Treeview.Heading"):
        style.configure(name, background=c["GRID_HEADER"], foreground=MUTED, padding=(px(10), px(9)))
    style.map("Treeview", background=[("selected", ACCENT_TINT), ("focus", c["FOCUS_SURFACE"])],
              foreground=[("selected", TEXT)])


DASHBOARD = Theme(
    key="dashboard",
    label=N_("Dashboard"),
    description=N_("A sidebar instead of tabs, a Monitor page with the counters and the latest files, and white "
                   "cards on a calm grey. IBM Plex fonts when they are installed."),
    colours=dict(
        BACKGROUND="#f3f4f6", SURFACE="#ffffff", SURFACE_ALT="#eef0f3", TEXT="#15181c", MUTED="#5b6470",
        BORDER="#d5d9df", DISABLED_SURFACE="#eceef1", DISABLED_TEXT="#7a838e", DISABLED_ACCENT="#6fa5a5",
        HOVER_SURFACE="#e8ebee", FOCUS_SURFACE="#e3f1f0", ACTIVE_HEADER="#e3e6ea", ACCENT="#0e6b6b",
        ACCENT_HOVER="#094848", ACCENT_TINT="#e3f1f0", FOCUS="#2e8f8a", SUCCESS="#1b6b43", WARNING="#9a5b00",
        ERROR="#b42318", ERROR_TINT="#fdecea", WARNING_TINT="#fdf1dc", IDLE="#5b6470", WHITE="#ffffff",
        GRID_LINE="#edeff2", GRID_HEADER="#f6f7f9", GRID_HEADER_TEXT="#4a525d", GRID_SELECTION="#e3f1f0",
        GRID_SELECTION_EDGE="#0e6b6b", GRID_HEADER_SELECTION="#c7e3e0",
        SIDEBAR="#14181d", SIDEBAR_TEXT="#c7cdd4", SIDEBAR_MUTED="#8e97a2", SIDEBAR_ACTIVE="#26303a",
        SIDEBAR_HOVER="#1d242b"),
    range_colours=(
        ("#e3f1f0", "#0e6b6b"), ("#e6eef9", "#1d5fb0"), ("#f6ebdd", "#8a4b08"), ("#efe8f7", "#6b3fa0"),
        ("#e6f2e7", "#2f7a3b"), ("#f8e7ef", "#a3295f"), ("#e3eff5", "#1f6f8f"), ("#f3efdb", "#7a6a12"),
        ("#eceef1", "#4a525d"), ("#fbe9e3", "#b3472c")),
    fonts=dict(
        heading=("IBM Plex Sans", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
        body=("IBM Plex Sans", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
        strong=("IBM Plex Sans SemiBold", "Segoe UI Semibold", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial"),
        mono=("IBM Plex Mono", "Cascadia Mono", "Consolas", "Menlo", "Monaco", "Courier New", "DejaVu Sans Mono")),
    layout=Layout(
        window=(1360, 840, 1120, 700), page_intros=True, intro_inline=False, metric_cards=True,
        tab_pad=(22, 16), box_pad=(16, 12), box_gap=16, row=5, range_size=11, text_mono=11, code_mono=True,
        counter_size=22, state=(12, 12), check_text="Yes", grid_font=11, grid_badge=9, grid_empty=(13, 11),
        menu_size=11, sidebar=True, monitor_page=True, cards=True, sidebar_width=232),
    bundled_fonts=False,
    ttk_base="clam",
    configure_styles=_dashboard_styles,
    apply_palette=_modern_palette,
)


# ---------------------------------------------------------------- dark versions
MODERN = replace(
    MODERN,
    dark_colours=dict(
        BACKGROUND="#171c20", SURFACE="#20272c", SURFACE_ALT="#2a3238", TEXT="#e6ecee", MUTED="#9fb0b8",
        BORDER="#3a464d", DISABLED_SURFACE="#252d32", DISABLED_TEXT="#76868d", DISABLED_ACCENT="#3d6a6a",
        HOVER_SURFACE="#2c353b", FOCUS_SURFACE="#26363a", ACTIVE_HEADER="#323d44", ACCENT="#4fb3b5",
        ACCENT_HOVER="#6cc6c8", ACCENT_TINT="#1f4a4c", FOCUS="#f0a53a", SUCCESS="#5cc98a", WARNING="#f0b445",
        ERROR="#ff8a80", ERROR_TINT="#4a2623", WARNING_TINT="#4a3a1c", IDLE="#9fb0b8", WHITE="#0c1a1b",
        GRID_LINE="#3a464d", GRID_HEADER="#2a3238", GRID_HEADER_TEXT="#9fb0b8", GRID_SELECTION="#1f4a4c",
        GRID_SELECTION_EDGE="#4fb3b5", GRID_HEADER_SELECTION="#2f5f61",
        SIDEBAR="#0f1316", SIDEBAR_TEXT="#dbe3e6", SIDEBAR_MUTED="#8d9ca5", SIDEBAR_ACTIVE="#26333a",
        SIDEBAR_HOVER="#1c262b"),
    dark_range_colours=(
        ("#1d3a3a", "#4fb3b5"), ("#222f44", "#7da4d8"), ("#3a3220", "#d4a94c"), ("#27381f", "#8cbf6f"),
        ("#3d2622", "#e08a78"), ("#2e2840", "#a994d4"), ("#1c3a36", "#5cc2b4"), ("#38361f", "#cfc25a"),
        ("#2c3235", "#9aa8ad"), ("#3c2634", "#d98fb0")))
DASHBOARD = replace(
    DASHBOARD,
    dark_colours=dict(
        BACKGROUND="#0f1317", SURFACE="#171c21", SURFACE_ALT="#1f262c", TEXT="#e6eaee", MUTED="#9aa5b1",
        BORDER="#2e3840", DISABLED_SURFACE="#1c2227", DISABLED_TEXT="#6f7a86", DISABLED_ACCENT="#2f6360",
        HOVER_SURFACE="#222a31", FOCUS_SURFACE="#1b2f30", ACTIVE_HEADER="#29323a", ACCENT="#4db6b0",
        ACCENT_HOVER="#7ccfca", ACCENT_TINT="#173b3b", FOCUS="#f0a53a", SUCCESS="#5cc98a", WARNING="#f0b445",
        ERROR="#ff8a80", ERROR_TINT="#3d2220", WARNING_TINT="#3d3018", IDLE="#9aa5b1", WHITE="#0b1516",
        GRID_LINE="#2a333a", GRID_HEADER="#1f262c", GRID_HEADER_TEXT="#9aa5b1", GRID_SELECTION="#173b3b",
        GRID_SELECTION_EDGE="#4db6b0", GRID_HEADER_SELECTION="#245354",
        SIDEBAR="#0a0d10", SIDEBAR_TEXT="#c7cdd4", SIDEBAR_MUTED="#8e97a2", SIDEBAR_ACTIVE="#1b2229",
        SIDEBAR_HOVER="#141a1f"),
    dark_range_colours=(
        ("#173b3b", "#4db6b0"), ("#1c2a40", "#7da4d8"), ("#3a2e1c", "#d4a257"), ("#2b2340", "#b09ad8"),
        ("#1f3523", "#7fc68a"), ("#3d2230", "#e08fb2"), ("#1a3340", "#6fb8d6"), ("#38341a", "#cfc25a"),
        ("#252c32", "#9aa8ad"), ("#3d2620", "#e5896f")))
LEGACY = replace(
    LEGACY,
    dark_colours={name: invert_colour(value) for name, value in LEGACY.colours.items()},
    dark_range_colours=tuple((invert_colour(tint), invert_colour(edge)) for tint, edge in LEGACY.range_colours),
    dark_ttk_base="alt",
    dark_configure_styles=_legacy_dark_styles,
    dark_apply_palette=_legacy_dark_palette)

# ---------------------------------------------------------------- the registry
THEMES = {t.key: t for t in (MODERN, LEGACY, DASHBOARD)}   # add new themes here
DEFAULT_THEME = "modern"
ACTIVE = MODERN
RANGE_COLOURS = MODERN.range_colours


def system_is_dark():
    """True when Windows is set to dark mode for apps."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:  # not Windows, or the setting is not there
        return False


def use(key, mode="light"):
    """
    Make this theme the one the window is built with, and return it. An unknown name gives the default
    theme, so a settings file from a newer version never stops the program from starting.

    mode is "light", "dark" or "system" (what Windows is set to). A theme without a dark version stays light.
    """
    global ACTIVE, RANGE_COLOURS
    theme = THEMES.get(str(key).strip().lower(), THEMES[DEFAULT_THEME])
    if theme.dark_colours and (mode == "dark" or (mode == "system" and system_is_dark())):
        theme = replace(
            theme, colours=theme.dark_colours, range_colours=theme.dark_range_colours or theme.range_colours,
            ttk_base=theme.ttk_base if theme.dark_ttk_base is None else theme.dark_ttk_base,
            configure_styles=theme.dark_configure_styles or theme.configure_styles,
            apply_palette=theme.dark_apply_palette or theme.apply_palette, dark=True)
    globals().update(theme.colours)
    ACTIVE, RANGE_COLOURS = theme, theme.range_colours
    return theme


def dark_title_bar(window, dark=True):
    """Make the Windows title bar of a window dark too. Does nothing where that is not possible."""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


# fonts offered in Settings > Appearance, when installed: clear user-interface fonts, not symbol or decorative ones
FONT_CHOICES = ("Segoe UI", "Roboto", "Arial", "Calibri", "Tahoma", "Verdana", "Bahnschrift", "Trebuchet MS",
                "Candara", "Corbel")


def installed_font_choices(root):
    available = {family.casefold() for family in tkfont.families(root)}
    return [name for name in FONT_CHOICES if name.casefold() in available]


use(DEFAULT_THEME)


def resolve_font_families(root, theme=None, family=""):
    """
    Choose installed font families for a theme, falling back cleanly when its fonts are absent. A chosen
    family (one of FONT_CHOICES) replaces the theme's heading, body and strong fonts; the monospaced one stays.
    """
    theme = theme or ACTIVE
    available = {name.casefold(): name for name in tkfont.families(root)}
    default = tkfont.nametofont("TkDefaultFont", root).actual("family")
    fixed = tkfont.nametofont("TkFixedFont", root).actual("family")

    def choose(candidates, fallback):
        return next((available[name.casefold()] for name in candidates if name.casefold() in available), fallback)

    if family and family.casefold() in available:
        chosen = available[family.casefold()]
        return {"heading": chosen, "body": chosen, "strong": available.get(f"{chosen} semibold".casefold(), chosen),
                "mono": choose(theme.fonts["mono"], fixed)}
    return {"heading": choose(theme.fonts["heading"], default),
            "body": choose(theme.fonts["body"], default),
            "strong": choose(theme.fonts["strong"], default),
            "mono": choose(theme.fonts["mono"], fixed)}


def apply_tk_palette(root, fonts, theme=None):
    """Set defaults for classic Tk widgets and dialogs, as the theme wants them."""
    (theme or ACTIVE).apply_palette(root, fonts)
