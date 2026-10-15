"""Run component tests and the real delivery proof, retaining an auditable report.

Linux desktop coverage: xvfb-run -a python scripts/verify_all.py --gui
Native engine/CLI coverage: python scripts/verify_all.py
"""
import argparse
import contextlib
from datetime import datetime
import faulthandler
import json
import os
import platform
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({"test": test.id(), "status": "passed"})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append({"test": test.id(), "status": "skipped", "reason": reason})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.records.append({"test": test.id(), "status": "failed"})

    def addError(self, test, error):
        super().addError(test, error)
        self.records.append({"test": test.id(), "status": "error"})

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        self.records.append({"test": subtest.id(), "status": "passed" if error is None else "failed"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true", help="enable desktop checks; requires an available display")
    parser.add_argument("--output", type=Path, help="new or empty evidence directory")
    args = parser.parse_args(argv)
    output = args.output or ROOT / "artifacts" / datetime.now().strftime("verification-%Y%m%d-%H%M%S-%f")
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new or empty output directory; existing evidence is preserved")
    output.mkdir(parents=True, exist_ok=True)
    if args.gui:
        os.environ["CNCBRIDGE_GUI_TESTS"] = "1"
    else:
        os.environ.pop("CNCBRIDGE_GUI_TESTS", None)
    print("Running component and integration tests...", flush=True)
    with (output / "component-tests.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            faulthandler.dump_traceback_later(240, file=log, exit=True)
            try:
                suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
                result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=RecordingResult).run(suite)
            finally:
                faulthandler.cancel_dump_traceback_later()
    print(f"Tests: {result.testsRun} run; {len(result.failures)} failures, {len(result.errors)} errors, {len(result.skipped)} skipped.", flush=True)
    print("Running nine real HTTP delivery checks...", flush=True)
    from demo.verify import run_verification
    from cncbridge.config import VERSION
    pipeline = run_verification(output / "pipeline")
    report = {"ok": result.wasSuccessful() and pipeline["ok"],
              "version": VERSION, "platform": platform.platform(), "python": sys.version.split()[0],
              "gui_requested": args.gui, "finished_at": datetime.now().isoformat(timespec="seconds"),
              "components": {"run": result.testsRun, "failures": len(result.failures),
                             "errors": len(result.errors), "skipped": len(result.skipped), "tests": result.records},
              "pipeline": pipeline}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = [f"# CNCBridge {VERSION} verification", "", f"Outcome: {'PASS' if report['ok'] else 'FAIL'}",
             f"Platform: {report['platform']} / Python {report['python']}",
             f"Desktop tests requested: {args.gui}", "",
             f"{result.testsRun} tests run; {len(result.failures)} failures; {len(result.errors)} errors; {len(result.skipped)} skipped.",
             "", "## Complete delivery proof", "", "| Check | Result | Evidence |", "| --- | --- | --- |"]
    for check in pipeline["checks"]:
        lines.append(f"| {cell(check['name'])} | {'PASS' if check['ok'] else 'FAIL'} | {cell(check.get('detail', ''))} |")
    lines += ["", "## Individual tests and scenarios", "", "| Test | Result | Reason if skipped |", "| --- | --- | --- |"]
    for record in result.records:
        lines.append(f"| {cell(record['test'])} | {record['status']} | {cell(record.get('reason', ''))} |")
    lines += ["", "Detailed failures and assertions: component-tests.log. Original delivery evidence: pipeline/.", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{'PASS' if report['ok'] else 'FAIL'} — evidence: {output.resolve()}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
