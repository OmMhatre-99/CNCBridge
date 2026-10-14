"""Preflight checks report configuration, preview and connectivity separately from delivery proof."""
import tempfile
from pathlib import Path

from . import engine
from .config import normalize_config


def run_checks(settings, data_dir, rows=None, sample_name="", check_api=False):
    cfg = normalize_config(settings)
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    problems = engine.validate(cfg)
    add("Settings", "FAIL" if problems else "PASS",
        "\n".join(problems) if problems else "Folders, selections and enabled delivery routes are configured.")
    if problems:
        return checks

    for label, folder in (("Local queue", data_dir), ("Back-up folder", cfg["backup_folder"])):
        try:
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=folder) as probe:
                probe.write(b"CNCBridge check")
                probe.flush()
            add(label, "PASS", f"Writable: {folder.resolve()}")
        except OSError as exc:
            add(label, "FAIL", str(exc))

    if rows is None:
        add("Extraction preview", "NOT TESTED", "Open a machine CSV in Pick ranges & preview, then check again.")
    else:
        try:
            _, blocks, warnings = engine.build_output(sample_name or "preview.csv", rows, cfg)
            add("Extraction preview", "WARNING" if warnings else "PASS",
                f"{sample_name}: {len(blocks)} range(s), {len(engine.manual_dict(cfg))} manual input(s). "
                + " ".join(warnings))
        except (ValueError, engine.FileError) as exc:
            add("Extraction preview", "FAIL", str(exc))

    if cfg["shared_enabled"]:
        try:
            add("Shared folder", "PASS", engine.test_shared(cfg))
        except OSError as exc:
            add("Shared folder", "FAIL", str(exc))
    else:
        add("Shared folder", "DISABLED", "This profile does not send to a shared folder.")
    if not cfg["api_enabled"]:
        add("API connection", "DISABLED", "This profile does not send to an API.")
    elif not check_api:
        add("API connection", "NOT TESTED", "Enable Send a small API test file to test the entered link.")
    else:
        try:
            code = engine.test_api(cfg)
            add("API connection", "PASS", f"The endpoint accepted a connection test (HTTP {code}).")
        except Exception as exc:
            add("API connection", "FAIL", str(exc))
    return checks
