"""Contextual help adapted from the friend's UI branch without coupling to its layout."""
from tkinter import ttk
from .tooltip import NotebookTabTooltip, Tooltip


FIELD_HELP = {
    "station_name": "Machine identifier included in every output file.",
    "source_folder": "Folder where the machine publishes completed CSV files.",
    "file_pattern": "Names to watch, for example *.csv. Temporary files should use a different extension.",
    "backup_folder": "Processed originals are archived here in dated folders.",
    "shared_folder": "Local or network folder that receives the extracted output.",
    "api_url": "Customer endpoint that receives output files. Use HTTPS for real credentials.",
    "api_token": "Optional bearer token. Encrypted on Windows; private but unencrypted on Linux/macOS.",
    "api_body": "Send the file as the request body, or as a multipart form upload.",
    "poll_seconds": "Seconds between checks for new machine files.",
    "encoding": "Choose an encoding if text looks incorrect; Auto detects common machine encodings.",
    "delimiter": "CSV column separator. Auto detects common separators.",
    "shared_enabled": "Enable output delivery to the shared folder.",
    "api_enabled": "Enable output delivery to the configured API endpoint.",
    "skip_duplicates": "Archive identical source content without sending it again.",
    "autostart": "Start monitoring automatically when this profile opens.",
}
TAB_HELP = {
    "Getting started": "Follow the setup steps or try the isolated demo first.",
    "Setup": "Machine folders, output format, ten data ranges and two manual inputs.",
    "Delivery": "Shared-folder and API destinations, plus file-reading options.",
    "History": "Recent source files and the delivery status of each output.",
    "Log": "Technical messages for troubleshooting processing and delivery.",
    "Appearance": "Change colors and text size without changing machine settings.",
    "User guide": "Search the complete offline manual, folder meanings, setup steps and troubleshooting.",
    "Checks": "Check folders and extraction. API testing sends a small test file only when selected.",
}


def install_help(app):
    variables = {str(var): FIELD_HELP[key] for key, var in {**app.v, **app.b}.items() if key in FIELD_HELP}
    for name, cell_range, header in app.sel_vars:
        variables.update({str(name): "Optional label for this extracted table or value.",
            str(cell_range): "Spreadsheet range, for example L1:M8, B6, or A12:I* (down to the first blank row).",
            str(header): "Use the first selected row as column names."})
    for name, value in app.man_vars:
        variables[str(name)] = "Unique name for this manually entered field, such as Operator."
        variables[str(value)] = "Value added to subsequent outputs after saving settings."
    controls = {
        "Start monitoring": "Watch the machine folder continuously for completed files.",
        "Stop": "Stop monitoring after the current file finishes.",
        "Process now": "Process currently waiting files once without continuous monitoring.",
        "Save settings": "Save this machine profile. Changes apply to subsequent files.",
        "Open CSV file": "Inspect a sample in the range picker without processing or moving it.",
        "Simulation": "Open the integrated demo in an isolated workspace, leaving this profile unchanged.",
        "Open folder": "Open a configured machine, backup, output or application data folder.",
        "Assign": "Assign the selected cells to the chosen extraction slot.",
        "Pick...": "Choose this slot's range visually in a sample file.",
    }
    tips = []
    def walk(parent):
        for widget in parent.winfo_children():
            keys = widget.keys()
            variable = next((str(widget.cget(key)) for key in ("textvariable", "variable")
                             if key in keys and str(widget.cget(key)) in variables), None)
            help_text = variables.get(variable)
            if not help_text and isinstance(widget, (ttk.Button, ttk.Menubutton)):
                label = str(widget.cget("text"))
                help_text = next((text for name, text in controls.items() if name in label), None)
            if help_text:
                tips.append(Tooltip(widget, help_text))
            walk(widget)
    walk(app)
    tips.append(Tooltip(app.grid_view.canvas, "Drag to select cells, or use arrow keys. Hold Shift to extend the range."))
    tips.append(Tooltip(app.headers_text, "Optional HTTP headers, one Name: value pair per line."))
    texts = {i: TAB_HELP.get(app.tabs.tab(tab, "text").strip(),
                           "Inspect data and use the controls on this page. Ctrl+Tab switches pages.")
             for i, tab in enumerate(app.tabs.tabs())}
    tips.append(NotebookTabTooltip(app.tabs, texts))
    return tips
