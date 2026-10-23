"""The Setup and Delivery tabs."""
import tkinter as tk
from tkinter import ttk

from .. import theme
from ..config import FORMATS, MAX_SELECTIONS
from .widgets import Disclosure
from .common import BODY_LABELS
from ..i18n import tr


class SetupTabMixin:
    def _build_setup(self):
        tab = self.setup_tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.tabs.add(tab, text=tr("  Setup  "))
        self._page_intro(tab, tr("Set up your machine"), tr("Choose where machine files arrive, what to include, and how outputs should look."))
        content = ttk.Frame(tab)
        content.pack(fill="both", expand=True)
        self._build_checklist(tab, content)
        content.columnconfigure(0, weight=4, uniform="setup")
        content.columnconfigure(1, weight=6, uniform="setup")
        content.rowconfigure(0, weight=1)
        left, right = ttk.Frame(content), ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        box = ttk.LabelFrame(left, text=tr(" 1  Machine folders "), padding=self.L.box_pad)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text=tr("Station name"), style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=self.L.row)
        station_entry = ttk.Entry(box, textvariable=self.v["station_name"])
        station_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=self.L.row)
        self.field_widgets["station_name"] = station_entry
        self._folder_row(box, 1, tr("Machine folder"), "source_folder")
        ttk.Label(box, text=tr("File pattern"), style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=self.L.row)
        pattern_entry = ttk.Entry(box, textvariable=self.v["file_pattern"], width=14,
                                  **self.code_font)
        pattern_entry.grid(row=2, column=1, sticky="w", padx=8, pady=self.L.row)
        self.field_widgets["file_pattern"] = pattern_entry
        if self.L.chips:
            self.v["file_pattern"].trace_add("write", lambda *_: self._schedule_folder_check())
        self._folder_row(box, 3, tr("Back-up folder"), "backup_folder")
        folder_help = ttk.Label(box, style="Small.TLabel", wraplength=self.px(470), justify="left",
                                text=tr("Each file is taken from the machine folder as soon as the machine has finished writing it. "
                                     "After processing it is moved into the back-up folder (one sub-folder per day)."))
        folder_help.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(9, 2))
        self._fit_wrap(folder_help, self.px(470))

        box = ttk.LabelFrame(left, text=tr(" 2  Output format "), padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        formats = ttk.Frame(box)
        formats.pack(fill="x")
        segmented = self._format_choice(formats)
        if segmented:
            segmented.pack(side="left", pady=2)
        for fmt in FORMATS if not segmented else ():
            choice = ttk.Radiobutton(formats, text=fmt.upper(), value=fmt, variable=self.v["output_format"])
            choice.pack(side="left", padx=(0, 28), pady=2)
            self.field_widgets[f"output_format_{fmt}"] = choice

        box = ttk.LabelFrame(left, text=tr(" 4  Typed values "), padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        box.columnconfigure(1, weight=1)
        box.columnconfigure(2, weight=2)
        ttk.Label(box, text=tr("Field name"), style="CardSmall.TLabel").grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(box, text=tr("Value"), style="CardSmall.TLabel").grid(row=0, column=2, sticky="w")
        for i, (name, value) in enumerate(self.man_vars, 1):
            ttk.Label(box, text=tr("Typed value {i}", i=i), style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=self.L.row)
            ttk.Entry(box, textvariable=name).grid(row=i, column=1, sticky="ew", padx=8, pady=self.L.row)
            ttk.Entry(box, textvariable=value).grid(row=i, column=2, sticky="ew", pady=self.L.row)
        manual_help = ttk.Label(box, style="Small.TLabel", wraplength=self.px(470), justify="left",
                                 text=tr("For example Operator and Work order. Change a value and press Save settings; "
                                      "it applies from the next file, without stopping."))
        manual_help.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 2))
        self._fit_wrap(manual_help, self.px(470))

        box = ttk.LabelFrame(right, text=tr(" 3  Choose data to include (up to 10 ranges) "), padding=self.L.box_pad)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=3)
        box.columnconfigure(2, weight=2)
        self.range_rows, self._ranges_shown = [], 1
        for col, text in ((1, tr("Name in the output")), (2, tr("Cell range")), (3, tr("First row is header"))):
            ttk.Label(box, text=text, style="CardSmall.TLabel").grid(row=0, column=col, sticky="w", padx=6, pady=(0, 6))
        for i, (name, rng, header) in enumerate(self.sel_vars):
            tk.Label(box, text=f" {i + 1} ", bg=theme.RANGE_COLOURS[i][1], fg=theme.WHITE,
                     font=self.strong(self.L.grid_badge + 1)).grid(row=i + 1, column=0, pady=3)
            name_entry = ttk.Entry(box, textvariable=name)
            name_entry.grid(row=i + 1, column=1, sticky="ew", padx=6, pady=self.L.row)
            range_entry = ttk.Entry(box, textvariable=rng, width=12, font=(self._font_mono, self.L.range_size))
            range_entry.grid(row=i + 1, column=2, sticky="ew", padx=6, pady=self.L.row)
            header_check = ttk.Checkbutton(box, text=self.L.check_text, variable=header)
            header_check.grid(row=i + 1, column=3, pady=self.L.row)
            pick_button = ttk.Button(box, text=tr("Pick..."), width=7,
                                     command=lambda i=i: self.open_picker(i))
            pick_button.grid(row=i + 1, column=4, padx=(6, 0), pady=self.L.row)
            self.field_widgets[f"selection_name_{i}"] = name_entry
            self.field_widgets[f"selection_range_{i}"] = range_entry
            self.field_widgets[f"selection_header_{i}"] = header_check
            self.field_widgets[f"selection_pick_{i}"] = pick_button
            self.range_rows.append(box.grid_slaves(row=i + 1))
        if self.L.chips:  # only the ranges in use, and a button for one more (see _show_range_rows)
            self.add_range_btn = ttk.Button(box, text="+  " + tr("Add a range"), command=self.add_range_row)
            self.add_range_btn.grid(row=MAX_SELECTIONS + 1, column=1, sticky="w", padx=6, pady=(self.px(8), 0))
            self.field_widgets["add_range"] = self.add_range_btn
            for slot in self.sel_vars:
                for var in slot[:2]:
                    var.trace_add("write", lambda *_: self._show_range_rows())
        range_help = ttk.Label(box, style="CardSmall.TLabel", wraplength=self.px(650), justify="left",
                               text=tr("Type a range like in a spreadsheet, or press Pick... and drag over a real file.\n"
                                    "L1:M8  (also L1 x M8)  = from cell L1 to cell M8\n"
                                    "B6  = one cell          A12:I*  = from A12 down to the first empty row\n"
                                    "@[ERRORS]  = the table under that title    @NOZZLE*  = every table whose title fits\n"
                                    "Add [Ref, Result] after a range to keep only those columns, in that order"))
        range_help.grid(row=MAX_SELECTIONS + 2, column=0, columnspan=5, sticky="ew", pady=(11, 2))
        self._fit_wrap(range_help, self.px(650))

    def _build_delivery(self):
        tab = self.delivery_tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.tabs.add(tab, text=tr("  Delivery  "))
        self._page_intro(tab, tr("Choose where files are delivered"),
                         tr("Enable one or more destinations. If a destination is unavailable, files wait safely and retry automatically."))
        content = ttk.Frame(tab)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=5, uniform="delivery")
        content.columnconfigure(1, weight=6, uniform="delivery")
        content.rowconfigure(0, weight=1)
        left, right = ttk.Frame(content), ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        box = ttk.LabelFrame(left, text=tr(" 5  Shared folder "), padding=self.L.box_pad)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        shared_chk = self._toggle(box, "shared_enabled", tr("Save every output file into a shared folder"))
        shared_chk.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 5))
        self.field_widgets["shared_enabled"] = shared_chk
        self._folder_row(box, 1, tr("Folder / UNC path"), "shared_folder")
        test_shared = ttk.Button(box, text=tr("Test shared folder"), command=self.test_shared)
        test_shared.grid(row=2, column=1, sticky="w", padx=8, pady=(5, 2))
        self.field_widgets["test_shared"] = test_shared
        self._test_result_line(box, "shared", row=3)

        box = ttk.LabelFrame(left, text=tr(" 7  FTP server "), padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        box.columnconfigure(1, weight=1)
        ftp_enabled = self._toggle(box, "ftp_enabled", tr("Upload every output file to an FTP server"))
        ftp_enabled.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.field_widgets["ftp_enabled"] = ftp_enabled
        for row, (label, key, options) in enumerate(((tr("Server"), "ftp_host", {}),
                                                     (tr("Port"), "ftp_port", {"width": 7, **self.code_font}),
                                                     (tr("User name"), "ftp_user", {}),
                                                     (tr("Password"), "ftp_password", {"show": "\u25cf"}),
                                                     (tr("Folder on server"), "ftp_folder", {})), 1):
            ttk.Label(box, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=self.L.row)
            entry = ttk.Entry(box, textvariable=self.v[key], **options)
            entry.grid(row=row, column=1, sticky="w" if key == "ftp_port" else "ew", padx=(8, 0), pady=self.L.row)
            self.field_widgets[key] = entry
        choices = ttk.Frame(box)
        choices.grid(row=6, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        for key, text in (("ftp_tls", tr("Encrypt (FTPS)")), ("ftp_passive", tr("Passive mode"))):
            check = ttk.Checkbutton(choices, text=text, variable=self.b[key])
            check.pack(side="left", padx=(0, 18))
            self.field_widgets[key] = check
        test_ftp = ttk.Button(box, text=tr("Test FTP server"), command=self.test_ftp)
        test_ftp.grid(row=7, column=1, sticky="w", padx=8, pady=(7, 2))
        self.field_widgets["test_ftp"] = test_ftp
        self._test_result_line(box, "ftp", row=8)
        if self.L.chips:  # off, the FTP card is only its switch: its fields come back when it is turned on
            self._ftp_rows = [w for w in box.grid_slaves() if int(w.grid_info()["row"]) > 0]
            self.b["ftp_enabled"].trace_add("write", lambda *_: self._show_ftp_fields())

        box = ttk.LabelFrame(left, text=tr(" How delivery works "), padding=self.L.box_pad)
        box.pack(fill="x", pady=(self.L.box_gap, 0))
        note = ttk.Label(box, style="Card.TLabel", wraplength=self.px(540), justify="left", text=(
            tr("Each output file is first queued on this PC and removed from the queue only after it has arrived. "
            "If a destination cannot be reached, the files wait and are sent in order when it is back; machine "
            "files are still processed and backed up.\n\n"
            "A file the receiving site refuses is set aside in 'rejected' in the data folder, and the others "
            "continue. More under Help.")))
        note.pack(anchor="nw", fill="x")
        self._fit_wrap(note, self.px(540))

        box = ttk.LabelFrame(right, text=tr(" 6  API link "), padding=self.L.box_pad)
        box.pack(fill="x") if self.L.chips else box.pack(fill="both", expand=True)  # a card as tall as its content
        box.columnconfigure(1, weight=1)
        api_enabled = self._toggle(box, "api_enabled", tr("Send every output file to the end user's site"))
        api_enabled.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.field_widgets["api_enabled"] = api_enabled
        ttk.Label(box, text=tr("API link (URL)"), style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=self.L.row)
        api_url = ttk.Entry(box, textvariable=self.v["api_url"])
        api_url.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=self.L.row)
        self.field_widgets["api_url"] = api_url
        ttk.Label(box, text=tr("Token (optional)"), style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=self.L.row)
        api_token = ttk.Entry(box, textvariable=self.v["api_token"], show="●")
        api_token.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=self.L.row)
        self.field_widgets["api_token"] = api_token
        extras, first = box, 3
        if self.L.chips:  # rarely needed: closed until asked for, or until something is in it (see fill)
            self.api_more = Disclosure(box, tr("More: send as, extra headers"), font=(self._font_body, 10))
            self.api_more.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(self.px(6), 0))
            extras, first = self.api_more.body, 0
            extras.columnconfigure(1, weight=1)
            self.field_widgets["api_more"] = self.api_more.header
        ttk.Label(extras, text=tr("Send as"), style="Card.TLabel").grid(row=first, column=0, sticky="w", pady=self.L.row)
        api_body = ttk.Combobox(extras, textvariable=self.v["api_body"], values=list(BODY_LABELS.values()),
                                state="readonly", width=26)
        api_body.grid(row=first, column=1, sticky="w", padx=(8, 0), pady=self.L.row)
        self.field_widgets["api_body"] = api_body
        ttk.Label(extras, text=tr("Extra headers"), style="Card.TLabel").grid(row=first + 1, column=0, sticky="nw",
                                                                              pady=self.L.row)
        self.headers_text = tk.Text(extras, height=4, width=30, font=(self._font_mono, self.L.text_mono), relief="solid", borderwidth=1,
                                    background=theme.SURFACE, foreground=theme.TEXT, insertbackground=theme.TEXT,
                                    highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
                                    padx=7, pady=5, wrap="word", undo=True)
        self.headers_text.grid(row=first + 1, column=1, sticky="ew", padx=(8, 0), pady=self.L.row)
        self.field_widgets["api_headers"] = self.headers_text
        ttk.Label(extras, style="CardSmall.TLabel", wraplength=self.px(420), justify="left",
                  text=tr("One per line, for example  X-Api-Key: abc123. The token is sent as "
                       "Authorization: Bearer <token> and is stored encrypted.")).grid(row=first + 2, column=1,
                                                                                      sticky="w", padx=(8, 0),
                                                                                      pady=(0, 3))
        test_api = ttk.Button(box, text=tr("Test API link"), command=self.test_api)
        test_api.grid(row=6, column=1, sticky="w", padx=8, pady=(7, 2))
        self.field_widgets["test_api"] = test_api
        self._test_result_line(box, "api", row=7)

    def _show_range_rows(self):
        """A theme with chips: the slots up to the last one in use, at least one, and those added with the button."""
        used = [i for i, (name, rng, _) in enumerate(self.sel_vars) if name.get().strip() or rng.get().strip()]
        shown = min(max((used[-1] + 1) if used else 1, self._ranges_shown), MAX_SELECTIONS)
        for i, widgets in enumerate(self.range_rows):
            for widget in widgets:
                if i < shown:
                    widget.grid()
                else:
                    widget.grid_remove()
        if shown >= MAX_SELECTIONS:
            self.add_range_btn.grid_remove()
        else:
            self.add_range_btn.grid()
        return shown

    def add_range_row(self):
        self._ranges_shown = self._show_range_rows() + 1
        shown = self._show_range_rows()
        self.field_widgets[f"selection_name_{shown - 1}"].focus_set()

    def _test_result_line(self, box, kind, row):
        """A theme with chips: what the last test of this destination said, under its Test button."""
        if not self.L.chips:
            return
        label = ttk.Label(box, text=tr("Not tested yet"), style="CardSmall.TLabel", wraplength=self.px(420),
                          justify="left")
        label.grid(row=row, column=1, sticky="w", padx=8, pady=(2, 0))
        self.test_results[kind] = label

    def _show_ftp_fields(self):
        on = self.b["ftp_enabled"].get()
        for widget in self._ftp_rows:
            if on:
                widget.grid()
            else:
                widget.grid_remove()
