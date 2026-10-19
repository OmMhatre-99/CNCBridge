"""
A drop-down menu drawn by the program.

Windows' own popup menus run a modal loop of their own: the arrow keys cannot move to the next menu, hovering
another title does not switch to it, and nothing else (a test, a screenshot) can run while one is open. This
drop-down shows the entries of an ordinary tk.Menu, which stays the place where entries, states and commands
live, so the rest of the window works with the menus as before.
"""
import tkinter as tk

from .. import theme


class DropDown(tk.Toplevel):
    """The entries of one tk.Menu, in a borderless window below a menu title (or beside a submenu entry)."""

    def __init__(self, app, menu, x, y, parent=None, keyboard=False):
        super().__init__(app)
        self.app, self.menu, self.parent_dropdown, self.child = app, menu, parent, None
        self.parent_row = None   # the entry of the parent menu this one opened from
        self.overrideredirect(True)
        self.withdraw()
        self.configure(background=theme.BORDER)
        font = (app._font_body, app.L.menu_size)
        inner = tk.Frame(self, background=theme.SURFACE)
        inner.pack(padx=1, pady=1)
        self.rows = []          # one dict per selectable entry: index, kind, frame, labels, enabled
        self.selected = None
        px = app.px
        for index in range(menu.index("end") + 1):
            kind = menu.type(index)
            if kind == "separator":
                tk.Frame(inner, height=1, background=theme.BORDER).pack(fill="x", padx=px(6), pady=px(3))
                continue
            if kind not in ("command", "cascade"):
                continue
            enabled = str(menu.entrycget(index, "state")) != "disabled"
            colour = theme.TEXT if enabled else theme.DISABLED_TEXT
            frame = tk.Frame(inner, background=theme.SURFACE)
            frame.pack(fill="x")
            text = tk.Label(frame, text=menu.entrycget(index, "label"), font=font, anchor="w", foreground=colour,
                            background=theme.SURFACE)
            text.pack(side="left", padx=(px(14), px(30)), pady=px(5))
            side = "▸" if kind == "cascade" else menu.entrycget(index, "accelerator")
            hint = tk.Label(frame, text=side, font=font, anchor="e", background=theme.SURFACE,
                            foreground=theme.MUTED if enabled else theme.DISABLED_TEXT)
            hint.pack(side="right", padx=(0, px(14)))
            row = {"index": index, "kind": kind, "frame": frame, "labels": (text, hint), "enabled": enabled}
            position = len(self.rows)
            self.rows.append(row)
            for widget in (frame, text, hint):
                widget.bind("<Enter>", lambda event, p=position: self._hover(p))
                widget.bind("<ButtonRelease-1>", lambda event, p=position: self.activate(p))
        self.bind("<Key>", lambda event: self.press(event.keysym))
        self.bind("<ButtonPress-1>", self._click_outside)
        self.bind("<Motion>", self._motion)
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max(0, min(x, self.winfo_screenwidth() - width))
        y = max(0, min(y, self.winfo_screenheight() - height))
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_force()
        if keyboard:
            self._move(1, start=-1)

    # ---------------------------------------------------------------- selection
    def _paint(self):
        for position, row in enumerate(self.rows):
            on = position == self.selected
            background = theme.ACCENT_TINT if on else theme.SURFACE
            row["frame"].configure(background=background)
            text, hint = row["labels"]
            text.configure(background=background)
            hint.configure(background=background)

    def _hover(self, position):
        if not self.rows[position]["enabled"]:
            return
        self.selected = position
        self._paint()
        if self.child is not None and self.child.parent_row != position:
            self._close_child()
        if self.rows[position]["kind"] == "cascade" and self.child is None:
            self.after(250, lambda: self.selected == position and self.child is None and self.exists()
                       and self._open_child(position, keyboard=False))

    def exists(self):
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _move(self, step, start=None):
        """Select the next enabled entry in this direction, wrapping round."""
        count = len(self.rows)
        if start is not None:
            position = start
        else:
            position = self.selected if self.selected is not None else (-1 if step > 0 else 0)
        for _ in range(count):
            position = (position + step) % count
            if self.rows[position]["enabled"]:
                self.selected = position
                self._paint()
                return

    # ---------------------------------------------------------------- submenus
    def _open_child(self, position, keyboard):
        row = self.rows[position]
        submenu = self.menu.nametowidget(str(self.menu.entrycget(row["index"], "menu")))
        frame = row["frame"]
        x = self.winfo_rootx() + self.winfo_width() - 2
        y = frame.winfo_rooty() - 1
        self.child = DropDown(self.app, submenu, x, y, parent=self, keyboard=keyboard)
        self.child.parent_row = position

    def _close_child(self):
        if self.child is not None:
            child, self.child = self.child, None
            child.close(regrab=False)
            try:
                self.grab_set()
                self.focus_force()
            except tk.TclError:
                pass

    # ---------------------------------------------------------------- actions
    def activate(self, position):
        row = self.rows[position]
        if not row["enabled"]:
            return
        if row["kind"] == "cascade":
            if self.child is None:
                self._open_child(position, keyboard=True)
            return
        index, menu = row["index"], self.menu
        self.app._close_menus()
        menu.invoke(index)

    def press(self, key):
        """A key pressed while this menu has the focus (also called directly, for example by tests)."""
        if key in ("Down", "Up"):
            self._move(1 if key == "Down" else -1)
        elif key == "Home":
            self._move(1, start=-1)
        elif key == "End":
            self._move(-1, start=0)
        elif key in ("Return", "KP_Enter", "space"):
            if self.selected is not None:
                self.activate(self.selected)
        elif key == "Escape":
            if self.parent_dropdown is not None:
                self.parent_dropdown._close_child()
            else:
                self.app._close_menus()
        elif key == "Right":
            row = self.rows[self.selected] if self.selected is not None else None
            if row and row["kind"] == "cascade" and row["enabled"]:
                self.activate(self.selected)
            else:
                self.app._step_menu(1)
        elif key == "Left":
            if self.parent_dropdown is not None:
                self.parent_dropdown._close_child()
            else:
                self.app._step_menu(-1)
        return "break"

    # ---------------------------------------------------------------- the mouse
    def _chain(self):
        top = self
        while top.parent_dropdown is not None:
            top = top.parent_dropdown
        chain = [top]
        while chain[-1].child is not None:
            chain.append(chain[-1].child)
        return chain

    def _inside(self, x, y):
        return any(d.winfo_rootx() <= x < d.winfo_rootx() + d.winfo_width()
                   and d.winfo_rooty() <= y < d.winfo_rooty() + d.winfo_height() for d in self._chain())

    def _click_outside(self, event):
        if self._inside(event.x_root, event.y_root):
            return
        under = self.winfo_containing(event.x_root, event.y_root)
        self.app._click_outside_menus(under)

    def _motion(self, event):
        if self.parent_dropdown is None and not self._inside(event.x_root, event.y_root):
            self.app._hover_menu(self.winfo_containing(event.x_root, event.y_root))

    # ---------------------------------------------------------------- closing
    def close(self, regrab=True):
        if self.child is not None:
            self.child.close(regrab=False)
            self.child = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass
