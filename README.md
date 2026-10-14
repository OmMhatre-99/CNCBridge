# CNCBridge

The simulator is now integrated with this desktop application. Use **Getting started → Open integrated
demo**, or run **python -m cncbridge --demo** from the repository folder. This opens the real CNCBridge
engine with invented machine data, a local HTTP MES receiver, and a separate demo workspace.

**Appearance** offers Light, Dark and High contrast themes, plus Small, Medium, Large and Extra large
text. The spreadsheet, preview, history and log follow these preferences, which are saved separately
from the machine profile. Setup and Delivery have scrollbars so settings remain reachable.

The **Checks** tab reports settings, folder access, extraction preview and optional API test results.
An untested API is labelled **NOT TESTED**. Full delivery proof is available with
**Verify demo pipeline**, which requires successful HTTP uploads and checks the delivered bytes,
archived originals, retries after restart, duplicate blocking, stability gate and bad-file handling.

The older **linebridge-Simulation** directory is retained for reference. Running its web demo alone
does not exercise the CNCBridge desktop engine.

Takes the CSV file an assembly machine writes for every board, cuts out the cell ranges you
choose, adds two operator-entered values, and delivers the result as JSON, CSV or TXT to a
shared folder and/or an API link. The used machine file is then moved to a back-up folder.

It is a native Tk desktop application. Windows executables and macOS app bundles
bundle Python; the Debian package uses the distribution's Python and Tk packages.
Local extraction needs no internet connection; API delivery needs access to its endpoint.
See [release readiness and build instructions](docs/RELEASE_READINESS.md) for limitations.

## What it does with each file

1. Watches the machine CSV folder. A file is picked up once the machine has finished with it:
   unchanged for 2 seconds, with an additional open-file check on Windows. Prefer
   temporary filenames followed by an atomic rename; timestamps alone cannot prove completion.
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

To run from source on Kali/Linux (Python and Tkinter required), open a terminal in the repository and
run **python3 -m pip install -r requirements.txt**, then **python3 -m cncbridge**. If the system Python
requires a virtual environment, create one with **python3 -m venv .venv** and activate it before
installing requirements. Kali may need its **python3-tk** package for the desktop window.

Start the integrated demo with **python3 -m cncbridge --demo** or **sh start_demo.sh**.
On Windows, use **start_demo.bat** or **CNCBridge.exe --demo** after rebuilding.
The existing committed executable is an older build; these changes are in the source.

Copy `dist\CNCBridge.exe` into a folder of its own on the machine PC, for example
`C:\CNCBridge\`, and start it. It creates a `data` folder beside itself for its settings,
history, send queue and log. (If that location is read-only, `%LOCALAPPDATA%\CNCBridge` is
used instead.)

For two machine folders on one PC, use two copies in two folders. Each has its own `data`.

On Linux, default state lives in `$XDG_STATE_HOME/CNCBridge` (normally
`~/.local/state/CNCBridge`); on macOS it lives in `~/Library/Application Support/CNCBridge`.
Use `--data DIR` for separate stations or to keep using an existing data directory.
Build the Linux package with `python scripts/package.py --deb`; artifacts and SHA-256
checksums are written to `artifacts/`. The GitHub workflow builds Windows and macOS natively.

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

## Output files

Named `<machine file name>_<date>_<time>_<unique ID>.<json|csv|txt>` to avoid overwriting
previous outputs when a machine reuses a filename.

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
| Shared folder or API cannot be reached | Output files wait in `data\outbox` and are sent in order when it is back. Machine files are still processed and backed up. |
| The API refuses one file (HTTP 400, 409, 413, 415, 422) | That file is moved to `data\rejected`, shown in red in the header and in History, and the next files continue. |
| Wrong token or wrong link (401, 403, 404...) or a redirect | Treated as a problem with the link, not the file: everything waits in the queue until the settings are corrected. |
| A machine file has no data in any chosen range, or is not a readable CSV | Moved to `back-up\_failed`, listed as failed. |
| The same file content arrives again | Moved to `back-up\_duplicates` and not sent. Can be switched off under Options. |
| Back-up drive not available | The machine file is left where it is and nothing is sent for it until the drive is back. |
| PC restarted | Settings, history and the send queue are on disk; start the program and it continues. |

The **History** tab lists every file with its result per destination. The **Log** tab and
`data\logs\cncbridge.log` hold the details.

## Start automatically

- **With the window**: tick *Start monitoring when the program opens* (Delivery tab,
  Options), then put a shortcut to `CNCBridge.exe` into the Startup folder (press Win+R,
  type `shell:startup`).
- **Without a window**: in Task Scheduler create a task "At startup" that runs
  `C:\CNCBridge\CNCBridge.exe --headless`. Set everything up in the window first; the task
  uses the same `data` folder. Close the window before the task runs, because only one copy
  can use a `data` folder at a time.

Command line: `--headless` (no window), `--headless --once` (process what is waiting and
exit), `--data DIR` (use another data folder).

## Demo and test kit

The preferred entry point is **python -m cncbridge --demo**. It opens a **Demo & integration** tab in
the same CNCBridge window, automatically starts monitoring the demo machine folder, and provides the
machine, outage, refusal, fixed-test and inbox controls. **CNCBridgeDemo.pyw** remains a compatible
launcher for the same integrated demo.

The demo workspace is **demo-workspace** inside the application's data directory by default; **--data DIR** with
**--demo** chooses a different workspace. It preserves files between runs. To repeat a clean presentation,
choose a new workspace. No automatic reset removes existing evidence.

Run **python -m cncbridge --demo-check** to perform the complete scripted proof without a window.
It prints a JSON result and retains its report, originals, payloads, ledger and rejected file in the
reported workspace. A failed receiver or delivery produces a failed result, not an integration pass.
Use **--demo-check --data EMPTY_DIRECTORY** to choose where the evidence stays.

For the presentation: Drop one board, inspect its ranges and manual inputs in the MES inbox, then
compare its shared file and back-up original. Simulate an outage and drop another board: the shared
route succeeds and the API queue grows. End the outage and observe the API queue drain. Finally send a
duplicate, drop a broken file, and use Write a file slowly. The slow writer publishes a CSV only after
finishing its temporary .part file; the engine also waits for its configured stability interval.

To show CNCBridge working without a real machine or a real MES, start the integrated demo. It:

- creates an isolated `demo-workspace` with a pretend machine folder, back-up
  folder and shared folder (normal machine settings and queues are not used);
- starts a stand-in customer MES on this PC that receives what CNCBridge sends, with an
  inbox page you can open in a browser;
- opens CNCBridge already set up for the demo with monitoring started automatically.

The **Demo & integration** tab drives the show:

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
| Verify demo pipeline | checks all formats, both routes, back-up, stability, refusal and retry in a new workspace |

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
- On Linux/macOS, the configuration is private to its owner (mode 0600), but tokens
  are not encrypted. Use HTTPS and protect the account and disk.
- New queue entries remember their destination. Restore the original link/folder
  to drain them before changing customers. Delivery is at-least-once; a receiving MES
  must honor Idempotency-Key to suppress duplicates after ambiguous network failures.
- Closing the window stops monitoring. Files that arrive meanwhile wait in the machine
  folder and are processed at the next start.
- Not included: picking tables by title, column filters, automatic deletion of old back-ups.
- The rarely changed settings `stable_seconds` (default 2) and `api_timeout` (default 20)
  are only in `data\config.json`.

## For developers

Python 3.9 or newer and the filelock package (used by the existing single-instance lock).
The UI uses Tkinter. Install source dependencies from requirements.txt.

Run **python -m unittest discover -s tests -v**. HTTP tests require loopback sockets; DPAPI tests run on
Windows. In restricted environments those checks are explicitly skipped. The offline verification
test uses a named transport double and exercises disk/engine behavior; it does not certify HTTP
connectivity. Run **python -m demo.verify** on the demo PC for actual HTTP proof.

The eight fixed test fixtures and presentation script can be regenerated with **python -m demo.testdata**.
For an actual factory pilot, use an approved machine CSV, configure its real ranges and manual values,
enter the customer shared path/API contract, run Checks, then monitor and verify delivery in History
and at the destination. The bundled MES is a demonstration receiver; the customer MES still needs its
own endpoint, authentication and expected schema.

```
CNCBridge.pyw            double-click to run from source
cncbridge/engine.py      read, cell ranges, render, queue, deliver, back up, ledger
cncbridge/config.py      settings, data folder, token encryption
cncbridge/app.py         main window
cncbridge/grid.py        spreadsheet view with drag selection
tests/test_engine.py     python -m unittest discover -s tests -v
demo/                    test data, stand-in MES, Demo control window (CNCBridgeDemo.pyw starts it)
build_exe.bat            builds both exe files into dist\ (downloads PyInstaller)
```
