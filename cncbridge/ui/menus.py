"""The menu bar, its enabled/disabled states and the keyboard shortcuts."""
import tkinter as tk
from tkinter import ttk

from .. import help as help_text


class MenusMixin:
    def _build_menubar(self):
        """The menu bar: everything that is not the daily work. Each entry calls the method the old button did."""
        strip = ttk.Frame(self, style="MenuBar.TFrame")
        strip.pack(fill="x")
        self.menu_items, self.menubar_buttons = {}, {}
        menu_font = (self._font_body, self.L.menu_size)

        def menu(label, parent=None):
            if parent is not None:
                sub = tk.Menu(parent, tearoff=False, font=menu_font)
                parent.add_cascade(label=label, menu=sub)
                return sub
            # a menu bar drawn by the program: Windows draws its own at a fixed size, whatever the font
            button = ttk.Menubutton(strip, text=label, style="MenuBar.Toolbutton", underline=0, takefocus=False)
            button.pack(side="left")
            sub = tk.Menu(button, tearoff=False, font=menu_font)
            button["menu"] = sub
            self.menubar_buttons[label] = button
            self.bind(f"<Alt-{label[0].lower()}>", lambda event, b=button: self._open_menu(b))
            return sub

        def item(parent, key, label, command, accelerator=None):
            parent.add_command(label=label, command=command, accelerator=accelerator)
            self.menu_items[key] = (parent, parent.index("end"))

        file_menu = menu("File")
        item(file_menu, "open_csv", "Open CSV file...", self.open_csv, "Ctrl+O")
        item(file_menu, "open_newest", "Open newest machine file", self.open_newest_file)
        file_menu.add_separator()
        item(file_menu, "save", "Save settings", self.save, "Ctrl+S")
        item(file_menu, "export", "Export settings...", self.export_settings)
        item(file_menu, "import", "Import settings...", self.import_settings)
        item(file_menu, "restore", "Restore an earlier version...",
             lambda: self.import_settings(self.data_dir / "backups"))
        file_menu.add_separator()
        folders = menu("Open folder", file_menu)
        for label, key in (("Machine CSV folder", "source_folder"), ("Back-up folder", "backup_folder"),
                           ("Shared folder", "shared_folder")):
            folders.add_command(label=label, command=lambda key=key: self._open_folder(self.v[key].get()))
        folders.add_separator()
        folders.add_command(label="Data folder (send queue, rejected files, log file)",
                            command=lambda: self._open_folder(self.data_dir))
        file_menu.add_separator()
        item(file_menu, "exit", "Exit", self.on_close)

        monitoring = menu("Monitoring")
        item(monitoring, "start", "Start monitoring", self.start, "F5")
        item(monitoring, "stop", "Stop", self.stop, "Shift+F5")
        item(monitoring, "process_now", "Process now", self.process_now, "F6")

        settings = menu("Settings")
        item(settings, "options", "Options...", self.open_options, "Ctrl+,")
        settings.add_separator()
        for key, page, label in (("opt_processing", "processing", "Processing..."),
                                 ("opt_output", "output", "Output..."),
                                 ("opt_alerts", "alerts", "Alerts..."),
                                 ("opt_access", "access", "Operators && access..."),
                                 ("opt_appearance", "appearance", "Appearance...")):
            item(settings, key, label.replace("&&", "&"), lambda page=page: self.open_options(page))
        settings.add_separator()
        item(settings, "lock", "Lock setup", self.toggle_lock)

        tools = menu("Tools")
        item(tools, "test_shared", "Test shared folder", self.test_shared)
        item(tools, "test_api", "Test API connection", self.test_api)
        item(tools, "test_alert", "Send test alert", self.test_alert)
        tools.add_separator()
        item(tools, "simulation", "Simulation...", self.open_simulation)

        view = menu("View")
        item(view, "log", "Activity log", self.open_log, "Ctrl+L")
        view.add_separator()
        for number, (key, attribute, label) in enumerate((("view_setup", "setup_tab", "Setup"),
                                                          ("view_delivery", "delivery_tab", "Delivery"),
                                                          ("view_pick", "pick_tab", "Pick ranges && preview"),
                                                          ("view_history", "history_tab", "History")), 1):
            item(view, key, label.replace("&&", "&"), lambda attribute=attribute: self._show_tab(getattr(self, attribute)),
                 f"Ctrl+{number}")

        help_menu = menu("Help")
        item(help_menu, "help", "Getting started", lambda: self.open_help("start"), "F1")
        for key, title, _ in help_text.TOPICS[1:]:
            item(help_menu, f"help_{key}", title, lambda key=key: self.open_help(key))
        help_menu.add_separator()
        item(help_menu, "about", "About CNCBridge", self.about)
        self.bind("<F10>", lambda event: self._open_menu(next(iter(self.menubar_buttons.values()))))
        ttk.Separator(self).pack(fill="x")

    def _open_menu(self, button):
        """Drop a menu down from its title, as Alt+letter and F10 do."""
        self.nametowidget(str(button["menu"])).tk_popup(button.winfo_rootx(),
                                                       button.winfo_rooty() + button.winfo_height())
        return "break"

    # entries that need the setup rights: they are disabled while the setup is locked
    LOCKED_ENTRIES = ("open_csv", "open_newest", "save", "export", "import", "restore", "options", "opt_processing",
                      "opt_output", "opt_alerts", "opt_access", "opt_appearance", "test_shared", "test_api",
                      "test_alert", "simulation", "view_setup", "view_delivery", "view_pick")

    def _set_menu(self, key, enabled):
        menu, index = self.menu_items[key]
        menu.entryconfigure(index, state="normal" if enabled else "disabled")

    def _menu_enabled(self, key):
        menu, index = self.menu_items[key]
        return str(menu.entrycget(index, "state")) != "disabled"

    def _bind_shortcuts(self):
        """Keyboard shortcuts; each does what its menu entry does, and nothing while that entry is disabled."""
        def bind(sequence, key, handler):
            def run(_event=None):
                if self._menu_enabled(key):
                    handler()
                return "break"
            self.bind(sequence, run)

        bind("<Control-o>", "open_csv", self.open_csv)
        bind("<Control-s>", "save", self.save)
        bind("<F5>", "start", self.start)
        bind("<Shift-F5>", "stop", self.stop)
        bind("<F6>", "process_now", self.process_now)
        bind("<Control-comma>", "options", self.open_options)
        bind("<Control-l>", "log", self.open_log)
        bind("<F1>", "help", lambda: self.open_help("start"))
        for number, (key, attribute) in enumerate((("view_setup", "setup_tab"), ("view_delivery", "delivery_tab"),
                                                   ("view_pick", "pick_tab"), ("view_history", "history_tab")), 1):
            bind(f"<Control-Key-{number}>", key, lambda attribute=attribute: self._show_tab(getattr(self, attribute)))

    def _show_tab(self, tab):
        if str(self.tabs.tab(tab, "state")) == "normal":
            self.tabs.select(tab)

    def open_newest_file(self):
        self.tabs.select(self.pick_tab)
        self.open_newest()
