# Component and complete-workflow verification

Run all component tests plus the live delivery proof, retaining individual results:

```sh
xvfb-run -a python scripts/verify_all.py --gui
```

With an existing display, omit xvfb-run. On a machine without a desktop, run
`python scripts/verify_all.py`; desktop tests are explicitly recorded as skipped.
Use `--output NEW_DIRECTORY` to choose the evidence location. Existing evidence
is preserved. A failed test or failed pipeline check causes a nonzero exit code.

The report directory contains a readable `report.md`, structured `report.json`, `component-tests.log` and `pipeline/`
with its own verification report, original archives, delivered payloads and ledger.
Expected simulated outage/refusal messages in logs are test scenarios, not failures;
the assertions and report determine whether handling worked.

| Component | Verified behavior |
| --- | --- |
| Configuration | Defaults, malformed values, numeric intervals, booleans, duplicate manual names, token save/load and POSIX permissions |
| Instance/CLI | A second instance is blocked; lock releases; bad/incomplete profiles return errors; headless demo performs real delivery |
| CSV reading | UTF variants and Windows encoding; explicit/automatic separators; semicolon files with decimal commas |
| Range selection | Fixed/single/open ranges, headers, empty/out-of-bounds data and collision-free column keys |
| Rendering | JSON, CSV and TXT include selections, metadata and two manual fields |
| Storage | Failed atomic replacement preserves the previous file and removes the temporary write |
| Ledger and duplicates | Processing/delivery status, identical-content archive, and unique outputs for reused source filenames |
| File pipeline | Source snapshot consistency, changing/unfinished files, available/unavailable backup and bad-file quarantine |
| Shared delivery | Exact output bytes, writable probes, independent queuing and held queues after destination changes |
| HTTP delivery | Raw/multipart bodies, authentication, redirects, outage retry/restart and isolated permanent refusal |
| Demo components | Deterministic test cases, variable table lengths, receiver storage and exclusive-port behavior |
| Fonts/themes | Font assets/licenses, bundle paths and missing-library fallback; all 32 layout/theme/size combinations in Linux UI tests |
| Tooltips/grid | Keyboard anchors, theme changes, tooltip colors and callback cleanup |
| Guided/full layouts | Navigation and mode changes preserve the selected page and unsaved settings |
| Offline guide | Search, no-results handling, current paths and related-page navigation without exposing tokens |
| Complete desktop | Both layouts, all three output formats, manual data, real worker threads, History, actual HTTP/shared bytes and original archives |
| Nine-check proof | Three formats, outage buffering, restart recovery, duplicates, malformed data, completion gate and refusal isolation |

The native GitHub workflow runs this verifier on Linux, Windows and macOS. Linux
includes desktop tests under Xvfb; Windows/macOS record desktop tests as skipped.
Windows DPAPI is tested on Windows; POSIX file permissions are tested on Unix.
Each Windows/macOS frozen executable also runs the live proof before packaging.
Debian packages can be extracted and tested without installing into the system.

Automated evidence covers the stated cases. Customer hardware, proprietary MES
rules, production loads, power-loss recovery, native desktop accessibility and
installer signing still need their own validation. A skipped check is not a pass.
