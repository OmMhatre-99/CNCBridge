"""Getting an output file to its destinations (shared folder, API link, FTP server), and the tests for them."""

import ftplib
import io
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from .config import APP_NAME, VERSION
from .output import parse_headers


class Rejected(Exception):
    """The receiver refused this particular file; it is moved to data/rejected."""


CONTENT_TYPES = {"json": "application/json", "csv": "text/csv", "txt": "text/plain"}


# The receiver looked at this file and refused it; sending the same bytes again cannot succeed.
# Anything else (401, 404, 5xx, no network...) is a problem with the link, so the file waits in the queue.
PERMANENT_HTTP = {400, 409, 413, 415, 422}


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
