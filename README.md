# p-chart

`p-chart` is a PySide6 desktop tool for reshaping Excel/CSV data with
`pandas.wide_to_long`, previewing the reshaped table, and creating interactive
Plotly scatter and box plots.

Version: `v2.0`  
Author: `cnwang`  
Date: `2024/04`

## Features

- Load CSV files directly.
- Load Excel workbooks and choose a worksheet.
- Reshape wide data to long data with `pandas.wide_to_long`.
- User-defined `stubnames`, `suffix regex`, and suffix column name.
- Mark columns that match `stubnames + suffix` in the column list.
- Enable the Plot tab only after `wide_to_long` succeeds.
- Preserve source column order in the preview output.
- Save reshaped data as CSV or Excel.
- Create Plotly scatter charts with configurable X/Y/series/color/symbol/size/opacity.
- Create Plotly box plots with configurable Y, group 1, and group 2.
- Add horizontal and vertical reference lines.
- Auto-fill X/Y ranges, 3-sigma reference lines, and Y statistics.
- Pick reference line color with a color picker.
- Adjust reference line opacity/width with a 0.0 to 1.0 control.
- Select Plotly theme, including a customized transparent theme.
- Adjust plot width and height.
- Download the current Plotly chart as a self-contained HTML file.
- Use `CascadiaNextTC.wght.ttf` as the application font.
- Use `AppIcon.appiconset` as the application/window icon.

## Requirements

Python dependencies are listed in `requirements.txt`:

```bash
PySide6
PySide6-WebEngine
pandas
plotly
openpyxl
PyInstaller
```

## Installation

Create and activate a virtual environment, then install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python app.py
```

## Package

Build the desktop app with PyInstaller:

```bash
.venv/bin/python -m PyInstaller p-chart.spec
```

For the Windows 10 release build, run the equivalent command on Windows:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller p-chart.spec
```

The spec includes `mainwindow.ui`, `CascadiaNextTC.wght.ttf`,
`CascadiaCode.ttf`, `w2l.png`, the runtime app icon PNG, and `app.ico` for the
Windows executable icon.

## Wide To Long Workflow

1. Click `Browse...` and choose a CSV or Excel file.
2. For CSV files, the data loads immediately and the `Load` button is disabled.
3. For Excel files, choose a worksheet and click `Load`.
4. Enter `Stubnames`, separated by commas.
5. Enter a `Suffix regex`.
6. Enter the suffix output column name.
7. Check `Columns (a/b)` before reshaping:
   - `a` is the number of columns matching `stubnames + suffix`.
   - `b` is the total number of source columns.
8. Matching columns are marked green.
9. Click `wide_to_long` to reshape the data.
10. The Plot tab is enabled only after this reshape succeeds.

Example:

```text
Source columns: AVG_T, AVG_B, AVG_C, AVG_L, AVG_R
Stubnames: AVG_
Suffix regex: [TBCLR]
Suffix column: site
```

The output keeps source columns in their original order, removes matched wide
columns, then appends:

```text
site, AVG_
```

## Scatter Workflow

The Scatter tab is enabled only after `wide_to_long` succeeds. If the file,
`stubnames`, suffix regex, or suffix column name changes, the previous reshaped
data is invalidated and plot tabs are disabled again.

1. Choose X and Y columns.
2. X/Y titles are filled automatically from the selected columns.
3. When both X and Y are selected, the chart is drawn automatically.
4. The plot title is generated as:

```text
x-title vs y-title
```

If `series`, `size`, `color`, `opacity`, or `symbol` are selected, the title
adds:

```text
by series=..., size=..., color=...
```

5. Click `Refresh Plot` to redraw manually when needed.
6. Changing combo boxes, theme, line opacity/width, plot width, or plot height
   refreshes an existing plot automatically.
7. Editing plot title, axis titles, X/Y ranges, H lines, or V lines redraws on
   Enter or when focus leaves the field.

## Reference Lines

- `H lines`: comma-separated Y values.
- `V lines`: comma-separated X values.
- `Color`: opens a color picker.
- `Width`: uses a 0.0 to 1.0 value for both reference line width and line/text
  opacity.
- `Auto`: fills X/Y ranges, V/H lines, and statistics from the current X/Y data:
  - X range is based on V lines expanded by 10%.
  - Y range is based on H lines expanded by 10%.
  - V lines are X average +/- 3 sigma.
  - H lines are Y average +/- 3 sigma.

Reference line labels use the axis title:

```text
y-title=value
x-title=value
```

The line and label color follow the selected line color and the selected
opacity/width value.

## Statistics

The statistics label can be selected and copied. It is based on the current Y
data and shows:

```text
y-title average = ..., stdev = ...
```

If `H lines` contains 2 values, they are treated as lower/upper spec limits and
the target is their midpoint. If `H lines` contains 3 values, the smallest and
largest values are the spec limits and the middle value is the target. In those
cases the label also shows:

```text
target = ..., range = ..., Ca = ..., Cp = ..., Cpk = ...
```

`Ca`, `Cp`, and `Cpk` are displayed with 2 decimal places.

## Boxplot Workflow

The Boxplot tab is also enabled only after `wide_to_long` succeeds.

1. Choose a Y column.
2. Choose `Group 1` for the main box groups.
3. Optionally choose `Group 2` to split each `Group 1` category into grouped
   boxes, such as `1 2 1 2 1 2`.
4. The X axis title is built from the selected group columns, such as
   `Group 1` or `Group 1 / Group 2`.
5. Choose `Points` to show only outliers, all points, jittered points, or no
   points.
6. The chart is drawn automatically when enough selections are available.
7. The Boxplot tab supports Y range, Y lines, Auto statistics, theme, plot size,
   line color, line opacity/width, and HTML export.

Boxplot does not use V lines or X range controls.

## Plotly HTML Export

Click `Download HTML` to save the current chart as a self-contained HTML file.
The exported file includes Plotly JS and can be opened without the app.

## Included Assets

- `mainwindow.ui`: Qt Designer UI file.
- `CascadiaNextTC.wght.ttf`: application font.
- `AppIcon.appiconset`: application icon source images.
- `app.ico`: Windows executable icon for PyInstaller.
- `w2l.png`: help image shown by `what is wide_to_long`.
- `demo.csv`, `demo2.csv`: sample data files.
