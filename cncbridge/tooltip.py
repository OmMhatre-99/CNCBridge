import tkinter as tk
from tkinter import ttk

from .theme import BORDER, SURFACE, TEXT, resolve_font_families


def _tooltip_window(widget, text, x, y):
    window = tk.Toplevel(widget)
    window.wm_overrideredirect(True)
    root = widget.winfo_toplevel()
    # Demo controls stay above other windows; keep their help popups above the
    # parent too, otherwise an overrideredirect tooltip can be painted behind
    # the always-on-top demo window on Windows.
    window.transient(root)
    window.attributes("-topmost", True)
    window.lift()
    fonts = getattr(root, "font_families", None) or resolve_font_families(root)
    label = tk.Label(window, text=text, background=SURFACE, foreground=TEXT,
                     relief="solid", borderwidth=1, highlightthickness=1, highlightbackground=BORDER,
                     font=(fonts["body"], 11), padx=8, pady=5, wraplength=340, justify="left")
    label.pack()
    window.update_idletasks()
    x = min(x, widget.winfo_screenwidth() - window.winfo_width() - 4)
    y = min(y, widget.winfo_screenheight() - window.winfo_height() - 4)
    window.wm_geometry(f"+{max(0, x)}+{max(0, y)}")
    return window

class Tooltip:
    """Create a tooltip for a given widget.

    Example::
        btn = ttk.Button(parent, text="Info")
        Tooltip(btn, "Press to start monitoring the CNC machine")

    The tooltip appears after 500 ms when the mouse hovers over the widget and disappears on leave or click.
    It also works for keyboard focus – pressing Tab to focus the widget shows the tooltip.
    """

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tipwindow: tk.Toplevel | None = None
        self.id: str | None = None
        self.widget.bind("<Enter>", self._enter, add="+")
        self.widget.bind("<Leave>", self._leave, add="+")
        self.widget.bind("<FocusIn>", self._enter, add="+")
        self.widget.bind("<FocusOut>", self._leave, add="+")
        self.widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, event=None):
        self._schedule()

    def _leave(self, event=None):
        self._unschedule()
        self._hide()

    def _schedule(self):
        self._unschedule()
        self.id = self.widget.after(self.delay, self._show)

    def _unschedule(self):
        if self.id is not None:
            self.widget.after_cancel(self.id)
            self.id = None

    def _show(self):
        self.id = None
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tipwindow = _tooltip_window(self.widget, self.text, x, y)

    def _hide(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()


class NotebookTabTooltip:
    """Show short help while the pointer rests over a notebook tab."""

    def __init__(self, notebook: ttk.Notebook, texts: dict[int, str], delay: int = 500):
        self.notebook = notebook
        self.texts = texts
        self.delay = delay
        self.tab_index: int | None = None
        self.position = (0, 0)
        self.id: str | None = None
        self.tipwindow: tk.Toplevel | None = None
        notebook.bind("<Motion>", self._motion, add="+")
        notebook.bind("<Leave>", self._leave, add="+")

    def _motion(self, event):
        try:
            index = self.notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            self._leave()
            return
        if index not in self.texts:
            self._leave()
            return
        if index == self.tab_index and (self.id is not None or self.tipwindow):
            return
        self._leave()
        self.tab_index = index
        self.position = (event.x, event.y)
        self.id = self.notebook.after(self.delay, self._show)

    def _show(self):
        self.id = None
        if self.tab_index is None or self.tipwindow:
            return
        x, y = self.position
        self.tipwindow = _tooltip_window(
            self.notebook, self.texts[self.tab_index],
            self.notebook.winfo_rootx() + x + 12,
            self.notebook.winfo_rooty() + y + 20)

    def _leave(self, event=None):
        if self.id is not None:
            self.notebook.after_cancel(self.id)
            self.id = None
        self.tab_index = None
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None
