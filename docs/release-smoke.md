# Release Smoke

Run these checks before publishing a p-chart release branch.

## 1. Headless Gate

```bash
.venv/bin/python scripts/release_check.py
```

This checks:

- Python architecture and repo `.venv` usage.
- PySide6 / shiboken package version consistency.
- PySide6 QtCore + QtWebEngine import smoke.
- Python syntax with `py_compile`.
- `mainwindow-win.ui` and `mainwindow-mac.ui` XML validity.
- `require_child()` objectName coverage in both UI files.
- App and README version text consistency.
- Demo data files and basic pandas load paths.

If this fails with `Incompatible processor ... neon`, the app has not reached
any tab code yet. Rebuild the local `.venv`/PySide6 install before trusting GUI
smoke results.

## 2. GUI Activation Smoke

```bash
.venv/bin/python scripts/gui_smoke.py
```

This starts the app and activates every tab once. It is useful for catching
startup and tab-lifecycle crashes.

## 3. GUI Render Smoke

Controller/data path without Qt WebEngine:

```bash
.venv/bin/python scripts/gui_smoke.py --no-webengine --render
```

Release render path with in-app Qt WebEngine:

```bash
.venv/bin/python scripts/gui_smoke.py --render
```

The render smoke loads `demo/06191623.xlsx`, then renders Scatter, Boxplot,
Wafermap, Contour, and Log from `x`, `y`, and `value`. If the process aborts, the last
`[GUI-SMOKE]` line identifies the active tab/action.

## 4. Manual IDIOT Checks

- Paste an Excel block where the first row is text headers and later rows are
  numeric.
- Paste a block with a non-numeric value below the first row and verify all four
  alarm choices.
- Right-click row and column headers to mark and delete rows/columns.
- Transfer to TabData and verify downstream tabs see the data.
