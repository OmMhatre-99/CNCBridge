"""Reading a machine file and cutting cell ranges out of it."""

import csv
import fnmatch
import io
import json
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
    """
    Read a machine file (path or raw bytes) into a list of rows of stripped cell texts. A CSV is read as it is;
    a JSON file is laid out as rows first (see json_rows), so that ranges, headings and the preview work the same.
    """
    is_json = not isinstance(source, bytes) and Path(source).suffix.lower() == ".json"
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    text = decode(data, encoding)
    if is_json or text.lstrip()[:1] in ("{", "["):  # a CSV can start with a title such as [HEADER], too
        try:
            return json_rows(json.loads(text))
        except ValueError as e:
            if is_json:
                raise FileError(f"not a readable JSON file ({e})") from None
    delim = DELIMITERS.get(delimiter) or sniff_delimiter(text)
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim)]


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _flatten(record, prefix=""):
    """{'a': 1, 'b': {'c': 2}} -> {'a': '1', 'b.c': '2'}; lists inside a record stay JSON text."""
    out = {}
    for key, value in record.items():
        if isinstance(value, dict):
            out.update(_flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = _cell(value)
    return out


def _records_table(records):
    rows = [_flatten(r) for r in records]
    columns = []
    for r in rows:
        columns += [c for c in r if c not in columns]
    return [columns] + [[r.get(c, "") for c in columns] for r in rows]


def json_rows(data):
    """
    A JSON machine file laid out as the rows of a CSV:
      a list of records  -> a header row and one row per record;
      an object          -> its single values first, one 'name, value' row each; then every list of records
                            under a title row [name], as many machine CSVs have it, each followed by an empty row.
                            Nested objects are followed, their names joined by '.'.
    Anything else is not a layout a machine writes, and raises FileError.
    """
    def is_records(value):
        return isinstance(value, list) and value and all(isinstance(v, dict) for v in value)

    if is_records(data):
        return _records_table(data)
    if not isinstance(data, dict):
        raise FileError("unsupported JSON layout: expected an object or a list of records")
    values, tables = [], []

    def walk(obj, prefix):
        for key, value in obj.items():
            name = f"{prefix}{key}"
            if isinstance(value, dict):
                walk(value, name + ".")
            elif is_records(value):
                tables.append((name, value))
            else:
                values.append([name, ", ".join(map(_cell, value)) if isinstance(value, list) else _cell(value)])

    walk(data, "")
    rows = values + ([[]] if values and tables else [])
    for name, records in tables:
        rows += [[f"[{name}]"]] + _records_table(records) + [[]]
    if not any(any(r) for r in rows):
        raise FileError("this JSON file holds no data")
    return rows


def split_patterns(text):
    """'*.csv; *.json' (or one per line) -> ['*.csv', '*.json']."""
    return [p.strip() for p in re.split(r"[;\n]", str(text or "")) if p.strip()]


def machine_files(folder, pattern, sub_folders=False):
    """The files in the folder that fit the file pattern(s); with sub_folders, those one level down instead."""
    found = {}
    for p in split_patterns(pattern) or ["*.csv"]:
        for f in Path(folder).glob(("*/" if sub_folders else "") + p):
            if f.is_file():
                found[str(f).lower()] = f
    return list(found.values())


def priority(name, patterns):
    """A file's place in the queue: the number of the first process-first pattern it fits, else after them all."""
    order = split_patterns(patterns)
    return next((i for i, p in enumerate(order) if fnmatch.fnmatch(name.lower(), p.lower())), len(order))


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


def is_wildcard(text):
    """'@NOZZLE*' or '@[L?xM8]': a heading with * or ? finds every table whose title fits it."""
    return is_heading(text) and any(ch in split_columns(text)[0] for ch in "*?")


COLUMNS_RE = re.compile(r"^(.*\S)\s*\[([^\[\]]*)\]\s*$")


def split_columns(text):
    """
    'A12:I*[Ref, Result]' -> ('A12:I*', ['Ref', 'Result']); 'A12:I*' -> ('A12:I*', None).
    The list is only taken off when what stays in front is a range or a heading on its own, so '@[ERRORS]' stays
    the heading [ERRORS] while '@[ERRORS][Code, Text]' is that heading with two columns.
    """
    text = str(text).strip()
    m = COLUMNS_RE.match(text)
    if m:
        front = m.group(1).strip()
        try:
            _check_cells(front)
            return front, [c.strip() for c in m.group(2).split(",") if c.strip()]
        except ValueError:
            pass
    return text, None


def _check_cells(text):
    if is_heading(text):
        if not str(text).strip()[1:].strip():
            raise ValueError("after @ put the heading to find, for example @[ERRORS]")
        return
    parse_range(text)


def check_range(text):
    """Raise ValueError unless the text is a cell range or a heading to find, with or without a column list."""
    cells, columns = split_columns(text)
    _check_cells(cells)
    if columns is not None and not columns:
        raise ValueError("put the column names inside the brackets, for example A12:I*[Ref, Result]")


def normalize_range(text):
    cells, columns = split_columns(text)
    cells = "@" + cells[1:].strip() if is_heading(cells) else format_range(*parse_range(cells))
    return cells + (f"[{', '.join(columns)}]" if columns else "")


def find_heading(rows, text):
    """(col, row) of the first cell, row by row, whose text is `text` (ignoring case), or None."""
    want = text.strip().lower()
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell.lower() == want:
                return c, r
    return None


def find_titles(rows, pattern):
    """
    [(col, row, title)] of every table title that fits a wildcard pattern (ignoring case). A title is a cell alone
    on its row, such as [NOZZLE_STATS]; a pattern without brackets also fits a title in brackets (NOZZLE* fits
    [NOZZLE_STATS]).
    """
    want = pattern.strip().lower()
    found = []
    for r, row in enumerate(rows):
        filled = [(c, cell) for c, cell in enumerate(row) if cell]
        if len(filled) == 1:
            c, cell = filled[0]
            if fnmatch.fnmatch(cell.lower(), want) or fnmatch.fnmatch(cell.strip("[] ").lower(), want):
                found.append((c, r, cell))
    return found


def _block_at(rows, c1, r1):
    """
    The block under a heading cell. A heading alone on its row (a title such as [ERRORS]) has its table on the
    rows below; a heading inside a header row (such as Timestamp) starts the table itself. The block runs
    as far right as its header row has cells, and down to the first empty row.
    """
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


def _resolve_heading(rows, text):
    found = find_heading(rows, text.strip()[1:].strip())
    if not found:
        return 0, 0, 0, -1
    return _block_at(rows, *found)


def resolve_range(rows, text):
    """Where a range actually sits in this file (a wildcard: its first table). row2 < row1 means nothing there."""
    text = split_columns(text)[0]
    if is_wildcard(text):
        titles = find_titles(rows, text[1:])
        return _block_at(rows, *titles[0][:2]) if titles else (0, 0, 0, -1)
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


def find_tables(rows):
    """
    The tables in a machine file, for the picker: every title alone on its row (such as [ERRORS] or
    Table: Errors) with a header row of two or more cells under it. [{title, range, columns, rows}].
    """
    tables = []
    for r, row in enumerate(rows[:-1]):
        filled = [(c, cell) for c, cell in enumerate(row) if cell]
        if len(filled) != 1 or sum(1 for cell in rows[r + 1] if cell) < 2:
            continue
        c, title = filled[0]
        c1, r1, c2, r2 = _block_at(rows, c, r)
        tables.append({"title": title, "range": format_range(c1, r1, c2, r2),
                       "columns": [cell for cell in rows[r1][c1:c2 + 1]], "rows": r2 - r1})
    return tables


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


def _keep_columns(block, wanted, missing):
    """Only the listed columns, in the listed order (ignoring case; renamed or original names)."""
    have = {c.lower(): i for i, c in enumerate(block["columns"])}
    for i, c in enumerate(block.pop("original", [])):
        have.setdefault(c.lower(), i)
    keep = []
    for name in wanted:
        if name.lower() in have:
            keep.append(have[name.lower()])
        else:
            missing.append(name)
    block["columns"] = [block["columns"][i] for i in keep]
    block["rows"] = [[r[i] for i in keep] for r in block["rows"]]


def _cut(rows, sel, c1, r1, c2, last, renames, name, missing):
    width = c2 - c1 + 1
    cells = [(rows[r][c1:c2 + 1] + [""] * width)[:width] for r in range(r1, last + 1)]
    rng, columns = split_columns(normalize_range(sel["range"]))
    block = {"name": name, "range": rng}
    if is_heading(rng):  # also say where the heading was found in this file
        block["cells"] = format_range(c1, r1, c2, last) if last >= r1 else ""
    if ":" not in rng and not is_heading(rng):
        block["value"] = cells[0][0] if cells else ""
    elif sel["header"]:
        original = cells[0] if cells else []
        names = [(renames or {}).get(h.lower(), h) if h else h for h in original]
        block["columns"] = _header(names)
        block["rows"] = cells[1:]
        if columns and cells:
            block["original"] = original
            _keep_columns(block, columns, missing)
    else:
        block["rows"] = cells
    return block, any(any(r) for r in cells)


def extract_all(rows, sel, renames=None, missing=None):
    """
    Cut one selection out of the file: [(block, has_data)]. One block, except for a heading with a wildcard,
    which gives one block per table whose title fits (none found: one empty block, so the warning says so).
    missing: a list that collects the names in a column list that the file does not have.
    """
    missing = [] if missing is None else missing
    rng = split_columns(sel["range"])[0]
    name = sel["name"].strip() or split_columns(normalize_range(sel["range"]))[0]
    if is_wildcard(rng):
        titles = find_titles(rows, rng.strip()[1:])
        if titles:
            out = []
            for c, r, title in titles:
                c1, r1, c2, last = _block_at(rows, c, r)
                label = f"{sel['name'].strip()} ({title})" if sel["name"].strip() else title
                out.append(_cut(rows, sel, c1, r1, c2, last, renames, label, missing))
            return out
    return [_cut(rows, sel, *resolve_range(rows, sel["range"]), renames, name, missing)]


def extract(rows, sel, renames=None):
    """
    Cut one selection out of the file. Returns (block, has_data), the first block for a wildcard.
    block is {name, range} plus 'value' (single cell), or 'columns' + 'rows' (header on), or 'rows' (header off).
    renames: {column name in lower case: new name}, applied to the column names of a header row.
    """
    return extract_all(rows, sel, renames)[0]
