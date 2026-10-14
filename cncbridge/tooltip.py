# """Utility for adding subtle hover tooltips to Tkinter widgets.
# The implementation follows a lightweight pattern using a Toplevel window that appears after a short delay.
# This module is UI‑only and does not affect any business logic or test behaviour.
#"""

import tkinter as tk
from tkinter import ttk

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
        self.id = None
        # Bind events for mouse and focus
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
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None

    def _show(self):
        if self.tipwindow or not self.text:
            return
        # Position the tooltip near the widget
        x, y, cx, cy = self.widget.bbox("insert") if self.widget.bbox("insert") else (0, 0, 0, 0)
        x = x + self.widget.winfo_rootx() + 20
        y = y + self.widget.winfo_rooty() + 20
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(tw, text=self.text, background="#ffffe0", relief="solid", borderwidth=1, padding=(4, 2))
        label.pack()

    def _hide(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()
