"""
The page header of a theme that has one: a white bar at the top of the content with the open page's title, a line
that says what the page is for, and that page's own buttons (Start and Stop on the Monitor, Save on Setup and
Delivery). It takes the place of the toolbar. Also the amber problem banner of such a theme.
"""
import tkinter as tk
from tkinter import ttk

from .. import messages, theme
from ..i18n import tr
from . import icons


class PageHeaderMixin:
    def _build_page_header(self):
        """Built instead of the toolbar. The pages say their title through _page_intro; their buttons are made here."""
        px = self.px
        bar = self.page_header = ttk.Frame(self.content, style="Header.TFrame", padding=(px(32), px(14), px(24), px(14)))
        bar.pack(fill="x")
        words = ttk.Frame(bar, style="Header.TFrame")
        words.pack(side="left", fill="x", expand=True)
        self.page_title = ttk.Label(words, text="", style="HeaderTitle.TLabel")
        self.page_title.pack(anchor="w")
        self.page_hint = ttk.Label(words, text="", style="HeaderHint.TLabel")
        self.page_hint.pack(anchor="w", pady=(px(2), 0))
        self.page_action_area = ttk.Frame(bar, style="Header.TFrame")
        self.page_action_area.pack(side="right")
        self.page_actions, self._page_texts = {}, {}

        monitor = self._page_action_frame("monitor")
        self._build_state_pill(monitor, side="left")
        self.once_btn = ttk.Button(monitor, text="↻  " + tr("Process now"), command=self.process_now)
        self.once_btn.pack(side="left", padx=(px(10), 0))
        self.start_btn = ttk.Button(monitor, text="▶  " + tr("Start monitoring"), style="Go.TButton", command=self.start)
        self.stop_btn = ttk.Button(monitor, text="■  " + tr("Stop"), style="Dark.TButton", command=self.stop)
        self.start_btn.pack(side="left", padx=(px(10), 0))  # Start or Stop: the one that applies (see _swap_start_stop)

        self.save_buttons, self.unsaved_labels = [], []
        for key in ("setup", "delivery"):
            frame = self._page_action_frame(key)
            note = ttk.Label(frame, text="", style="HeaderWarn.TLabel")
            note.pack(side="left", padx=(0, px(12)))
            button = ttk.Button(frame, text="✔  " + tr("Save settings"), style="Go.TButton", command=self.save)
            button.pack(side="left")
            self.save_buttons.append(button)
            self.unsaved_labels.append(note)
        self.save_btn = self.save_buttons[0]
        self._pill_shown = self._banner_shown_for = None

    def _page_action_frame(self, key):
        frame = ttk.Frame(self.page_action_area, style="Header.TFrame")
        self.page_actions[key] = frame
        return frame

    def _remember_page_text(self, page, title, hint):
        self._page_texts[str(page)] = (title, hint)

    def _show_page_header(self):
        """The open page's title, hint and buttons."""
        current = self.tabs.select()
        title, hint = self._page_texts.get(current, ("", ""))
        self.page_title.configure(text=title)
        self.page_hint.configure(text=hint)
        key = next((k for k, _label, attribute in self.NAV if str(getattr(self, attribute, "")) == current), None)
        for name, frame in self.page_actions.items():
            if name == key:
                frame.pack(side="right")
            else:
                frame.pack_forget()

    def _swap_start_stop(self, running):
        """The Monitor shows Start while stopped and Stop while running, as the design has it."""
        show, hide = (self.stop_btn, self.start_btn) if running else (self.start_btn, self.stop_btn)
        if not show.winfo_manager():
            hide.pack_forget()
            show.pack(side="left", padx=(self.px(10), 0))

    def _update_pill(self, text, kind):
        """kind: "ok" (monitoring), "warn" (monitoring with an open problem) or "idle"."""
        if self._pill_shown == (text, kind):  # called on every tick
            return
        self._pill_shown = (text, kind)
        name = {"ok": "PillOk", "warn": "PillWarn"}.get(kind, "PillIdle")
        self.state_pill.configure(style=f"{name}.TFrame")
        self.state_label.configure(style=f"{name}.TLabel", text=text, foreground="", width=max(11, len(text)))
        tint, ink = {"ok": (theme.SUCCESS_TINT, theme.SUCCESS), "warn": (theme.WARNING_TINT, theme.WARNING)}.get(
            kind, (theme.SURFACE_ALT, theme.MUTED))
        self.state_dot.configure(bg=tint, fg=ink)

    def _refresh_unsaved(self):
        """'Unsaved changes' beside Save while the form differs from the saved settings."""
        try:
            changed = self.collect() != self.cfg
        except Exception:  # a half-typed value: say nothing rather than fail
            changed = False
        for label in self.unsaved_labels:
            label.configure(text=tr("Unsaved changes") if changed else "")

    # ---------------------------------------------------------------- the problem banner
    def _build_problem_banner(self):
        """An amber card under the header: what is wrong, whether anything is lost, what to do, and where to look."""
        px = self.px
        banner = self.alert_banner = tk.Frame(self.content, bg=theme.WARNING_TINT, highlightthickness=1,
                                              highlightbackground=theme.WARNING, highlightcolor=theme.WARNING)
        icon = tk.Canvas(banner, width=px(22), height=px(22), bg=theme.WARNING_TINT, highlightthickness=0)
        icon.pack(side="left", anchor="n", padx=(px(16), px(12)), pady=px(14))
        icons.draw(icon, "warning", theme.WARNING, px(22))
        # the button first, so that the text is the part that gives way when the window is narrow
        self.banner_button = ttk.Button(banner, text=tr("See what is waiting"), command=self._banner_go)
        self.banner_button.pack(side="right", padx=px(16), pady=px(12))
        words = tk.Frame(banner, bg=theme.WARNING_TINT)
        words.pack(side="left", fill="x", expand=True, pady=px(10))
        self.banner_headline = tk.Label(words, text="", bg=theme.WARNING_TINT, fg=theme.TEXT, anchor="w",
                                        justify="left", font=self.strong(11))
        self.banner_headline.pack(fill="x")
        self.banner_advice = tk.Label(words, text="", bg=theme.WARNING_TINT, fg=theme.TEXT, anchor="w", justify="left",
                                      font=(self._font_body, 10))
        self.banner_advice.pack(fill="x")
        self.banner_detail = tk.Label(words, text="", bg=theme.WARNING_TINT, fg=theme.MUTED, anchor="w", justify="left",
                                      font=(self._font_body, 9))
        self.banner_detail.pack(fill="x", pady=(px(2), 0))
        self.alert_banner_shown, self._banner_page = False, "history"

    def _show_problem_banner(self, open_alerts, waiting):
        """The first open problem in plain words; the engine's own words stay underneath, small."""
        if not open_alerts:
            if self.alert_banner_shown:
                self.alert_banner.pack_forget()
                self.alert_banner_shown = False
            return
        subject, item = next(iter(open_alerts.items()))
        width = max(self.content.winfo_width() - self.px(420), self.px(300))
        shown = (subject, item["text"], len(open_alerts), waiting.get(subject, 0), width)
        if self.alert_banner_shown and shown == self._banner_shown_for:  # called on every tick
            return
        self._banner_shown_for = shown
        headline, advice, page = messages.plain_problem(subject, waiting.get(subject, 0))
        more = len(open_alerts) - 1
        if more:
            headline += "  " + tr("(and {n} more)", n=more)
        self.banner_headline.configure(text=headline, wraplength=width)
        self.banner_advice.configure(text=advice, wraplength=width)
        self.banner_detail.configure(text=tr("{problem}  (since {time})", problem=item["text"], time=item["since"][11:16]),
                                     wraplength=width)
        self._banner_page = page
        self.banner_button.configure(text={"history": tr("See what is waiting"), "setup": tr("Open Setup"),
                                           "delivery": tr("Open Delivery"), "monitor": tr("Open the Monitor")}.get(
                                               page, tr("Open the activity log")))
        if not self.alert_banner_shown:
            self.alert_banner.pack(fill="x", padx=self.px(32), pady=(self.px(14), 0), after=self.header_sep)
            self.alert_banner_shown = True

    def _banner_go(self):
        if self._banner_page == "log":
            return self.open_log()
        attribute = {"history": "history_tab", "setup": "setup_tab", "delivery": "delivery_tab",
                     "monitor": "monitor_tab"}[self._banner_page]
        self._show_tab(getattr(self, attribute))
