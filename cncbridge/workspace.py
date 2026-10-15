"""Two navigation layouts over the same machine profile and processing controls."""
import tkinter as tk
from tkinter import ttk

INTERFACES = ("Guided", "Full workspace")
PAGES = (
    ("Getting started", "1  Welcome", "Try the demo, or follow the numbered pages to connect a machine."),
    ("Setup", "2  Machine setup", "Choose input and back-up folders, output format, ranges and manual fields."),
    ("Pick ranges & preview", "3  Select data", "Open a sample CSV, assign ranges, and inspect the output preview."),
    ("Delivery", "4  Delivery", "Choose shared-folder delivery, an API endpoint, or both."),
    ("Checks", "5  Check and run", "Run checks, save settings, then use Start monitoring or Process now above."),
    ("History", "6  Results", "Check each file's Shared and API columns; processed does not mean delivered."),
    ("Log", "Activity log", "Read processing, retry and connection messages when troubleshooting."),
    ("Appearance", "Appearance", "Choose a theme and text size. Your machine profile stays the same."),
    ("Demo & integration", "Live demo", "Generate invented machine records and inspect the local MES inbox."),
    ("User guide", "Detailed user guide", "Search explanations, folder meanings, examples and troubleshooting steps."),
)


class Workspace(ttk.Frame):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self.mode = tk.StringVar(self, app.appearance.preferences["interface"])
        bar = ttk.Frame(self, padding=(10, 4))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(bar, text="CNCBridge", style="Go.TLabel").pack(side="left")
        ttk.Label(bar, text="  Machine CSV → selected data → MES", style="Small.TLabel").pack(side="left")
        selector = ttk.Combobox(bar, textvariable=self.mode, values=INTERFACES, state="readonly", width=18)
        selector.pack(side="right")
        selector.bind("<<ComboboxSelected>>", lambda _event: self.set_mode(self.mode.get(), save=True))
        ttk.Label(bar, text="Interface  ").pack(side="right")

        self.sidebar = ttk.Frame(self, padding=(8, 8, 6, 8))
        self.sidebar.grid(row=1, column=0, sticky="ns")
        self.sidebar.rowconfigure(1, weight=1)
        ttk.Label(self.sidebar, text="YOUR WORKFLOW", style="Small.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        self.navigation = ttk.Treeview(self.sidebar, show="tree", selectmode="browse", style="Nav.Treeview")
        self.navigation.column("#0", width=app.px(190), minwidth=120, stretch=False)
        scroll = ttk.Scrollbar(self.sidebar, orient="vertical", command=self.navigation.yview)
        self.navigation.configure(yscrollcommand=scroll.set)
        self.navigation.grid(row=1, column=0, sticky="ns")
        scroll.grid(row=1, column=1, sticky="ns")
        self.navigation.bind("<<TreeviewSelect>>", self._selected)
        self.content = ttk.Frame(self)
        self.content.grid(row=1, column=1, sticky="nsew", padx=(4, 10), pady=(4, 0))
        self.context = ttk.Frame(self.content, padding=(8, 8))
        self.context.pack(fill="x")
        actions = ttk.Frame(self.context)
        actions.pack(side="right", anchor="n")
        self.previous = ttk.Button(actions, text="Back", command=lambda: self.move(-1))
        self.previous.pack(side="left", padx=3)
        self.next = ttk.Button(actions, text="Next", command=lambda: self.move(1))
        self.next.pack(side="left", padx=3)
        self.title = ttk.Label(self.context, style="PageTitle.TLabel")
        self.title.pack(anchor="w")
        self.hint = ttk.Label(self.context, wraplength=app.px(570), justify="left", style="Small.TLabel")
        self.hint.pack(anchor="w", fill="x", pady=(3, 0))
        self.hint.bind("<Configure>", lambda event: self.hint.configure(wraplength=max(180, event.width - 8)))
        self.notebook = ttk.Notebook(self.content)
        self.notebook.pack(fill="both", expand=True)
        ttk.Style(self).layout("Guided.TNotebook.Tab", [])
        self.pages = []
        self.descriptions = {}
        self.notebook.bind("<<NotebookTabChanged>>", self.sync, add="+")

    def configure_pages(self):
        tabs = {self.notebook.tab(tab, "text").strip(): tab for tab in self.notebook.tabs()}
        for key, title, hint in PAGES:
            tab = tabs.get(key)
            if not tab:
                continue
            self.pages.append(tab)
            self.descriptions[tab] = (title, hint)
            self.navigation.insert("", "end", iid=tab, text=title)
        for key, tab in tabs.items():
            if tab not in self.pages:
                self.pages.append(tab)
                self.descriptions[tab] = (key, "Use the controls below.")
                self.navigation.insert("", "end", iid=tab, text=key)
        self.set_mode(self.mode.get())
        self.sync()

    def set_mode(self, mode, save=False):
        mode = mode if mode in INTERFACES else "Guided"
        self.mode.set(mode)
        if mode == "Guided":
            self.sidebar.grid()
            self.context.pack(fill="x", before=self.notebook)
            self.notebook.configure(style="Guided.TNotebook")
        else:
            self.sidebar.grid_remove()
            self.context.pack_forget()
            self.notebook.configure(style="TNotebook")
        self.app.appearance.preferences["interface"] = mode
        if save:
            self.app.appearance.save()
            self.app.status_msg.set(f"{mode} interface saved.")

    def _selected(self, _event=None):
        selected = self.navigation.selection()
        if selected and selected[0] != self.notebook.select():
            self.notebook.select(selected[0])

    def sync(self, _event=None):
        tab = self.notebook.select()
        if tab not in self.descriptions:
            return
        title, hint = self.descriptions[tab]
        self.title.configure(text=title)
        self.hint.configure(text=hint)
        if self.navigation.selection() != (tab,):
            self.navigation.selection_set(tab)
            self.navigation.see(tab)
        index = self.pages.index(tab)
        self.previous.configure(state="disabled" if index == 0 else "normal")
        self.next.configure(state="disabled" if index == len(self.pages) - 1 else "normal")

    def move(self, delta):
        tab = self.notebook.select()
        if tab in self.pages:
            index = min(max(0, self.pages.index(tab) + delta), len(self.pages) - 1)
            self.notebook.select(self.pages[index])

    def go_to(self, title):
        for tab in self.notebook.tabs():
            if self.notebook.tab(tab, "text").strip() == title:
                self.notebook.select(tab)
                return
