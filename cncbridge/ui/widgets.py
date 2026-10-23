"""
Controls of the themes that have chips (Layout.chips), drawn on small canvases because Tk has no rounded shapes:

    Chip        a rounded label with a tint, for a result or a destination (✓ Done, API link waiting)
    Switch      an on/off switch for a setting, in place of a check box
    Segmented   one row of choices, one of them chosen, in place of radio buttons
    Disclosure  a line that opens and closes the part below it ("More: send as, extra headers")

Switch and Segmented's segments answer to invoke() and configure(state=...) like the ttk controls they replace,
so the setup lock, the menus and the tests treat them the same.
"""
import tkinter as tk
from tkinter import font as tkfont, ttk

from .. import theme

# chip kinds -> (tint, text colour) names in the theme
KINDS = {"ok": ("SUCCESS_TINT", "SUCCESS"), "warn": ("WARNING_TINT", "WARNING"), "error": ("ERROR_TINT", "ERROR"),
         "idle": ("SURFACE_ALT", "MUTED"), "accent": ("ACCENT_TINT", "ACCENT")}


def _rounded(canvas, x1, y1, x2, y2, r, **options):
    """A rectangle with round ends (a pill when r is half the height)."""
    r = min(r, (y2 - y1) / 2, (x2 - x1) / 2)
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
              x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **options)


class Chip(tk.Canvas):
    def __init__(self, master, text="", kind="idle", bg=None, font=None, command=None):
        self.font = tkfont.Font(font=font or ("TkDefaultFont", 9))
        self.height = self.font.metrics("linespace") + 8
        super().__init__(master, height=self.height, width=10, bg=bg or theme.SURFACE, highlightthickness=0,
                         cursor="hand2" if command else "")
        self.text, self.kind = None, None
        if command:
            self.bind("<Button-1>", lambda event: command())
        self.set(text, kind)

    def set(self, text, kind="idle"):
        if (text, kind) == (self.text, self.kind):
            return
        self.text, self.kind = text, kind
        tint, ink = (getattr(theme, name) for name in KINDS.get(kind, KINDS["idle"]))
        width = self.font.measure(text) + 20
        self.delete("all")
        self.configure(width=width)
        _rounded(self, 1, 1, width - 1, self.height - 1, self.height / 2, fill=tint, outline=tint)
        self.create_text(width / 2, self.height / 2, text=text, fill=ink, font=self.font)


class Switch(tk.Frame):
    """A switch with its text beside it, bound to a BooleanVar."""

    def __init__(self, master, variable, text="", command=None, bg=None, font=None, px=None):
        bg = bg or theme.SURFACE
        self.px = px or (lambda n: n)
        super().__init__(master, bg=bg, takefocus=True, highlightthickness=2, highlightbackground=bg,
                         highlightcolor=theme.FOCUS, cursor="hand2")
        self.variable, self.command, self.state, self.bg = variable, command, "normal", bg
        self.track = tk.Canvas(self, width=self.px(38), height=self.px(22), bg=bg, highlightthickness=0)
        self.track.pack(side="left", padx=(2, 8), pady=2)
        self.label = tk.Label(self, text=text, bg=bg, fg=theme.TEXT, font=font, anchor="w")
        self.label.pack(side="left")
        for widget in (self, self.track, self.label):
            widget.bind("<Button-1>", lambda event: self.invoke())
        for key in ("<space>", "<Return>"):
            self.bind(key, lambda event: (self.invoke(), "break")[1])
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def invoke(self):
        if self.state == "disabled":
            return
        self.focus_set()
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def _draw(self):
        try:
            on = bool(self.variable.get())
        except tk.TclError:
            on = False
        off = self.state == "disabled"
        track = theme.DISABLED_SURFACE if off else theme.ACCENT if on else theme.BORDER
        knob = theme.DISABLED_TEXT if off else theme.WHITE if on else theme.SURFACE
        c, px = self.track, self.px
        c.delete("all")
        _rounded(c, 1, 1, px(37), px(21), px(10), fill=track, outline=track)
        x = px(27) if on else px(11)
        c.create_oval(x - px(8), px(3), x + px(8), px(19), fill=knob, outline=theme.BORDER if not on else knob)
        self.label.configure(fg=theme.DISABLED_TEXT if off else theme.TEXT)
        self.configure(cursor="" if off else "hand2", takefocus=not off)

    # the ttk way of asking and setting the state and the text
    def configure(self, cnf=None, **kw):
        if "state" in kw:
            self.state = str(kw.pop("state"))
            self._draw()
        if "text" in kw:
            self.label.configure(text=kw.pop("text"))
        return super().configure(cnf, **kw) if cnf or kw else None

    config = configure

    def cget(self, key):
        if key == "state":
            return self.state
        if key == "text":
            return self.label.cget("text")
        return super().cget(key)

    def __getitem__(self, key):
        return self.cget(key)

    def __setitem__(self, key, value):
        self.configure(**{key: value})


class Segment(tk.Label):
    """One choice of a Segmented control."""

    def __init__(self, owner, value, text):
        super().__init__(owner, text=text, padx=14, pady=5, cursor="hand2", font=owner.font)
        self.owner, self.value, self.state = owner, value, "normal"
        self.bind("<Button-1>", lambda event: self.invoke())
        self.bind("<Enter>", lambda event: owner._draw(hover=self))
        self.bind("<Leave>", lambda event: owner._draw())

    def invoke(self):
        if self.state != "disabled":
            self.owner.choose(self.value)

    def configure(self, cnf=None, **kw):
        if "state" in kw:
            self.state = str(kw.pop("state"))
            self.owner._draw()
        return super().configure(cnf, **kw) if cnf or kw else None

    config = configure

    def cget(self, key):
        return self.state if key == "state" else super().cget(key)

    def __getitem__(self, key):
        return self.cget(key)


class Segmented(tk.Frame):
    """A row of choices (JSON | CSV | TXT | PDF) bound to a StringVar; Left and Right move the choice."""

    def __init__(self, master, variable, choices, command=None, font=None):
        super().__init__(master, bg=theme.BORDER, padx=1, pady=1, takefocus=True, highlightthickness=2,
                         highlightbackground=theme.SURFACE, highlightcolor=theme.FOCUS)
        self.variable, self.command, self.font = variable, command, font
        self.segments = {}
        for i, (value, text) in enumerate(choices):
            segment = Segment(self, value, text)
            segment.pack(side="left", padx=(0 if i == 0 else 1, 0))
            self.segments[value] = segment
        self.bind("<Left>", lambda event: self._step(-1))
        self.bind("<Right>", lambda event: self._step(1))
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def choose(self, value):
        self.focus_set()
        self.variable.set(value)
        if self.command:
            self.command()

    def _step(self, by):
        values = [v for v, s in self.segments.items() if s.state != "disabled"]
        if values:
            now = self.variable.get()
            self.choose(values[(values.index(now) + by) % len(values) if now in values else 0])
        return "break"

    def _draw(self, hover=None):
        chosen = self.variable.get()
        for value, segment in self.segments.items():
            off = segment.state == "disabled"
            if value == chosen:
                bg, fg = (theme.DISABLED_ACCENT, theme.WHITE) if off else (theme.ACCENT, theme.WHITE)
            else:
                bg = theme.HOVER_SURFACE if segment is hover and not off else theme.SURFACE
                fg = theme.DISABLED_TEXT if off else theme.TEXT
            tk.Label.configure(segment, bg=bg, fg=fg, cursor="" if off else "hand2")


class Disclosure(ttk.Frame):
    """'▸ More: send as, extra headers' that opens the part below it. Put the content in .body."""

    def __init__(self, master, text, bg=None, font=None, opened=False):
        super().__init__(master)
        self.text = text
        self.header = tk.Label(self, text="", bg=bg or theme.SURFACE, fg=theme.ACCENT, cursor="hand2", font=font,
                               takefocus=True, highlightthickness=2, highlightbackground=bg or theme.SURFACE,
                               highlightcolor=theme.FOCUS, anchor="w")
        self.header.pack(fill="x")
        self.header.bind("<Button-1>", lambda event: self.toggle())
        for key in ("<space>", "<Return>"):
            self.header.bind(key, lambda event: (self.toggle(), "break")[1])
        self.body = ttk.Frame(self)
        self.opened = not opened
        self.toggle()

    def toggle(self):
        self.opened = not self.opened
        self.header.configure(text=("▾  " if self.opened else "▸  ") + self.text)
        if self.opened:
            self.body.pack(fill="x", pady=(4, 0))
        else:
            self.body.pack_forget()

    def open(self):
        if not self.opened:
            self.toggle()
