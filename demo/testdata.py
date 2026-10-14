"""
Test data for the CNCBridge demo: invented pick-and-place board logs, the fixed test cases,
the settings the demo runs with, and the written test script. Everything about the test
cases is defined once here, so the files, the script and the automated test cannot drift apart.

Layout of a board log (13 columns, A to M):

    A1:B9    header block: machine, line, program (B5), board serial (B6), times
    L1:M8    board summary, beside the header
    A12:I... placements table, one row per part, so its length changes per board
    below    errors table; where it starts depends on the placements above it

    python -m demo.testdata      rewrites demo/test_files and demo/TEST_SCRIPT.md
"""
import random
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path

PARTS = [("R101", "RC0402-10K"), ("R102", "RC0402-4K7"), ("C201", "CC0603-100N"), ("C202", "CC0603-1U"),
         ("C203", "CC0805-10U"), ("U1", "STM32F405RG"), ("U2", "TPS62160"), ("U3", "MCP2551"),
         ("D1", "LED0603-GRN"), ("Q1", "BSS138"), ("L1", "IND-4R7"), ("J1", "USB-C-16P"),
         ("R103", "RC0402-100R"), ("R104", "RC0402-1K"), ("C204", "CC0402-22P"), ("C205", "CC0402-22P"),
         ("Y1", "XTAL-8MHZ"), ("D2", "LED0603-RED"), ("U4", "W25Q64JV"), ("J2", "HDR-2X5-1.27")]
NOZZLES = ["N1", "N2", "N3", "N4"]
ERRORS = {"PICKUP_ERR": ("E101", "Pick-up failure"), "VISION_NG": ("E205", "Vision misalignment")}
WIDTH = 13  # every row is padded to columns A..M, as machine software usually does

# What the demo takes out of each file. L1:M8 is the example from the original brief.
RANGES = [("Summary", "L1:M8"), ("Board serial", "B6"), ("Program", "B5"), ("Placements", "A12:I*")]
TOKEN = "demo-token"


def serial(n):
    return f"B{240000 + n}"


def board_name(n):
    return f"board_{serial(n)}.csv"


def make_board(n, placements=None, fail=None, start=None, delimiter=",", decimal=".", special=False):
    """
    One board log as text. The same arguments always give the same text.
    placements: number of parts (default: 6 to 20, chosen from n). fail: indexes of failed
    placements (default: about 6 % at random). special: put non-ASCII characters in it.
    """
    rnd = random.Random(n)
    start = start or datetime.now()
    count = rnd.randint(6, 20) if placements is None else placements

    def num(value, digits):
        return f"{value:.{digits}f}".replace(".", decimal)

    placed, errors, feeders, nozzles, t = [], [], set(), set(), start
    for i, (ref, part) in enumerate(PARTS[:count]):
        t += timedelta(milliseconds=rnd.randint(250, 900))
        bad = i in fail if fail is not None else rnd.random() < 0.06
        result = rnd.choice(sorted(ERRORS)) if bad else "OK"
        if special and i == 0:
            part = "TEMP-SENS-85°C"
        stamp = t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        feeder, nozzle = f"F{10 + i}", NOZZLES[i % 4]
        feeders.add(feeder), nozzles.add(nozzle)
        placed.append([stamp, ref, part, feeder, nozzle, num(rnd.uniform(5, 95), 2), num(rnd.uniform(5, 75), 2),
                       rnd.choice(["0", "90", "180", "270"]), result])
        if bad:
            errors.append([stamp, ERRORS[result][0], ref, ERRORS[result][1]])
    end = t + timedelta(seconds=rnd.uniform(2, 5))
    cycle = num((end - start).total_seconds(), 1)
    good = count - len(errors)

    header = [["[HEADER]"], ["Key", "Value"], ["Machine", "PNP-M8"], ["Line", "L1"],
              ["Program", "ECU-CAFÉ-REV3" if special else "ECU-MAIN-REV3"], ["Board_Serial", serial(n)],
              ["Start_Time", f"{start:%Y-%m-%d %H:%M:%S}"], ["End_Time", f"{end:%Y-%m-%d %H:%M:%S}"],
              ["Cycle_Time_s", cycle]]
    summary = [["Item", "Value"], ["Placed", good], ["Errors", len(errors)],
               ["Yield_pct", num(100 * good / count, 1)], ["Feeders_used", len(feeders)],
               ["Nozzles_used", len(nozzles)], ["Cycle_Time_s", cycle], ["Result", "FAIL" if errors else "PASS"]]

    rows = []
    for i, left in enumerate(header):
        row = left + [""] * (WIDTH - len(left))
        if i < len(summary):
            row[11:13] = summary[i]  # columns L and M
        rows.append(row)
    rows += [[], ["[PLACEMENTS]"],
             ["Timestamp", "Ref", "Part_No", "Feeder", "Nozzle", "X_mm", "Y_mm", "Rot_deg", "Result"], *placed,
             [], ["[ERRORS]"], ["Timestamp", "Code", "Ref", "Description"], *errors]
    return "".join(delimiter.join(str(c) for c in (r + [""] * WIDTH)[:WIDTH]) + "\r\n" for r in rows)


def not_a_board(when=None):
    """A file the machine also drops sometimes, with nothing in any of the chosen ranges."""
    when = when or datetime.now()
    return ("Alarm log\r\n"
            f"{when:%Y-%m-%d %H:%M:%S},E900,Door open\r\n"
            f"{when + timedelta(seconds=29):%Y-%m-%d %H:%M:%S},E900,Door closed\r\n")


# ---------------------------------------------------------------- fixed test cases
Case = namedtuple("Case", "name what expect status data")
_T0 = datetime(2026, 10, 9, 10, 0, 0)


def _board(n, encoding="utf-8", **options):
    return make_board(n, start=_T0 + timedelta(minutes=n), **options).encode(encoding)


CASES = [
    Case("01_normal_board.csv", "A standard board with 12 parts.",
         "Processed. The output is in the shared folder and in the customer inbox. Board serial is B240001.",
         "ok", _board(1, placements=12, fail=())),
    Case("02_long_board.csv", "A board with 20 parts, so the parts table is longer.",
         "Processed. Placements has 20 rows. Summary still has 7 rows.",
         "ok", _board(2, placements=20, fail=())),
    Case("03_board_with_errors.csv", "A board where two placements failed.",
         "Processed. Summary shows Errors 2 and Result FAIL.",
         "ok", _board(3, placements=12, fail=(1, 7))),
    Case("04_single_placement.csv", "The shortest possible board: one part.",
         "Processed. Placements has 1 row.",
         "ok", _board(4, placements=1, fail=())),
    Case("05_semicolon_decimal_comma.csv", "European export: semicolons between columns, comma as decimal sign.",
         "Processed like file 01. Numbers keep their comma, for example Yield_pct 100,0.",
         "ok", _board(5, placements=12, fail=(), delimiter=";", decimal=",")),
    Case("06_windows_1252.csv", "Special characters saved in the Windows code page instead of UTF-8.",
         "Processed. Program reads ECU-CAFÉ-REV3 and the first part is TEMP-SENS-85°C.",
         "ok", _board(6, "cp1252", placements=12, fail=(), special=True)),
    Case("07_copy_of_01.csv", "Exactly the same content as file 01 under another name.",
         "Not sent. History shows duplicate. The file is in back-up\\_duplicates.",
         "duplicate", _board(1, placements=12, fail=())),
    Case("08_not_a_board_log.csv", "An alarm log with nothing in any of the chosen cells.",
         "Not sent. History shows failed. The file is in back-up\\_failed.",
         "failed", not_a_board(_T0).encode("utf-8")),
]


def write_test_files(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        (folder / case.name).write_bytes(case.data)
    return folder


def demo_settings(workspace, api_url):
    """The CNCBridge settings the demo starts with."""
    ws = Path(workspace)
    return {
        "station_name": "SMT Line 1 - PNP M8",
        "source_folder": str(ws / "machine"), "backup_folder": str(ws / "backup"),
        "output_format": "json",
        "selections": [{"name": name, "range": rng, "header": True} for name, rng in RANGES],
        "manual_inputs": [{"name": "Operator", "value": "OP-17"}, {"name": "Work order", "value": "WO-1187"}],
        "shared_enabled": True, "shared_folder": str(ws / "shared"),
        "api_enabled": True, "api_url": api_url, "api_token": TOKEN,
    }


# ---------------------------------------------------------------- written test script
LIVE_STEPS = [
    ("Press Start machine in the Demo control window.",
     "A new board file appears every few seconds. Processed counts up. Each one shows in the customer inbox."),
    ("In CNCBridge open the tab Pick ranges & preview and press Newest machine file.",
     "The file is shown as a grid. The four chosen ranges are tinted and numbered 1 to 4."),
    ("On the Setup tab change Operator to OP-99 and press Save settings. Do not stop monitoring.",
     "The next output file has Operator OP-99."),
    ("On the Setup tab choose CSV, press Save settings. Then choose TXT and save again.",
     "New files in the shared folder and the customer inbox end in .csv, then .txt."),
    ("Press Simulate outage in the Demo control window and wait for two or three boards.",
     "Queued to send goes up. The shared folder still receives every file. Nothing is lost."),
    ("Press Simulate outage again to end it.",
     "Queued to send returns to 0 within a few seconds. The customer inbox receives the waiting files in order."),
    ("Press Refuse next file.",
     "Refused by API shows 1. That file is in data\\rejected. The boards after it are delivered."),
    ("Press Send a duplicate.",
     "History shows duplicate. Nothing new reaches the customer inbox."),
    ("Press Drop a broken file.",
     "Failed files shows 1 more. The file is in back-up\\_failed. Monitoring continues."),
    ("Press Write a file slowly.",
     "Waiting in folder shows the file for about 12 seconds. It is processed only after it is complete."),
    ("Press Stop in CNCBridge, let two boards arrive, then press Start monitoring.",
     "The two waiting boards are processed straight away."),
    ("Use Open folder > Back-up folder in CNCBridge.",
     "Today's folder holds every processed machine file. The machine folder is empty."),
]


def test_script():
    lines = ["# CNCBridge test script", "",
             "Do each step and compare with what you should see. Start with the Demo control window open,",
             "CNCBridge open on the demo workspace, and the customer inbox open in a browser.", "",
             "## Part 1: start", "",
             "1. Do: press Start monitoring in CNCBridge.",
             "   See: the state changes to Monitoring. All counters are 0.", "",
             "## Part 2: the fixed test files", "",
             "Copy the files one at a time with Copy one, or all together with Copy all test files.",
             "They are processed in the order of their numbers.", ""]
    step = 2
    for case in CASES:
        lines += [f"{step}. Do: copy {case.name} into the machine folder. {case.what}",
                  f"   See: {case.expect}", ""]
        step += 1
    ok = sum(c.status == "ok" for c in CASES)
    lines += [f"After all {len(CASES)} files: Processed {ok}, Failed files 1, Waiting in folder 0,",
              f"and the customer inbox holds {ok} files.", "",
              "## Part 3: live cases", ""]
    for do, see in LIVE_STEPS:
        lines += [f"{step}. Do: {do}", f"   See: {see}", ""]
        step += 1
    return "\n".join(lines)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    write_test_files(here / "test_files")
    (here / "TEST_SCRIPT.md").write_text(test_script(), encoding="utf-8", newline="\n")
    print(f"wrote {len(CASES)} test files and TEST_SCRIPT.md")
