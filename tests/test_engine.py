"""Run:  python -m unittest discover -s tests -v"""
import csv
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cncbridge import alerts, config, engine, pdf  # noqa: E402

alerts.SENDERS["notify"] = lambda *a, **k: None  # no Windows notifications on screen during tests

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)
SAMPLE = ROOT / "samples" / "pnp_L1M8_board_00001.csv"
OVEN = ROOT / "samples" / "reflow_oven_log.csv"


def sel(rng, name="", header=True):
    return {"name": name, "range": rng, "header": header}


class Receiver:
    """A stand-in for the customer's API. `status` is a number or a function of the file name."""

    def __init__(self):
        self.status, self.got = 200, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                name = self.headers.get("X-Filename", "")
                code = outer.status(name) if callable(outer.status) else outer.status
                if code == 200:
                    outer.got.append({"path": self.path, "headers": dict(self.headers), "body": body})
                self.send_response(code)
                if code == 302:
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/upload"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Ranges(unittest.TestCase):
    def test_forms(self):
        for text in ("L1:M8", "l1 x m8", "L1xM8", "L1×M8", "L1 to M8", "L1-M8", "M8:L1"):
            self.assertEqual(engine.normalize_range(text), "L1:M8", text)
        self.assertEqual(engine.normalize_range(" b6 "), "B6")
        self.assertEqual(engine.normalize_range("a12 : i*"), "A12:I*")
        self.assertEqual(engine.parse_range("AA3:AB4"), (26, 2, 27, 3))

    def test_invalid(self):
        for text in ("", "hello", "L0:M8", "1A", "L1:M", "L1:M8:N9"):
            with self.assertRaises(ValueError, msg=text):
                engine.parse_range(text)


class Extraction(unittest.TestCase):
    def setUp(self):
        self.rows = engine.read_rows(SAMPLE)

    def test_table_with_header(self):
        block, filled = engine.extract(self.rows, sel("A12:I24", "Placements"))
        self.assertTrue(filled)
        self.assertEqual(block["columns"][:3], ["Timestamp", "Ref", "Part_No"])
        self.assertEqual(len(block["rows"]), 12)
        self.assertEqual(block["rows"][0][1], "R101")

    def test_single_cell(self):
        block, _ = engine.extract(self.rows, sel("B6", "Serial"))
        self.assertEqual(block, {"name": "Serial", "range": "B6", "value": "B240001"})

    def test_open_range_stops_at_first_empty_row(self):
        block, _ = engine.extract(self.rows, sel("A12:I*"))
        self.assertEqual(block["name"], "A12:I*")
        self.assertEqual(len(block["rows"]), 12)  # not the FEEDER_STATUS table further down

    def test_no_header(self):
        block, _ = engine.extract(self.rows, sel("A3:B4", header=False))
        self.assertNotIn("columns", block)
        self.assertEqual(block["rows"], [["Machine", "PNP-M8"], ["Line", "L1"]])

    def test_past_end_of_file(self):
        cfg = config.normalize_config({"selections": [sel("A49:D99")]})
        _, blocks, warnings = engine.build_output("x.csv", self.rows, cfg)
        self.assertEqual(len(blocks[0]["rows"]), 1)
        self.assertTrue(any("past the last row" in w for w in warnings))

    def test_nothing_selected_has_data(self):
        cfg = config.normalize_config({"selections": [sel("X1:Z5"), sel("A300")]})
        with self.assertRaises(engine.FileError):
            engine.build_output("x.csv", self.rows, cfg)

    def test_semicolon_file(self):
        rows = engine.read_rows(OVEN)
        block, _ = engine.extract(rows, sel("B1 x D*"))
        self.assertEqual(block["columns"], ["Zone", "Setpoint_C", "Actual_C"])
        self.assertEqual(len(block["rows"]), 6)


class HeadingRanges(unittest.TestCase):
    """A block found by its heading (@[ERRORS], @Timestamp) instead of by fixed cells."""

    def setUp(self):
        self.rows = engine.read_rows(SAMPLE)

    def extract(self, rng, rows=None, header=True):
        return engine.extract(rows or self.rows, sel(rng, header=header))

    def test_title_row_finds_the_table_below(self):
        block, filled = self.extract("@[L1xM8]")
        self.assertTrue(filled)
        self.assertEqual(block["columns"][:3], ["Timestamp", "Ref", "Part_No"])
        self.assertEqual((len(block["columns"]), len(block["rows"])), (9, 12))
        self.assertEqual(block["cells"], "A12:I24")  # where it was found in this file
        self.assertEqual(block["range"], "@[L1xM8]")

    def test_width_follows_the_header_row(self):
        feeders, _ = self.extract("@[FEEDER_STATUS]")
        self.assertEqual(feeders["columns"], ["Feeder", "Part_No", "Remaining", "Picks", "Misses"])
        self.assertEqual(len(feeders["rows"]), 12)
        errors, _ = self.extract("@[ERRORS]")
        self.assertEqual((errors["columns"], len(errors["rows"])), (["Timestamp", "Code", "Ref", "Description"], 1))

    def test_heading_inside_a_header_row_starts_the_table_there(self):
        block, _ = self.extract("@Part_No")
        self.assertEqual(block["columns"], ["Part_No", "Feeder", "Nozzle", "X_mm", "Y_mm", "Rot_deg", "Result"])
        self.assertEqual(block["rows"][0][0], "RC0402-10K")
        self.assertEqual(block["cells"], "C12:I24")

    def test_case_and_spaces_do_not_matter(self):
        self.assertEqual(self.extract("@  [errors] ")[0]["columns"][1], "Code")
        self.assertEqual(engine.normalize_range("@ [ERRORS]"), "@[ERRORS]")

    def test_the_table_is_found_wherever_it_moves(self):
        def board(lines_before, count):
            lines = [f"info {i},x" for i in range(lines_before)]
            lines += ["", "[DATA]", "Ref,Result"] + [f"R{i},OK" for i in range(count)]
            lines += ["", "[ERRORS]", "Code", "E1"]
            return engine.read_rows(chr(10).join(lines).encode())
        for before, count in ((2, 3), (9, 40)):
            block, _ = self.extract("@[DATA]", board(before, count))
            self.assertEqual((block["columns"], len(block["rows"])), (["Ref", "Result"], count))
            errors, _ = self.extract("@[ERRORS]", board(before, count))
            self.assertEqual(errors["rows"], [["E1"]])

    def test_without_header_row_the_header_is_data(self):
        block, _ = self.extract("@[ERRORS]", header=False)
        self.assertEqual(len(block["rows"]), 2)
        self.assertNotIn("columns", block)

    def test_heading_that_is_not_in_the_file(self):
        block, filled = self.extract("@[NOT THERE]")
        self.assertFalse(filled)
        self.assertEqual((block["rows"], block["cells"]), ([], ""))
        cfg = config.normalize_config({"selections": [sel("@[NOT THERE]"), sel("B6")]})
        _, _, warnings = engine.build_output("x.csv", self.rows, cfg)
        self.assertEqual(warnings, ["selection 1 (@[NOT THERE]) is empty in this file"])
        with self.assertRaises(engine.FileError):
            engine.build_output("x.csv", self.rows, config.normalize_config({"selections": [sel("@[NOT THERE]")]}))

    def test_resolve_gives_the_cells_for_the_grid(self):
        self.assertEqual(engine.resolve_range(self.rows, "@[ERRORS]"), (0, 48, 3, 49))
        c1, r1, c2, r2 = engine.resolve_range(self.rows, "@[NOT THERE]")
        self.assertLess(r2, r1)  # nothing to mark

    def test_validation_and_fixed_ranges_are_unchanged(self):
        engine.check_range("@[ERRORS]")
        for bad in ("@", "@   "):
            with self.assertRaises(ValueError, msg=bad):
                engine.check_range(bad)
        with self.assertRaises(ValueError):
            engine.check_range("nonsense")
        self.assertEqual(engine.normalize_range("l1 x m8"), "L1:M8")
        self.assertTrue(engine.is_heading("@x") and not engine.is_heading("A1"))
        with tempfile.TemporaryDirectory() as d:
            cfg = config.normalize_config({"source_folder": d, "backup_folder": d + "/b", "shared_enabled": True,
                                           "shared_folder": d + "/s", "selections": [sel("@[ERRORS]"), sel("@")]})
            problems = engine.validate(cfg)
            self.assertEqual(len(problems), 1)
            self.assertIn("Selection 2: after @ put the heading", problems[0])

    def test_pipeline_delivers_the_found_block(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "machine").mkdir()
            shutil.copy(SAMPLE, d / "machine" / "board.csv")
            eng = engine.Engine({"source_folder": str(d / "machine"), "backup_folder": str(d / "backup"),
                                 "shared_enabled": True, "shared_folder": str(d / "shared"), "stable_seconds": 0,
                                 "selections": [sel("@[FEEDER_STATUS]", "Feeders")]}, d / "data")
            self.assertEqual(eng.scan_once(), 1)
            doc = json.loads(next((d / "shared").glob("board_*.json")).read_text(encoding="utf-8"))
            feeders = doc["selections"][0]
            self.assertEqual((feeders["range"], feeders["cells"], len(feeders["rows"])), ("@[FEEDER_STATUS]", "A27:E39", 12))
            self.assertEqual(feeders["rows"][0]["Feeder"], "F10")


class SmarterPicking(unittest.TestCase):
    """Column lists ([Ref, Result]), headings with wildcards (@NOZZLE*) and the tables found in a file."""

    def setUp(self):
        self.rows = engine.read_rows(SAMPLE)

    def build(self, *selections, **settings):
        cfg = config.normalize_config(dict(settings, selections=list(selections)))
        return engine.build_output("x.csv", self.rows, cfg)

    def test_a_column_list_keeps_those_columns_in_that_order(self):
        _, blocks, warnings = self.build(sel("A12:I*[result, Ref]"))
        self.assertEqual(blocks[0]["columns"], ["Result", "Ref"])  # the file's spelling, in the listed order
        self.assertEqual(blocks[0]["rows"][1], ["PICKUP_ERR", "R102"])
        self.assertEqual(blocks[0]["range"], "A12:I*")
        self.assertEqual(warnings, [])
        _, blocks, _ = self.build(sel("@[ERRORS][Code, Description]"))
        self.assertEqual(blocks[0]["rows"], [["E101", "Pick-up failure"]])

    def test_a_column_list_works_with_renamed_columns_under_either_name(self):
        _, blocks, _ = self.build(sel("A12:I*[Reference, Part_No]"), rename_columns="Ref=Reference\nPart_No=Part")
        self.assertEqual(blocks[0]["columns"], ["Reference", "Part"])

    def test_a_missing_column_is_warned_about_once(self):
        _, blocks, warnings = self.build(sel("A12:I*[Ref, Colour]"))
        self.assertEqual(blocks[0]["columns"], ["Ref"])
        self.assertEqual(warnings, ["selection 1: there is no column 'Colour' in this file"])

    def test_a_heading_in_brackets_is_not_a_column_list(self):
        self.assertEqual(engine.split_columns("@[ERRORS]"), ("@[ERRORS]", None))
        self.assertEqual(engine.split_columns("@[ERRORS][Code]"), ("@[ERRORS]", ["Code"]))
        self.assertEqual(engine.normalize_range("a12 : i* [ref,result]"), "A12:I*[ref, result]")

    def test_column_lists_that_cannot_work_are_named_before_starting(self):
        problems = engine.validate(config.normalize_config({"selections": [
            sel("B6[Ref]"), sel("A12:I*[Ref]", header=False), sel("A12:I*[]")]}))
        self.assertTrue(any("Selection 1: a column list needs a block of cells" in p for p in problems))
        self.assertTrue(any("Selection 2: a column list needs 'First row is header'" in p for p in problems))
        self.assertTrue(any("Selection 3: put the column names inside the brackets" in p for p in problems))

    def test_a_heading_with_a_wildcard_takes_every_table_whose_title_fits(self):
        _, blocks, _ = self.build(sel("@*STAT*", "Stats"))
        self.assertEqual([b["name"] for b in blocks], ["Stats ([FEEDER_STATUS])", "Stats ([NOZZLE_STATS])"])
        self.assertEqual([b["cells"] for b in blocks], ["A27:E39", "A42:D46"])
        _, blocks, _ = self.build(sel("@NOZZLE*[Nozzle, Errors]"))  # without brackets, a [title] fits too
        self.assertEqual((blocks[0]["name"], blocks[0]["columns"]), ("[NOZZLE_STATS]", ["Nozzle", "Errors"]))
        _, blocks, warnings = self.build(sel("@NOTHING*"), sel("B6"))
        self.assertEqual(warnings, ["selection 1 (@NOTHING*) is empty in this file"])

    def test_the_tables_in_a_file_are_found_with_their_cells(self):
        tables = engine.find_tables(self.rows)
        self.assertEqual([(t["title"], t["range"], t["rows"]) for t in tables],
                         [("[HEADER]", "A2:B9", 7), ("[L1xM8]", "A12:I24", 12), ("[FEEDER_STATUS]", "A27:E39", 12),
                          ("[NOZZLE_STATS]", "A42:D46", 4), ("[ERRORS]", "A49:D50", 1)])
        self.assertEqual(tables[3]["columns"], ["Nozzle", "Picks", "Errors", "Error_Rate_pct"])
        self.assertEqual(engine.find_tables(engine.read_rows(b"a,b\n1,2\n")), [])  # no title, no table


class PdfOutput(unittest.TestCase):
    """The PDF report: standard library only, built-in fonts, tables over several pages."""

    def setUp(self):
        self.rows = engine.read_rows(SAMPLE)

    def build(self, *selections, **settings):
        cfg = config.normalize_config(dict(settings, output_format="pdf", selections=list(selections)))
        return engine.build_output("board.csv", self.rows, cfg)[0]

    def test_a_pdf_is_a_well_formed_file(self):
        data = self.build(sel("B6", "Serial"), sel("A12:I*", "Placements"), station_name="Line 7")
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        start = int(data.rsplit(b"startxref", 1)[1].split()[0])
        self.assertEqual(data[start:start + 4], b"xref")  # the reader finds its table of contents
        self.assertIn(b"/BaseFont /Helvetica", data)
        self.assertIn(b"/Producer (CNCBridge " + config.VERSION.encode() + b")", data)
        self.assertEqual(pdf.page_count(data), 1)

    def test_a_long_table_continues_on_more_pages(self):
        lines = ["[DATA]", "Ref,Result"] + [f"R{i},OK" for i in range(200)]
        rows = engine.read_rows("\n".join(lines).encode())
        cfg = config.normalize_config({"output_format": "pdf", "selections": [sel("@[DATA]")]})
        data = engine.build_output("long.csv", rows, cfg)[0]
        self.assertGreaterEqual(pdf.page_count(data), 5)
        self.assertIn(("Page 1 of %d" % pdf.page_count(data)).encode("cp1252").hex().upper().encode(), data)

    def test_a_wide_table_is_printed_in_groups_of_columns(self):
        columns = [f"Column_{i}" for i in range(30)]
        groups = pdf._groups(columns, [[f"value {i}" for i in range(30)]])
        self.assertGreater(len(groups), 1)
        self.assertEqual(sum(len(cols) for cols, _ in groups), 30)
        for _, widths in groups:
            self.assertLessEqual(sum(widths), pdf.USABLE + 0.01)

    def test_text_outside_the_built_in_fonts_does_not_stop_it(self):
        data = self.build(sel("B6"), manual_inputs=[{"name": "Operator", "value": "山田 – Süd"}])
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(pdf._text("山田 – Süd"), "?? – Süd")
        self.assertTrue(pdf._fit("x" * 500, 60, 8).endswith("…"))

    def test_a_pdf_goes_out_like_any_other_output(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        api = Receiver()
        self.addCleanup(api.close)
        (tmp / "machine").mkdir()
        shutil.copy(SAMPLE, tmp / "machine" / "board.csv")
        eng = engine.Engine({"source_folder": str(tmp / "machine"), "backup_folder": str(tmp / "backup"),
                             "output_format": "pdf", "selections": [sel("A12:I*")], "stable_seconds": 0,
                             "shared_enabled": True, "shared_folder": str(tmp / "shared"),
                             "api_enabled": True, "api_url": api.url}, tmp / "data")
        self.assertEqual(eng.scan_once(), 1)
        out = list((tmp / "shared").glob("board_*.pdf"))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].read_bytes().startswith(b"%PDF"))
        self.assertEqual(api.got[0]["headers"]["Content-Type"], "application/pdf")
        self.assertEqual(api.got[0]["body"], out[0].read_bytes())


class OutputSwitches(unittest.TestCase):
    """Numbers as numbers, plain CSV, time zone and renamed columns. All off means files stay as they were."""

    def setUp(self):
        self.rows = engine.read_rows(SAMPLE)
        self.now = datetime(2026, 10, 19, 9, 30, 0)
        self.base = {"station_name": "Line 7", "manual_inputs": [{"name": "Operator", "value": "OP-17"}],
                     "selections": [sel("A12:I14", "Placements"), sel("B6", "Serial"),
                                    sel("A3:B4", "Raw", header=False)]}

    def out(self, fmt, rows=None, **switches):
        cfg = config.normalize_config(dict(self.base, output_format=fmt, **switches))
        return engine.build_output("board.csv", rows or self.rows, cfg, self.now)[0]

    def test_defaults_keep_text_and_sections(self):
        first = json.loads(self.out("json"))["selections"][0]["rows"][0]
        self.assertEqual((first["X_mm"], first["Rot_deg"]), ("40.53", "0"))
        text = self.out("csv")
        self.assertIn("[Placements],A12:I14", text)
        self.assertIn("[INFO]", text)
        self.assertIn("processed_at,2026-10-19T09:30:00", text.replace("\r", ""))

    def test_as_number(self):
        for text, want in (("12", 12), ("-3", -3), ("0", 0), ("40.53", 40.53), ("0.5", 0.5),
                           ("123456789012345", 123456789012345)):
            self.assertEqual(engine.as_number(text), want, text)
            self.assertIs(type(engine.as_number(text)), type(want), text)
        for text in ("00123", "B240001", "1,5", "", "1e5", "+5", "1.", ".5", "-", "1234567890123456",
                     "2026-10-09 10:00:00.581"):
            self.assertEqual(engine.as_number(text), text, text)

    def test_json_numbers(self):
        doc = json.loads(self.out("json", json_numbers=True))
        placements, serial, raw = doc["selections"]
        first = placements["rows"][0]
        self.assertEqual((first["X_mm"], first["Rot_deg"]), (40.53, 0))
        self.assertEqual((first["Ref"], first["Timestamp"]), ("R101", "2026-10-09 10:00:00.581"))
        self.assertEqual(serial["value"], "B240001")                      # not a number
        self.assertEqual(raw["rows"], [["Machine", "PNP-M8"], ["Line", "L1"]])
        self.base["selections"] = [sel("B9", "Cycle")]
        self.assertEqual(json.loads(self.out("json", json_numbers=True))["selections"][0]["value"], 11.9)
        header_off = {"selections": [sel("F13:G14", header=False)]}
        self.base.update(header_off)
        self.assertEqual(json.loads(self.out("json", json_numbers=True))["selections"][0]["rows"],
                         [[40.53, 8.38], [8.37, 35.36]])

    def test_numbers_only_change_json(self):
        for fmt in ("csv", "txt"):
            self.assertEqual(self.out(fmt, json_numbers=True), self.out(fmt))

    def test_flat_csv(self):
        table = list(csv.reader(io.StringIO(self.out("csv", csv_flat=True), newline="")))
        self.assertEqual(table[0], ["source_file", "processed_at", "station", "Operator", "selection", "Timestamp",
                                    "Ref", "Part_No", "Feeder", "Nozzle", "X_mm", "Y_mm", "Rot_deg", "Result",
                                    "value", "col1", "col2"])
        self.assertEqual(len(table), 1 + 2 + 1 + 2)  # two placements, the serial, two raw rows
        self.assertEqual(table[1][:7], ["board.csv", "2026-10-19T09:30:00", "Line 7", "OP-17", "Placements",
                                        "2026-10-09 10:00:00.581", "R101"])
        serial = table[3]
        self.assertEqual((serial[4], serial[14], serial[5]), ("Serial", "B240001", ""))
        self.assertEqual(table[4][4:] [-3:], ["", "Machine", "PNP-M8"])
        self.assertTrue(all(len(r) == len(table[0]) for r in table))

    def test_flat_csv_never_hides_its_own_columns(self):
        rows = engine.read_rows(b"station,selection,x\nA,B,C\n")
        table = list(csv.reader(io.StringIO(self.out("csv", rows, csv_flat=True,
                                                     selections=[sel("A1:C2", "T")]), newline="")))
        self.assertEqual(table[0][-3:], ["data_station", "data_selection", "x"])
        self.assertEqual(table[1][-3:], ["A", "B", "C"])
        self.assertEqual(table[1][2], "Line 7")

    def test_flat_only_changes_csv(self):
        for fmt in ("json", "txt"):
            self.assertEqual(self.out(fmt, csv_flat=True), self.out(fmt))

    def test_time_zone(self):
        self.assertEqual(json.loads(self.out("json"))["processed_at"], "2026-10-19T09:30:00")
        zoned = json.loads(self.out("json", time_zone=True))["processed_at"]
        self.assertRegex(zoned, r"^2026-10-19T09:30:00[+-]\d\d:\d\d$")
        self.assertRegex(self.out("txt", time_zone=True), r"PROCESSED AT  : 2026-10-19T09:30:00[+-]\d\d:\d\d")
        self.assertRegex(self.out("csv", time_zone=True), r"processed_at,2026-10-19T09:30:00[+-]\d\d:\d\d")

    def test_rename_columns(self):
        names = "Ref=Reference\n  x_mm = X \n# a comment\n\nTimestamp=Time\nnot a rename line\n"
        for fmt, find in (("json", '"Reference"'), ("csv", "Time,Reference,Part_No"), ("txt", "Time ")):
            self.assertIn(find, self.out(fmt, rename_columns=names), fmt)
        first = json.loads(self.out("json", rename_columns=names))["selections"][0]
        self.assertEqual(first["columns"][:3], ["Time", "Reference", "Part_No"])
        self.assertIn("X", first["columns"])
        self.assertEqual(first["rows"][0]["Reference"], "R101")
        raw = json.loads(self.out("json", rename_columns="Machine=Maker"))["selections"][2]
        self.assertEqual(raw["rows"][0], ["Machine", "PNP-M8"])           # no header row, nothing to rename

    def test_renaming_to_an_existing_name_keeps_both(self):
        columns = json.loads(self.out("json", rename_columns="Ref=Part_No"))["selections"][0]["columns"]
        self.assertEqual(columns[1:3], ["Part_No", "Part_No_2"])

    def test_rename_also_works_on_a_block_found_by_heading(self):
        self.base["selections"] = [sel("@[FEEDER_STATUS]", "Feeders")]
        columns = json.loads(self.out("json", rename_columns="feeder=Unit"))["selections"][0]["columns"]
        self.assertEqual(columns[:2], ["Unit", "Part_No"])

    def test_bad_rename_line_is_reported_by_validate_and_ignored_by_the_output(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.normalize_config(dict(self.base, source_folder=d, backup_folder=d + "/b",
                                               shared_enabled=True, shared_folder=d + "/s",
                                               rename_columns="Ref=Reference\nnonsense"))
            self.assertEqual(engine.validate(cfg),
                             ["Rename columns: 'nonsense' must look like  Old name=New name"])
        self.assertIn('"Reference"', self.out("json", rename_columns="Ref=Reference\nnonsense"))
        for bad in ("=New", "Old=", "  = "):
            with self.assertRaises(ValueError, msg=bad):
                engine.parse_renames(bad, strict=True)

    def test_settings_keep_the_switches(self):
        with tempfile.TemporaryDirectory() as d:
            path, exported = Path(d) / "config.json", Path(d) / "export.json"
            config.save_config(dict(json_numbers=True, csv_flat=True, time_zone=True, rename_columns="A=B"), path)
            loaded = config.load_config(path)
            self.assertEqual([loaded[k] for k in ("json_numbers", "csv_flat", "time_zone", "rename_columns")],
                             [True, True, True, "A=B"])
            config.export_settings(loaded, exported)
            self.assertEqual(config.import_settings(exported, {})["rename_columns"], "A=B")
        defaults = config.normalize_config({})
        self.assertEqual([defaults[k] for k in ("json_numbers", "csv_flat", "time_zone", "rename_columns")],
                         [False, False, False, ""])

    def test_pipeline_with_everything_on(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "machine").mkdir()
            shutil.copy(SAMPLE, d / "machine" / "board.csv")
            cfg = dict(self.base, source_folder=str(d / "machine"), backup_folder=str(d / "backup"),
                       shared_enabled=True, shared_folder=str(d / "shared"), stable_seconds=0, json_numbers=True,
                       time_zone=True, rename_columns="Ref=Reference")
            self.assertEqual(engine.Engine(cfg, d / "data").scan_once(), 1)
            doc = json.loads(next((d / "shared").glob("board_*.json")).read_text(encoding="utf-8"))
            self.assertRegex(doc["processed_at"], r"[+-]\d\d:\d\d$")
            self.assertEqual(doc["selections"][0]["rows"][0]["X_mm"], 40.53)
            self.assertIn("Reference", doc["selections"][0]["columns"])


class Reading(unittest.TestCase):
    def test_decimal_commas_do_not_fool_the_delimiter(self):
        rows = engine.read_rows(b"Zone;Temp;Speed\r\nZ1;149,6;12,0\r\nZ2;181,2;12,0\r\n")
        self.assertEqual(rows[1], ["Z1", "149,6", "12,0"])

    def test_forced_delimiter(self):
        self.assertEqual(engine.read_rows(b"a|b,c\n", delimiter="|"), [["a", "b,c"]])
        self.assertEqual(engine.read_rows(b"a\tb\n"), [["a", "b"]])

    def test_encodings(self):
        self.assertEqual(engine.read_rows("Ref,Temp\nR1,25°C\n".encode("cp1252"))[1][1], "25°C")
        self.assertEqual(engine.read_rows("﻿Ref,Temp\nR1,25°C\n".encode("utf-8"))[0][0], "Ref")
        self.assertEqual(engine.read_rows("Ref,Temp\nR1,25°C\n".encode("utf-16"))[1][1], "25°C")
        self.assertEqual(engine.read_rows("品番,数\n".encode("shift_jis"), encoding="shift_jis")[0][0], "品番")


class Rendering(unittest.TestCase):
    def setUp(self):
        self.rows = engine.read_rows(SAMPLE)
        self.cfg = config.normalize_config({
            "station_name": "SMT Line 1",
            "selections": [sel("A12:I*", "Placements"), sel("B6", "Serial"), sel("A3:B4", "Machine", header=False)],
            "manual_inputs": [{"name": "Operator", "value": "OP-17"}, {"name": "", "value": "WO-1187"}]})
        self.now = datetime(2026, 10, 10, 9, 30, 0)

    def out(self, fmt):
        self.cfg["output_format"] = fmt
        return engine.build_output("board.csv", self.rows, self.cfg, self.now)[0]

    def test_json(self):
        doc = json.loads(self.out("json"))
        self.assertEqual(doc["source_file"], "board.csv")
        self.assertEqual(doc["processed_at"], "2026-10-10T09:30:00")
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-17", "Manual input 2": "WO-1187"})
        placements, serial, machine = doc["selections"]
        self.assertEqual(placements["rows"][1], dict(placements["rows"][1], Ref="R102", Result="PICKUP_ERR"))
        self.assertEqual(serial, {"name": "Serial", "range": "B6", "value": "B240001"})
        self.assertEqual(machine["rows"], [["Machine", "PNP-M8"], ["Line", "L1"]])

    def test_csv(self):
        rows = list(csv.reader(io.StringIO(self.out("csv"), newline="")))
        self.assertEqual(rows[0], ["[INFO]"])
        self.assertIn(["station", "SMT Line 1"], rows)
        self.assertIn(["Operator", "OP-17"], rows)
        i = rows.index(["[Placements]", "A12:I*"])
        self.assertEqual(rows[i + 1][:2], ["Timestamp", "Ref"])
        self.assertEqual(rows[rows.index(["[Serial]", "B6"]) + 1], ["B240001"])

    def test_txt(self):
        text = self.out("txt")
        self.assertIn("Operator      : OP-17", text)
        self.assertIn("=== Placements  [A12:I*] ===", text)
        self.assertIn("Machine | PNP-M8", text)


class Settings(unittest.TestCase):
    def test_token_is_encrypted_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.save_config({"api_token": "s3cret-token"}, path)
            self.assertNotIn("s3cret-token", path.read_text(encoding="utf-8"))
            self.assertEqual(config.load_config(path)["api_token"], "s3cret-token")

    def test_every_save_keeps_a_backup_copy(self):
        with tempfile.TemporaryDirectory() as d:
            backups = Path(d) / "backups"
            backups.mkdir()
            for i in range(25):  # older copies, oldest first by name
                (backups / f"config-2020010{i // 10}-0000{i % 10:02d}.json").write_text("{}")
            config.save_config({"station_name": "Line 7"}, Path(d) / "config.json")
            kept = sorted(p.name for p in backups.glob("config-*.json"))
            self.assertEqual(len(kept), config.BACKUPS_KEPT)
            newest = backups / kept[-1]
            self.assertEqual(config.load_config(newest)["station_name"], "Line 7")

    def test_export_leaves_out_the_token_and_import_keeps_the_current_one(self):
        with tempfile.TemporaryDirectory() as d:
            exported = Path(d) / "export.json"
            config.export_settings({"station_name": "Line 7", "api_token": "secret", "stable_seconds": 9,
                                    "selections": [sel("L1:M8", "Summary")]}, exported)
            self.assertNotIn("secret", exported.read_text(encoding="utf-8"))
            cfg = config.import_settings(exported, {"api_token": "current-token"})
            self.assertEqual((cfg["station_name"], cfg["api_token"], cfg["stable_seconds"]),
                             ("Line 7", "current-token", 9))
            self.assertEqual(cfg["selections"][0]["range"], "L1:M8")

    def test_a_backup_holds_no_passwords_and_restoring_it_keeps_the_current_ones(self):
        with tempfile.TemporaryDirectory() as d:
            config.save_config({"station_name": "Line 7", "api_token": "old-token",
                                "manual_inputs": [{"name": "Operator", "value": "OP-1"}]}, Path(d) / "config.json")
            backup = next((Path(d) / "backups").glob("config-*.json"))
            self.assertNotIn("old-token", backup.read_text(encoding="utf-8"))
            self.assertNotIn("dpapi", backup.read_text(encoding="utf-8"))
            cfg = config.import_settings(backup, {"api_token": "current-token",
                                                  "manual_inputs": [{"name": "Operator", "value": "OP-9"}]})
            self.assertEqual((cfg["station_name"], cfg["api_token"]), ("Line 7", "current-token"))
            self.assertEqual(cfg["manual_inputs"][0], {"name": "Operator", "value": "OP-9"})  # the work order is today's

    def test_a_backup_made_before_the_split_still_brings_back_its_token(self):
        with tempfile.TemporaryDirectory() as d:
            old = Path(d) / "config-20260101-000000.json"
            old.write_text(json.dumps({"station_name": "Line 7", "api_token": config.protect("old-token")}),
                           encoding="utf-8")
            cfg = config.import_settings(old, {"api_token": "current-token"})
            self.assertEqual(cfg["api_token"], "old-token")

    def test_password_hash(self):
        stored = config.hash_password("secret1")
        self.assertTrue(stored.startswith("pbkdf2$"))
        self.assertNotIn("secret1", stored)
        self.assertTrue(config.check_password("secret1", stored))
        for wrong in ("secret2", "Secret1", "", " secret1"):
            self.assertFalse(config.check_password(wrong, stored), wrong)
        self.assertNotEqual(stored, config.hash_password("secret1"))  # a fresh salt every time
        for bad in ("", "plain", "pbkdf2$x$y", "md5$1$00$00", None):
            self.assertFalse(config.check_password("secret1", bad), repr(bad))

    def test_password_is_kept_in_the_file_but_never_exported_or_imported(self):
        with tempfile.TemporaryDirectory() as d:
            path, exported = Path(d) / "config.json", Path(d) / "export.json"
            stored = config.hash_password("secret1")
            config.save_config({"station_name": "Line 7", "setup_password": stored}, path)
            self.assertEqual(config.load_config(path)["setup_password"], stored)
            config.export_settings(config.load_config(path), exported)
            self.assertNotIn(stored, exported.read_text(encoding="utf-8"))
            current = {"setup_password": config.hash_password("other")}
            self.assertEqual(config.import_settings(exported, current)["setup_password"], current["setup_password"])
            backup = next((Path(d) / "backups").glob("config-*.json"))  # an older copy has the old password
            self.assertEqual(config.import_settings(backup, current)["setup_password"], current["setup_password"])
            self.assertEqual(config.import_settings(exported, {})["setup_password"], "")
        self.assertEqual(config.normalize_config({})["setup_password"], "")

    def test_import_refuses_other_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("other.json", '{"name": "not settings"}'), ("list.json", "[1, 2]"),
                               ("broken.json", "{not json")):
                (Path(d) / name).write_text(text)
                with self.assertRaises(ValueError, msg=name):
                    config.import_settings(Path(d) / name, {})

    def test_validate(self):
        with tempfile.TemporaryDirectory() as d:
            problems = engine.validate(config.normalize_config({}))
            self.assertEqual(len(problems), 4)  # source, back-up, range, delivery
            cfg = config.normalize_config({
                "source_folder": d, "backup_folder": d, "selections": [sel("nonsense")],
                "shared_enabled": True, "shared_folder": d, "api_enabled": True, "api_url": "ftp://x",
                "api_headers": "no colon here", "encoding": "klingon"})
            self.assertEqual(len(engine.validate(cfg)), 6)
            cfg = config.normalize_config({
                "source_folder": d, "backup_folder": d + "/bak", "selections": [sel("L1 x M8")],
                "api_enabled": True, "api_url": "https://mes.example/api", "api_headers": "X-Api-Key: abc"})
            self.assertEqual(engine.validate(cfg), [])


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src, self.bak, self.shared = self.tmp / "machine", self.tmp / "backup", self.tmp / "shared"
        self.src.mkdir()
        self.api = Receiver()
        self.cfg = {
            "station_name": "SMT Line 1", "source_folder": str(self.src), "backup_folder": str(self.bak),
            "selections": [sel("A12:I*", "Placements"), sel("B6", "Serial")],
            "manual_inputs": [{"name": "Operator", "value": "OP-17"}, {"name": "Work order", "value": "WO-1"}],
            "shared_enabled": True, "shared_folder": str(self.shared), "stable_seconds": 0}

    def tearDown(self):
        self.api.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **overrides):
        return engine.Engine(dict(self.cfg, **overrides), self.tmp / "data")

    def drop(self, name="board1.csv", serial="B240001"):
        (self.src / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def today(self):
        return self.bak / f"{datetime.now():%Y-%m-%d}"

    def test_shared_folder_and_backup(self):
        self.drop()
        eng = self.engine()
        self.assertEqual(eng.scan_once(), 1)
        out = list(self.shared.glob("board1_*.json"))
        self.assertEqual(len(out), 1)
        doc = json.loads(out[0].read_text(encoding="utf-8"))
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-17", "Work order": "WO-1"})
        self.assertEqual(doc["selections"][1]["value"], "B240001")
        self.assertEqual(list(self.src.iterdir()), [])
        self.assertTrue((self.today() / "board1.csv").exists())
        row = eng.ledger.recent()[0]
        self.assertEqual((row["status"], row["shared"], row["api"]), ("ok", "ok", "off"))
        self.assertEqual(engine.count_queued(self.tmp / "data"), {"shared": 0, "api": 0, "ftp": 0})

    def test_each_format(self):
        for fmt in ("csv", "txt"):
            self.drop(f"{fmt}.csv", serial=fmt)
            self.engine(output_format=fmt).scan_once()
            self.assertEqual(len(list(self.shared.glob(f"{fmt}_*.{fmt}"))), 1)

    def test_manual_inputs_can_change_while_running(self):
        eng = self.engine()
        self.drop("a.csv", "A")
        eng.scan_once()
        eng.cfg = config.normalize_config(dict(self.cfg, manual_inputs=[{"name": "Operator", "value": "OP-99"}]))
        self.drop("b.csv", "B")
        eng.scan_once()
        doc = json.loads(next(self.shared.glob("b_*.json")).read_text(encoding="utf-8"))
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-99"})

    def test_duplicate_is_not_sent_twice(self):
        eng = self.engine()
        self.drop("a.csv")
        eng.scan_once()
        self.drop("a_copy.csv")
        self.assertEqual(eng.scan_once(), 0)
        self.assertEqual(len(list(self.shared.iterdir())), 1)
        self.assertTrue((self.bak / "_duplicates" / "a_copy.csv").exists())
        self.assertEqual(eng.ledger.recent()[0]["status"], "duplicate")

    def test_unusable_file_is_quarantined_and_the_rest_continue(self):
        (self.src / "junk.csv").write_text("only,one,line\n")
        os.utime(self.src / "junk.csv", (time.time() - 60,) * 2)
        self.drop("good.csv")
        eng = self.engine()
        self.assertEqual(eng.scan_once(), 1)
        self.assertTrue((self.bak / "_failed" / "junk.csv").exists())
        self.assertEqual([r["status"] for r in eng.ledger.recent()], ["ok", "failed"])

    def test_file_still_being_written_is_left_alone(self):
        self.drop()
        eng = self.engine(stable_seconds=30)
        self.assertEqual(eng.pending_files(), [])
        eng = self.engine()
        with open(self.src / "board1.csv", "ab"):  # the machine still has it open
            self.assertEqual(eng.pending_files(), [])
        self.assertEqual(len(eng.pending_files()), 1)

    def test_backup_drive_away_sends_nothing(self):
        (self.tmp / "not_a_folder").write_text("x")
        self.drop()
        eng = self.engine(backup_folder=str(self.tmp / "not_a_folder"))
        self.assertEqual(eng.scan_once(), 0)
        self.assertTrue((self.src / "board1.csv").exists())
        self.assertFalse(self.shared.exists())
        self.assertEqual(eng.ledger.recent(), [])

    def test_api_raw(self):
        self.drop()
        self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url, api_token="tok",
                    api_headers="X-Api-Key: abc\nX-Line: 1").scan_once()
        got = self.api.got[0]
        self.assertEqual(json.loads(got["body"])["selections"][1]["value"], "B240001")
        h = got["headers"]
        self.assertEqual((h["Authorization"], h["X-Api-Key"], h["X-Line"]), ("Bearer tok", "abc", "1"))
        self.assertEqual(h["Content-Type"], "application/json")
        self.assertTrue(h["X-Filename"].startswith("board1_"))

    def test_api_multipart(self):
        self.drop()
        self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url, api_body="multipart",
                    output_format="csv").scan_once()
        got = self.api.got[0]
        self.assertIn("multipart/form-data; boundary=", got["headers"]["Content-Type"])
        self.assertIn(b'name="file"; filename="board1_', got["body"])
        self.assertIn(b'name="station"\r\n\r\nSMT Line 1', got["body"])
        self.assertIn(b"[Serial],B6", got["body"])

    def test_api_outage_queues_then_delivers_in_order(self):
        eng = self.engine(api_enabled=True, api_url=self.api.url)
        self.api.status = 503
        for i in (1, 2):
            self.drop(f"b{i}.csv", f"S{i}")
            os.utime(self.src / f"b{i}.csv", (time.time() - 60 + i,) * 2)
        self.assertEqual(eng.scan_once(), 2)
        self.assertEqual(len(list(self.shared.iterdir())), 2)  # the shared folder is not held up by the API
        self.assertEqual(engine.count_queued(self.tmp / "data"), {"shared": 0, "api": 2, "ftp": 0})
        self.assertEqual({r["api"] for r in eng.ledger.recent()}, {"queued"})
        self.api.status = 200
        eng.scan_once()
        self.assertEqual([g["headers"]["X-Filename"][:2] for g in self.api.got], ["b1", "b2"])
        self.assertEqual({r["api"] for r in eng.ledger.recent()}, {"ok"})
        self.assertEqual(engine.count_queued(self.tmp / "data")["api"], 0)

    def test_refused_file_does_not_block_the_queue(self):
        eng = self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url)
        self.api.status = lambda name: 422 if name.startswith("bad") else 200
        self.drop("bad.csv", "S1")
        os.utime(self.src / "bad.csv", (time.time() - 60,) * 2)
        self.drop("good.csv", "S2")
        eng.scan_once()
        self.assertEqual(len(self.api.got), 1)
        self.assertEqual(engine.count_rejected(self.tmp / "data"), 1)
        self.assertEqual(engine.count_queued(self.tmp / "data")["api"], 0)
        by_source = {r["source"]: r["api"] for r in eng.ledger.recent()}
        self.assertEqual(by_source["good.csv"], "ok")
        self.assertTrue(by_source["bad.csv"].startswith("rejected (HTTP 422"))

    def test_wrong_token_keeps_files_queued(self):
        eng = self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url)
        self.api.status = 401
        self.drop()
        eng.scan_once()
        self.assertEqual(engine.count_queued(self.tmp / "data")["api"], 1)
        self.assertEqual(engine.count_rejected(self.tmp / "data"), 0)

    def test_redirect_is_not_followed(self):
        eng = self.engine(shared_enabled=False, api_enabled=True, api_url=self.api.url)
        self.api.status = 302
        self.drop()
        eng.scan_once()
        self.assertEqual(self.api.got, [])
        self.assertEqual(engine.count_queued(self.tmp / "data")["api"], 1)
        with self.assertRaisesRegex(RuntimeError, "redirects to /elsewhere"):
            engine.test_api(eng.cfg)

    def test_connection_tests(self):
        cfg = config.normalize_config(dict(self.cfg, api_url=self.api.url))
        self.assertEqual(engine.test_api(cfg), 200)
        self.assertTrue(Path(engine.test_shared(cfg)).is_dir())
        self.assertEqual(list(self.shared.iterdir()), [])


class MachineFiles(unittest.TestCase):
    """Several file patterns, files that go first, and machine files written as JSON."""
    # the same folders and helpers as Pipeline, without running its tests a second time
    setUp, tearDown, engine, drop, today = (Pipeline.setUp, Pipeline.tearDown, Pipeline.engine, Pipeline.drop,
                                            Pipeline.today)

    def aged(self, name, seconds_ago):
        os.utime(self.src / name, (time.time() - seconds_ago,) * 2)

    def test_files_that_fit_a_process_first_pattern_go_first_in_the_patterns_order(self):
        for name, age in (("old.csv", 90), ("rework_1.csv", 30), ("urgent_1.csv", 10), ("urgent_0.csv", 20)):
            self.drop(name, name)
            self.aged(name, age)
        eng = self.engine(priority_patterns="URGENT_*; rework_*")  # ignoring case, as Windows does
        self.assertEqual([f.name for f in eng.pending_files()],
                         ["urgent_0.csv", "urgent_1.csv", "rework_1.csv", "old.csv"])
        self.assertEqual([f.name for f in self.engine().pending_files()][0], "old.csv")  # without: oldest first

    def test_several_file_patterns_are_watched(self):
        self.drop("a.csv")
        (self.src / "b.log").write_text("x")
        (self.src / "c.txt").write_text("x")
        eng = self.engine(file_pattern="*.csv; *.log")
        self.assertEqual(sorted(f.name for f in eng.pending_files()), ["a.csv", "b.log"])
        self.assertEqual(engine.count_waiting(eng.cfg), 2)
        self.assertEqual(eng.cfg["file_pattern"], "*.csv; *.log")

    def test_a_json_machine_file_is_cut_like_a_csv(self):
        machine = {"machine": "L1M8", "board": {"serial": "B240777"},
                   "placements": [{"Ref": "R1", "Result": "OK", "pos": {"x": 1.5}}, {"Ref": "R2", "Result": "NG"}]}
        (self.src / "board.json").write_text(json.dumps(machine), encoding="utf-8")
        rows = engine.read_rows(self.src / "board.json")
        self.assertEqual(rows[:3], [["machine", "L1M8"], ["board.serial", "B240777"], []])
        self.assertEqual(rows[3:6], [["[placements]"], ["Ref", "Result", "pos.x"], ["R1", "OK", "1.5"]])
        eng = self.engine(file_pattern="*.json", selections=[sel("B2", "Serial"), sel("@[placements]", "Placements")])
        self.assertEqual(eng.scan_once(), 1)
        doc = json.loads(next(self.shared.glob("board_*.json")).read_text(encoding="utf-8"))
        self.assertEqual(doc["selections"][0]["value"], "B240777")
        self.assertEqual(doc["selections"][1]["rows"],
                         [{"Ref": "R1", "Result": "OK", "pos.x": "1.5"}, {"Ref": "R2", "Result": "NG", "pos.x": ""}])
        self.assertTrue((self.today() / "board.json").exists())

    def test_a_list_of_records_is_one_table(self):
        rows = engine.read_rows(b'[{"Ref": "R1", "ok": true}, {"Ref": "R2", "note": null}]')
        self.assertEqual(rows, [["Ref", "ok", "note"], ["R1", "true", ""], ["R2", "", ""]])

    def test_a_csv_that_starts_with_a_title_is_still_a_csv(self):
        self.assertEqual(engine.read_rows(b"[HEADER]\nKey,Value\nSerial,B1\n")[2], ["Serial", "B1"])

    def test_an_unreadable_json_file_is_set_aside_with_the_reason(self):
        (self.src / "bad.json").write_text('{"placements": [', encoding="utf-8")
        (self.src / "odd.json").write_text("42", encoding="utf-8")
        eng = self.engine(file_pattern="*.json", selections=[sel("A1")])
        self.assertEqual(eng.scan_once(), 0)
        notes = {r["source"]: r["message"] for r in eng.ledger.recent()}
        self.assertIn("not a readable JSON file", notes["bad.json"])
        self.assertIn("unsupported JSON layout", notes["odd.json"])
        self.assertTrue((self.bak / "_failed" / "bad.json").exists())


class KeptOutputs(unittest.TestCase):
    """A copy of each output stays beside its machine file in the back-up day folder, for the viewer."""
    setUp, tearDown, engine, drop, today = (Pipeline.setUp, Pipeline.tearDown, Pipeline.engine, Pipeline.drop,
                                            Pipeline.today)

    def test_each_output_is_kept_in_the_day_folder_and_found_again(self):
        self.drop()
        eng = self.engine(output_format="csv")
        eng.scan_once()
        row = eng.ledger.recent()[0]
        kept = self.today() / "outputs" / row["output"]
        self.assertEqual(kept.read_bytes(), next(self.shared.glob("board1_*.csv")).read_bytes())
        self.assertEqual(engine.find_output(eng.cfg, self.tmp / "data", row), kept)
        blocks = engine.read_output(kept)["blocks"]
        self.assertEqual([b["name"] for b in blocks], ["Placements", "Serial"])
        self.assertEqual(blocks[1]["rows"], [["B240001"]])

    def test_without_a_kept_copy_a_file_still_in_the_queue_is_found(self):
        self.drop()
        eng = self.engine(shared_enabled=False, api_enabled=True, api_url="http://127.0.0.1:9/never")
        eng.scan_once()
        row = eng.ledger.recent()[0]
        (self.today() / "outputs" / row["output"]).unlink()
        self.assertEqual(engine.find_output(eng.cfg, self.tmp / "data", row),
                         self.tmp / "data" / "outbox" / "api" / row["output"])
        (self.tmp / "data" / "outbox" / "api" / row["output"]).unlink()
        self.assertIsNone(engine.find_output(eng.cfg, self.tmp / "data", row))
        self.assertIsNone(engine.find_output(eng.cfg, self.tmp / "data", {"output": "", "processed_at": ""}))

    def test_the_copies_go_with_their_day_folder_at_clean_up(self):
        self.drop()
        eng = self.engine(keep_backup_days=1)
        eng.scan_once()
        later = datetime.now() + timedelta(days=3)
        engine.clean_up(eng.cfg, eng.ledger, later)
        self.assertFalse(self.today().exists())

    def test_a_copy_that_cannot_be_written_does_not_stop_the_delivery(self):
        self.drop()
        self.today().mkdir(parents=True)
        (self.today() / "outputs").write_text("a file where the folder should be")
        eng = self.engine()
        self.assertEqual(eng.scan_once(), 1)
        self.assertEqual(len(list(self.shared.glob("board1_*.json"))), 1)
        self.assertIsNone(engine.find_output(eng.cfg, self.tmp / "data", eng.ledger.recent()[0]))


class _MailServer:
    """A minimal SMTP server that keeps the messages it receives (no encryption, no login)."""

    def __init__(self):
        import socketserver
        self.messages = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                def say(line):
                    self.wfile.write(line.encode() + b"\r\n")
                say("220 test mail server")
                while True:
                    line = self.rfile.readline().decode(errors="replace").strip()
                    if not line or line.upper().startswith("QUIT"):
                        say("221 bye")
                        return
                    if line.upper().startswith("DATA"):
                        say("354 go on")
                        body = []
                        for raw in iter(self.rfile.readline, b""):
                            if raw in (b".\r\n", b".\n"):
                                break
                            body.append(raw.decode(errors="replace"))
                        outer.messages.append("".join(body))
                        say("250 stored")
                    else:
                        say("250 ok")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Alerting(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "machine").mkdir()
        self.sent = []
        for name in ("notify", "webhook", "email"):
            original = alerts.SENDERS[name]
            self.addCleanup(alerts.SENDERS.__setitem__, name, original)
            alerts.SENDERS[name] = lambda *a, _n=name, **k: self.sent.append((_n, a))
        self.api = Receiver()
        self.addCleanup(self.api.close)
        self.cfg = {"station_name": "Line 7", "source_folder": str(self.tmp / "machine"),
                    "backup_folder": str(self.tmp / "backup"), "selections": [sel("A12:I*"), sel("B6")],
                    "api_enabled": True, "api_url": self.api.url, "stable_seconds": 0,
                    "alert_after_minutes": 0}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **overrides):
        return engine.Engine(dict(self.cfg, **overrides), self.tmp / "data")

    def drop(self, name, serial):
        (self.tmp / "machine" / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def messages(self, eng):
        eng.alerts.flush()
        return [a for name, a in self.sent if name == "notify"]  # (title, text, kind)

    def test_lasting_problem_is_announced_once_and_its_end_too(self):
        eng = self.engine()
        self.api.status = 503
        self.drop("a.csv", "S1")
        eng.scan_once()
        eng.scan_once()  # still down: no second alert
        titles = [t for t, *_ in self.messages(eng)]
        self.assertEqual(titles, ["CNCBridge needs attention - Line 7"])
        self.assertIn("api", alerts.read_state(self.tmp / "data"))
        self.api.status = 200
        eng.scan_once()
        titles = [t for t, *_ in self.messages(eng)]
        self.assertEqual(titles[-1], "CNCBridge is working again - Line 7")
        self.assertEqual(alerts.read_state(self.tmp / "data"), {})

    def test_short_problem_does_not_alert(self):
        eng = self.engine(alert_after_minutes=10)
        self.api.status = 503
        self.drop("a.csv", "S1")
        eng.scan_once()
        self.assertEqual(self.messages(eng), [])

    def test_files_set_aside_are_announced_each_time(self):
        eng = self.engine(alert_after_minutes=10)
        self.api.status = 422
        self.drop("a.csv", "S1")
        (self.tmp / "machine" / "junk.csv").write_text("only,one,line\n")
        os.utime(self.tmp / "machine" / "junk.csv", (time.time() - 60,) * 2)
        eng.scan_once()
        texts = [text for _, text, _ in self.messages(eng)]
        self.assertTrue(any("junk.csv" in t and "_failed" in t for t in texts), texts)
        self.assertTrue(any("refused" in t and "a_" in t for t in texts), texts)

    def test_channels_that_are_off_send_nothing(self):
        eng = self.engine(alert_notify=False)
        self.api.status = 503
        self.drop("a.csv", "S1")
        eng.scan_once()
        eng.alerts.flush()
        self.assertEqual(self.sent, [])

    def test_stopping_clears_the_open_problems(self):
        eng = self.engine(poll_seconds=0.5)
        self.api.status = 503
        self.drop("a.csv", "S1")
        worker = threading.Thread(target=eng.run_forever, daemon=True)
        worker.start()
        deadline = time.time() + 15
        while not alerts.read_state(self.tmp / "data") and time.time() < deadline:
            time.sleep(0.1)
        self.assertIn("api", alerts.read_state(self.tmp / "data"))
        eng.stop()
        worker.join(10)
        self.assertEqual(alerts.read_state(self.tmp / "data"), {})

    def test_webhook_is_really_posted(self):
        alerts.SENDERS["webhook"] = alerts.post_webhook
        hook = Receiver()
        self.addCleanup(hook.close)
        cfg = config.normalize_config(dict(self.cfg, alert_notify=False, alert_webhook_enabled=True,
                                           alert_webhook_url=hook.url))
        self.assertEqual(alerts.send_test(cfg), "Webhook: sent")
        payload = json.loads(hook.got[0]["body"])
        self.assertEqual((payload["station"], payload["state"]), ("Line 7", "test"))
        self.assertIn("Line 7", payload["text"])

    def test_email_is_really_sent(self):
        alerts.SENDERS["email"] = alerts.send_email
        mail = _MailServer()
        self.addCleanup(mail.close)
        cfg = config.normalize_config(dict(self.cfg, alert_notify=False, alert_email_enabled=True,
                                           alert_email_server="127.0.0.1", alert_email_port=mail.port,
                                           alert_email_starttls=False, alert_email_to="qa@example.com"))
        self.assertEqual(alerts.send_test(cfg), "Email: sent")
        self.assertIn("Subject: CNCBridge test alert - Line 7", mail.messages[0])
        self.assertIn("To: qa@example.com", mail.messages[0])

    def test_a_failing_channel_is_reported_not_raised(self):
        alerts.SENDERS["email"] = alerts.send_email
        cfg = config.normalize_config(dict(self.cfg, alert_notify=False, alert_email_enabled=True))
        self.assertIn("Email: failed - enter the mail server", alerts.send_test(cfg))
        self.assertEqual(alerts.send_test(config.normalize_config(dict(self.cfg, alert_notify=False))),
                         "No alert channel is switched on.")

    def test_email_password_is_encrypted_and_never_exported(self):
        path, exported = self.tmp / "config.json", self.tmp / "export.json"
        config.save_config({"alert_email_password": "mail-secret"}, path)
        self.assertNotIn("mail-secret", path.read_text(encoding="utf-8"))
        self.assertEqual(config.load_config(path)["alert_email_password"], "mail-secret")
        config.export_settings(config.load_config(path), exported)
        self.assertNotIn("mail-secret", exported.read_text(encoding="utf-8"))


class Health(unittest.TestCase):
    """The facts line (last file, queue age, back-up drive) and the alert for a back-up drive that fills up."""

    GB = 1024 ** 3

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "machine").mkdir()
        self.sent = []
        for name in ("notify", "webhook", "email"):
            original = alerts.SENDERS[name]
            self.addCleanup(alerts.SENDERS.__setitem__, name, original)
            alerts.SENDERS[name] = lambda *a, _n=name, **k: self.sent.append((_n, a))
        self.api = Receiver()
        self.addCleanup(self.api.close)
        self.cfg = {"station_name": "Line 7", "source_folder": str(self.tmp / "machine"),
                    "backup_folder": str(self.tmp / "backup"), "selections": [sel("A12:I*"), sel("B6")],
                    "api_enabled": True, "api_url": self.api.url, "stable_seconds": 0, "alert_after_minutes": 0}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **overrides):
        return engine.Engine(dict(self.cfg, **overrides), self.tmp / "data")

    def drop(self, name, serial):
        (self.tmp / "machine" / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def messages(self, eng):
        eng.alerts.flush()
        return [a for name, a in self.sent if name == "notify"]

    def space(self, free_gb, total_gb=100):
        return lambda folder: (int(free_gb * self.GB), total_gb * self.GB)

    def test_levels(self):
        for free, total, expected in ((50, 100, "ok"), (1.5, 100, "low"), (0.5, 100, "critical"), (10, 1000, "low"),
                                      (2.5, 20, "ok"), (2.5, 100, "low"), (1.9, 20, "low")):
            self.assertEqual(engine.disk_level(int(free * self.GB), int(total * self.GB)), expected, (free, total))

    def test_disk_space_uses_the_nearest_existing_folder(self):
        free, total = engine.disk_space(self.tmp / "does" / "not" / "exist")
        self.assertGreater(total, free)
        self.assertIsNone(engine.disk_space(""))

    def test_health_of_an_idle_installation(self):
        eng = self.engine()
        h = engine.health(eng.cfg, self.tmp / "data", eng.ledger)
        self.assertEqual((h["last_file"], h["last_file_age"], h["oldest_queued_age"]), (None, None, None))
        self.assertTrue(h["source_reachable"])
        self.assertEqual(h["disk_level"], "ok")

    def test_health_reports_the_last_file_and_the_oldest_queued_file(self):
        eng = self.engine()
        self.api.status = 503  # nothing can be delivered, so the output stays queued
        self.drop("a.csv", "S1")
        eng.scan_once()
        now = time.time()
        h = engine.health(eng.cfg, self.tmp / "data", eng.ledger, now=now + 90)
        self.assertIsNotNone(h["last_file"])
        self.assertGreater(h["last_file_age"], 85)
        self.assertLess(h["last_file_age"], 100)
        self.assertGreater(h["oldest_queued_age"], 85)
        queued = next((self.tmp / "data" / "outbox" / "api").glob("*"))
        os.utime(queued, (now - 3000, now - 3000))
        h = engine.health(eng.cfg, self.tmp / "data", eng.ledger, now=now)
        self.assertAlmostEqual(h["oldest_queued_age"], 3000, delta=5)
        self.assertFalse(engine.health(dict(eng.cfg, source_folder=str(self.tmp / "gone")), self.tmp / "data",
                                       eng.ledger)["source_reachable"])

    def test_a_filling_back_up_drive_alerts_once_and_its_end_too(self):
        eng = self.engine()
        original = engine.disk_space
        self.addCleanup(setattr, engine, "disk_space", original)
        engine.disk_space = self.space(0.4)
        eng.scan_once()
        eng.scan_once()  # the check runs once a minute, and a standing fault alerts once
        eng._last_disk_check = 0
        eng.scan_once()
        titles = [t for t, *_ in self.messages(eng)]
        self.assertEqual(titles, ["CNCBridge needs attention - Line 7"])
        self.assertIn("disk", alerts.read_state(self.tmp / "data"))
        engine.disk_space = self.space(50)
        eng._last_disk_check = 0
        eng.scan_once()
        titles = [t for t, *_ in self.messages(eng)]
        self.assertEqual(titles[-1], "CNCBridge is working again - Line 7")
        self.assertEqual(alerts.read_state(self.tmp / "data"), {})

    def test_the_disk_is_checked_at_most_once_a_minute(self):
        eng = self.engine()
        calls = []
        original = engine.disk_space
        self.addCleanup(setattr, engine, "disk_space", original)
        engine.disk_space = lambda folder: calls.append(1) or (50 * self.GB, 100 * self.GB)
        for _ in range(5):
            eng.scan_once()
        self.assertEqual(len(calls), 1)


class SecondChances(unittest.TestCase):
    """Send a refused file again, and process a set-aside machine file again."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.machine, self.backup, self.shared = self.tmp / "machine", self.tmp / "backup", self.tmp / "shared"
        self.machine.mkdir()
        self.api = Receiver()
        self.addCleanup(self.api.close)
        self.cfg = {"source_folder": str(self.machine), "backup_folder": str(self.backup), "stable_seconds": 0,
                    "selections": [sel("B6", "Serial")], "alert_after_minutes": 60}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **overrides):
        return engine.Engine(dict(self.cfg, **overrides), self.tmp / "data")

    def drop(self, name, serial):
        (self.machine / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def row(self, eng, source, status=None):
        return next(r for r in eng.ledger.recent() if r["source"] == source and (status is None or r["status"] == status))

    def test_refused_file_can_be_sent_again(self):
        eng = self.engine(api_enabled=True, api_url=self.api.url)
        self.api.status = 422
        self.drop("a.csv", "S1")
        eng.scan_once()
        self.assertTrue(self.row(eng, "a.csv")["api"].startswith("rejected"))
        self.api.status = 200  # the receiving side fixed its problem
        self.assertEqual(engine.resend_rejected(eng.data, self.row(eng, "a.csv"), eng.ledger), ["api"])
        self.assertEqual(self.row(eng, "a.csv")["api"], "queued")
        eng.drain_outbox()
        row = self.row(eng, "a.csv")
        self.assertEqual(row["api"], "ok")
        self.assertIn("queued to send again", row["message"])
        self.assertEqual(len(self.api.got), 1)
        self.assertEqual(engine.count_rejected(eng.data), 0)

    def test_failed_file_can_be_processed_again_after_a_fix(self):
        eng = self.engine(shared_enabled=True, shared_folder=str(self.shared), selections=[sel("X1:Z5")])
        self.drop("a.csv", "S1")
        eng.scan_once()
        self.assertEqual(self.row(eng, "a.csv")["status"], "failed")
        eng.cfg = config.normalize_config(dict(self.cfg, shared_enabled=True, shared_folder=str(self.shared)))
        target = engine.reprocess(eng.cfg, eng.data, self.row(eng, "a.csv", "failed"), eng.ledger)
        self.assertEqual(target, self.machine / "a.csv")
        eng.scan_once()
        self.assertEqual(self.row(eng, "a.csv", "ok")["status"], "ok")
        self.assertIn("sent back to the machine folder", self.row(eng, "a.csv", "failed")["message"])
        self.assertEqual(len(list(self.shared.glob("a_*.json"))), 1)

    def test_duplicate_passes_once_when_sent_back(self):
        eng = self.engine(shared_enabled=True, shared_folder=str(self.shared))
        self.drop("a.csv", "S1")
        eng.scan_once()
        self.drop("a_copy.csv", "S1")
        eng.scan_once()
        duplicate = self.row(eng, "a_copy.csv", "duplicate")
        engine.reprocess(eng.cfg, eng.data, duplicate, eng.ledger)
        eng.scan_once()
        self.assertEqual(self.row(eng, "a_copy.csv", "ok")["status"], "ok")  # let through once
        self.drop("a_third.csv", "S1")
        eng.scan_once()
        self.assertEqual(self.row(eng, "a_third.csv")["status"], "duplicate")  # the pass is used up
        self.assertEqual(len(list(self.shared.glob("*.json"))), 2)

    def test_kept_copy_with_a_number_is_found(self):
        folder = self.backup / "_failed"
        folder.mkdir(parents=True)
        (folder / "a.csv").write_text("old")
        newer = folder / "a_1.csv"
        newer.write_text("newer")
        os.utime(folder / "a.csv", (time.time() - 60,) * 2)
        (folder / "ab.csv").write_text("other file")
        self.assertEqual(engine._kept_copy(folder, "a.csv"), newer)
        self.assertIsNone(engine._kept_copy(folder, "missing.csv"))


class CleanUp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.backup = self.tmp / "backup"
        self.now = datetime(2026, 10, 17, 9, 0, 0)
        for day in ("2026-08-01", "2026-09-17", "2026-10-10", "2026-10-17"):
            (self.backup / day).mkdir(parents=True)
            (self.backup / day / "board.csv").write_text("x")
        (self.backup / "notes").mkdir()           # not a day folder: never touched
        (self.backup / "notes" / "keep.txt").write_text("x")
        for name, age_days in (("old.csv", 45), ("new.csv", 2)):
            for sub in ("_failed", "_duplicates"):
                f = self.backup / sub / name
                f.parent.mkdir(exist_ok=True)
                f.write_text("x")
                stamp = (self.now - timedelta(days=age_days)).timestamp()
                os.utime(f, (stamp, stamp))
        self.ledger = engine.Ledger(self.tmp / "data" / "ledger.db")
        for when in ("2026-08-01T10:00:00", "2026-10-16T10:00:00"):
            self.ledger.add(processed_at=when, source=f"{when}.csv", status="ok")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, backup_days=0, history_days=0):
        return config.normalize_config({"backup_folder": str(self.backup), "keep_backup_days": backup_days,
                                        "keep_history_days": history_days})

    def test_nothing_is_deleted_by_default(self):
        self.assertEqual(engine.clean_up(self.cfg(), self.ledger, self.now), (0, 0))
        self.assertEqual(len(list(self.backup.iterdir())), 7)
        self.assertEqual(len(self.ledger.recent()), 2)

    def test_old_back_ups_and_history_are_deleted(self):
        removed, forgotten = engine.clean_up(self.cfg(backup_days=30, history_days=30), self.ledger, self.now)
        self.assertEqual(sorted(p.name for p in self.backup.iterdir()),
                         ["2026-09-17", "2026-10-10", "2026-10-17", "_duplicates", "_failed", "notes"])
        self.assertEqual(sorted(p.name for p in (self.backup / "_failed").iterdir()), ["new.csv"])
        self.assertEqual(sorted(p.name for p in (self.backup / "_duplicates").iterdir()), ["new.csv"])
        self.assertEqual((removed, forgotten), (3, 1))
        self.assertEqual([r["source"] for r in self.ledger.recent()], ["2026-10-16T10:00:00.csv"])

    def test_a_missing_back_up_folder_is_fine(self):
        shutil.rmtree(self.backup)
        self.assertEqual(engine.clean_up(self.cfg(backup_days=1), self.ledger, self.now), (0, 0))

    def test_engine_cleans_up_at_most_once_an_hour(self):
        (self.tmp / "machine").mkdir()
        eng = engine.Engine({"source_folder": str(self.tmp / "machine"), "backup_folder": str(self.backup),
                             "selections": [sel("B6")], "shared_enabled": True,
                             "shared_folder": str(self.tmp / "shared"), "keep_backup_days": 30}, self.tmp / "data")
        eng.scan_once()
        self.assertFalse((self.backup / "2026-08-01").exists())
        (self.backup / "2026-08-02").mkdir()
        eng.scan_once()  # within the hour: left alone
        self.assertTrue((self.backup / "2026-08-02").exists())


class CompleteMarker(unittest.TestCase):
    """'File is complete when it contains ...': a file is taken only once the machine has written the marker."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.machine = self.tmp / "machine"
        self.machine.mkdir()
        self.cfg = {"source_folder": str(self.machine), "backup_folder": str(self.tmp / "backup"),
                    "selections": [sel("A1:B3")], "shared_enabled": True, "shared_folder": str(self.tmp / "shared"),
                    "stable_seconds": 0, "complete_marker": "End_Time", "alert_after_minutes": 0}
        self.sent = []
        original = alerts.SENDERS["notify"]
        self.addCleanup(alerts.SENDERS.__setitem__, "notify", original)
        alerts.SENDERS["notify"] = lambda *a, **k: self.sent.append(a)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **overrides):
        return engine.Engine(dict(self.cfg, **overrides), self.tmp / "data")

    def names(self, eng):
        return [f.name for f in eng.pending_files()]

    def test_a_burst_writer_is_not_read_half_written(self):
        eng = self.engine()
        part = self.machine / "board.csv"
        part.write_text("Key,Value\nMachine,PNP\n")
        self.assertEqual(self.names(eng), [])          # idle, not held open, but the marker is not there yet
        self.assertEqual(eng.scan_once(), 0)
        self.assertTrue(part.exists())
        with open(part, "a") as f:                      # the machine finishes the file
            f.write("End_Time,2026-10-19 10:00:11\n")
        self.assertEqual(self.names(eng), ["board.csv"])
        self.assertEqual(eng.scan_once(), 1)
        self.assertFalse(part.exists())

    def test_marker_ignores_case_and_works_in_other_encodings(self):
        eng = self.engine(complete_marker="end_time")
        (self.machine / "a.csv").write_text("Key,Value\nEND_TIME,x\n")
        (self.machine / "b.csv").write_bytes("Key,Value\r\nEnd_Time,\xe9\r\n".encode("cp1252"))
        (self.machine / "c.csv").write_bytes("Key,Value\r\nEnd_Time,x\r\n".encode("utf-16"))
        self.assertEqual(sorted(self.names(eng)), ["a.csv", "b.csv", "c.csv"])

    def test_without_the_setting_nothing_changes(self):
        eng = self.engine(complete_marker="")
        (self.machine / "a.csv").write_text("Key,Value\nMachine,PNP\n")
        self.assertEqual(self.names(eng), ["a.csv"])

    def test_a_file_that_never_gets_the_marker_is_alerted_not_forgotten(self):
        eng = self.engine()
        (self.machine / "alarms.csv").write_text("Door open\n")
        eng.scan_once()
        eng.alerts.flush()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("alarms.csv does not contain 'End_Time'", self.sent[0][1])
        eng.scan_once()  # still waiting: not announced again
        eng.alerts.flush()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("marker:alarms.csv", alerts.read_state(self.tmp / "data"))
        (self.machine / "alarms.csv").unlink()  # removed by hand: the problem ends
        eng.scan_once()
        eng.alerts.flush()
        self.assertEqual(alerts.read_state(self.tmp / "data"), {})
        self.assertIn("working again", self.sent[-1][0])

    def test_switching_the_setting_off_clears_the_waiting_files(self):
        eng = self.engine()
        (self.machine / "alarms.csv").write_text("Door open\n")
        eng.scan_once()
        self.assertTrue(alerts.read_state(self.tmp / "data"))
        eng.cfg = config.normalize_config(dict(self.cfg, complete_marker=""))
        self.assertEqual(self.names(eng), ["alarms.csv"])
        self.assertEqual(alerts.read_state(self.tmp / "data"), {})

    def test_the_setting_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            config.save_config({"complete_marker": "  End_Time "}, Path(d) / "config.json")
            self.assertEqual(config.load_config(Path(d) / "config.json")["complete_marker"], "End_Time")
        self.assertEqual(config.normalize_config({})["complete_marker"], "")

    def test_the_sample_machine_file_has_a_marker(self):
        self.assertTrue(engine.has_marker(SAMPLE, "End_Time"))
        self.assertFalse(engine.has_marker(SAMPLE, "Not_There"))
        self.assertFalse(engine.has_marker(self.tmp / "missing.csv", "End_Time"))


class Shifts(unittest.TestCase):
    def test_parse_times(self):
        from datetime import time
        from cncbridge import shifts
        self.assertEqual(shifts.parse_times("06:00, 14:00 22:00;6:00"), [time(6), time(14), time(22)])
        self.assertEqual(shifts.parse_times(""), [])
        self.assertEqual(shifts.parse_times("06:00, nonsense, 25:00, 12:60"), [time(6)])  # lenient by default
        for bad in ("24:00", "14.00", "7", "12:5", "6am"):
            with self.assertRaises(ValueError, msg=bad):
                shifts.parse_times(bad, strict=True)

    def test_latest_boundary_and_pending(self):
        from cncbridge import shifts
        times = shifts.parse_times("06:00, 14:00, 22:00")
        at = lambda day, h, m=0: datetime(2026, 10, day, h, m)
        self.assertEqual(shifts.latest_boundary(times, at(19, 14, 0)), at(19, 14))
        self.assertEqual(shifts.latest_boundary(times, at(19, 13, 59)), at(19, 6))
        self.assertEqual(shifts.latest_boundary(times, at(19, 3)), at(18, 22))   # before today's first shift
        self.assertIsNone(shifts.latest_boundary([], at(19, 3)))
        self.assertEqual(shifts.pending(times, at(19, 5, 55), at(19, 6, 5)), at(19, 6))   # not yet confirmed
        self.assertIsNone(shifts.pending(times, at(19, 6, 1), at(19, 13, 59)))            # confirmed this shift
        self.assertEqual(shifts.pending(times, at(19, 6, 1), at(19, 14, 1)), at(19, 14))  # the next one is due
        self.assertEqual(shifts.pending(times, at(18, 22, 30), at(19, 2)), None)          # night shift confirmed
        self.assertEqual(shifts.pending(times, None, at(19, 2)), at(18, 22))              # never confirmed
        self.assertIsNone(shifts.pending([], None, at(19, 2)))

    def test_the_confirmation_is_remembered(self):
        from cncbridge import shifts
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(shifts.read_confirmed(d))
            shifts.write_confirmed(d, datetime(2026, 10, 19, 14, 2, 30, 999))
            self.assertEqual(shifts.read_confirmed(d), datetime(2026, 10, 19, 14, 2, 30))
            (Path(d) / shifts.STATE_FILE).write_text("{broken")
            self.assertIsNone(shifts.read_confirmed(d))
            shifts.write_confirmed(Path(d) / "missing_folder_is_created", datetime(2026, 10, 19, 1, 0))
            self.assertEqual(shifts.read_confirmed(Path(d) / "missing_folder_is_created"), datetime(2026, 10, 19, 1, 0))
        self.assertEqual(config.normalize_config({"shift_times": " 06:00, 14:00 "})["shift_times"], "06:00, 14:00")


class Background(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        (self.tmp / "machine").mkdir()
        self.cfg = {"source_folder": str(self.tmp / "machine"), "backup_folder": str(self.tmp / "backup"),
                    "selections": [sel("A12:I*"), sel("B6")], "shared_enabled": True,
                    "shared_folder": str(self.tmp / "shared"), "stable_seconds": 0, "poll_seconds": 0.5,
                    "manual_inputs": [{"name": "Operator", "value": "OP-17"}]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drop(self, name, serial):
        (self.tmp / "machine" / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def test_stop_request_ends_the_run_and_an_old_one_is_ignored(self):
        from cncbridge import background
        background.request_stop(self.data)  # left over from earlier: must not stop the new run
        eng = engine.Engine(self.cfg, self.data)
        worker = threading.Thread(target=eng.run_forever, daemon=True)
        worker.start()
        time.sleep(1.5)
        self.assertTrue(worker.is_alive())
        background.request_stop(self.data)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse((self.data / background.STOP_FILE).exists())

    def test_saved_settings_are_picked_up_while_running(self):
        path = self.data / "config.json"
        config.save_config(self.cfg, path)
        eng = engine.Engine(config.load_config(path), self.data, config_path=path)
        self.assertFalse(eng.reload_settings())  # nothing changed yet
        config.save_config(dict(self.cfg, manual_inputs=[{"name": "Operator", "value": "OP-99"}]), path)
        later = time.time() + 5
        os.utime(path, (later, later))  # some file systems keep times in whole seconds
        self.assertTrue(eng.reload_settings())
        self.drop("a.csv", "S1")
        eng.scan_once()
        doc = json.loads(next((self.tmp / "shared").glob("a_*.json")).read_text(encoding="utf-8"))
        self.assertEqual(doc["manual_inputs"], {"Operator": "OP-99"})

    def test_who_holds_the_data_folder(self):
        from cncbridge import background
        self.assertIsNone(background.holder(self.data))
        lock = background.acquire_lock(self.data)
        try:
            background.mark_holder(self.data, "background")
            self.assertEqual(background.holder(self.data), "background")
            self.assertTrue(background.is_running(self.data))
            background.mark_holder(self.data, "window")
            self.assertFalse(background.is_running(self.data))
        finally:
            lock.close()
        self.assertIsNone(background.holder(self.data))

    def test_window_mode(self):
        from cncbridge import __main__ as entry, background
        original = background.task_exists
        self.addCleanup(setattr, background, "task_exists", original)
        background.task_exists = lambda d: True
        self.assertEqual(entry.window_mode(self.data), ("connected", None))
        background.task_exists = lambda d: False
        mode, lock = entry.window_mode(self.data)
        self.assertEqual(mode, "normal")
        background.mark_holder(self.data, "window")
        self.assertEqual(entry.window_mode(self.data), ("busy", None))
        background.mark_holder(self.data, "background")
        self.assertEqual(entry.window_mode(self.data), ("connected", None))
        lock.close()

    def test_the_task_name_never_creates_folders(self):
        from cncbridge import background
        saved = {k: os.environ.get(k) for k in ("PROGRAMDATA", "LOCALAPPDATA")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])
        os.environ["PROGRAMDATA"], os.environ["LOCALAPPDATA"] = str(self.tmp / "PD"), str(self.tmp / "LA")
        self.assertEqual(background.task_name(self.tmp / "PD" / "CNCBridge"), "CNCBridge background")  # the default
        self.assertEqual(background.task_name(self.tmp / "LA" / "CNCBridge"), "CNCBridge background")
        self.assertNotEqual(background.task_name(self.data), "CNCBridge background")
        self.assertFalse((self.tmp / "PD").exists() or (self.tmp / "LA").exists())
        self.assertEqual(background.task_name(self.data), background.task_name(self.data))  # stable per folder

    def test_task_definition(self):
        from cncbridge import background
        self.assertNotEqual(background.task_name(self.data), "CNCBridge background")  # not the default folder
        self.assertTrue(background.task_name(self.data).startswith("CNCBridge background "))
        program, arguments, workdir = background.task_command(self.data)
        self.assertTrue(program.lower().endswith(("pythonw.exe", "python.exe")))
        self.assertIn("CNCBridge.pyw", arguments)
        self.assertIn(f'--headless --data "{self.data.resolve()}"', arguments)
        xml = background.task_xml(self.data, user="PLANT\\op & co")
        self.assertIn("<UserId>PLANT\\op &amp; co</UserId>", xml)
        self.assertIn("<LogonTrigger>", xml)
        self.assertIn("--headless", xml)

    def test_install_and_remove_use_schtasks(self):
        import subprocess
        from cncbridge import background
        calls = []

        def fake(*args):
            calls.append(args)
            if args[0] == "/Create":
                calls.append(Path(args[args.index("/XML") + 1]).read_text(encoding="utf-16"))
            return subprocess.CompletedProcess(args, 0, "SUCCESS", "")
        original = background._schtasks
        self.addCleanup(setattr, background, "_schtasks", original)
        background._schtasks = fake
        background.install_task(self.data)
        self.assertEqual(calls[0][:2], ("/Create", "/TN"))
        self.assertIn("<Command>", calls[1])
        background.start_task(self.data)
        self.assertEqual(calls[2][0], "/Run")
        background.remove_task(self.data)
        self.assertEqual(calls[3][0], "/Delete")
        background._schtasks = lambda *a: subprocess.CompletedProcess(a, 1, "", "ERROR: Access is denied.")
        with self.assertRaisesRegex(OSError, "could not be created: Access is denied"):
            background.install_task(self.data)


class DataFolder(unittest.TestCase):
    """Settings and history live outside the program folder, and an old 'data' folder is moved there once."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old, self.new = self.tmp / "program" / "data", self.tmp / "ProgramData" / "CNCBridge"
        (self.old / "outbox" / "shared").mkdir(parents=True)
        (self.old / "logs").mkdir()
        config.save_config({"station_name": "Line 7", "api_token": "tok"}, self.old / "config.json")
        engine.Ledger(self.old / "ledger.db").add(source="a.csv", sha256="abc", status="ok", output="a.json")
        (self.old / "outbox" / "shared" / "a.json").write_text('{"queued": true}')
        (self.old / "instance.lock").write_text("")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **values):
        saved = {k: os.environ.get(k) for k in values}
        os.environ.update({k: str(v) for k, v in values.items()})
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])

    def test_default_is_programdata(self):
        self.env(PROGRAMDATA=self.tmp / "ProgramData", LOCALAPPDATA=self.tmp / "Local")
        self.assertEqual(config.default_data_dir(), self.tmp / "ProgramData" / "CNCBridge")

    def test_falls_back_to_localappdata(self):
        (self.tmp / "not_a_folder").write_text("x")  # ProgramData cannot be used
        self.env(PROGRAMDATA=self.tmp / "not_a_folder", LOCALAPPDATA=self.tmp / "Local")
        self.assertEqual(config.default_data_dir(), self.tmp / "Local" / "CNCBridge")

    def test_old_folder_is_moved_once(self):
        message = config.move_data(self.old, self.new, today=datetime(2026, 10, 17))
        self.assertIn("moved", message)
        self.assertEqual(config.load_config(self.new / "config.json")["station_name"], "Line 7")
        self.assertEqual(config.load_config(self.new / "config.json")["api_token"], "tok")
        self.assertEqual(engine.Ledger(self.new / "ledger.db").recent()[0]["source"], "a.csv")
        self.assertTrue((self.new / "outbox" / "shared" / "a.json").exists())
        self.assertFalse((self.new / "instance.lock").exists())
        self.assertFalse(self.old.exists())
        self.assertTrue((self.tmp / "program" / "data.moved-20261017" / "config.json").exists())
        self.assertIsNone(config.move_data(self.old, self.new))  # nothing left to move

    def test_existing_settings_are_never_overwritten(self):
        config.save_config({"station_name": "New PC"}, self.new / "config.json")
        self.assertIsNone(config.move_data(self.old, self.new))
        self.assertEqual(config.load_config(self.new / "config.json")["station_name"], "New PC")
        self.assertTrue(self.old.exists())

    def test_history_alone_is_moved_too(self):
        (self.old / "config.json").unlink()  # settings lost, history still there
        self.assertIn("moved", config.move_data(self.old, self.new, today=datetime(2026, 10, 17)))
        self.assertEqual(engine.Ledger(self.new / "ledger.db").recent()[0]["source"], "a.csv")

    def test_existing_history_is_never_overwritten(self):
        engine.Ledger(self.new / "ledger.db").add(source="new.csv", status="ok")
        self.assertIsNone(config.move_data(self.old, self.new))
        self.assertEqual(engine.Ledger(self.new / "ledger.db").recent()[0]["source"], "new.csv")

    def test_second_move_on_the_same_day_gets_its_own_name(self):
        (self.tmp / "program" / "data.moved-20261017").mkdir()
        config.move_data(self.old, self.new, today=datetime(2026, 10, 17))
        self.assertTrue((self.tmp / "program" / "data.moved-20261017-2" / "config.json").exists())

    def test_not_moved_while_an_old_copy_runs(self):
        from cncbridge import __main__ as entry
        held = entry.acquire_lock(self.old)
        try:
            folder, message = entry.choose_data_dir(old=self.old, new=self.new)
        finally:
            held.close()
        self.assertEqual((folder, message), (self.old, None))  # the lock then reports "already running"
        self.assertFalse((self.new / "config.json").exists())
        self.assertTrue((self.old / "config.json").exists())

    def test_second_copy_keeps_its_own_folder(self):
        from cncbridge import __main__ as entry
        config.save_config({"station_name": "First copy"}, self.new / "config.json")
        folder, message = entry.choose_data_dir(old=self.old, new=self.new)
        self.assertEqual((folder, message), (self.old, None))
        self.assertEqual(config.load_config(self.new / "config.json")["station_name"], "First copy")
        self.assertEqual(config.load_config(self.old / "config.json")["station_name"], "Line 7")

    def test_fresh_install_and_explicit_folder(self):
        from cncbridge import __main__ as entry
        empty = self.tmp / "empty_program" / "data"
        self.assertEqual(entry.choose_data_dir(old=empty, new=self.new), (self.new, None))
        self.assertEqual(entry.choose_data_dir(requested=self.tmp / "mine", old=self.old, new=self.new),
                         ((self.tmp / "mine").resolve(), None))
        self.assertTrue(self.old.exists())  # an explicit --data never moves anything

    def test_queued_file_is_delivered_after_the_move(self):
        from cncbridge import __main__ as entry
        folder, message = entry.choose_data_dir(old=self.old, new=self.new)
        self.assertEqual(folder, self.new)
        self.assertIn("moved", message)
        shared = self.tmp / "shared"
        eng = engine.Engine(dict(shared_enabled=True, shared_folder=str(shared)), self.new)
        eng.drain_outbox()
        self.assertEqual((shared / "a.json").read_text(), '{"queued": true}')
        self.assertEqual(eng.ledger.recent()[0]["shared"], "ok")


if __name__ == "__main__":
    unittest.main()
