"""
CNCBridge engine.

For every machine CSV that appears in the source folder:

    wait until the machine has finished writing -> fingerprint (duplicate guard)
    -> read -> cut out up to 10 cell ranges -> add the 2 manual inputs
    -> render JSON / CSV / TXT -> queue it for each destination (shared folder, API link)
    -> move the source into the dated back-up folder -> record it in the ledger
    -> deliver everything queued; what cannot be delivered now is retried every cycle

Only the Python standard library is used.
"""
import codecs
import contextlib
import csv
import ftplib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .alerts import Alerts
from .background import stop_requested
from .config import APP_NAME, VERSION, load_config, normalize_config

CONTENT_TYPES = {"json": "application/json", "csv": "text/csv", "txt": "text/plain"}
DELIMITERS = {",": ",", ";": ";", "tab": "\t", "|": "|"}
# The receiver looked at this file and refused it; sending the same bytes again cannot succeed.
# Anything else (401, 404, 5xx, no network...) is a problem with the link, so the file waits in the queue.
PERMANENT_HTTP = {400, 409, 413, 415, 422}

log = logging.getLogger("cncbridge")


class FileError(Exception):
    """The source file itself is unusable; it is moved to back-up/_failed."""


class Rejected(Exception):
    """The receiver refused this particular file; it is moved to data/rejected."""


# =============================================================== reading
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


# =============================================================== cell ranges
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


# =============================================================== rendering
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
        block, filled = extract(rows, sel, renames)
        blocks.append(block)
        has_data = has_data or filled
        last = None if is_heading(sel["range"]) else parse_range(sel["range"])[3]
        if not filled:
            warnings.append(f"selection {i} ({block['range']}) is empty in this file")
        elif last is not None and last >= len(rows):
            warnings.append(f"selection {i} ({block['range']}) reaches past the last row of the file ({len(rows)})")
    if not blocks:
        raise ValueError("no cell range is configured")
    if not has_data:
        raise FileError("none of the selected cell ranges contain data in this file")
    stamp = now.astimezone() if cfg["time_zone"] else now  # +05:30 and the like, when switched on
    meta = {"source_file": src_name, "processed_at": stamp.isoformat(timespec="seconds"),
            "station": cfg["station_name"], "manual_inputs": manual_dict(cfg)}
    text = render(cfg["output_format"], meta, blocks, numbers=cfg["json_numbers"], flat=cfg["csv_flat"])
    return text, blocks, warnings


# =============================================================== settings check
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


def validate(cfg):
    """Reasons why monitoring cannot run with these settings (empty list = ready)."""
    p = []
    src, bak = cfg["source_folder"], cfg["backup_folder"]
    if not src:
        p.append("Choose the machine CSV folder.")
    elif not Path(src).is_dir():
        p.append(f"Machine CSV folder not found: {src}")
    if not bak:
        p.append("Choose the back-up folder.")
    elif src and _same_folder(src, bak):
        p.append("The back-up folder must be different from the machine CSV folder.")

    ranges = []
    for i, sel in enumerate(cfg["selections"], 1):
        if sel["range"]:
            try:
                check_range(sel["range"])
                ranges.append(sel["range"])
            except ValueError as e:
                p.append(f"Selection {i}: {e}")
    if not ranges and not any(msg.startswith("Selection") for msg in p):
        p.append("Enter at least one cell range to extract (for example L1:M8).")

    try:
        parse_renames(cfg["rename_columns"], strict=True)
    except ValueError as e:
        p.append(f"Rename columns: {e}")

    if not cfg["shared_enabled"] and not cfg["api_enabled"] and not cfg["ftp_enabled"]:
        p.append("Turn on at least one delivery: shared folder, API link or FTP server.")
    if cfg["ftp_enabled"] and not cfg["ftp_host"]:
        p.append("Enter the FTP server address.")
    if cfg["shared_enabled"]:
        if not cfg["shared_folder"]:
            p.append("Enter the shared folder.")
        elif src and _same_folder(src, cfg["shared_folder"]):
            p.append("The shared folder must be different from the machine CSV folder.")
    if cfg["api_enabled"]:
        if not re.match(r"https?://\S+$", cfg["api_url"], re.I):
            p.append("Enter the API link (it must start with http:// or https://).")
        try:
            parse_headers(cfg["api_headers"])
        except ValueError as e:
            p.append(f"API: {e}")
    if cfg["encoding"] != "auto":
        try:
            codecs.lookup(cfg["encoding"])
        except LookupError:
            p.append(f"Unknown file encoding: {cfg['encoding']}")
    return p


# =============================================================== audit ledger
class Ledger:
    """SQLite record of every file: fingerprint, outcome, delivery status. History + duplicate guard."""

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS files(
                id INTEGER PRIMARY KEY, processed_at TEXT, source TEXT, sha256 TEXT, size INTEGER,
                status TEXT, output TEXT, shared TEXT, api TEXT, message TEXT)""")
            if "ftp" not in {row[1] for row in c.execute("PRAGMA table_info(files)")}:
                c.execute("ALTER TABLE files ADD COLUMN ftp TEXT DEFAULT 'off'")  # histories from before FTP
            c.execute("CREATE INDEX IF NOT EXISTS ix_sha ON files(sha256)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_output ON files(output)")

    @contextlib.contextmanager
    def _db(self):
        with self._lock:
            con = sqlite3.connect(self.path, timeout=10)
            try:
                yield con
                con.commit()
            finally:
                con.close()

    def seen(self, sha):
        with self._db() as c:
            return c.execute("SELECT 1 FROM files WHERE sha256=? AND status='ok' LIMIT 1", (sha,)).fetchone() is not None

    def add(self, **row):
        row.setdefault("processed_at", datetime.now().isoformat(timespec="seconds"))
        keys = ",".join(row)
        with self._db() as c:
            c.execute(f"INSERT INTO files({keys}) VALUES({','.join('?' * len(row))})", list(row.values()))

    def set_delivery(self, output, kind, status):
        with self._db() as c:
            c.execute(f"UPDATE files SET {kind}=? WHERE output=?", (status, output))

    def note(self, row_id, text):
        """Add a remark to a file's row, e.g. that it was sent back for another try."""
        with self._db() as c:
            c.execute("UPDATE files SET message = CASE WHEN message IS NULL OR message = '' THEN ? "
                      "ELSE message || '; ' || ? END WHERE id=?", (text, text, row_id))

    def stats(self):
        with self._db() as c:
            return dict(c.execute("SELECT status, COUNT(*) FROM files GROUP BY status").fetchall())

    def forget_before(self, cutoff):
        """Delete history rows processed before this time. Returns how many."""
        with self._db() as c:
            return c.execute("DELETE FROM files WHERE processed_at < ?",
                             (cutoff.isoformat(timespec="seconds"),)).rowcount

    def last_processed(self):
        """When the latest file was handled (ISO text), or None."""
        with self._db() as c:
            row = c.execute("SELECT processed_at FROM files ORDER BY id DESC LIMIT 1").fetchone()
            return row[0] if row else None

    def recent(self, n=200):
        with self._db() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM files ORDER BY id DESC LIMIT ?", (n,))]


# =============================================================== delivery
def atomic_write(path, data):
    """Write to a temp name then rename, so an MES polling the folder never reads a half-written file."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.part")
    tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.replace(tmp, path)


def _multipart(file_path, extra):
    boundary = uuid.uuid4().hex
    p = Path(file_path)
    parts = []
    for k, v in extra.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    ctype = CONTENT_TYPES.get(p.suffix.lstrip("."), "application/octet-stream")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{p.name}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n".encode("utf-8") + p.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _header_safe(value):
    """HTTP header values must be Latin-1; percent-encode anything else (e.g. a station name in Chinese)."""
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        return urllib.parse.quote(value)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would turn a redirected POST into a GET and report success while the data went nowhere."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def send_api(file_path, cfg):
    p = Path(file_path)
    if cfg["api_body"] == "multipart":
        body, ctype = _multipart(p, {"station": cfg["station_name"]})
    else:
        body, ctype = p.read_bytes(), CONTENT_TYPES.get(p.suffix.lstrip("."), "application/octet-stream")
    headers = {"Content-Type": ctype, "X-Filename": _header_safe(p.name),
               "X-Station": _header_safe(cfg["station_name"]), "User-Agent": f"{APP_NAME}/{VERSION}"}
    if cfg["api_token"]:
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    headers.update(parse_headers(cfg["api_headers"]))
    req = urllib.request.Request(cfg["api_url"], data=body, method="POST", headers=headers)
    try:
        with _opener.open(req, timeout=cfg["api_timeout"]) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        msg = f"HTTP {e.code} {e.reason}"
        if 300 <= e.code < 400:
            msg += f" - the link redirects to {e.headers.get('Location', '?')}; enter the final address"
        else:
            detail = " ".join(e.read(300).decode("utf-8", "replace").split())
            msg += f": {detail}" if detail else ""
        if e.code in PERMANENT_HTTP:
            raise Rejected(msg) from None
        raise RuntimeError(msg) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {cfg['api_url']} ({e.reason})") from None


def copy_shared(file_path, cfg):
    dest = Path(cfg["shared_folder"])
    dest.mkdir(parents=True, exist_ok=True)
    atomic_write(dest / Path(file_path).name, Path(file_path).read_bytes())


PERMANENT_FTP = ("552", "553")  # replies that refuse this one file (too big, name not allowed), not the server


def _ftp_connect(cfg):
    """A logged-in connection: plain FTP, or explicit FTPS (encrypted login and data)."""
    ftp = (ftplib.FTP_TLS if cfg["ftp_tls"] else ftplib.FTP)(timeout=cfg["api_timeout"])
    ftp.encoding = "utf-8"
    try:
        ftp.connect(cfg["ftp_host"], cfg["ftp_port"])
        ftp.login(cfg["ftp_user"] or "anonymous", cfg["ftp_password"] or "")
        if cfg["ftp_tls"]:
            ftp.prot_p()
        ftp.set_pasv(cfg["ftp_passive"])
    except BaseException:
        _ftp_quit(ftp)
        raise
    return ftp


def _ftp_quit(ftp):
    try:
        ftp.quit()
    except (ftplib.Error, OSError, EOFError, AttributeError):  # AttributeError: it never connected
        ftp.close()


def _ftp_enter(ftp, folder):
    """Change into the target folder, making each missing level; an empty folder is the login folder."""
    folder = (folder or "").strip().replace("\\", "/")
    if not folder:
        return
    if folder.startswith("/"):
        ftp.cwd("/")
    for part in (p for p in folder.split("/") if p):
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def _ftp_free_name(ftp, name):
    """The name to give the file on the server: its own, or with a number when that name is taken."""
    try:
        taken = {n.rsplit("/", 1)[-1] for n in ftp.nlst()}
    except ftplib.error_perm:  # an empty folder answers 550 on some servers
        taken = set()
    stem, dot, ext = name.rpartition(".")
    stem, ext = (stem, dot + ext) if dot else (name, "")
    candidate, n = name, 1
    while candidate in taken:
        candidate = f"{stem}_{n}{ext}"
        n += 1
    return candidate


def send_ftp(file_path, cfg):
    """
    Upload one output file. It goes up under a temporary name starting with '.' and is renamed when
    complete, so a reader of the folder never sees half a file (as with the shared folder).
    A reply that refuses this file (552, 553) raises Rejected; trouble with the server or the settings
    raises RuntimeError, and the file waits in the queue and is tried again.
    """
    p = Path(file_path)
    host = cfg["ftp_host"]
    try:
        ftp = _ftp_connect(cfg)
    except ftplib.error_perm as e:
        raise RuntimeError(f"the FTP server {host} refused the login ({e})") from None
    except (ftplib.Error, OSError, EOFError) as e:
        raise RuntimeError(f"cannot reach the FTP server {host} ({e})") from None
    part = None
    try:
        _ftp_enter(ftp, cfg["ftp_folder"])
        final = _ftp_free_name(ftp, p.name)
        part = f".{final}.part"
        with p.open("rb") as fh:
            ftp.storbinary(f"STOR {part}", fh)
        ftp.rename(part, final)
        part = None
    except ftplib.error_perm as e:
        if str(e)[:3] in PERMANENT_FTP:
            raise Rejected(f"FTP {e}") from None
        raise RuntimeError(f"the FTP server {host} said: {e}") from None
    except (ftplib.Error, OSError, EOFError) as e:
        raise RuntimeError(f"FTP upload to {host} failed ({e})") from None
    finally:
        if part:
            try:
                ftp.delete(part)
            except (ftplib.Error, OSError, EOFError):
                pass
        _ftp_quit(ftp)


DESTINATIONS = {
    "shared": (copy_shared, lambda c: c["shared_enabled"] and c["shared_folder"]),
    "api": (send_api, lambda c: c["api_enabled"] and c["api_url"]),
    "ftp": (send_ftp, lambda c: c["ftp_enabled"] and c["ftp_host"]),
}


def test_ftp(cfg):
    """Upload and delete a small file on the FTP server. Returns the folder it used; raises on failure."""
    try:
        ftp = _ftp_connect(cfg)
    except ftplib.error_perm as e:
        raise RuntimeError(f"the server refused the login ({e})") from None
    except (ftplib.Error, OSError, EOFError) as e:
        raise RuntimeError(f"cannot reach the server ({e})") from None
    try:
        _ftp_enter(ftp, cfg["ftp_folder"])
        probe = f".cncbridge_probe_{uuid.uuid4().hex[:6]}"
        ftp.storbinary(f"STOR {probe}", io.BytesIO(b"ok"))
        ftp.delete(probe)
        return ftp.pwd()
    except (ftplib.Error, OSError, EOFError) as e:
        raise RuntimeError(f"the server did not accept a test file ({e})") from None
    finally:
        _ftp_quit(ftp)


def test_shared(cfg):
    """Raise unless the shared folder can be written to."""
    folder = Path(cfg["shared_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / f".cncbridge_probe_{uuid.uuid4().hex[:6]}"
    probe.write_text("ok")
    probe.unlink()
    return str(folder.resolve())


def test_api(cfg):
    """POST a small test file to the API link. Returns the HTTP status, raises on failure."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cncbridge_connection_test.json"
        p.write_text(json.dumps({"test": True, "station": cfg["station_name"],
                                 "sent_at": datetime.now().isoformat(timespec="seconds")}))
        return send_api(p, cfg)


# =============================================================== engine
def _unique(dest_dir, name):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    p, n = dest_dir / name, 1
    while p.exists():
        p = dest_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ready(path, stable_seconds):
    """True once the machine has finished with the file: untouched for a while and not held open."""
    try:
        # with 0 the age test is skipped: file times can be a few ms ahead of the clock right after a write
        if stable_seconds and time.time() - path.stat().st_mtime < stable_seconds:
            return False
        os.rename(path, path)  # fails on Windows while another program still has the file open
        return True
    except OSError:
        return False


def has_marker(path, marker, encoding="auto"):
    """True when the file contains the text the machine writes last (ignoring case)."""
    try:
        text = decode(Path(path).read_bytes(), encoding)
    except OSError:
        return False
    return marker.lower() in text.lower()


def _visible(folder):
    return [f for f in Path(folder).glob("*") if f.is_file() and not f.name.startswith(".")]


def count_waiting(cfg):
    try:
        return len([f for f in Path(cfg["source_folder"]).glob(cfg["file_pattern"]) if f.is_file()])
    except OSError:
        return 0


def count_queued(data_dir):
    return {k: len(_visible(Path(data_dir) / "outbox" / k)) for k in DESTINATIONS}


def count_rejected(data_dir):
    return sum(len(_visible(Path(data_dir) / "rejected" / k)) for k in DESTINATIONS)


# =============================================================== health
LOW_DISK_BYTES = 2 * 1024 ** 3       # the back-up drive is "low" under this, or under LOW_DISK_FRACTION of its size
LOW_DISK_FRACTION = 0.05
CRITICAL_DISK_BYTES = 1024 ** 3      # ... and "critical" under this


def disk_space(folder):
    """(free, total) bytes of the drive holding this folder, or its nearest existing parent; None if unknown."""
    path = Path(folder) if folder else None
    try:
        while path is not None:
            if path.exists():
                usage = shutil.disk_usage(path)
                return usage.free, usage.total
            if path.parent == path:
                return None
            path = path.parent
    except OSError:
        pass
    return None


def disk_level(free, total):
    """'ok', 'low' or 'critical'."""
    if free < CRITICAL_DISK_BYTES:
        return "critical"
    if free < LOW_DISK_BYTES or (total and free / total < LOW_DISK_FRACTION):
        return "low"
    return "ok"


def oldest_queued_age(data_dir, now=None):
    """Seconds since the oldest file waiting in any send queue was put there; None when the queues are empty."""
    now = time.time() if now is None else now
    ages = []
    for kind in DESTINATIONS:
        for f in _visible(Path(data_dir) / "outbox" / kind):
            try:
                ages.append(now - f.stat().st_mtime)
            except OSError:
                pass
    return max(ages) if ages else None


def health(cfg, data_dir, ledger, now=None):
    """What an operator wants to know at a glance: is it alive, is anything stuck, is there room to keep going."""
    now = time.time() if now is None else now
    last = ledger.last_processed()
    last_age = None
    if last:
        try:
            last_age = max(0.0, now - datetime.fromisoformat(last).timestamp())
        except ValueError:
            pass
    space = disk_space(cfg["backup_folder"]) if cfg.get("backup_folder") else None
    return {
        "last_file": last, "last_file_age": last_age,
        "oldest_queued_age": oldest_queued_age(data_dir, now),
        "backup_free": space[0] if space else None, "backup_total": space[1] if space else None,
        "disk_level": disk_level(*space) if space else None,
        "source_reachable": Path(cfg["source_folder"]).is_dir() if cfg.get("source_folder") else None}


# =============================================================== second chances
ALLOW_ONCE = "allow_once.json"  # fingerprints that may pass the duplicate check one more time


def _allowed(data_dir):
    try:
        return json.loads((Path(data_dir) / ALLOW_ONCE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def allow_once(data_dir, sha):
    shas = _allowed(data_dir)
    if sha not in shas:
        atomic_write(Path(data_dir) / ALLOW_ONCE, json.dumps(shas + [sha]))


def take_allowance(data_dir, sha):
    """True (once) when this content was sent back on purpose and may be processed again."""
    shas = _allowed(data_dir)
    if sha not in shas:
        return False
    shas.remove(sha)
    atomic_write(Path(data_dir) / ALLOW_ONCE, json.dumps(shas))
    return True


def _kept_copy(folder, name):
    """The file kept under this name in a back-up folder, also when it was stored as name_1.csv etc."""
    stem, suffix = Path(name).stem, Path(name).suffix
    pattern = re.compile(rf"^{re.escape(stem)}(_\d+)?{re.escape(suffix)}$", re.I)
    found = [f for f in Path(folder).glob(f"{stem}*{suffix}") if pattern.match(f.name)] if Path(folder).is_dir() else []
    return max(found, key=lambda f: f.stat().st_mtime) if found else None


def resend_rejected(data_dir, row, ledger):
    """Put an output file that the receiving site refused back in the send queue. Returns the destinations."""
    sent = []
    for kind in DESTINATIONS:
        refused = _kept_copy(Path(data_dir) / "rejected" / kind, row["output"] or "")
        if row["output"] and refused:
            queue = Path(data_dir) / "outbox" / kind
            queue.mkdir(parents=True, exist_ok=True)
            shutil.move(str(refused), str(queue / row["output"]))
            ledger.set_delivery(row["output"], kind, "queued")
            sent.append(kind)
    if sent:
        ledger.note(row["id"], f"queued to send again at {datetime.now():%H:%M}")
        log.info("%s queued to send again (%s)", row["output"], ", ".join(sent))
    return sent


def reprocess(cfg, data_dir, row, ledger):
    """
    Put a machine file that was set aside (failed or duplicate) back in the machine folder, so it is
    processed again. A duplicate is let through the duplicate check once. Returns the new path or None.
    """
    folder = Path(cfg["backup_folder"]) / ("_failed" if row["status"] == "failed" else "_duplicates")
    kept = _kept_copy(folder, row["source"])
    if not kept or not cfg["source_folder"]:
        return None
    if row["status"] == "duplicate" and row["sha256"]:
        allow_once(data_dir, row["sha256"])
    target = _unique(Path(cfg["source_folder"]), row["source"])
    shutil.move(str(kept), str(target))
    ledger.note(row["id"], f"sent back to the machine folder at {datetime.now():%H:%M}")
    log.info("%s sent back to the machine folder to be processed again", row["source"])
    return target


# =============================================================== clean-up
DAY_FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_up(cfg, ledger, now=None):
    """
    Delete what is older than the configured number of days (0 = keep everything):
    back-up day folders (judged by the date in their name), old files in back-up\\_failed and
    _duplicates, and old history rows. Only ever deletes inside the back-up folder; the send queue and
    refused files are never touched. Returns (back-up items deleted, history rows deleted).
    """
    now = now or datetime.now()
    removed = forgotten = 0
    days, backup = cfg["keep_backup_days"], Path(cfg["backup_folder"]) if cfg["backup_folder"] else None
    if days and backup and backup.is_dir():
        cutoff = now - timedelta(days=days)
        for folder in backup.iterdir():
            if folder.is_dir() and DAY_FOLDER.match(folder.name):
                try:
                    if datetime.strptime(folder.name, "%Y-%m-%d") < cutoff.replace(hour=0, minute=0, second=0,
                                                                                   microsecond=0):
                        shutil.rmtree(folder)
                        removed += 1
                except (ValueError, OSError) as e:
                    log.warning("Could not delete the old back-up folder %s: %s", folder, e)
        for name in ("_failed", "_duplicates"):
            for f in (backup / name).glob("*") if (backup / name).is_dir() else []:
                try:
                    if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                        f.unlink()
                        removed += 1
                except OSError as e:
                    log.warning("Could not delete the old file %s: %s", f, e)
    if cfg["keep_history_days"]:
        forgotten = ledger.forget_before(now - timedelta(days=cfg["keep_history_days"]))
    if removed or forgotten:
        log.info("Clean-up: %d old back-up folder(s) or file(s) and %d history row(s) deleted", removed, forgotten)
    return removed, forgotten


class Engine:
    CLEAN_UP_EVERY = 3600  # seconds
    DISK_CHECK_EVERY = 60  # seconds

    def __init__(self, cfg, data_dir, config_path=None):
        self.cfg = normalize_config(cfg)  # may be replaced while running; each file uses the current one
        self.data = Path(data_dir)
        # In the background there is no window to hand over new settings, so the settings file is
        # read again whenever it changes.
        self.config_path = Path(config_path) if config_path else None
        self._config_mtime = self._settings_time()
        self.ledger = Ledger(self.data / "ledger.db")
        self.alerts = Alerts(self.data)
        self._stop = threading.Event()
        self._last_clean_up = 0.0
        self._last_disk_check = 0.0
        self._waiting_marker = set()  # files seen without the complete-marker, by name
        self._reported = {}  # last problem logged per subject, so a standing fault is logged once, not every cycle
        self._since = {}     # when each standing fault was first seen, for alert_after_minutes

    def stop(self):
        self._stop.set()

    def _settings_time(self):
        try:
            return self.config_path.stat().st_mtime if self.config_path else None
        except OSError:
            return None

    def reload_settings(self):
        """Pick up settings saved by a window while this runs in the background."""
        mtime = self._settings_time()
        if mtime is None or mtime == self._config_mtime:
            return False
        self._config_mtime = mtime
        try:
            self.cfg = load_config(self.config_path)
        except (OSError, ValueError) as e:
            log.error("The changed settings could not be read, the previous ones stay in use: %s", e)
            return False
        log.info("Settings reloaded")
        return True

    def _check_disk(self):
        """A back-up drive that fills up stops everything: say so early, through the usual alerts."""
        if time.time() - self._last_disk_check < self.DISK_CHECK_EVERY:
            return
        self._last_disk_check = time.time()
        space = disk_space(self.cfg["backup_folder"])
        if space is None:
            return
        level = disk_level(*space)
        if level == "ok":
            self._report("disk", None)
        else:
            self._report("disk", f"The back-up drive is nearly full: {space[0] / 1024 ** 3:.1f} GB free of "
                                 f"{space[1] / 1024 ** 3:.0f} GB.",
                         logging.ERROR if level == "critical" else logging.WARNING)

    def outbox(self, kind):
        d = self.data / "outbox" / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _report(self, subject, problem, level=logging.WARNING):
        """
        Log a fault when it appears or changes; pass problem=None when the subject is healthy again.
        A fault that lasts alert_after_minutes raises an alert, and its end is announced too.
        """
        if problem is None:
            self._since.pop(subject, None)
            self.alerts.resolved(subject, self.cfg)
            return self._reported.pop(subject, None) is not None
        if self._reported.get(subject) != problem:
            self._reported[subject] = problem
            log.log(level, "%s", problem)
        since = self._since.setdefault(subject, time.time())
        if time.time() - since >= self.cfg["alert_after_minutes"] * 60:
            self.alerts.problem(subject, problem, self.cfg)
        return False

    # ---- delivery
    def drain_outbox(self):
        cfg = self.cfg
        for kind, (fn, enabled) in DESTINATIONS.items():
            if not enabled(cfg):
                continue
            files = sorted(_visible(self.outbox(kind)), key=lambda p: (p.stat().st_mtime, p.name))
            for f in files:
                try:
                    fn(f, cfg)
                except Rejected as e:
                    shutil.move(str(f), str(_unique(self.data / "rejected" / kind, f.name)))
                    self.ledger.set_delivery(f.name, kind, f"rejected ({e})")
                    log.error("%s refused %s: %s - moved to data/rejected", kind, f.name, e)
                    self.alerts.event(f"The receiving site refused {f.name} ({e}). It was set aside in "
                                      f"'rejected' in the data folder; the next files continue.", cfg)
                    continue
                except Exception as e:
                    # destination is down: keep the order and try again next cycle
                    self._report(kind, f"{kind} delivery not possible, {len(files)} file(s) waiting: {e}")
                    break
                f.unlink()
                self.ledger.set_delivery(f.name, kind, "ok")
                if self._report(kind, None):
                    log.info("%s delivery is working again", kind)
                log.info("Delivered to %s: %s", kind, f.name)

    # ---- pipeline
    def process_file(self, src):
        cfg, src = self.cfg, Path(src)
        now = datetime.now()
        sha, size = sha256_file(src), src.stat().st_size
        backup = Path(cfg["backup_folder"])

        if cfg["skip_duplicates"] and self.ledger.seen(sha) and not take_allowance(self.data, sha):
            shutil.move(str(src), str(_unique(backup / "_duplicates", src.name)))
            self.ledger.add(source=src.name, sha256=sha, size=size, status="duplicate",
                            message="identical content was already processed")
            log.warning("Duplicate skipped: %s", src.name)
            return "duplicate"

        try:
            rows = read_rows(src, cfg["encoding"], cfg["delimiter"])
        except csv.Error as e:
            raise FileError(f"not a readable CSV ({e})") from None
        text, blocks, warnings = build_output(src.name, rows, cfg, now)
        for w in warnings:
            log.warning("%s: %s", src.name, w)

        kinds = [k for k, (_, enabled) in DESTINATIONS.items() if enabled(cfg)]
        day = backup / f"{now:%Y-%m-%d}"
        day.mkdir(parents=True, exist_ok=True)  # if the back-up drive is away, fail before anything is queued
        stem, name, n = f"{src.stem}_{now:%Y%m%d_%H%M%S}", "", 0
        while not name or any((self.outbox(k) / name).exists() for k in kinds):
            name = f"{stem}{f'_{n}' if n else ''}.{cfg['output_format']}"
            n += 1
        queued = []
        try:
            for k in kinds:
                queued.append(self.outbox(k) / name)
                atomic_write(queued[-1], text)
            shutil.move(str(src), str(_unique(day, src.name)))
        except OSError:
            for q in queued:  # the source stays where it is and is processed again, so nothing may go out twice
                q.unlink(missing_ok=True)
            raise
        self.ledger.add(processed_at=now.isoformat(timespec="seconds"), source=src.name, sha256=sha, size=size,
                        status="ok", output=name, message="; ".join(warnings),
                        **{k: "queued" if k in kinds else "off" for k in DESTINATIONS})
        log.info("Processed %s -> %s (%s); source moved to %s", src.name, name,
                 ", ".join(b["name"] for b in blocks), day)
        return "ok"

    def _quarantine(self, src, reason):
        log.error("Failed %s: %s", src.name, reason)
        try:
            sha, size = sha256_file(src), src.stat().st_size
            shutil.move(str(src), str(_unique(Path(self.cfg["backup_folder"]) / "_failed", src.name)))
            self.ledger.add(source=src.name, sha256=sha, size=size, status="failed", message=str(reason))
            self.alerts.event(f"Machine file {src.name} could not be used ({reason}). It was moved to "
                              f"back-up\\_failed.", self.cfg)
        except OSError as e:
            self._report(src.name, f"Could not move {src.name} to back-up/_failed: {e}", logging.ERROR)

    def pending_files(self):
        cfg = self.cfg
        files = [f for f in Path(cfg["source_folder"]).glob(cfg["file_pattern"])
                 if f.is_file() and not f.name.startswith(".") and is_ready(f, cfg["stable_seconds"])]
        if cfg["complete_marker"]:
            files = self._with_marker(files, cfg["complete_marker"])
        elif self._waiting_marker:
            self._with_marker([], "")  # the setting was switched off: nothing waits for a marker any more
        return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))

    def _with_marker(self, files, marker):
        """
        Only files that contain the marker are taken. A file without it is left alone and reported (and
        alerted, when it lasts) so it is not forgotten: the machine may still be writing it, or the
        marker setting may be wrong.
        """
        ready, waiting = [], set()
        for f in files:
            if has_marker(f, marker, self.cfg["encoding"]):
                ready.append(f)
                self._report(f"marker:{f.name}", None)
            else:
                waiting.add(f.name)
                self._report(f"marker:{f.name}", f"{f.name} does not contain '{marker}' yet, so it is not "
                                                  f"taken. Is the machine still writing it, or is the marker "
                                                  f"setting wrong?")
        for name in self._waiting_marker - waiting:  # taken in the meantime, or no longer in the folder
            self._report(f"marker:{name}", None)
        self._waiting_marker = waiting
        return ready

    def scan_once(self):
        """One cycle: process every finished file in the source folder, then deliver what is queued."""
        problems = validate(self.cfg)
        if problems:
            self._report("settings", "Settings are not complete: " + " ".join(problems), logging.ERROR)
            return 0
        self._report("settings", None)
        done = 0
        for f in self.pending_files():
            if self._stop.is_set():
                break
            try:
                if self.process_file(f) == "ok":
                    done += 1
                self._report(f.name, None)
            except FileError as e:
                self._quarantine(f, e)
            except Exception as e:  # folder or disk trouble, not the file's fault: leave it and retry
                self._report(f.name, f"Could not process {f.name} yet, will retry: {e}", logging.ERROR)
        self.drain_outbox()
        self._check_disk()
        if time.time() - self._last_clean_up >= self.CLEAN_UP_EVERY:
            self._last_clean_up = time.time()
            clean_up(self.cfg, self.ledger)
        return done

    def run_forever(self):
        self._stop.clear()
        stop_requested(self.data)  # an old request must not stop this new run
        log.info("Monitoring %s", self.cfg["source_folder"])
        while not self._stop.is_set():
            if stop_requested(self.data):
                log.info("Stop requested from the window")
                break
            try:
                self.reload_settings()
                self.scan_once()
            except Exception as e:  # never let the watcher die on the shop floor
                self._report("scan", f"Scan error: {e}", logging.ERROR)
            self._stop.wait(self.cfg["poll_seconds"])
        self.alerts.reset()
        log.info("Monitoring stopped")
