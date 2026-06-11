# p-chart

`p-chart` is a PySide6 desktop tool for reshaping CSV/Excel data with
`pandas.wide_to_long` and creating interactive Plotly scatter and box plots.

Version: `v2.8.0`
Author: `cnwang`  
License: MIT

## Features

- Load CSV or Excel worksheets. accept "log-type", "KGD", "mapping", "log with heading info"
- Reshape wide data to long data with custom `stubnames`, suffix regex, and
  suffix column name.
- Preview and export reshaped data as CSV or Excel.
- Create Plotly scatter charts with X/Y, series, color, symbol, size, opacity,
  reference lines, auto ranges, and Y statistics.
- Create Plotly box plots with Y, Group 1, Group 2, sorted X categories, point
  display options, Y lines, and selectable per-box annotations.
- Create wafer maps with frame/die layout, matplotlib contour maps, and KGD-like
  die heatmaps.
- Compare CSV/TXT log files with selectable X, Y1, and Y2 columns in overlay
  or subplot layouts.
- Support drag-and-drop loading, date-aware scatter X axes, and copyable pivot
  summary tables.
- Use embedded Qt WebEngine for Plotly charts, with system-browser fallback for
  Remote Desktop sessions or `--no-webengine` / `-W`.
- Export Plotly charts as offline HTML that uses the bundled `plotly.min.js`.
- Package as a Windows 10 desktop app with PyInstaller.

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python app.py
```

To force the system-browser Plotly fallback:

```bash
.venv/bin/python app.py --no-webengine
.venv/bin/python app.py -W
```

## Package

```bash
.venv/bin/python -m PyInstaller p-chart.spec
```

For Windows 10 release builds, run the equivalent command on Windows:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller p-chart.spec
```

The spec bundles the Windows/macOS UI files, local Plotly JavaScript, font, help
image, app icons, and Windows `.ico`.

## Workflow

1. Load a CSV or Excel worksheet.
2. app will auto find out  `Stubnames`, `Suffix regex`, and suffix output column.
3. Confirm auto generated matching columns in `Columns (a/b)`.
4. Click `wide_to_long`.
5. Use the enabled Scatter, Boxplot, Wafermap, or Log tab.

Scatter charts draw automatically after X and Y are selected. Reference lines
support color, opacity/width, auto ranges, and Y-based statistics including Ca,
Cp, and Cpk when H lines define spec limits. A regression line with formula also available.

Boxplot charts draw automatically after Y and grouping choices are selected.
Group categories are naturally sorted. Annotation rows are selected with
checkboxes and are displayed in a fixed order: `N`, `max`, `1/4Q`, `median`,
`average`, `3/4Q`, `min`, `standard deviation`, and `range`. Numeric formatting
accepts Python specifiers such as `.2f`, `.3g`, `,.1f`, or `{value:.2f}`.

Wafermap heatmap mode treats the selected X/Y/Z columns as KGD-style
`column / row / value` data. The map origin is the lower-left die. Data
coordinates are normalized before plotting:

```python
col_on_map = col_on_data - min(col_on_data) + 1
row_on_map = row_on_data - min(row_on_data) + 1
```

So the smallest column and row in the loaded data are always drawn at map
position `(1, 1)`.
without X,Y,Z selected, a wafer map with frame and die map will be generated.

Log charts use the Data-tab dataset as the primary file and can compare
additional dropped CSV/TXT log files. Select X plus one or more Y1/Y2 columns,
then choose overlap or subplot mode and click Refresh Plot. With one selected
file, overlap draws all Y1/Y2 items in one chart; subplot splits by Y1 item.
With multiple selected files, overlap splits by Y1 item and draws every file in
the same subplot, while subplot mode splits by file. If no file is selected,
Refresh Plot clears the plot area and export state.

## Files

- `app.py`: application entry point.
- `mainwindow-win.ui`, `mainwindow-mac.ui`: Qt Designer UI files selected
  automatically at startup. Non-Windows/non-macOS platforms use the mac UI.
- `plotly_local.py`: local Plotly HTML helper for offline chart rendering.
- `tabData.py`, `tabScatter.py`, `tabBoxplot.py`: tab controllers.
- `tabWafermap.py`, `wafermap_core.py`: wafer map controller and geometry logic.
- `tabLog.py`: multi-file log chart controller.
- `p-chart.spec`: PyInstaller build config.
- `LICENSE`: MIT License.

## License

MIT. See `LICENSE`.
