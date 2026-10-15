CNCBridge's demo entry point referenced a missing package, and its separate web
simulator did not exercise the native application. The integrated demo now uses
the production extraction and delivery engine with generated machine CSVs, a
local HTTP MES, outage/refusal controls, and retained verification evidence.

The desktop adds Guided and Full workspace interfaces over the same profile,
numbered navigation, preflight checks, four themes and four text sizes. A searchable
offline manual explains folders, ranges, delivery, operation and troubleshooting;
the Markdown manual also ships with packages.
UI work from the supplied friend ZIP is integrated through the existing theme
system: Industrial palette, bundled Roboto fonts with OFL licenses, contextual
tooltips, keyboard range selection, metric cards, empty states and horizontal
history/log scrolling. Font assets ship in every package. Simulator entry points
share the current demo, and shutdown cancels pending window callbacks. The demo
selects its actual notebook page, fixing a launch failure exposed by full UI tests.

Reliability fixes prevent column-name collisions and output overwrites, use one
file snapshot for fingerprinting/extraction, validate settings, close HTTP error
responses, publish durable writes, send stable idempotency keys and bind new
queued payloads to their original destination. Demo listeners require exclusive
ports on Windows/macOS. The ZIP's older engine, private data and Git metadata are
excluded; docs/FRIEND_ZIP_MERGE.md records the integration decisions.

Validation includes 77 tests on Kali with native Tk enabled, with the Windows DPAPI
check skipped. GUI coverage includes all 32 layout/theme/size combinations, keyboard
anchors, tooltip cleanup, mode changes and guide search. A complete desktop test
uses both layouts and all three formats with real shared/HTTP delivery and archives.
CLI tests cover locking, error exits and a real headless demo. A separate 19-test
suite checks the retained LineBridge reference implementation. The nine HTTP checks cover bytes on both
routes, original archives, outage/restart recovery, duplicates, malformed files,
incomplete writes and isolated file refusals. The three-OS workflow reruns native
tests, verifies delivery and builds Windows EXEs, a Debian package and a macOS DMG.

Version 1.2.0 is an unsigned release candidate. See docs/RELEASE_READINESS.md for
site qualification, crash reconciliation, signing and other deployment gates.

Launch: python -m cncbridge --demo
Proof: python -m cncbridge --demo-check
Tests and retained per-test report: xvfb-run -a python scripts/verify_all.py --gui
