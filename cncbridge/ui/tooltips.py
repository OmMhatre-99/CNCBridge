"""Tooltips for the window's controls."""
from .. import help as help_text
from ..config import FORMATS, MAX_SELECTIONS
from ..tooltip import NotebookTabTooltip, Tooltip


class TooltipsMixin:
    # ================================================================ run control
    def _add_tooltips(self):
        """Attach concise help to the controls that benefit from extra explanation."""
        Tooltip(self.start_btn, "Start monitoring the source folder for new CNC files.")
        Tooltip(self.stop_btn, "Stop monitoring and processing.")
        Tooltip(self.once_btn, "Process the current files once without continuous monitoring.")
        Tooltip(self.save_btn, "Save all current settings to configuration.")
        Tooltip(self.sample_open_btn, "Choose a sample machine file to inspect and select cell ranges.")
        Tooltip(self.sample_newest_btn, "Open the newest file waiting in the machine folder or most recent back-up.")
        Tooltip(self.grid_view.canvas, "Drag across cells to select them. Arrow keys move; hold Shift to extend a range.")

        help_text = {
            "station_name": "Name used to identify this machine in output files.",
            "source_folder": "Folder where the machine places completed CSV files.",
            "source_folder_browse": "Choose the machine's CSV folder.",
            "file_pattern": "File names to watch; the default matches all CSV files.",
            "backup_folder": "Processed machine files are moved here, grouped by date.",
            "backup_folder_browse": "Choose where processed machine files are archived.",
            "shared_enabled": "Turn on delivery to a local or network shared folder.",
            "shared_folder": "Optional destination folder, such as a network share.",
            "shared_folder_browse": "Choose the shared destination folder.",
            "test_shared": "Check that CNCBridge can write to the shared folder.",
            "api_enabled": "Turn on delivery to the receiving site's API.",
            "api_url": "Address that receives the output files.",
            "api_token": "Optional authentication token; CNCBridge stores it encrypted.",
            "api_body": "Choose whether the file is sent as the request body or as a form upload.",
            "api_headers": "Optional HTTP headers, one per line in Name: value format.",
            "test_api": "Send a small test file to check the API connection.",
            "ftp_enabled": "Upload every output file to an FTP server, as well as to the other destinations.",
            "ftp_host": "The FTP server's name or address.",
            "ftp_port": "Usually 21, for both plain FTP and explicit FTPS.",
            "ftp_user": "Login name; leave empty for anonymous.",
            "ftp_password": "Stored encrypted, and never included when settings are exported.",
            "ftp_folder": "Folder on the server, for example /incoming/line1. Missing folders are created; "
                          "empty uses the login folder.",
            "ftp_tls": "FTPS: encrypt the login and the files (explicit TLS). Turn on when the server supports it.",
            "ftp_passive": "Passive mode, which works through most firewalls. Turn off only if the server needs it.",
            "test_ftp": "Upload a small test file and remove it again, to check the FTP settings.",
            "autostart": "Begin monitoring automatically when CNCBridge opens.",
            "skip_duplicates": "Ignore files whose content has already been processed.",
            "poll_seconds": "How often CNCBridge checks the machine folder for new files.",
            "encoding": "Choose a character encoding if CSV text looks incorrect; Auto is recommended.",
            "delimiter": "Choose the CSV column separator; Auto is recommended.",
            "alert_after_minutes": "A problem such as an unreachable destination must last this long before an "
                                   "alert is sent. Files set aside are announced at once.",
            "alert_notify": "Show a notification in the corner of this PC's screen.",
            "alert_webhook_enabled": "Post each alert as JSON to a link, such as a Teams or Slack incoming webhook.",
            "alert_webhook_url": "The address that receives the alert.",
            "alert_email_enabled": "Send each alert by email through your mail server.",
            "alert_email_server": "Name or address of the mail server, for example smtp.example.com.",
            "alert_email_port": "Usually 587 (with STARTTLS), 465 (encrypted) or 25.",
            "alert_email_starttls": "Encrypt the connection to the mail server. Leave on unless told otherwise.",
            "alert_email_user": "Leave empty if the mail server does not ask for a login.",
            "alert_email_password": "Stored encrypted, like the API token, and never exported.",
            "alert_email_from": "Sender address; the user name is used when this is empty.",
            "alert_email_to": "One or more addresses, separated by commas.",
            "test_alert": "Send a test message through every alert channel that is switched on.",
            "keep_backup_days": "Back-up day folders and set-aside files older than this are deleted, checked "
                                "once an hour. 0 keeps everything. The send queue is never touched.",
            "keep_history_days": "History rows older than this are deleted. 0 keeps the whole history.",
            "run_in_background": "Applied with Save settings. Processing then runs as a Windows background task "
                                 "for this user; this window shows it and Start/Stop control it.",
            "pick_range": "Shows the cell range selected in the grid.",
            "json_numbers": "In JSON files, cells such as 12 or 40.53 become numbers. IDs with leading zeros, text and "
                            "numbers with a decimal comma stay text. Other formats are not affected.",
            "csv_flat": "CSV files become one plain table: a row per record, with the file, time, station, your "
                        "inputs and the selection name in front. Other formats are not affected.",
            "time_zone": "The processing time written in each file ends with this PC's time zone, such as +05:30. "
                         "Times copied from the machine file are not changed.",
            "rename_columns": "Change column names in every file, for example Ref=Reference. Applies to selections "
                              "that use a header row. Names are matched ignoring upper and lower case.",
            "complete_marker": "Optional. Text the machine writes last, such as End_Time. A file is taken only once "
                               "it contains this text (ignoring case), on top of the usual idle time. Leave empty "
                               "for machines that write a file in one go.",
            "pick_open": "Extend the selected range down to the first empty row.",
            "pick_heading": "Instead of fixed cells, find the block by the text in the top-left cell you marked. "
                            "It then still works when the table moves down the file.",
            "pick_name": "Name shown for this data in the output file.",
            "assign": "Save the selected range into the chosen extraction slot.",
            "clear_slot": "Remove the range and name from the chosen slot.",
        }
        for key, text in help_text.items():
            Tooltip(self.field_widgets[key], text)

        Tooltip(self.slot_box, "Choose which extraction slot receives the selected cells.")
        for fmt in FORMATS:
            Tooltip(self.field_widgets[f"output_format_{fmt}"], f"Create the output as {fmt.upper()}.")
        for i in range(MAX_SELECTIONS):
            Tooltip(self.field_widgets[f"selection_name_{i}"], "Optional label used for this extracted data.")
            Tooltip(self.field_widgets[f"selection_range_{i}"], "Spreadsheet-style cells to include, e.g. A1:C8.")
            Tooltip(self.field_widgets[f"selection_header_{i}"], "Use the first selected row as column names.")
            Tooltip(self.field_widgets[f"selection_pick_{i}"], "Choose this range by selecting cells in a sample file.")

        self.tab_tooltip = NotebookTabTooltip(self.tabs, {
            0: "Machine folders, output format, data ranges and manual inputs.",
            1: "Shared-folder and API destinations, plus file-handling options.",
            2: "Select cells from a sample file and preview the resulting output.",
            3: "Review recent machine files and their delivery results.",
            4: "Technical event log for processing and delivery details.",
            5: "The typed values that go into every file.",
        })
        Tooltip(self.field_widgets["shift_times"], "Optional. At each of these times the operator is asked to confirm "
                                                   "or change the typed values. Files keep being processed meanwhile.")
        Tooltip(self.field_widgets["restart_now"], "Save, then close this window and open it again with the "
                                                   "chosen look. Monitoring resumes by itself.")
        Tooltip(self.field_widgets["set_password"], "Set or change the password that locks the setup tabs.")
        Tooltip(self.field_widgets["remove_password"], "Remove the password so everyone can change settings.")
        Tooltip(self.field_widgets["operator_apply"], "Save the typed values; they apply from the next file.")
        Tooltip(self.slot_box, "Select which slot (range) you are editing.")
