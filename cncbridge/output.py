"""From cut-out ranges to the JSON / CSV / TXT text, and the check that the settings are complete."""

import codecs
import csv
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

from .csvread import (FileError, check_range, extract_all, is_heading, normalize_range as normalize_cells,
                      parse_range, parse_renames, split_columns)


def manual_dict(cfg):
    out = {}
    for i, m in enumerate(cfg["manual_inputs"], 1):
        if m["name"] or m["value"]:
            out[m["name"] or f"Manual input {i}"] = m["value"]
    return out


def _aligned(table):
    widths = [max(len(r[i]) for r in table) for i in range(len(table[0]))]
    return [" | ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in table], widths


NUMBER = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


def as_number(text):
    """
    '12' -> 12 and '40.53' -> 40.53. Anything that could lose meaning stays text: IDs with leading zeros
    ('00123'), letters ('B240001'), decimal commas ('12,5'), and numbers longer than 15 digits.
    """
    if isinstance(text, str) and NUMBER.match(text) and len(text.replace("-", "").replace(".", "")) <= 15:
        return float(text) if "." in text else int(text)
    return text


def _flat_csv(info, manual, blocks):
    """One plain table: a row per record, with the file, time, station, inputs and selection in front."""
    fixed = ["source_file", "processed_at", "station"] + list(manual) + ["selection"]
    safe = lambda name: f"data_{name}" if name in fixed else name  # a data column never hides a fixed one
    records = []
    for b in blocks:
        if "value" in b:
            records.append((b["name"], {"value": b["value"]}))
        elif "columns" in b:
            records += [(b["name"], dict(zip(map(safe, b["columns"]), r))) for r in b["rows"]]
        else:
            records += [(b["name"], {f"col{i}": v for i, v in enumerate(r, 1)}) for r in b["rows"]]
    columns = []
    for _, rec in records:
        columns += [c for c in rec if c not in columns]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(fixed + columns)
    front = [info["source_file"], info["processed_at"], info["station"]] + list(manual.values())
    for name, rec in records:
        w.writerow(front + [name] + [rec.get(c, "") for c in columns])
    return buf.getvalue()


def render(fmt, meta, blocks, numbers=False, flat=False):
    """
    Return the output document as text.
    numbers: JSON only, write cells that are numbers as numbers.
    flat: CSV only, one plain table with a row per record instead of one block per selection.
    """
    manual = meta["manual_inputs"]
    info = {k: meta[k] for k in ("source_file", "processed_at", "station")}

    if fmt == "json":
        doc = dict(info, manual_inputs=manual, selections=[])
        conv = as_number if numbers else (lambda cell: cell)
        for b in blocks:
            item = dict(b)
            if "value" in b:
                item["value"] = conv(b["value"])
            if "columns" in b:
                item["rows"] = [dict(zip(b["columns"], [conv(c) for c in r])) for r in b["rows"]]
            elif numbers and "rows" in b:
                item["rows"] = [[conv(c) for c in r] for r in b["rows"]]
            doc["selections"].append(item)
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"

    if fmt == "csv" and flat:
        return _flat_csv(info, manual, blocks)

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["[INFO]"])
        w.writerows(info.items())
        w.writerows(manual.items())
        for b in blocks:
            w.writerow([])
            w.writerow([f"[{b['name']}]", b["range"]])
            if "value" in b:
                w.writerow([b["value"]])
            else:
                if "columns" in b:
                    w.writerow(b["columns"])
                w.writerows(b["rows"])
        return buf.getvalue()

    if fmt == "txt":
        lines = [f"{'SOURCE FILE':<14}: {info['source_file']}",
                 f"{'PROCESSED AT':<14}: {info['processed_at']}",
                 f"{'STATION':<14}: {info['station']}"]
        lines += [f"{k:<14}: {v}" for k, v in manual.items()]
        for b in blocks:
            lines += ["", f"=== {b['name']}  [{b['range']}] ==="]
            if "value" in b:
                lines.append(b["value"])
                continue
            table = ([b["columns"]] if "columns" in b else []) + b["rows"]
            if not table:
                continue
            text, widths = _aligned(table)
            if "columns" in b:
                text.insert(1, "-+-".join("-" * w for w in widths))
            lines += text
        return "\r\n".join(lines) + "\r\n"

    raise ValueError(f"Unsupported format: {fmt}")


def build_output(src_name, rows, cfg, now=None):
    """Select + render. Returns (text, blocks, warnings). Used by the engine and by the preview."""
    now = now or datetime.now()
    renames = parse_renames(cfg["rename_columns"])
    blocks, warnings, has_data = [], [], False
    for i, sel in enumerate(cfg["selections"], 1):
        if not sel["range"]:
            continue
        missing = []
        cells = split_columns(sel["range"])[0]
        last = None if is_heading(cells) else parse_range(cells)[3]
        for block, filled in extract_all(rows, sel, renames, missing):
            blocks.append(block)
            has_data = has_data or filled
            if not filled:
                warnings.append(f"selection {i} ({block['range']}) is empty in this file")
            elif last is not None and last >= len(rows):
                warnings.append(f"selection {i} ({block['range']}) reaches past the last row of the file "
                                f"({len(rows)})")
        for column in dict.fromkeys(missing):
            warnings.append(f"selection {i}: there is no column '{column}' in this file")
    if not blocks:
        raise ValueError("no cell range is configured")
    if not has_data:
        raise FileError("none of the selected cell ranges contain data in this file")
    stamp = now.astimezone() if cfg["time_zone"] else now  # +05:30 and the like, when switched on
    meta = {"source_file": src_name, "processed_at": stamp.isoformat(timespec="seconds"),
            "station": cfg["station_name"], "manual_inputs": manual_dict(cfg)}
    text = render(cfg["output_format"], meta, blocks, numbers=cfg["json_numbers"], flat=cfg["csv_flat"])
    return text, blocks, warnings


def parse_headers(text):
    """'Name: value' lines -> dict."""
    out = {}
    for line in str(text).splitlines():
        if not line.strip():
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip() or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name.strip()):
            raise ValueError(f"extra header '{line.strip()}' must look like  Name: value")
        out[name.strip()] = value.strip()
    return out


def _same_folder(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# The parts of the setup a problem can belong to; the first-start checklist follows them.
FOLDERS, RANGES, DELIVERY, OTHER = "folders", "ranges", "delivery", "other"


def problems(cfg):
    """Why monitoring cannot run with these settings: [(part of the setup, reason)]. Empty list = ready."""
    p = []
    src, bak = cfg["source_folder"], cfg["backup_folder"]
    if not src:
        p.append((FOLDERS, "Choose the machine folder."))
    elif not Path(src).is_dir():
        p.append((FOLDERS, f"Machine folder not found: {src}"))
    if not bak:
        p.append((FOLDERS, "Choose the back-up folder."))
    elif src and _same_folder(src, bak):
        p.append((FOLDERS, "The back-up folder must be different from the machine folder."))

    ranges = []
    for i, sel in enumerate(cfg["selections"], 1):
        if sel["range"]:
            try:
                check_range(sel["range"])
                ranges.append(sel["range"])
            except ValueError as e:
                p.append((RANGES, f"Selection {i}: {e}"))
                continue
            cells, columns = split_columns(sel["range"])
            if columns and not is_heading(cells) and ":" not in normalize_cells(cells):
                p.append((RANGES, f"Selection {i}: a column list needs a block of cells, not a single cell."))
            elif columns and not sel["header"]:
                p.append((RANGES, f"Selection {i}: a column list needs 'First row is header' to know the names."))
    if not ranges and not any(part == RANGES for part, _ in p):
        p.append((RANGES, "Enter at least one cell range to extract (for example L1:M8)."))

    try:
        parse_renames(cfg["rename_columns"], strict=True)
    except ValueError as e:
        p.append((OTHER, f"Rename columns: {e}"))

    if not cfg["shared_enabled"] and not cfg["api_enabled"] and not cfg["ftp_enabled"]:
        p.append((DELIVERY, "Turn on at least one delivery: shared folder, API link or FTP server."))
    if cfg["ftp_enabled"] and not cfg["ftp_host"]:
        p.append((DELIVERY, "Enter the FTP server address."))
    if cfg["shared_enabled"]:
        if not cfg["shared_folder"]:
            p.append((DELIVERY, "Enter the shared folder."))
        elif src and _same_folder(src, cfg["shared_folder"]):
            p.append((DELIVERY, "The shared folder must be different from the machine folder."))
    if cfg["api_enabled"]:
        if not re.match(r"https?://\S+$", cfg["api_url"], re.I):
            p.append((DELIVERY, "Enter the API link (it must start with http:// or https://)."))
        try:
            parse_headers(cfg["api_headers"])
        except ValueError as e:
            p.append((DELIVERY, f"API: {e}"))
    if cfg["heartbeat_enabled"]:
        if not cfg["heartbeat_url"] and not cfg["heartbeat_folder"]:
            p.append((OTHER, "The heartbeat is on: enter its web address or its folder (Options > Alerts)."))
        elif cfg["heartbeat_url"] and not re.fullmatch(r"https?://[^ ]+", cfg["heartbeat_url"], re.I):
            p.append((OTHER, "The heartbeat address must start with http:// or https://."))
    if cfg["encoding"] != "auto":
        try:
            codecs.lookup(cfg["encoding"])
        except LookupError:
            p.append((OTHER, f"Unknown file encoding: {cfg['encoding']}"))
    return p


def validate(cfg):
    """Reasons why monitoring cannot run with these settings (empty list = ready)."""
    return [reason for _, reason in problems(cfg)]
