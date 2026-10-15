"""Atomic, durable replacement for configuration and delivery payloads."""
import os
import uuid
from pathlib import Path


def atomic_write(path, data, *, private=True):
    """Publish complete bytes; keep the previous file intact if publication fails.

    Files are private by default; shared destinations use normal umask-controlled
    permissions so a receiving account can read them. Directory fsync is best
    effort because Windows and some network shares do not support it.
    """
    path = Path(path)
    payload = data if isinstance(data, bytes) else data.encode("utf-8")
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o666)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if os.name != "nt":
            directory = None
            try:
                directory = os.open(path.parent, os.O_RDONLY)
                os.fsync(directory)
            except OSError:
                pass
            finally:
                if directory is not None:
                    os.close(directory)
    finally:
        tmp.unlink(missing_ok=True)
