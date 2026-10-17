# CNCBridge

Takes the CSV file an assembly machine writes for every board, cuts out the cell ranges you
choose, adds two operator-entered values, and delivers the result as JSON, CSV or TXT to a
shared folder and/or an API link. The used machine file is then moved to a back-up folder.

It is a native Windows program (`CNCBridge.exe`, about 12 MB). Nothing else has to be
installed on the machine PC and it needs no internet connection.

## What it does with each file

1. Watches the machine CSV folder. A file is picked up once the machine has finished with it:
   unchanged for 2 seconds and no longer held open.
2. Reads it (comma, semicolon, tab or `|` separated; UTF-8, UTF-16 or Windows code page).
3. Cuts out up to **10 cell ranges**, written like in a spreadsheet:

   | You enter | Meaning |
   |---|---|
   | `L1:M8` (also `L1 x M8`, `L1xM8`) | the block from cell L1 to cell M8 |
   | `B6` | one cell |
   | `A12:I*` | from A12 down to the first empty row, for tables whose length changes per board |

   Per range you choose a name and whether its first row is a header row.
4. Adds the **2 manual inputs** (for example Operator and Work order) plus the source file
   name, time and station name.
5. Writes one output file in the chosen format: **JSON, CSV or TXT**.
6. Delivers it to the **shared folder**, to the **API link**, or to both.
7. Moves the machine file to `back-up\YYYY-MM-DD\`, so the machine folder only ever holds new files.

## Install

Copy `dist\CNCBridge.exe` into a folder of its own on the machine PC, for example
`C:\CNCBridge\`, and start it. Settings, history, send queue and log are kept in the data
folder `%ProgramData%\CNCBridge` (or `%LOCALAPPDATA%\CNCBridge` if that cannot be written),
outside the program folder, so replacing or rebuilding the program never removes them.
**Open folder > Data folder** opens it.

Upgrading from version 1.0, which kept these in `data` beside the program: the first start
moves that folder to the new place and renames the old one to `data.moved-<date>`.

For two machine folders on one PC, give each copy its own data folder, for example a shortcut
to `CNCBridge.exe --data C:\CNCBridge\line2`. Two copies upgraded from 1.0 need nothing: the
first moves to the new place and the second keeps using its own `data` folder.

### Bundled fonts

CNCBridge bundles the official Google Fonts Roboto and Roboto Mono variable TrueType files
in `assets/fonts/`, together with each family's SIL Open Font License. Before creating the
Tk window, the app registers both fonts privately for its own process: GDI on Windows,
Core Text on macOS, and Fontconfig on Linux. This does not install fonts system-wide and
does not require an internet connection. Linux systems need Fontconfig available for
process-local registration. If registration is unavailable, CNCBridge falls back to
matching fonts already installed on the computer.

PyInstaller builds must include the `assets/fonts` directory as application data. The
Windows `build_exe.bat` command does this automatically; the `.spec` files also include
the fonts and license files. For a direct PyInstaller CLI build on macOS or Linux, use
`--add-data "assets/fonts:assets/fonts"` (Windows uses `;` as the source/destination
separator, as in `build_exe.bat`).

## Set up

**Setup tab**

1. *Folders*: the machine CSV folder and the back-up folder. They must be different folders.
2. *Output format*: JSON, CSV or TXT.
3. *Data to extract*: type the ranges, or press **Pick...**, which opens a real machine file
   as a grid with column letters and row numbers. Drag over the cells, choose the slot, give
   it a name, press **Assign**. The right-hand side shows the exact file that will be produced.
4. *Manual inputs*: two name/value pairs. A changed value applies from the next file after
   **Save settings**; monitoring does not have to be stopped.

**Delivery tab**

5. *Shared folder*: a local or network path such as `\\MES-SERVER\inbox`. **Test folder**
   checks that it can be written to.
6. *API link*: the address the files are POSTed to. Optional token (sent as
   `Authorization: Bearer <token>`), optional extra headers (one `Name: value` per line),
   and whether the file is sent as the request body or as a form upload (field `file`, plus
   `station`). **Test connection** sends a small test file.

Press **Save settings**, then **Start monitoring**. **Process now** handles the files that
are waiting once, without continuing to watch.

These commands are on the toolbar at the top of the window, visible from every tab:
**Start monitoring**, **Stop**, **Process now**, **Save settings**, **Open CSV file...**
(opens a machine file in the range picker) and **Open folder** (machine, back-up, shared or
data folder in Explorer).

**Settings** on the toolbar exports the settings to a file (without the API token, which only
works on this PC), imports them on another PC, or restores one of the last 20 saved versions.
Every **Save settings** keeps a copy in `backups` in the data folder. An imported or restored
version only fills the form; press **Save settings** to keep it.

## Output files

Named `<machine file name>_<date>_<time>.<json|csv|txt>`.

JSON:

```json
{
  "source_file": "board_00001.csv",
  "processed_at": "2026-10-10T15:38:15",
  "station": "SMT Line 1 - PNP M8",
  "manual_inputs": { "Operator": "OP-17", "Work order": "WO-1187" },
  "selections": [
    { "name": "Placements", "range": "A12:I*",
      "columns": ["Timestamp", "Ref", "Part_No"],
      "rows": [ { "Timestamp": "2026-10-09 10:00:00.581", "Ref": "R101", "Part_No": "RC0402-10K" } ] },
    { "name": "Board serial", "range": "B6", "value": "B240001" },
    { "name": "Raw block", "range": "L1:M8", "rows": [ ["a", "b"], ["c", "d"] ] }
  ]
}
```

A range with "first row is header" gives `columns` and one object per row; without it, `rows`
is a list of lists; a single cell gives `value`.

CSV: an `[INFO]` block of `key,value` lines (source file, time, station, manual inputs),
then for each range a line `[name],range` followed by its rows.

TXT: the same content as an aligned, readable report.

All output is UTF-8. While a file is being copied into the shared folder it has a temporary
name starting with `.` and ending in `.part`; it gets its real name only when complete.

## What the API receives

`POST <your link>` with the file as body (`Content-Type` `application/json`, `text/csv` or
`text/plain`) or as `multipart/form-data`. Headers: `X-Filename`, `X-Station`,
`Authorization` if a token is set, and your extra headers. Any 2xx answer counts as delivered.

## When something goes wrong

| Situation | What happens |
|---|---|
| Shared folder or API cannot be reached | Output files wait in `outbox` in the data folder and are sent in order when it is back. Machine files are still processed and backed up. |
| The API refuses one file (HTTP 400, 409, 413, 415, 422) | That file is moved to `rejected` in the data folder, shown in red in the header and in History, and the next files continue. |
| Wrong token or wrong link (401, 403, 404...) or a redirect | Treated as a problem with the link, not the file: everything waits in the queue until the settings are corrected. |
| A machine file has no data in any chosen range, or is not a readable CSV | Moved to `back-up\_failed`, listed as failed. |
| The same file content arrives again | Moved to `back-up\_duplicates` and not sent. Can be switched off under Options. |
| Back-up drive not available | The machine file is left where it is and nothing is sent for it until the drive is back. |
| PC restarted | Settings, history and the send queue are on disk; start the program and it continues. |

The **History** tab lists every file with its result per destination. Select rows there and press
**Send again** to put files the receiving site refused back in the send queue (once its problem is
fixed), or **Process again** to put machine files that were set aside back in the machine folder
(for example after correcting a cell range). A duplicate processed again is delivered a second time,
so CNCBridge asks first. The **Log** tab and
`logs\cncbridge.log` in the data folder hold the details.

## Alerts

CNCBridge tells someone when it needs attention, instead of only writing it to the log:

- a problem that lasts longer than the set time (default 10 minutes): a destination that cannot
  be reached, a machine file stuck because a folder is unavailable, or incomplete settings. When
  it clears, a "working again" message follows;
- a file set aside: a machine file moved to `back-up\_failed`, or a file the API refused.

Set them up under **Alerts** on the Delivery tab:

| Channel | Default | Needs |
|---|---|---|
| Windows notification on this PC | on | nothing |
| Webhook (JSON POST; Teams and Slack incoming webhooks accept it) | off | the link |
| Email | off | mail server, port, and the address to send to; a login if the server asks for one |

**Send test alert** sends a test through every channel that is switched on and shows the result.
While a problem is open, a red banner under the counters shows it. The email password is stored
encrypted, like the API token, and is never exported.

## Processing in the background

Tick *Process in the background* on the Delivery tab and press **Save settings**. Processing
then moves to a Windows background task for your user. It goes on after the window is
closed and starts by itself when you log on to Windows (no administrator rights needed).

While the background runs, the window is *connected* to it: the header shows
**In background**, the counters, history, alerts and the **Log** tab show the background's
work, and **Start monitoring** and **Stop** start and stop it. Settings you save are picked up
by the background within a few seconds, without a restart. **Process now** is not needed
and is greyed out.

Untick the option and press **Save settings** to go back: the background stops after the
current file, the task is removed, and the window processes files again.

Without the option, closing the window stops processing, as before. To start the window
with Windows, tick *Start monitoring when the program opens* and put a shortcut to
`CNCBridge.exe` into the Startup folder (press Win+R, type `shell:startup`).

Command line: `--headless` (no window), `--headless --once` (process what is waiting and
exit), `--data DIR` (use another data folder).

## Demo and test kit

To show CNCBridge working without a real machine or a real MES, put `CNCBridgeDemo.exe`
in the same folder as `CNCBridge.exe` and start it. It:

- creates a folder `CNCBridge_demo` beside itself with a pretend machine folder, back-up
  folder and shared folder (your normal `data` folder is not used);
- starts a stand-in customer MES on this PC that receives what CNCBridge sends, with an
  inbox page you can open in a browser;
- opens CNCBridge already set up for the demo. Press **Start monitoring** there.

The Demo control window then drives the show:

| Button | What it demonstrates |
|---|---|
| Start machine / Drop one board now | board files arriving, being converted, delivered and backed up |
| Send a duplicate | the same content is not sent twice |
| Drop a broken file | an unusable file is put aside and work continues |
| Write a file slowly | a file is not touched until the machine has finished it |
| Simulate outage / End outage | files wait in the queue and are sent in order afterwards |
| Refuse next file | one refused file is put aside, the rest continue |
| Copy all test files / Copy one | eight fixed, numbered test files, one situation each |
| Open test script | the step-by-step sheet: do this, see that |
| Reset demo | empties the demo folder (close CNCBridge first) |

The test data are invented pick-and-place board logs. Each has a header block (`B5`
program, `B6` board serial), a summary block at `L1:M8`, and a parts table from `A12`
whose length changes per board, so the demo uses all three kinds of range.

The fixed test files and the test script are also in `demo	est_files` and
`demo\TEST_SCRIPT.md`. `tests	est_demo.py` checks that every test file ends up where the
script says.

## Good to know

- The API token is stored encrypted with Windows DPAPI, bound to this PC. A `config.json`
  copied to another PC keeps every setting except the token. Any Windows user of this PC
  can run the program with it.
- Closing the window stops monitoring. Files that arrive meanwhile wait in the machine
  folder and are processed at the next start.
- Not included: picking tables by title, column filters, automatic deletion of old back-ups.
- The rarely changed settings `stable_seconds` (default 2) and `api_timeout` (default 20)
  are only in `config.json` in the data folder.

## Making a release

`release.bat` builds both programs and writes `release\CNCBridge-<version>.zip` (the two exe
files and this README) and `release\CNCBridge-<version>.sha256` (checksums, so the receiving
PC can check the files arrived intact). Raise `VERSION` in `cncbridge\config.py` before each
release; an existing zip with the same version is never overwritten. The exe files are not
kept in git; publish the zip instead, for example on GitHub Releases with the command that
`release.bat` prints at the end.

## For developers

Python 3.9 or newer, standard library only.

```
CNCBridge.pyw            double-click to run from source
cncbridge/engine.py      read, cell ranges, render, queue, deliver, back up, ledger
cncbridge/config.py      settings, data folder, token encryption
cncbridge/app.py         main window
cncbridge/grid.py        spreadsheet view with drag selection
assets/fonts/            bundled Roboto/Roboto Mono fonts and OFL licenses
tests/test_engine.py     python -m unittest discover -s tests -v
demo/                    test data, stand-in MES, Demo control window (CNCBridgeDemo.pyw starts it)
build_exe.bat            builds CNCBridge.exe, and CNCBridgeDemo.exe where demo\ exists (options: app, demo, clean)
```
