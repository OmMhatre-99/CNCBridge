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
from datetime import datetime
from pathlib import Path

from .config import APP_NAME, VERSION, normalize_config

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


def normalize_range(text):
    return format_range(*parse_range(text))


def resolve_range(rows, text):
    """Where a range actually sits in this file. row2 < row1 means there is nothing there."""
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


def extract(rows, sel):
    """
    Cut one selection out of the file. Returns (block, has_data).
    block is {name, range} plus 'value' (single cell), or 'columns' + 'rows' (header on), or 'rows' (header off).
    """
    c1, r1, c2, last = resolve_range(rows, sel["range"])
    width = c2 - c1 + 1
    cells = [(rows[r][c1:c2 + 1] + [""] * width)[:width] for r in range(r1, last + 1)]
    rng = normalize_range(sel["range"])
    block = {"name": sel["name"].strip() or rng, "range": rng}
    if ":" not in rng:
        block["value"] = cells[0][0] if cells else ""
    elif sel["header"]:
        block["columns"] = _header(cells[0]) if cells else []
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


def render(fmt, meta, blocks):
    """Return the output document as text."""
    manual = meta["manual_inputs"]
    info = {k: meta[k] for k in ("source_file", "processed_at", "station")}

    if fmt == "json":
        doc = dict(info, manual_inputs=manual, selections=[])
        for b in blocks:
            item = dict(b)
            if "columns" in b:
                item["rows"] = [dict(zip(b["columns"], r)) for r in b["rows"]]
            doc["selections"].append(item)
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"

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
    blocks, warnings, has_data = [], [], False
    for i, sel in enumerate(cfg["selections"], 1):
        if not sel["range"]:
            continue
        block, filled = extract(rows, sel)
        blocks.append(block)
        has_data = has_data or filled
        last = parse_range(sel["range"])[3]
        if not filled:
            warnings.append(f"selection {i} ({block['range']}) is empty in this file")
        elif last is not None and last >= len(rows):
            warnings.append(f"selection {i} ({block['range']}) reaches past the last row of the file ({len(rows)})")
    if not blocks:
        raise ValueError("no cell range is configured")
    if not has_data:
        raise FileError("none of the selected cell ranges contain data in this file")
    meta = {"source_file": src_name, "processed_at": now.isoformat(timespec="seconds"),
            "station": cfg["station_name"], "manual_inputs": manual_dict(cfg)}
    return render(cfg["output_format"], meta, blocks), blocks, warnings


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
                ranges.append(parse_range(sel["range"]))
            except ValueError as e:
                p.append(f"Selection {i}: {e}")
    if not ranges and not any(msg.startswith("Selection") for msg in p):
        p.append("Enter at least one cell range to extract (for example L1:M8).")

    if not cfg["shared_enabled"] and not cfg["api_enabled"]:
        p.append("Turn on at least one delivery: shared folder or API link.")
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

    def stats(self):
        with self._db() as c:
            return dict(c.execute("SELECT status, COUNT(*) FROM files GROUP BY status").fetchall())

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


DESTINATIONS = {
    "shared": (copy_shared, lambda c: c["shared_enabled"] and c["shared_folder"]),
    "api": (send_api, lambda c: c["api_enabled"] and c["api_url"]),
}


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


class Engine:
    def __init__(self, cfg, data_dir):
        self.cfg = normalize_config(cfg)  # may be replaced while running; each file uses the current one
        self.data = Path(data_dir)
        self.ledger = Ledger(self.data / "ledger.db")
        self._stop = threading.Event()
        self._reported = {}  # last problem logged per subject, so a standing fault is logged once, not every cycle

    def stop(self):
        self._stop.set()

    def outbox(self, kind):
        d = self.data / "outbox" / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _report(self, subject, problem, level=logging.WARNING):
        """Log a fault when it appears or changes; pass problem=None when the subject is healthy again."""
        if problem is None:
            return self._reported.pop(subject, None) is not None
        if self._reported.get(subject) != problem:
            self._reported[subject] = problem
            log.log(level, "%s", problem)
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

        if cfg["skip_duplicates"] and self.ledger.seen(sha):
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
        except OSError as e:
            self._report(src.name, f"Could not move {src.name} to back-up/_failed: {e}", logging.ERROR)

    def pending_files(self):
        cfg = self.cfg
        files = [f for f in Path(cfg["source_folder"]).glob(cfg["file_pattern"])
                 if f.is_file() and not f.name.startswith(".") and is_ready(f, cfg["stable_seconds"])]
        return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))

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
        return done

    def run_forever(self):
        self._stop.clear()
        log.info("Monitoring %s", self.cfg["source_folder"])
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as e:  # never let the watcher die on the shop floor
                self._report("scan", f"Scan error: {e}", logging.ERROR)
            self._stop.wait(self.cfg["poll_seconds"])
        log.info("Monitoring stopped")
