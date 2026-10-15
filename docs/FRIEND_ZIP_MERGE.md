# Friend ZIP integration

Source: user-supplied Downloads/CNCBridge.zip, inspected 2026-10-15.
SHA-256: f63095a3a46acdd4308adcbc7547282728e52f4007b69574f7eb0065ea11a2d8.
The pre-merge project is preserved in Git at 0583698.

## Incorporated

- The industrial teal palette is an additional selectable theme, keeping Light,
  Dark and High contrast and all four font sizes.
- Roboto/Roboto Mono assets and their supplied SIL OFL licenses; process-private
  Windows/macOS/Linux font registration with safe installed-font fallbacks.
- Mouse and keyboard-focus tooltips, tab hints, keyboard-accessible toolbar,
  and grid arrow-key selection with Shift extension. Tooltip cleanup and colors
  were adapted to the existing application lifecycle and theme settings.
- Metric cards, clearer preview/history/log empty states, history/log horizontal
  scrolling, and a Simulation toolbar shortcut into the integrated demo.
- The friend's `python -m demo.run_demo` entry point now delegates to the current
  demo instead of starting a second, incompatible simulator implementation.
- All package build paths include the fonts and license files.

## Deliberately preserved from our project

The ZIP's engine and configuration match the older upstream baseline (ignoring
line endings). Replacing ours would remove durability, validation, queue binding,
snapshot and naming fixes. Those modules remain unchanged by this merge.
The configuration version is bumped to 1.1.1 for the merged packages.
Our integrated demo, receiver, proof suite and generated fixtures stay together;
the ZIP's competing standalone MES/machine/control implementations are not copied.
Our per-page scroll layout stays in place: global mouse-wheel handlers from the
ZIP would interfere with the spreadsheet and log scrolling.

No Git metadata/hooks, machine configuration, ledgers, logs, caches, demo session
data or old executables were imported. The original ZIP was not modified.

## Verification

The GUI suite checks every theme/font-size combination, keyboard range anchors,
theme changes, tooltip colors and destruction cleanup. Font tests check source
assets/licenses, frozen-bundle lookup and missing-library fallbacks. Existing
engine/delivery tests and the nine-check real HTTP proof remain required.
Artifacts from the previous release candidate do not contain this merge until rebuilt.
