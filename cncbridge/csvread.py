"""Reading a machine file and cutting cell ranges out of it."""

import csv
import io
import re
from pathlib import Path


class FileError(Exception):
    """The source file itself is unusable; it is moved to back-up/_failed."""


DELIMITERS = {",": ",", ";": ";", "tab": "\t", "|": "|"}


def decode(data, encoding="auto"):
    if encoding != "auto":
        return data.decode(encoding, errors="replace").lstrip("﻿")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")  # usual code page of Western Windows machine PCs


def sniff_delimiter(text):
    """The first line (normally a header, so free of decimal commas) decides; otherwise the first 30 lines."""
    lines = [ln for ln in text.splitlines()[:30] if ln.strip()]
    for sample in (lines[:1], lines):
        counts = {d: sum(ln.count(d) for ln in sample) for d in DELIMITERS.values()}
        best = max(counts, key=counts.get)
        if counts[best]:
            return best
    return ","


def read_rows(source, encoding="auto", delimiter="auto"):
    """Read a CSV (path or raw bytes) into a list of rows of stripped cell texts."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    text = decode(data, encoding)
    delim = DELIMITERS.get(delimiter) or sniff_delimiter(text)
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim)]


RANGE_RE = re.compile(r"^([A-Z]{1,3})([1-9]\d*)(?:(?:x|×|:|-|to)([A-Z]{1,3})([1-9]\d*|\*))?$", re.I)


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def parse_range(text):
    """
    'L1:M8', 'L1 x M8', 'B6', 'A12:I*' -> zero-based (col1, row1, col2, row2).
    row2 is None for an open range ('*' = down to the first empty row).
    """
    m = RANGE_RE.match(re.sub(r"\s+", "", str(text)))
    if not m:
        raise ValueError(f"'{text}' is not a cell range (examples: L1:M8, B6, A12:I*)")
    c1, r1, c2, r2 = m.groups()
    a, r1 = col_index(c1), int(r1) - 1
    if c2 is None:
        return a, r1, a, r1
    b = col_index(c2)
    if r2 == "*":
        return min(a, b), r1, max(a, b), None
    r2 = int(r2) - 1
    return min(a, b), min(r1, r2), max(a, b), max(r1, r2)


def format_range(c1, r1, c2, r2):
    start = f"{col_letter(c1)}{r1 + 1}"
    if r2 is None:
        return f"{start}:{col_letter(c2)}*"
    if (c1, r1) == (c2, r2):
        return start
    return f"{start}:{col_letter(c2)}{r2 + 1}"


def is_heading(text):
    """'@[ERRORS]' or '@Timestamp': the block is found by its heading, wherever it is in the file."""
    return str(text).strip().startswith("@")


def check_range(text):
    """Raise ValueError unless the text is a cell range or a heading to find."""
    if is_heading(text):
        if not str(text).strip()[1:].strip():
            raise ValueError("after @ put the heading to find, for example @[ERRORS]")
        return
    parse_range(text)


def normalize_range(text):
    if is_heading(text):
        return "@" + str(text).strip()[1:].strip()
    return format_range(*parse_range(text))


def find_heading(rows, text):
    """(col, row) of the first cell, row by row, whose text is `text` (ignoring case), or None."""
    want = text.strip().lower()
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell.lower() == want:
                return c, r
    return None


def _resolve_heading(rows, text):
    """
    The block under a heading. A heading alone on its row (a title such as [ERRORS]) has its table on the
    rows below; a heading inside a header row (such as Timestamp) starts the table itself. The block runs
    as far right as its header row has cells, and down to the first empty row.
    """
    found = find_heading(rows, normalize_range(text)[1:])
    if not found:
        return 0, 0, 0, -1
    c1, r1 = found
    if sum(1 for cell in rows[r1] if cell) == 1 and r1 + 1 < len(rows):
        r1 += 1
    header = rows[r1]
    c2 = c1
    while c2 + 1 < len(header) and header[c2 + 1]:
        c2 += 1
    r2 = r1
    while r2 + 1 < len(rows) and any(rows[r2 + 1][c1:c2 + 1]):
        r2 += 1
    return c1, r1, c2, r2


def resolve_range(rows, text):
    """Where a range actually sits in this file. row2 < row1 means there is nothing there."""
    if is_heading(text):
        return _resolve_heading(rows, text)
    c1, r1, c2, r2 = parse_range(text)
    if r2 is None:
        r2 = r1 - 1
        while r2 + 1 < len(rows) and any(rows[r2 + 1][c1:c2 + 1]):
            r2 += 1
    else:
        r2 = min(r2, len(rows) - 1)
    return c1, r1, c2, r2


def _header(cells):
    out, seen = [], {}
    for i, h in enumerate(cells):
        h = h or f"col{i + 1}"
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 1
        out.append(h)
    return out


def parse_renames(text, strict=False):
    """
    'Old=New' lines -> {old name in lower case: new name}. Blank lines and lines starting with # are skipped.
    A line that is not Old=New is an error when strict, otherwise it is ignored.
    """
    renames = {}
    for line in str(text).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        old, sep, new = line.partition("=")
        if not sep or not old.strip() or not new.strip():
            if strict:
                raise ValueError(f"'{line.strip()}' must look like  Old name=New name")
            continue
        renames[old.strip().lower()] = new.strip()
    return renames


def extract(rows, sel, renames=None):
    """
    Cut one selection out of the file. Returns (block, has_data).
    block is {name, range} plus 'value' (single cell), or 'columns' + 'rows' (header on), or 'rows' (header off).
    renames: {column name in lower case: new name}, applied to the column names of a header row.
    """
    c1, r1, c2, last = resolve_range(rows, sel["range"])
    width = c2 - c1 + 1
    cells = [(rows[r][c1:c2 + 1] + [""] * width)[:width] for r in range(r1, last + 1)]
    rng = normalize_range(sel["range"])
    block = {"name": sel["name"].strip() or rng, "range": rng}
    if is_heading(rng):  # also say where the heading was found in this file
        block["cells"] = format_range(c1, r1, c2, last) if last >= r1 else ""
    if ":" not in rng and not is_heading(rng):
        block["value"] = cells[0][0] if cells else ""
    elif sel["header"]:
        names = [(renames or {}).get(h.lower(), h) if h else h for h in cells[0]] if cells else []
        block["columns"] = _header(names)
        block["rows"] = cells[1:]
    else:
        block["rows"] = cells
    return block, any(any(r) for r in cells)
