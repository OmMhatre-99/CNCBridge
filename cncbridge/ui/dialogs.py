"""The shift-change question shown to the operator."""
import tkinter as tk
from tkinter import ttk

from .. import theme


class ShiftDialog(tk.Toplevel):
    """
    Asks the operator, at a shift change, to confirm or change the typed values. It does not block anything:
    processing goes on, and closing it with Later leaves the banner up in the main window.
    """

    def __init__(self, app, boundary):
        super().__init__(app)
        self.app = app
        self.title("Shift change")
        self.transient(app)
        self.configure(background=theme.BACKGROUND)
        if app.theme.dark:
            theme.dark_title_bar(self)
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=f"A new shift started at {boundary:%H:%M}", font=(app._font_heading, 14, "bold")
                  ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, wraplength=app.px(420), justify="left", text=(
            "Are these values still right? Change them if the operator or the work order is different. "
            "Files keep being processed meanwhile.")).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))
        self.entries = []
        for i, (name, value) in enumerate(app.man_vars):
            ttk.Label(frame, text=name.get().strip() or f"Input {i + 1}", style="Card.TLabel").grid(
                row=2 + i, column=0, sticky="w", pady=5)
            var = tk.StringVar(self, value.get())
            ttk.Entry(frame, textvariable=var, width=34).grid(row=2 + i, column=1, sticky="ew", padx=(10, 0), pady=5)
            self.entries.append(var)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2 + len(self.entries), column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Later", command=self.later).pack(side="right")
        ttk.Button(buttons, text="Confirm", style="Go.TButton", command=self.confirm).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self.later)
        self.update_idletasks()  # centred over the main window
        x = app.winfo_rootx() + max((app.winfo_width() - self.winfo_reqwidth()) // 2, 0)
        y = app.winfo_rooty() + max((app.winfo_height() - self.winfo_reqheight()) // 3, 0)
        self.geometry(f"+{x}+{y}")
        self._raise_job = self.after(50, self.lift)

    def destroy(self):
        try:
            self.after_cancel(self._raise_job)  # no raise may run after the dialog is gone
        except (AttributeError, tk.TclError):
            pass
        super().destroy()

    def confirm(self):
        if self.app.finish_shift([v.get() for v in self.entries]):
            self.destroy()

    def later(self):
        self.destroy()
