"""Local display preferences, separate from machine and delivery settings."""
import json
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, ttk
from .storage import atomic_write

PALETTES = {
    "Industrial": {
        "background": "#f4f5f2", "panel": "#ffffff", "text": "#26343d", "muted": "#52636b",
        "line": "#d3dcda", "accent": "#176b70", "selection": "#dcefee",
        "success": "#176b45", "error": "#a92720", "warning": "#855600",
    },
    "Light": {
        "background": "#f2f5f8", "panel": "#ffffff", "text": "#17212e", "muted": "#4f5f72",
        "line": "#c7d0db", "accent": "#0759aa", "selection": "#d6e8ff",
        "success": "#147336", "error": "#b42318", "warning": "#995500",
    },
    "Dark": {
        "background": "#17212e", "panel": "#222e3d", "text": "#f0f4fa", "muted": "#c1ccda",
        "line": "#55677e", "accent": "#9ccbff", "selection": "#304c6d",
        "success": "#8be8aa", "error": "#ffaaaa", "warning": "#ffcf83",
    },
    "High contrast": {
        "background": "#000000", "panel": "#111111", "text": "#ffffff", "muted": "#eeeeee",
        "line": "#dddddd", "accent": "#61e5ff", "selection": "#173c50",
        "success": "#8dff9e", "error": "#ffb0b0", "warning": "#fff16d",
    },
}
SIZES = {"Small": 9, "Medium": 10, "Large": 12, "Extra large": 14}


def resolve_font_families(root):
    """Friend's bundled typography, with installed-font fallbacks on every OS."""
    available = {family.casefold(): family for family in tkfont.families(root)}
    def choose(names, fallback):
        return next((available[n.casefold()] for n in names if n.casefold() in available),
                    tkfont.nametofont(fallback, root).actual("family"))
    return {"body": choose(("Roboto", "Segoe UI", "Helvetica Neue"), "TkDefaultFont"),
            "heading": choose(("Josefin Sans", "Avenir Next", "Roboto", "Segoe UI"), "TkDefaultFont"),
            "mono": choose(("Roboto Mono", "Menlo", "Consolas", "DejaVu Sans Mono"), "TkFixedFont")}


def normalize_preferences(values):
    values = values if isinstance(values, dict) else {}
    theme, size, interface = values.get("theme"), values.get("text_size"), values.get("interface")
    return {"theme": theme if isinstance(theme, str) and theme in PALETTES else "Light",
            "text_size": size if isinstance(size, str) and size in SIZES else "Medium",
            "interface": interface if isinstance(interface, str) and interface in ("Guided", "Full workspace") else "Guided"}


class Appearance:
    def __init__(self, root, data_dir):
        self.root = root
        self.path = Path(data_dir) / "appearance.json"
        try:
            self.preferences = normalize_preferences(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            self.preferences = normalize_preferences({})
        self.style = ttk.Style(root)
        self.style.theme_use("clam")  # consistently supports application colors on Windows and Linux
        self.defaults = {name: tkfont.nametofont(name).actual() for name in tkfont.names()
                         if name.startswith("Tk")}
        self.explicit_fonts = {}
        self.font_objects = {}

    @property
    def colors(self):
        return PALETTES[self.preferences["theme"]]

    @property
    def points(self):
        return SIZES[self.preferences["text_size"]]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(self.preferences, indent=2))

    def apply(self):
        c, size = self.colors, self.points
        self.root.configure(background=c["background"])
        for name, original in self.defaults.items():
            tkfont.nametofont(name).configure(
                family=self.root.font_families["mono" if name == "TkFixedFont" else "body"],
                size=max(7, round(abs(original["size"]) * size / 9)))
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.style.configure(".", background=c["background"], foreground=c["text"],
                             font=(family, size), bordercolor=c["line"],
                             lightcolor=c["line"], darkcolor=c["line"],
                             troughcolor=c["background"], selectbackground=c["selection"],
                             selectforeground=c["text"])
        self.style.configure("TEntry", fieldbackground=c["panel"], insertcolor=c["text"])
        self.style.configure("TCombobox", fieldbackground=c["panel"], arrowsize=size + 4)
        self.style.map("TCombobox", fieldbackground=[("readonly", c["panel"])],
                       foreground=[("readonly", c["text"])],
                       selectbackground=[("readonly", c["selection"])],
                       selectforeground=[("readonly", c["text"])])
        self.style.configure("TButton", padding=(8, 4))
        self.style.map("TButton", background=[("active", c["selection"])],
                       foreground=[("disabled", c["muted"]), ("active", c["text"])])
        self.style.configure("Small.TLabel", foreground=c["muted"], font=(family, max(8, size - 1)))
        self.style.configure("PageTitle.TLabel", font=(self.root.font_families["heading"], size + 4, "bold"))
        self.style.configure("EmptyState.TLabel", foreground=c["muted"], padding=12)
        self.style.configure("Metric.TFrame", relief="solid", borderwidth=1)
        for name in ("TButton", "Toolbutton", "TEntry", "TCombobox", "TCheckbutton", "TRadiobutton"):
            self.style.configure(name, focuscolor=c["accent"])
            self.style.map(name, bordercolor=[("focus", c["accent"])])
        for style in ("Go.TButton", "Go.Toolbutton", "Go.TLabel"):
            self.style.configure(style, font=(family, size, "bold"))
        self.style.configure("Count.TLabel", font=(family, size + 5, "bold"))
        self.style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"],
                             rowheight=self.root.px(size * 2 + 6))
        self.style.configure("Nav.Treeview", rowheight=self.root.px(size * 2 + 14), font=(family, size))
        self.style.map("Treeview", background=[("selected", c["selection"])],
                       foreground=[("selected", c["text"])])
        self.style.map("TNotebook.Tab", background=[("selected", c["panel"])],
                       foreground=[("selected", c["accent"])])
        self.style.map("TCheckbutton", background=[("active", c["background"])])
        self.root.option_add("*TCombobox*Listbox.background", c["panel"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["selection"])
        self._widgets(self.root)

    def _widgets(self, parent):
        c = self.colors
        for widget in parent.winfo_children():
            # Preserve each explicit font's family and weight, and scale it from its original size.
            if "font" in widget.keys() and widget.cget("font"):
                key = str(widget)
                if key not in self.explicit_fonts:
                    configured = str(widget.cget("font"))
                    self.explicit_fonts[key] = self.defaults.get(configured) or tkfont.Font(
                        root=self.root, font=configured).actual()
                original = self.explicit_fonts[key]
                font = self.font_objects.get(key) or tkfont.Font(root=self.root, **original)
                mono = (isinstance(widget, tk.Text) and not getattr(widget, "reading_text", False)) or original["family"].lower() in (
                    "consolas", "courier", "courier new", "roboto mono", "dejavu sans mono")
                font.configure(family=self.root.font_families["mono" if mono else "body"],
                               size=max(7, round(abs(original["size"]) * self.points / 9)))
                self.font_objects[key] = font
                widget.configure(font=font)
            if isinstance(widget, tk.Text):
                widget.configure(background=c["panel"], foreground=c["text"], insertbackground=c["text"],
                                 selectbackground=c["selection"], selectforeground=c["text"])
            elif isinstance(widget, tk.Canvas):
                widget.configure(background=c["background"], highlightbackground=c["line"])
            elif isinstance(widget, tk.Menu):
                widget.configure(background=c["panel"], foreground=c["text"],
                                 activebackground=c["selection"], activeforeground=c["text"])
            elif isinstance(widget, tk.Label) and widget.cget("text") == "●":
                widget.configure(background=c["background"])
            self._widgets(widget)
