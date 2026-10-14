Title: Integrate the desktop demo and add operator appearance and preflight controls

The demo launcher and tests referenced a missing demo package, while the committed
LineBridge simulation exercised a separate engine. CNCBridge now opens its own
integrated demo with generated CSVs, the production extraction/delivery engine,
an HTTP MES inbox, outage and refusal controls, and fixed presentation cases.

The desktop UI adds a getting-started guide, explicit preflight results, three
themes and four text sizes. Font and color changes cover the spreadsheet, preview,
history and log. Scrollable settings keep controls reachable at larger sizes.

A headless verification command retains evidence for all formats, exact shared/API
bytes, manual inputs, original archives, duplicate blocking, malformed files,
incomplete writes, HTTP 503 recovery after restart and HTTP 422 isolation.
Verification fails if the HTTP receiver cannot start or a delivery check fails.

Validation:

- Source compilation and the available unit tests pass.
- Loopback HTTP tests are skipped only when local sockets are denied.
- DPAPI encryption is checked on Windows.
- On Kali, 62 tests ran: 61 passed and one Windows-only DPAPI check was skipped.
- Native Tk smoke checks pass all 12 theme/text-size combinations under Xvfb.
- All nine real HTTP integration proof checks pass. Debian packaging builds locally.

Additional hardening fixes column-key collisions, reused output filenames,
inconsistent file snapshots, malformed settings and HTTP response resource leaks.
Atomic writes are durable, POSIX config files are private, uploads include stable
idempotency keys, and new queues retain their original destination. Native Windows
and macOS build jobs and a Debian packaging script are included.

This is a release candidate, not a claim of zero bugs or industrial certification.
See docs/RELEASE_READINESS.md for site qualification, crash reconciliation and
signing limitations. Native artifacts require successful workflow runs.

Launch: python -m cncbridge --demo
Proof: python -m cncbridge --demo-check
Tests: python -m unittest discover -s tests -v
