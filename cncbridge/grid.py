"""A read-only spreadsheet view of a CSV file: column letters, row numbers, drag to mark a cell range."""
import tkinter as tk
from bisect import bisect_right
from tkinter import font as tkfont
from tkinter import ttk

from .engine import col_letter

MAX_CELL_CHARS = 28
LINE, HEAD_BG, HEAD_FG = "#dcdcdc", "#f1f1f1", "#555555"
SEL_FILL, SEL_EDGE, HEAD_SEL = "#cfe2ff", "#1a66d9", "#c5d7f2"
# one tint + edge colour per selection slot
MARK_COLOURS = [("#fde9c8", "#c77700"), ("#d9f2dc", "#2e8b3d"), ("#f6dcee", "#b0308a"), ("#dbeafe", "#3b6fd4"),
                ("#fbe0dc", "#c0392b"), ("#e6e0f8", "#6b4fc4"), ("#d8f3f1", "#11847c"), ("#f3efc9", "#8a7a00"),
                ("#e8e8e8", "#666666"), ("#fcdfe9", "#c2185b")]


class Grid(ttk.Frame):
    """
    set_rows(rows) shows a file. The user drags (or shift-clicks) to mark a block; on_select is called
    with zero-based (col1, row1, col2, row2). set_marks() tints ranges already assigned to slots.
    """

    def __init__(self, master, on_select=None):
        super().__init__(master)
        self.on_select = on_select
        self.rows, self.ncols, self.colx = [], 0, [0]
        self.sel = self.anchor = self.active_cell = None
        self.marks = []  # (slot index, (col1, row1, col2, row2))
        self.colors = {"background": "white", "text": "#17212e", "muted": HEAD_FG,
                       "line": LINE, "panel": HEAD_BG, "selection": SEL_FILL, "accent": SEL_EDGE}
        self.mark_colors = MARK_COLOURS

        self.font = tkfont.Font(family="Consolas", size=10)
        self.badge_font = tkfont.Font(family="Segoe UI", size=7, weight="bold")
        self.cw = max(self.font.measure("0"), 6)
        self.rh = self.font.metrics("linespace") + 8
        self.rw = self.cw * 5 + 10

        self.canvas = tk.Canvas(self, background="white", highlightthickness=1, highlightbackground="#b5b5b5",
                                takefocus=True,
                                yscrollincrement=self.rh, xscrollincrement=self.cw * 4)
        vs = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        hs = ttk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        c = self.canvas
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<Button-1>", self._press)
        c.bind("<Shift-Button-1>", self._drag)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._release)
        for key in ("Left", "Right", "Up", "Down"):
            c.bind(f"<{key}>", self._key_move)
        c.bind("<MouseWheel>", lambda e: self._yview("scroll", -1 if e.delta > 0 else 1, "units"))
        c.bind("<Shift-MouseWheel>", lambda e: self._xview("scroll", -1 if e.delta > 0 else 1, "units"))
        c.bind("<Button-4>", lambda e: self._yview("scroll", -1, "units"))
        c.bind("<Button-5>", lambda e: self._yview("scroll", 1, "units"))

    # ---- content
    def set_appearance(self, colors, points):
        self.colors = colors
        self.font.configure(size=points)
        families = getattr(self.winfo_toplevel(), "font_families", {})
        self.font.configure(family=families.get("mono", self.font.actual("family")))
        self.badge_font.configure(size=max(7, points - 2))
        self.cw = max(self.font.measure("0"), 6)
        self.rh = self.font.metrics("linespace") + 8
        self.canvas.configure(background=colors["panel"], highlightbackground=colors["line"],
                              yscrollincrement=self.rh, xscrollincrement=self.cw * 4)
        # Darken the range tints so light cell text stays legible in dark/high-contrast themes.
        if colors["text"] in ("#17212e", "#26343d"):
            self.mark_colors = MARK_COLOURS
        else:
            self.mark_colors = [
                ("#" + "".join(f"{int(tint[i:i + 2], 16) // 4:02x}" for i in (1, 3, 5)), edge)
                for tint, edge in MARK_COLOURS
            ]
        selection, anchor, active = self.sel, self.anchor, self.active_cell
        position = (self.canvas.xview()[0], self.canvas.yview()[0])
        self.set_rows(self.rows)
        self.sel, self.anchor, self.active_cell = selection, anchor, active
        self.canvas.xview_moveto(position[0])
        self.canvas.yview_moveto(position[1])
        self.redraw()

    def set_rows(self, rows):
        self.rows = rows
        self.ncols = max((len(r) for r in rows), default=0)
        self.sel = self.anchor = self.active_cell = None
        widths = [3] * self.ncols
        for r in rows[:500]:  # the first rows are enough to size the columns
            for i, cell in enumerate(r):
                if len(cell) > widths[i]:
                    widths[i] = len(cell)
        self.colx = [0]
        for w in widths:
            self.colx.append(self.colx[-1] + min(w, MAX_CELL_CHARS) * self.cw + 12)
        self.rw = self.cw * (len(str(len(rows))) + 1) + 10
        self.canvas.configure(scrollregion=(0, 0, self.rw + self.colx[-1], self.rh * (len(rows) + 1)))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.redraw()

    def set_marks(self, marks):
        self.marks = marks
        self.redraw()

    def set_selection(self, bounds):
        self.sel = bounds
        self.anchor = bounds[:2] if bounds else None
        self.active_cell = bounds[2:] if bounds else None
        self.redraw()

    def see(self, col, row):
        """Scroll so that a cell is near the top-left of the view."""
        if self.rows:
            self.canvas.xview_moveto(max(0, self.colx[min(col, self.ncols - 1)] - self.cw * 4)
                                     / max(1, self.rw + self.colx[-1]))
            self.canvas.yview_moveto(max(0, row - 2) / (len(self.rows) + 1))
            self.redraw()

    # ---- scrolling
    def _yview(self, *args):
        self.canvas.yview(*args)
        self.redraw()

    def _xview(self, *args):
        self.canvas.xview(*args)
        self.redraw()

    # ---- mouse
    def _cell_at(self, event):
        x = self.canvas.canvasx(event.x) - self.rw
        y = self.canvas.canvasy(event.y) - self.rh
        col = min(max(bisect_right(self.colx, x) - 1, 0), self.ncols - 1)
        row = min(max(int(y // self.rh), 0), len(self.rows) - 1)
        return col, row

    def _press(self, event):
        if self.rows:
            self.anchor = self._cell_at(event)
            self.active_cell = self.anchor
            self.canvas.focus_set()
            self.sel = self.anchor * 2
            self.redraw()

    def _drag(self, event):
        if not self.rows or self.anchor is None:
            return
        c = self.canvas
        if event.y > c.winfo_height():
            c.yview_scroll(1, "units")
        elif event.y < self.rh:
            c.yview_scroll(-1, "units")
        if event.x > c.winfo_width():
            c.xview_scroll(1, "units")
        elif event.x < self.rw:
            c.xview_scroll(-1, "units")
        (ac, ar), (col, row) = self.anchor, self._cell_at(event)
        self.active_cell = (col, row)
        self.sel = (min(ac, col), min(ar, row), max(ac, col), max(ar, row))
        self.redraw()
        if event.type == tk.EventType.ButtonPress and self.on_select:  # shift-click
            self.on_select(self.sel)

    def _release(self, event):
        if self.rows and self.sel and self.on_select:
            self.on_select(self.sel)

    def _key_move(self, event):
        """Arrow-key range picking from the ZIP; Shift extends from the anchor."""
        if not self.rows or not self.ncols:
            return "break"
        previous = self.active_cell or (0, 0)
        col, row = previous
        col = min(max(col + (event.keysym == "Right") - (event.keysym == "Left"), 0), self.ncols - 1)
        row = min(max(row + (event.keysym == "Down") - (event.keysym == "Up"), 0), len(self.rows) - 1)
        self.active_cell = (col, row)
        self.anchor = (self.anchor or previous) if event.state & 1 else self.active_cell
        ac, ar = self.anchor
        self.sel = (min(ac, col), min(ar, row), max(ac, col), max(ar, row))
        self.see(col, row)
        if self.on_select:
            self.on_select(self.sel)
        return "break"

    # ---- drawing
    def _box(self, bounds):
        c1, r1, c2, r2 = bounds
        c1, c2 = min(c1, self.ncols - 1), min(c2, self.ncols - 1)
        return (self.rw + self.colx[c1], self.rh * (r1 + 1), self.rw + self.colx[c2 + 1], self.rh * (r2 + 2))

    def redraw(self):
        c = self.canvas
        c.delete("all")
        width, height = c.winfo_width(), c.winfo_height()
        if not self.rows or not self.ncols:
            c.create_text(width / 2, height / 2, text="Open a CSV file from the machine to see it here",
                          fill=self.colors["muted"], font=self.font)
            return
        x0, y0, rh, rw = int(c.canvasx(0)), int(c.canvasy(0)), self.rh, self.rw
        r_first = max(0, y0 // rh)
        r_last = min(len(self.rows) - 1, (y0 + height) // rh)
        c_first = min(max(bisect_right(self.colx, x0) - 1, 0), self.ncols - 1)
        c_last = min(max(bisect_right(self.colx, x0 + width - rw) - 1, 0), self.ncols - 1)

        visible = [(slot, b) for slot, b in self.marks if b[3] >= b[1] and b[0] < self.ncols]
        for slot, bounds in visible:
            c.create_rectangle(*self._box(bounds), fill=self.mark_colors[slot % 10][0], outline="")
        if self.sel:
            c.create_rectangle(*self._box(self.sel), fill=self.colors["selection"], outline="")

        for r in range(r_first, r_last + 2):
            c.create_line(x0, rh * (r + 1), x0 + width, rh * (r + 1), fill=self.colors["line"])
        for col in range(c_first, c_last + 2):
            c.create_line(rw + self.colx[col], y0, rw + self.colx[col], y0 + height, fill=self.colors["line"])

        for r in range(r_first, r_last + 1):
            row, y = self.rows[r], rh * (r + 1) + rh / 2
            for col in range(c_first, min(c_last, len(row) - 1) + 1):
                text = row[col]
                if text:
                    room = (self.colx[col + 1] - self.colx[col] - 10) // self.cw
                    if len(text) > room:
                        text = text[:room - 1] + "…"
                    c.create_text(rw + self.colx[col] + 5, y, text=text, anchor="w", font=self.font,
                                  fill=self.colors["text"])

        for slot, bounds in visible:
            left, top, right, bottom = self._box(bounds)
            c.create_rectangle(left, top, right, bottom, outline=self.mark_colors[slot % 10][1])
        if self.sel:
            c.create_rectangle(*self._box(self.sel), outline=self.colors["accent"], width=2)

        # frozen headers, drawn last so that cells scroll underneath them
        sc1, sr1, sc2, sr2 = self.sel or (-1, -1, -1, -1)
        c.create_rectangle(x0, y0, x0 + width, y0 + rh, fill=self.colors["background"], outline=self.colors["line"])
        for col in range(c_first, c_last + 1):
            left, right = rw + self.colx[col], rw + self.colx[col + 1]
            if sc1 <= col <= sc2:
                c.create_rectangle(left, y0, right, y0 + rh, fill=self.colors["selection"],
                                   outline=self.colors["line"])
            c.create_line(left, y0, left, y0 + rh, fill=self.colors["line"])
            c.create_text((left + right) / 2, y0 + rh / 2, text=col_letter(col),
                          fill=self.colors["text"], font=self.font)
        c.create_rectangle(x0, y0, x0 + rw, y0 + height, fill=self.colors["background"],
                           outline=self.colors["line"])
        for r in range(r_first, r_last + 1):
            top = rh * (r + 1)
            if top < y0 + rh:
                continue
            if sr1 <= r <= sr2:
                c.create_rectangle(x0, top, x0 + rw, top + rh, fill=self.colors["selection"],
                                   outline=self.colors["line"])
            c.create_line(x0, top, x0 + rw, top, fill=self.colors["line"])
            c.create_text(x0 + rw / 2, top + rh / 2, text=str(r + 1),
                          fill=self.colors["text"], font=self.font)
        c.create_rectangle(x0, y0, x0 + rw, y0 + rh, fill=self.colors["background"],
                           outline=self.colors["line"])

        # slot number on the top-left corner of each assigned range, on top of the headers so column A shows it too
        for slot, bounds in visible:
            left, top = self._box(bounds)[:2]
            if left >= x0 + rw and top >= y0 + rh:
                c.create_oval(left - 8, top - 8, left + 8, top + 8, fill=MARK_COLOURS[slot % 10][1], outline="white")
                c.create_text(left, top, text=str(slot + 1), fill="white", font=self.badge_font)
