# Release candidate, not production certification

## Design boundaries

The Tk views collect settings and render status on the UI thread. Extraction,
rendering, the SQLite audit ledger, and delivery live in the engine. File storage
has one atomic-write implementation. Themes are separate from machine profiles.
The demo supplies invented machine files and an HTTP receiver but uses the same
engine as the application; the earlier web simulator is not the native engine.

## Verification and packaging

Run `python -m unittest discover -s tests -v` and
`python -m cncbridge --demo-check`. The proof creates an isolated workspace and
retains exact shared/HTTP payloads, archived originals, a ledger and verification.json.
Linux UI checks: `CNCBRIDGE_GUI_TESTS=1 xvfb-run -a python -m unittest discover -s tests -v`.
The UI test exercises all three themes and four sizes; it is not a substitute for
operator usability testing, screen-reader evaluation, or a visual layout review.

Build `.deb` with `python scripts/package.py --deb` on Debian/Kali/Ubuntu.
Build Windows `.exe` or macOS `.dmg` with `python scripts/package.py` on that OS,
after installing requirements and PyInstaller 6.19.0. Each frozen build runs the
real HTTP proof against the bundled executable. CI runs native-platform tests and
uploads packages and proof artifacts. No package is automatically published as a release.
The macOS build targets the architecture of its runner, not a universal binary.
Windows installers and Apple signing/notarization are not configured: these are
unsigned portable executables/app bundles, not trusted production installers.

## Deployment gates still required

- Test representative customer CSVs and obtain MES agreement on schema, auth,
  HTTP success semantics, retry policy and idempotency. A stable Idempotency-Key
  is sent, but the customer must enforce it; exactly-once delivery is not promised.
- Use HTTPS for real API delivery. Plain HTTP is supported for the local demo and
  existing installations; it does not protect credentials. Windows encrypts the
  token with machine-scope DPAPI. On Linux/macOS configuration is mode 0600 but
  is not encrypted. Protect the user account and storage; do not share config files.
- Newly queued payloads are bound to their original destination/body mode. Changing
  these settings holds that queue until the old destination is restored. Legacy
  queues without route metadata retain the earlier behavior; drain them before migration.
- Atomic publication avoids partial destination files and duplicate output names.
  It is not a transaction spanning source archive, SQLite and both destinations.
  Abrupt power loss between those operations still requires reconciliation using
  archives, outboxes and history. Test backup availability and recovery before rollout.
- An unchanged modification time is a heuristic, especially on Linux. Configure
  the machine to write a temporary extension then rename to .csv when complete.
  Do not have multiple data directories monitor the same machine folder.
- Load/soak tests at customer rates, network-share disconnects, disk exhaustion,
  real-machine trials, signing, OS validation and operational retention policies
  remain required. Large input files are currently read into memory.

No test suite establishes that software has zero bugs or is certified for
industrial deployment. Keep an independent backup and stage rollout with operators.
