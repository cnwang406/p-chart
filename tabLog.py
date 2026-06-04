import re
import tempfile
import webbrowser
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from pandas.api.types import is_datetime64_any_dtype
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from async_helpers import BackgroundTaskMixin
from loading_overlay import LoadingOverlay
from plot_export_helpers import save_plotly_png_and_copy_to_clipboard
from plot_templates import CUSTOM_TEMPLATE_NAME, FOR_PPT_TEMPLATE_NAME
from plotly_local import local_plotly_html
from qt_helpers import require_child

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEB_ENGINE_AVAILABLE = True
except ImportError:
    QWebEngineView = None
    WEB_ENGINE_AVAILABLE = False


class CheckableColumnCombo:
    def __init__(self, comboBox: QComboBox, placeholder: str, onChanged=None) -> None:
        self.comboBox = comboBox
        self.placeholder = placeholder
        self.onChanged = onChanged
        self.model = QStandardItemModel(comboBox)
        self.comboBox.setModel(self.model)
        self.comboBox.setEditable(True)
        self.comboBox.lineEdit().setReadOnly(True)
        self.comboBox.lineEdit().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.comboBox.view().pressed.connect(self._toggle_item)
        self.model.itemChanged.connect(self._update_display_text)
        self._updating = False
        self._update_display_text()

    def set_items(self, columnNames: list[str], checkedColumns: set[str] | None = None) -> None:
        checkedColumns = checkedColumns or set()
        self._updating = True
        self.model.clear()
        for columnName in columnNames:
            item = QStandardItem(columnName)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(
                Qt.CheckState.Checked if columnName in checkedColumns else Qt.CheckState.Unchecked,
                Qt.ItemDataRole.CheckStateRole,
            )
            self.model.appendRow(item)
        self._updating = False
        self._update_display_text()

    def checked_items(self) -> list[str]:
        checkedItems = []
        for rowIndex in range(self.model.rowCount()):
            item = self.model.item(rowIndex)
            if item.checkState() == Qt.CheckState.Checked:
                checkedItems.append(item.text())
        return checkedItems

    def _toggle_item(self, index) -> None:
        item = self.model.itemFromIndex(index)
        if item is None:
            return
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def _update_display_text(self, *_args) -> None:
        if self._updating:
            return
        checkedItems = self.checked_items()
        selectedCount = len(checkedItems)
        if selectedCount <= 0:
            displayText = self.placeholder
        elif selectedCount == 1:
            displayText = checkedItems[0]
        else:
            displayText = f'{selectedCount} selected'
        self.comboBox.lineEdit().setText(displayText)
        if self.onChanged is not None:
            self.onChanged()


class TabLogWidget(BackgroundTaskMixin):
    _dateColumnPattern = re.compile(r'(date|timestamp|time)', re.IGNORECASE)

    def __init__(self, rootWidget: QWidget, preferWebEngine: bool = True) -> None:
        self.rootWidget = rootWidget
        self.tabDataWidget = None
        self.preferWebEngine = preferWebEngine
        self.useExternalBrowser = True

        self.xComboBox = require_child(rootWidget, QComboBox, 'logXComboBox')
        self.y1ComboBox = require_child(rootWidget, QComboBox, 'logY1ComboBox')
        self.y2ComboBox = require_child(rootWidget, QComboBox, 'logY2ComboBox')
        self.plotTitleLineEdit = require_child(rootWidget, QLineEdit, 'logTitleLineEdit')
        self.y1TitleLineEdit = require_child(rootWidget, QLineEdit, 'logY1TitleLineEdit')
        self.y2TitleLineEdit = require_child(rootWidget, QLineEdit, 'logY2TitleLineEdit')
        self.plotButton = require_child(rootWidget, QPushButton, 'logPlotButton')
        self.downloadHtmlButton = require_child(rootWidget, QPushButton, 'logDownloadHtmlButton')
        self.downloadPngButton = require_child(rootWidget, QPushButton, 'logDownloadPngButton')
        self.plotlyThemeComboBox = require_child(rootWidget, QComboBox, 'logPlotlyThemeComboBox')
        self.plotWidthSpinBox = require_child(rootWidget, QSpinBox, 'logPlotWidthSpinBox')
        self.plotHeightSpinBox = require_child(rootWidget, QSpinBox, 'logPlotHeightSpinBox')
        self.legendFontSizeSpinBox = require_child(rootWidget, QSpinBox, 'logLegendFontSizeSpinButton')
        self.plotAreaWidget = require_child(rootWidget, QWidget, 'logPlotAreaWidget')
        self.statusLabel = require_child(rootWidget, QLabel, 'logStatusLabel')

        self.currentPlotHtml = ''
        self.currentPlotFigure = None
        self.currentPlotFilePath = ''
        self.currentViewerFilePath = ''
        self._browserViewerOpened = False

        self.y1ColumnCombo = CheckableColumnCombo(
            self.y1ComboBox,
            'Select Y1 columns',
            self._redraw_existing_plot,
        )
        self.y2ColumnCombo = CheckableColumnCombo(
            self.y2ComboBox,
            'Select Y2 columns',
            self._redraw_existing_plot,
        )

        self._configure_plot_area()
        self.loadingOverlay = LoadingOverlay(self.plotAreaWidget)
        self._configure_defaults()
        self._configure_signals()

    def _configure_plot_area(self) -> None:
        if self.preferWebEngine and WEB_ENGINE_AVAILABLE and QWebEngineView is not None:
            try:
                self.chartView = QWebEngineView(self.plotAreaWidget)
                self.useExternalBrowser = False
            except Exception:
                self.chartView = QTextBrowser(self.plotAreaWidget)
                self.chartView.setOpenExternalLinks(True)
                self.useExternalBrowser = True
        else:
            self.chartView = QTextBrowser(self.plotAreaWidget)
            self.chartView.setOpenExternalLinks(True)
            self.useExternalBrowser = True

        plotLayout = self.plotAreaWidget.layout() or QVBoxLayout(self.plotAreaWidget)
        plotLayout.setContentsMargins(0, 0, 0, 0)
        plotLayout.addWidget(self.chartView)

    def _switch_to_external_browser_view(self) -> None:
        self.useExternalBrowser = True
        if isinstance(self.chartView, QTextBrowser):
            self.chartView.setOpenExternalLinks(True)
            return

        plotLayout = self.plotAreaWidget.layout() or QVBoxLayout(self.plotAreaWidget)
        plotLayout.removeWidget(self.chartView)
        self.chartView.deleteLater()
        self.chartView = QTextBrowser(self.plotAreaWidget)
        self.chartView.setOpenExternalLinks(True)
        plotLayout.addWidget(self.chartView)

    def _configure_defaults(self) -> None:
        self.plotlyThemeComboBox.addItems([
            'plotly',
            'plotly_white',
            'plotly_dark',
            'ggplot2',
            'seaborn',
            'simple_white',
            'presentation',
            'gridon',
            FOR_PPT_TEMPLATE_NAME,
            CUSTOM_TEMPLATE_NAME,
            'none',
        ])
        self.plotlyThemeComboBox.setCurrentText('plotly')
        self.plotWidthSpinBox.setRange(200, 5000)
        self.plotWidthSpinBox.setSingleStep(50)
        if self.plotWidthSpinBox.value() <= 200:
            self.plotWidthSpinBox.setValue(max(200, self.plotAreaWidget.width()))
        self.plotHeightSpinBox.setRange(200, 5000)
        self.plotHeightSpinBox.setSingleStep(50)
        if self.plotHeightSpinBox.value() <= 200:
            self.plotHeightSpinBox.setValue(max(200, self.plotAreaWidget.height()))
        self.legendFontSizeSpinBox.setRange(6, 32)
        self.legendFontSizeSpinBox.setValue(self.legendFontSizeSpinBox.value() or 12)
        self.statusLabel.setText('Log plot status messages appear here.')

    def _configure_signals(self) -> None:
        self.plotButton.clicked.connect(self._draw_plot)
        self.downloadHtmlButton.clicked.connect(self._download_html)
        self.downloadPngButton.clicked.connect(self._download_png)
        self.plotlyThemeComboBox.currentTextChanged.connect(self._redraw_existing_plot)
        self.plotWidthSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.plotHeightSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.legendFontSizeSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.xComboBox.currentTextChanged.connect(self._update_plot_title)
        self.plotTitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.y1TitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.y2TitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)

    def set_tab_data(self, tabDataWidget) -> None:
        self.tabDataWidget = tabDataWidget
        if hasattr(self.tabDataWidget, 'add_data_changed_callback'):
            self.tabDataWidget.add_data_changed_callback(self._refresh_column_options)
        self._refresh_column_options()

    def _refresh_column_options(self) -> None:
        if self.tabDataWidget is None:
            return
        dataFrame = self.tabDataWidget.get_plot_data()
        columnNames = list(dataFrame.columns.astype(str))
        currentX = self.xComboBox.currentText().strip()
        currentY1 = set(self.y1ColumnCombo.checked_items())
        currentY2 = set(self.y2ColumnCombo.checked_items())

        self.xComboBox.clear()
        self.xComboBox.addItems(columnNames)
        if currentX in columnNames:
            self.xComboBox.setCurrentText(currentX)
        elif columnNames:
            self.xComboBox.setCurrentText(self._default_x_column(columnNames))

        self.y1ColumnCombo.set_items(columnNames, currentY1.intersection(columnNames))
        self.y2ColumnCombo.set_items(columnNames, currentY2.intersection(columnNames))
        self._sync_default_axis_titles()
        self._update_plot_title()
        self._redraw_existing_plot()

    def _default_x_column(self, columnNames: list[str]) -> str:
        for columnName in columnNames:
            if self._dateColumnPattern.search(columnName):
                return columnName
        return columnNames[0] if columnNames else ''

    def _sync_default_axis_titles(self) -> None:
        if not self.y1TitleLineEdit.text().strip() or self.y1TitleLineEdit.text().strip() == 'log Title':
            self.y1TitleLineEdit.setText('Y1')
        if not self.y2TitleLineEdit.text().strip() or self.y2TitleLineEdit.text().strip() == 'log Title':
            self.y2TitleLineEdit.setText('Y2')

    def _build_auto_plot_title(self) -> str:
        xColumn = self.xComboBox.currentText().strip()
        if not xColumn:
            return 'Log Plot'
        return f'{xColumn} log plot'

    def _update_plot_title(self, *_args) -> None:
        if not self.plotTitleLineEdit.text().strip() or self.plotTitleLineEdit.text().strip() == 'log Title':
            self.plotTitleLineEdit.setText(self._build_auto_plot_title())

    def _redraw_existing_plot(self, *_args) -> None:
        if self.currentPlotHtml:
            self._draw_plot()

    def _draw_plot_when_ready(self, *_args) -> None:
        if self.currentPlotHtml:
            self._draw_plot()

    def _draw_plot(self) -> None:
        if self.tabDataWidget is None:
            self._set_status('No data source attached to log tab.', error=True)
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        if dataFrame.empty:
            self._set_status('No data available for log plot.', error=True)
            return

        xColumn = self.xComboBox.currentText().strip()
        y1Columns = self.y1ColumnCombo.checked_items()
        y2Columns = self.y2ColumnCombo.checked_items()
        if not xColumn or xColumn not in dataFrame.columns:
            self._set_status('Choose a valid X column first.', error=True)
            return
        if not y1Columns and not y2Columns:
            self._set_status('Choose at least one Y1 or Y2 column first.', error=True)
            return

        missingColumns = [columnName for columnName in [*y1Columns, *y2Columns] if columnName not in dataFrame.columns]
        if missingColumns:
            self._set_status(f'Selected column not found: {missingColumns[0]}', error=True)
            return

        plotData = dataFrame[[xColumn, *dict.fromkeys([*y1Columns, *y2Columns])]].copy()
        xIsDate = self._is_date_series(plotData[xColumn])
        if xIsDate:
            plotData[xColumn] = pd.to_datetime(plotData[xColumn], errors='coerce')
        for columnName in [*y1Columns, *y2Columns]:
            plotData[columnName] = pd.to_numeric(plotData[columnName], errors='coerce')
        plotData = plotData.dropna(subset=[xColumn], how='any')
        if plotData.empty:
            self._set_status('X column has no usable values.', error=True)
            return

        plotTitle = self.plotTitleLineEdit.text().strip() or self._build_auto_plot_title()
        y1Title = self.y1TitleLineEdit.text().strip() or 'Y1'
        y2Title = self.y2TitleLineEdit.text().strip() or 'Y2'
        plotlyTheme = self.plotlyThemeComboBox.currentText().strip()
        plotlyTheme = None if plotlyTheme == 'none' else plotlyTheme or 'plotly'
        fig = go.Figure()

        for columnName in y1Columns:
            validData = plotData.dropna(subset=[columnName])
            if validData.empty:
                continue
            fig.add_trace(go.Scatter(
                x=validData[xColumn],
                y=validData[columnName],
                mode='lines+markers',
                name=columnName,
                yaxis='y',
            ))
        for columnName in y2Columns:
            validData = plotData.dropna(subset=[columnName])
            if validData.empty:
                continue
            fig.add_trace(go.Scatter(
                x=validData[xColumn],
                y=validData[columnName],
                mode='lines+markers',
                name=columnName,
                yaxis='y2',
            ))

        if not fig.data:
            self._set_status('Selected Y columns have no numeric data.', error=True)
            return

        fig.update_layout(
            title=plotTitle,
            template=plotlyTheme,
            width=self.plotWidthSpinBox.value(),
            height=self.plotHeightSpinBox.value(),
            xaxis=dict(title=xColumn),
            yaxis=dict(title=y1Title),
            legend=dict(
                orientation='v',
                y=1,
                x=1.14,
                title_text='Legend',
                font=dict(size=self.legendFontSizeSpinBox.value()),
                title_font=dict(size=self.legendFontSizeSpinBox.value()),
                bordercolor='rgba(0,0,0,0.15)',
                borderwidth=1,
            ),
            margin={'t': 60, 'r': 180 if y2Columns else 80, 'l': 70, 'b': 60},
        )
        if y2Columns:
            fig.update_layout(yaxis2=dict(title=y2Title, overlaying='y', side='right'))
        if xIsDate:
            fig.update_xaxes(tickformat='%Y/%m/%d %H:%M')
        self._render_figure(fig)

    def _is_date_series(self, series: pd.Series) -> bool:
        nonNullSeries = series.dropna()
        if nonNullSeries.empty:
            return False
        if is_datetime64_any_dtype(nonNullSeries):
            return True
        if pd.api.types.is_numeric_dtype(nonNullSeries):
            return False
        parsedDates = pd.to_datetime(nonNullSeries, errors='coerce')
        return parsedDates.notna().mean() >= 0.8

    def _render_figure(self, figure) -> None:
        self.currentPlotFigure = figure
        self.currentPlotHtml = ''
        self._set_status('Rendering log HTML...')
        self.loadingOverlay.show('Loading...')

        def work() -> dict[str, str]:
            result = {'fullHtml': local_plotly_html(figure, fullHtml=True)}
            if not self.useExternalBrowser:
                result['embeddedHtml'] = local_plotly_html(figure, fullHtml=False)
            return result

        self._activeRenderTaskId = self._start_background_task(
            work,
            self._on_render_figure_finished,
            self._on_render_figure_failed,
        )

    def _on_render_figure_finished(self, taskId: int, result: dict[str, str]) -> None:
        if taskId != getattr(self, '_activeRenderTaskId', None):
            return
        self.loadingOverlay.hide()
        self.currentPlotHtml = result['fullHtml']
        if not self.useExternalBrowser:
            assetsDir = Path(__file__).resolve().parent
            try:
                baseUrl = QUrl.fromLocalFile(str(assetsDir) + '/')
                self.chartView.setHtml(result.get('embeddedHtml', self.currentPlotHtml), baseUrl)
                self._set_status('Log plot created successfully.')
                return
            except Exception:
                self._switch_to_external_browser_view()

        self.currentPlotFilePath = str(Path(tempfile.gettempdir()) / 'p-chart-log.html')
        with open(self.currentPlotFilePath, 'w', encoding='utf-8') as htmlFile:
            htmlFile.write(self.currentPlotHtml)

        plotUri = Path(self.currentPlotFilePath).resolve().as_uri()
        self.currentViewerFilePath = str(Path(tempfile.gettempdir()) / 'p-chart-log-viewer.html')
        viewerUri = Path(self.currentViewerFilePath).resolve().as_uri()
        viewerHtml = f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>p-chart Log</title>
  <style>
    html, body, iframe {{ width: 100%; height: 100%; margin: 0; border: 0; overflow: hidden; }}
  </style>
</head>
<body>
  <iframe id="plotFrame" src="{plotUri}"></iframe>
  <script>
    const plotUri = {plotUri!r};
    const frame = document.getElementById('plotFrame');
    setInterval(() => {{
      frame.src = `${{plotUri}}?t=${{Date.now()}}`;
    }}, 2000);
  </script>
</body>
</html>
'''
        with open(self.currentViewerFilePath, 'w', encoding='utf-8') as htmlFile:
            htmlFile.write(viewerHtml)

        if not self._browserViewerOpened:
            webbrowser.open(viewerUri)
            self._browserViewerOpened = True
        self.chartView.setHtml(
            '<div style="font-family: Cascadia Next TC, sans-serif; font-size: 14px; padding: 16px;">'
            '<p>Log plot is shown in the system browser.</p>'
            f'<p><a href="{viewerUri}">Open log browser viewer</a></p>'
            '<p>The viewer reloads the latest Plotly HTML automatically. Use Download HTML to save a copy.</p>'
            '</div>'
        )
        self._set_status('Log plot created successfully.')

    def _on_render_figure_failed(self, taskId: int, errorText: str) -> None:
        if taskId != getattr(self, '_activeRenderTaskId', None):
            return
        self.loadingOverlay.hide()
        self._set_status(f'Failed to render log HTML: {errorText}', error=True)

    def _download_html(self) -> None:
        if not self.currentPlotHtml:
            self._set_status('No plot HTML available. Draw a plot first.', error=True)
            return
        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Download Log Plot HTML',
            self._default_export_filename('.html'),
            'HTML Files (*.html);;All Files (*)',
        )
        if not selectedFile:
            return
        if not selectedFile.lower().endswith(('.html', '.htm')):
            selectedFile = f'{selectedFile}.html'
        try:
            with open(selectedFile, 'w', encoding='utf-8') as htmlFile:
                htmlFile.write(self.currentPlotHtml)
            self._set_status(f'HTML saved to {selectedFile}')
        except Exception as exc:
            self._set_status(f'Failed to save HTML: {exc}', error=True)

    def _download_png(self) -> None:
        if self.currentPlotFigure is None:
            self._set_status('No plot available. Draw a plot first.', error=True)
            return
        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Download Log Plot PNG',
            self._default_export_filename('.png'),
            'PNG Files (*.png);;All Files (*)',
        )
        if not selectedFile:
            return
        if not selectedFile.lower().endswith('.png'):
            selectedFile = f'{selectedFile}.png'
        try:
            save_plotly_png_and_copy_to_clipboard(self.currentPlotFigure, selectedFile)
            self._set_status(f'PNG saved to {selectedFile} and copied to clipboard.')
        except Exception as exc:
            self._set_status(f'Failed to save PNG: {exc}', error=True)

    def _default_export_filename(self, suffix: str) -> str:
        title = self.plotTitleLineEdit.text().strip() or 'log_plot'
        safeTitle = re.sub(r'[^A-Za-z0-9._-]+', '_', title).strip('._') or 'log_plot'
        return f'{safeTitle}{suffix}'

    def _set_status(self, message: str, error: bool = False) -> None:
        self.statusLabel.setText(message)
        self.statusLabel.setStyleSheet('color: red;' if error else 'color: black;')
