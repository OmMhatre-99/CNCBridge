"""The sidebar of a theme that has one: the way between the pages, and the station at a glance."""
import tkinter as tk
from tkinter import ttk

from .. import theme
from ..config import APP_NAME
from ..i18n import N_, tr
from . import icons

BRIGHT = "#ffffff"   # the names in the sidebar: it is dark in every colour mode of the themes that have one


class SidebarMixin:
    # N_ marks the texts for translation; they are translated where they are shown (this is read at import)
    NAV = (("monitor", N_("Monitor"), "monitor_tab"), ("setup", N_("Setup"), "setup_tab"),
           ("pick", N_("Ranges & preview"), "pick_tab"), ("delivery", N_("Delivery"), "delivery_tab"),
           ("history", N_("History"), "history_tab"), ("operator", N_("Operator"), "operator_tab"))
    # the state colours of the page are for a light page; on the dark sidebar the lighter ones are used
    STATE_ON_DARK = {"SUCCESS": "#5cc98a", "WARNING": "#f0b445", "ERROR": "#ff8a80"}

    def _build_body(self):
        """The frame the rest of the window goes in: the sidebar on its left, if the theme has one, and the content."""
        self.sidebar, self.content, self.nav_items = None, self, {}
        if not self.L.sidebar:
            return
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(body, bg=theme.SIDEBAR, width=self.px(self.L.sidebar_width))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)

    # ---------------------------------------------------------------- building it
    def _fill_sidebar(self):
        """The name, the pages, Settings and Help, and the station. Built once, when the pages exist."""
        if self.sidebar is None:
            return
        side, px = self.sidebar, self.px
        head = tk.Frame(side, bg=theme.SIDEBAR)
        head.pack(fill="x", padx=px(16), pady=(px(20), px(18)))
        tile = tk.Canvas(head, width=px(34), height=px(34), bg=theme.ACCENT, highlightthickness=0)
        tile.pack(side="left")
        icons.draw(tile, "logo", BRIGHT, px(20))
        tile.configure(width=px(34), height=px(34))
        tile.move("all", px(7), px(7))  # the icon is 20 px in a 34 px tile
        words = tk.Frame(head, bg=theme.SIDEBAR)
        words.pack(side="left", padx=(px(10), 0))
        tk.Label(words, text=APP_NAME, bg=theme.SIDEBAR, fg=BRIGHT, font=self.strong(13)).pack(anchor="w")
        tk.Label(words, text=tr("Machine files to MES"), bg=theme.SIDEBAR, fg=theme.SIDEBAR_MUTED,
                 font=(self._font_body, 9)).pack(anchor="w")

        self.nav_frame = tk.Frame(side, bg=theme.SIDEBAR)
        self.nav_frame.pack(fill="x", padx=px(10))
        for key, label, attribute in self.NAV:
            self.nav_items[key] = self._nav_row(self.nav_frame, key, tr(label),
                                                lambda a=attribute: self._show_tab(getattr(self, a)))

        # the station, at the bottom; Settings and Help above it
        card = tk.Frame(side, bg=theme.SIDEBAR_HOVER)
        card.pack(side="bottom", fill="x", padx=px(14), pady=px(14))
        tk.Label(card, text=tr("Station"), bg=theme.SIDEBAR_HOVER, fg=theme.SIDEBAR_MUTED,
                 font=(self._font_body, 9)).pack(anchor="w", padx=px(12), pady=(px(10), 0))
        self.sidebar_station = tk.StringVar(self)
        tk.Label(card, textvariable=self.sidebar_station, bg=theme.SIDEBAR_HOVER, fg=BRIGHT, anchor="w",
                 font=self.strong(11)).pack(fill="x", padx=px(12))
        state = tk.Frame(card, bg=theme.SIDEBAR_HOVER)
        state.pack(fill="x", padx=px(12), pady=(0, px(10)))
        self.sidebar_dot = tk.Label(state, text="●", bg=theme.SIDEBAR_HOVER, fg=theme.SIDEBAR_MUTED,
                                    font=(self._font_body, 9))
        self.sidebar_dot.pack(side="left")
        self.sidebar_state = tk.Label(state, text=tr("Stopped"), bg=theme.SIDEBAR_HOVER, fg=theme.SIDEBAR_TEXT,
                                      font=(self._font_body, 10))
        self.sidebar_state.pack(side="left", padx=(px(6), 0))
        self.sidebar_go = None
        if self.L.page_headers:  # there is no toolbar: monitoring can be started and stopped from every page here
            self.sidebar_go = tk.Button(card, text="", bg=theme.ACCENT, fg=theme.WHITE, activebackground=theme.SIDEBAR_ACTIVE,
                                        activeforeground=BRIGHT, disabledforeground=theme.SIDEBAR_MUTED, relief="flat",
                                        bd=0, cursor="hand2", font=self.strong(10), pady=px(5),
                                        highlightthickness=2, highlightbackground=theme.SIDEBAR_HOVER,
                                        highlightcolor=theme.FOCUS,
                                        command=lambda: self.stop() if self.processing else self.start())
            self.sidebar_go.pack(fill="x", padx=px(12), pady=(0, px(12)))
        extras = tk.Frame(side, bg=theme.SIDEBAR)
        extras.pack(side="bottom", fill="x", padx=px(10))
        for key, label, command in (("settings", tr("Settings"), lambda: self.open_options()),
                                    ("help", tr("Help"), lambda: self.open_help("start")),
                                    ("log", tr("Activity log"), self.open_log)):
            self.nav_items[key] = self._nav_row(extras, key, label, command, always=True)

        self.v["station_name"].trace_add("write", lambda *_: self._sidebar_station_changed())
        self._sidebar_station_changed()
        self.tabs.bind("<<NotebookTabChanged>>", lambda event: self._sync_sidebar(), add="+")
        self._sync_sidebar()

    def _nav_row(self, parent, key, label, command, always=False):
        """One entry: an icon and a name, as wide as the sidebar, reached by the mouse and by the keyboard."""
        px = self.px
        row = tk.Frame(parent, bg=theme.SIDEBAR, cursor="hand2", takefocus=True, highlightthickness=2,
                       highlightbackground=theme.SIDEBAR, highlightcolor=theme.FOCUS)
        icon = tk.Canvas(row, width=px(20), height=px(20), bg=theme.SIDEBAR, highlightthickness=0)
        icon.pack(side="left", padx=(px(12), px(12)), pady=px(11))
        text = tk.Label(row, text=label, bg=theme.SIDEBAR, fg=theme.SIDEBAR_TEXT, anchor="w",
                        font=(self._font_body, 11))
        text.pack(side="left", fill="x", expand=True)
        item = {"row": row, "icon": icon, "text": text, "name": key, "active": False, "always": always}
        for widget in (row, icon, text):
            widget.bind("<Button-1>", lambda event, c=command: c())
            widget.bind("<Enter>", lambda event, i=item: self._paint_nav(i, hover=True))
            widget.bind("<Leave>", lambda event, i=item: self._paint_nav(i, hover=False))
        for sequence in ("<Return>", "<space>"):
            row.bind(sequence, lambda event, c=command: (c(), "break")[1])
        self._paint_nav(item)
        if always:
            row.pack(fill="x", pady=(0, px(3)))
        return item

    def _paint_nav(self, item, hover=False):
        active = item["active"]
        bg = theme.SIDEBAR_ACTIVE if active else theme.SIDEBAR_HOVER if hover else theme.SIDEBAR
        fg = BRIGHT if active else theme.SIDEBAR_TEXT
        item["row"].configure(bg=bg, highlightbackground=bg)
        item["icon"].configure(bg=bg)
        item["text"].configure(bg=bg, fg=fg, font=self.strong(11) if active else (self._font_body, 11))
        icons.draw(item["icon"], item["name"], fg, self.px(20))

    # ---------------------------------------------------------------- keeping it in step
    def _sync_sidebar(self):
        """Show the pages that can be opened (the setup ones are hidden while it is locked), and mark the open one."""
        if self.L.page_headers:
            self._show_page_header()
        if self.sidebar is None or not self.nav_items:
            return
        current = self.tabs.select()
        for item in self.nav_items.values():
            if not item["always"]:
                item["row"].pack_forget()
        for key, _label, attribute in self.NAV:
            item, tab = self.nav_items[key], getattr(self, attribute, None)
            if tab is None or str(self.tabs.tab(tab, "state")) == "hidden":
                continue
            item["active"] = current == str(tab)
            self._paint_nav(item)
            item["row"].pack(fill="x", pady=(0, self.px(3)))

    def _sidebar_station_changed(self):
        self.sidebar_station.set(self.v["station_name"].get().strip() or tr("(no name yet)"))

    def _update_sidebar_state(self, text, colour):
        """The station card shows what the status pill says."""
        name = next((n for n in self.STATE_ON_DARK if getattr(theme, n) == colour), None)
        self.sidebar_dot.configure(fg=self.STATE_ON_DARK.get(name, theme.SIDEBAR_MUTED))
        self.sidebar_state.configure(text=text)
        if self.sidebar_go is not None:
            running = self.processing
            usable = running or not self.busy
            self.sidebar_go.configure(text="■  " + tr("Stop") if running else "▶  " + tr("Start monitoring"),
                                      state="normal" if usable else "disabled",
                                      bg=theme.SIDEBAR_ACTIVE if running else theme.ACCENT,
                                      fg=BRIGHT if running else theme.WHITE)  # WHITE: the text on the accent colour
