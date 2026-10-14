"""Reproducible, invented machine logs with the same coordinates as the demo profile."""
import csv
import io
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

FIXED_START = datetime(2026, 10, 9, 10)
PARTS = ("RC0402-10K", "CC0603-100N", "STM32F405RG", "TPS62160")


def make_board(number, placements=None, fail=(), start=None, delimiter=",", decimal_comma=False,
               program="ECU-MAIN-REV3", part=None):
    rnd = random.Random(number)
    placements = rnd.randint(6, 20) if placements is None else placements
    start = start or datetime.now()
    errors = len(set(fail) & set(range(placements)))
    decimal = lambda value: value.replace(".", ",") if decimal_comma else value
    rows = [
        ["[HEADER]"], ["Key", "Value"], ["Machine", "PNP-M8"], ["Line", "L1"],
        ["Program", program], ["Board_Serial", f"B{240000 + number}"],
        ["Start_Time", start.isoformat(timespec="seconds")],
        ["End_Time", (start + timedelta(seconds=placements)).isoformat(timespec="seconds")],
        ["Cycle_Time_s", decimal(f"{placements * .7:.1f}")], [], ["[PLACEMENTS]"],
        ["Timestamp", "Ref", "Part_No", "Feeder", "Nozzle", "X_mm", "Y_mm", "Rot_deg", "Result"],
    ]
    summary = [
        ["Item", "Value"], ["Placed", str(placements - errors)], ["Errors", str(errors)],
        ["Result", "FAIL" if errors else "PASS"],
        ["Yield_pct", decimal(f"{100 * (placements - errors) / max(placements, 1):.1f}")],
        ["Machine", "PNP-M8"], ["Line", "L1"], ["Board", f"B{240000 + number}"],
    ]
    for i, values in enumerate(summary):
        rows[i] = (rows[i] + [""] * 13)[:13]
        rows[i][11:13] = values
    for i in range(placements):
        rows.append([(start + timedelta(milliseconds=700 * i)).isoformat(timespec="milliseconds"),
                     f"R{101 + i}", part if part and i == 0 else PARTS[i % len(PARTS)],
                     f"F{i + 10}", f"N{i % 4 + 1}", decimal(f"{rnd.uniform(5, 95):.2f}"),
                     decimal(f"{rnd.uniform(5, 75):.2f}"), "0",
                     "PICKUP_ERR" if i in fail else "OK"])
    rows += [[], ["[FEEDERS]"], ["Feeder", "Remaining"], ["F10", "4800"]]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\r\n")
    writer.writerows((row + [""] * 13)[:13] for row in rows)
    return stream.getvalue()


def demo_settings(workspace, url):
    ws = Path(workspace).resolve()
    return {
        "station_name": "DEMO - SMT Line 1 / Machine 8",
        "source_folder": str(ws / "machine"), "backup_folder": str(ws / "backup"),
        "shared_enabled": True, "shared_folder": str(ws / "shared"),
        "api_enabled": bool(url), "api_url": url, "api_token": "demo-token",
        "output_format": "json", "poll_seconds": 1, "stable_seconds": 2,
        "selections": [
            {"name": "Summary", "range": "L1:M8", "header": True},
            {"name": "Board serial", "range": "B6", "header": False},
            {"name": "Program", "range": "B5", "header": False},
            {"name": "Placements", "range": "A12:I*", "header": True},
        ],
        "manual_inputs": [{"name": "Operator", "value": "OP-17"},
                          {"name": "Work order", "value": "WO-1187"}],
    }


@dataclass(frozen=True)
class Case:
    name: str
    data: bytes
    status: str
    expect: str


def _board(number, **kwargs):
    encoding = kwargs.pop("encoding", "utf-8")
    return make_board(number, start=FIXED_START, **kwargs).encode(encoding)


CASES = (
    Case("01_normal_board.csv", _board(1, placements=12), "ok",
         "12 placements, serial B240001, both manual inputs, source in dated back-up."),
    Case("02_long_board.csv", _board(2, placements=20), "ok",
         "20 placements; the variable range stops before the feeder table."),
    Case("03_board_with_errors.csv", _board(3, placements=12, fail=(0, 4)), "ok",
         "Summary reports 2 errors and FAIL; this valid machine log is still delivered."),
    Case("04_single_placement.csv", _board(4, placements=1), "ok",
         "Exactly one placement; the seven summary entries remain intact."),
    Case("05_semicolon_decimal_comma.csv",
         _board(5, placements=12, delimiter=";", decimal_comma=True), "ok",
         "Semicolon delimiter detected; Yield_pct is 100,0; nine placement columns."),
    Case("06_windows_1252.csv",
         _board(6, placements=12, program="ECU-CAFÉ-REV3", part="TEMP-SENS-85°C",
                encoding="cp1252"), "ok",
         "Windows-1252 characters survive in the UTF-8 output."),
    Case("07_copy_of_01.csv", _board(1, placements=12), "duplicate",
         "Duplicate blocked; source in backup/_duplicates; no second delivery."),
    Case("08_not_a_board_log.csv", b"this,is,not,a,board\r\n", "failed",
         "No selected range contains data; source in backup/_failed; next file continues."),
)


def test_script():
    lines = [
        "# CNCBridge presentation script", "",
        "Start the integrated demo, leave JSON selected, and start monitoring.",
        "Use Copy one in the order below. Check History, Shared folder and the MES inbox.", "",
    ]
    for case in CASES:
        lines += [f"## {case.name}", "", case.expect, ""]
    lines += [
        "## Outage and recovery", "",
        "Click Simulate outage, then Drop one board. Shared delivery should succeed while API is queued.",
        "Click End outage. The API queue should drain and the MES inbox should gain the file.", "",
        "## Slow file", "",
        "Click Write a file slowly. A temporary .part file is written first; monitoring picks up the CSV",
        "after it is complete and renamed. The source then appears in the dated back-up folder.", "",
        "## Automated proof", "",
        "Run python -m demo.verify to check all three formats, both destinations, archive, duplicate",
        "blocking, bad-file handling, and recovery after a real HTTP 503 response.", "",
    ]
    return "\n".join(lines)


def write_test_files(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        (folder / case.name).write_bytes(case.data)
    return folder


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    write_test_files(root / "test_files")
    (root / "TEST_SCRIPT.md").write_text(test_script(), encoding="utf-8")
    print("Regenerated eight fixed fixtures and the presentation script.")
