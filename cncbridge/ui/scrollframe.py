"""A page that scrolls when its content is taller than the room it has (large text, a small screen)."""
import tkinter as tk
from tkinter import ttk


class ScrollFrame(ttk.Frame):
    """
    Put the widgets of the page in .body. The scrollbar shows only while the content is taller than the page;
    the mouse wheel scrolls it while the pointer is over it.
    """

    def __init__(self, parent, background, padding=0):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=background, highlightthickness=0, borderwidth=0, width=1, height=1)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas, padding=padding)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._fit, add="+")
        self.canvas.bind("<Configure>", self._fit, add="+")
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    @property
    def scrolls(self):
        """True while the content is taller than the page."""
        return bool(self.bar.winfo_ismapped())

    def _fit(self, _event=None):
        # only the width is fixed: the height stays natural, so a change in the content's height is seen as a
        # resize of the body and comes back here
        self.canvas.itemconfigure(self._window, width=self.canvas.winfo_width())
        need, have = self.body.winfo_reqheight(), self.canvas.winfo_height()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        needed = have > 1 and need > have + 1
        if needed != self.scrolls:
            if needed:
                self.bar.pack(side="right", fill="y")
            else:
                self.bar.pack_forget()
                self.canvas.yview_moveto(0)

    def scroll(self, steps):
        """Scroll down by this many lines (up when negative), if the content needs it."""
        if self.scrolls:
            self.canvas.yview_scroll(steps, "units")

    def _wheel(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError):
            return
        while widget is not None:
            if widget is self:
                self.scroll(-1 if event.delta > 0 else 1)
                return
            widget = getattr(widget, "master", None)
