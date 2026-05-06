import re
import tempfile
import webbrowser
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pandas.api.types import is_datetime64_any_dtype
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
import warnings

from qt_helpers import require_child
from pivot_helpers import build_pivot_table, show_pivot_dialog
from plot_templates import CUSTOM_TEMPLATE_NAME, FOR_PPT_TEMPLATE_NAME
from plotly_local import local_plotly_html

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEB_ENGINE_AVAILABLE = True
except ImportError:
    QWebEngineView = None
    WEB_ENGINE_AVAILABLE = False

PLOT_ROW_ID = '__plotRowId'


class TabScatterWidget:
    _excelZeroDatePattern = re.compile(
        r'^\s*\d{4}[/-](?:0[/-]\d{1,2}|\d{1,2}[/-]0)(?:\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)?\s*$'
    )

    def __init__(self, rootWidget: QWidget, preferWebEngine: bool = True):
        self.rootWidget = rootWidget
        self.tabDataWidget = None
        self.preferWebEngine = preferWebEngine
        self.useExternalBrowser = True

        self.xComboBox = require_child(rootWidget, QComboBox, 'xComboBox')
        self.yComboBox = require_child(rootWidget, QComboBox, 'yComboBox')
        self.seriesComboBox = require_child(rootWidget, QComboBox, 'seriesComboBox')
        self.symbolComboBox = require_child(rootWidget, QComboBox, 'symbolComboBox')
        self.colorComboBox = require_child(rootWidget, QComboBox, 'colorComboBox')
        self.sizeComboBox = require_child(rootWidget, QComboBox, 'sizeComboBox')
        self.opacityComboBox = require_child(rootWidget, QComboBox, 'opacityComboBox')
        self.plotTitleLineEdit = require_child(rootWidget, QLineEdit, 'plotTitleLineEdit')
        self.xTitleLineEdit = require_child(rootWidget, QLineEdit, 'xTitleLineEdit')
        self.yTitleLineEdit = require_child(rootWidget, QLineEdit, 'yTitleLineEdit')
        self.xRangeLineEdit = require_child(rootWidget, QLineEdit, 'xRangeLineEdit')
        self.yRangeLineEdit = require_child(rootWidget, QLineEdit, 'yRangeLineEdit')
        self.xFormatLineEdit = require_child(rootWidget, QLineEdit, 'xFormatLineEdit')
        self.yFormatLineEdit = require_child(rootWidget, QLineEdit, 'yFormatLineEdit')
        self.legendCheckBox = require_child(rootWidget, QCheckBox, 'legendCheckBox')
        self.autoStatsButton = require_child(rootWidget, QPushButton, 'autoStatsButton')
        self.hlineLineEdit = require_child(rootWidget, QLineEdit, 'hlineLineEdit')
        self.vlineLineEdit = require_child(rootWidget, QLineEdit, 'vlineLineEdit')
        self.lineColorButton = require_child(rootWidget, QPushButton, 'lineColorButton')
        self.lineWidthSpinBox = require_child(rootWidget, QDoubleSpinBox, 'lineWidthSpinBox')
        self.plotButton = require_child(rootWidget, QPushButton, 'plotButton')
        self.scatterPivotButton = require_child(rootWidget, QPushButton, 'scatterPivotButton')
        self.downloadHtmlButton = require_child(rootWidget, QPushButton, 'downloadHtmlButton')
        self.downloadPngButton = require_child(rootWidget, QPushButton, 'scatterDownloadPngButton')
        self.plotlyThemeComboBox = require_child(rootWidget, QComboBox, 'plotlyThemeComboBox')
        self.plotWidthSpinBox = require_child(rootWidget, QSpinBox, 'plotWidthSpinBox')
        self.plotHeightSpinBox = require_child(rootWidget, QSpinBox, 'plotHeightSpinBox')
        self.plotAreaWidget = require_child(rootWidget, QWidget, 'plotAreaWidget')
        self.statisticLabel = require_child(rootWidget, QLabel, 'statisticLabel')
        self.statusLabel = require_child(rootWidget, QLabel, 'statusLabelTab2')

        self.currentPlotHtml = ''
        self.currentPlotFigure = None
        self.currentPlotFilePath = ''
        self.currentViewerFilePath = ''
        self._browserViewerOpened = False
        self.lineColor = '#ff0000'
        self._configure_plot_area()
        self._configure_signals()
        self._configure_defaults()

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

    def _configure_signals(self) -> None:
        self.plotButton.clicked.connect(self._draw_plot)
        self.scatterPivotButton.clicked.connect(self._show_pivot_table)
        self.downloadHtmlButton.clicked.connect(self._download_html)
        self.downloadPngButton.clicked.connect(self._download_png)
        self.autoStatsButton.clicked.connect(self._auto_fill_plot_stats)
        self.lineColorButton.clicked.connect(self._pick_line_color)
        self.plotlyThemeComboBox.currentTextChanged.connect(self._redraw_existing_plot)
        self.lineWidthSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.plotWidthSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.plotHeightSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.plotTitleLineEdit.editingFinished.connect(self._draw_plot_when_xy_ready)
        self.xTitleLineEdit.editingFinished.connect(self._draw_plot_when_xy_ready)
        self.yTitleLineEdit.editingFinished.connect(self._draw_plot_when_xy_ready)
        self.xRangeLineEdit.editingFinished.connect(self._draw_plot_when_xy_ready)
        self.yRangeLineEdit.editingFinished.connect(self._draw_plot_when_xy_ready)
        self.xFormatLineEdit.editingFinished.connect(self._draw_plot_when_xy_ready)
        self.yFormatLineEdit.editingFinished.connect(self._draw_plot_when_xy_ready)
        self.hlineLineEdit.editingFinished.connect(self._update_stats_and_redraw)
        self.vlineLineEdit.editingFinished.connect(self._draw_plot_when_xy_ready)
        self.xComboBox.currentTextChanged.connect(self._sync_x_title_from_column)
        self.xComboBox.currentTextChanged.connect(self._sync_x_format_from_column)
        self.xComboBox.currentTextChanged.connect(self._update_plot_title)
        self.xComboBox.currentTextChanged.connect(self._draw_plot_when_xy_ready)
        self.yComboBox.currentTextChanged.connect(self._sync_y_title_from_column)
        self.yComboBox.currentTextChanged.connect(self._update_plot_title)
        self.yComboBox.currentTextChanged.connect(self._draw_plot_when_xy_ready)
        for combo in [
            self.seriesComboBox,
            self.sizeComboBox,
            self.colorComboBox,
            self.opacityComboBox,
            self.symbolComboBox,
        ]:
            combo.currentTextChanged.connect(self._update_plot_title)
            combo.currentTextChanged.connect(self._redraw_existing_plot)
        self.xTitleLineEdit.textChanged.connect(self._update_plot_title)
        self.yTitleLineEdit.textChanged.connect(self._update_plot_title)

    def _configure_defaults(self) -> None:
        self._update_line_color_button()
        self.lineWidthSpinBox.setMinimum(0.0)
        self.lineWidthSpinBox.setMaximum(1.0)
        self.lineWidthSpinBox.setSingleStep(0.1)
        self.lineWidthSpinBox.setDecimals(2)
        self.lineWidthSpinBox.setValue(0.5)
        self.plotWidthSpinBox.setRange(200, 5000)
        self.plotWidthSpinBox.setSingleStep(50)
        if self.plotWidthSpinBox.value() <= 200:
            self.plotWidthSpinBox.setValue(max(200, self.plotAreaWidget.width()))
        self.plotHeightSpinBox.setRange(200, 5000)
        self.plotHeightSpinBox.setSingleStep(50)
        if self.plotHeightSpinBox.value() <= 200:
            self.plotHeightSpinBox.setValue(max(200, self.plotAreaWidget.height()))
        self.plotlyThemeComboBox.addItems([
            'plotly',
            'plotly_white',
            'plotly_dark',
            'ggplot2',
            'seaborn',
            'simple_white',
            'presentation',
#           'xgridoff',
#           'ygridoff',
            'gridon',
            FOR_PPT_TEMPLATE_NAME,
            CUSTOM_TEMPLATE_NAME,
            'none',
        ])
        self.plotlyThemeComboBox.setCurrentText('plotly')
        self.statisticLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )

    def _pick_line_color(self) -> None:
        selectedColor = QColorDialog.getColor(
            QColor(self.lineColor),
            self.rootWidget,
            'Pick reference line color',
        )
        if not selectedColor.isValid():
            return
        self.lineColor = selectedColor.name()
        self._update_line_color_button()

    def _update_line_color_button(self) -> None:
        self.lineColorButton.setText('')
        self.lineColorButton.setToolTip(self.lineColor)
        self.lineColorButton.setWhatsThis(self.lineColor)
        self.lineColorButton.setStyleSheet(
            f'QPushButton {{ background-color: {self.lineColor}; }}'
        )

    def _sync_x_title_from_column(self, columnName: str) -> None:
        self.xTitleLineEdit.setText(columnName.strip().rstrip('_'))

    def _sync_y_title_from_column(self, columnName: str) -> None:
        self.yTitleLineEdit.setText(columnName.strip().rstrip('_'))

    def _sync_x_format_from_column(self, columnName: str) -> None:
        if self.tabDataWidget is None:
            return
        dataFrame = self.tabDataWidget.get_plot_data()
        columnName = columnName.strip()
        if not columnName or columnName not in dataFrame.columns:
            return
        if self._is_date_series(dataFrame[columnName]):
            self.xFormatLineEdit.setText('mm/dd')
        elif self.xFormatLineEdit.text().strip().lower() == 'mm/dd':
            self.xFormatLineEdit.clear()

    def _current_combo_text(self, combo: QComboBox) -> str:
        return combo.currentText().strip()

    def _build_auto_plot_title(self) -> str:
        xTitle = self.xTitleLineEdit.text().strip()
        yTitle = self.yTitleLineEdit.text().strip()
        xTitle = xTitle or self._current_combo_text(self.xComboBox)
        yTitle = yTitle or self._current_combo_text(self.yComboBox)
        if not xTitle and not yTitle:
            return ''
        if not xTitle or not yTitle:
            return xTitle or yTitle

        title = f'{xTitle} vs {yTitle}'
        byParts = []
        for label, combo in [
            ('series', self.seriesComboBox),
            ('size', self.sizeComboBox),
            ('color', self.colorComboBox),
            ('opacity', self.opacityComboBox),
            ('symbol', self.symbolComboBox),
        ]:
            value = self._current_combo_text(combo)
            if value:
                byParts.append(f'{label}={value}')
        if byParts:
            title = f'{title} by {", ".join(byParts)}'
        return title

    def _update_plot_title(self, *_args) -> None:
        self.plotTitleLineEdit.setText(self._build_auto_plot_title())

    def _redraw_existing_plot(self, *_args) -> None:
        if self.currentPlotHtml:
            self._draw_plot()

    def _draw_plot_when_xy_ready(self, *_args) -> None:
        if self.tabDataWidget is None:
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        if dataFrame.empty:
            return

        xColumn = self.xComboBox.currentText().strip()
        yColumn = self.yComboBox.currentText().strip()
        if not xColumn or not yColumn:
            return

        if xColumn not in dataFrame.columns or yColumn not in dataFrame.columns:
            return

        self._draw_plot()

    def _update_stats_and_redraw(self, *_args) -> None:
        if self.tabDataWidget is None:
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        xColumn = self.xComboBox.currentText().strip()
        yColumn = self.yComboBox.currentText().strip()
        if dataFrame.empty or not yColumn or yColumn not in dataFrame.columns:
            return

        xIsDate = xColumn in dataFrame.columns and self._is_date_series(dataFrame[xColumn])
        vLines = self._current_vline_values(xIsDate)
        statData, intervalLabel = self._filter_data_by_x_vlines(
            dataFrame,
            xColumn,
            xIsDate,
            vLines,
        )
        ySeries = pd.to_numeric(statData[yColumn], errors='coerce').dropna()
        if ySeries.empty:
            return

        yStdev = float(ySeries.std(ddof=1)) if len(ySeries) > 1 else 0.0
        self._update_statistic_label(float(ySeries.mean()), yStdev, intervalLabel)
        self._draw_plot_when_xy_ready()

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
        for combo in [
            self.xComboBox,
            self.yComboBox,
            self.seriesComboBox,
            self.symbolComboBox,
            self.colorComboBox,
            self.sizeComboBox,
            self.opacityComboBox,
        ]:
            currentText = combo.currentText()
            combo.clear()
            combo.addItem('')
            combo.addItems(columnNames)
            if currentText in columnNames:
                combo.setCurrentText(currentText)
        self._update_plot_title()
        self._redraw_existing_plot()

    def _parse_range(self, value: str) -> tuple | None:
        if not value:
            return None

        rangeParts = [part.strip() for part in value.split(',') if part.strip()]
        if len(rangeParts) != 2:
            return None
        try:
            fromValue = float(rangeParts[0])
            toValue = float(rangeParts[1])
            return fromValue, toValue
        except ValueError:
            return None

    def _parse_date_range(self, value: str) -> list | None:
        if not value:
            return None
        rangeParts = [part.strip() for part in value.split(',') if part.strip()]
        if len(rangeParts) != 2:
            return None
        parsedDates = pd.to_datetime(rangeParts, errors='coerce')
        if pd.isna(parsedDates).any():
            return None
        return [parsedDate.to_pydatetime() for parsedDate in parsedDates]

    def _parse_line_values(self, value: str) -> list[float]:
        if not value:
            return []
        lineParts = [item.strip() for item in value.split(',') if item.strip()]
        values = []
        for linePart in lineParts:
            try:
                values.append(float(linePart))
            except ValueError:
                continue
        return values

    def _parse_date_line_values(self, value: str) -> list:
        if not value:
            return []
        lineParts = [item.strip() for item in value.split(',') if item.strip()]
        parsedDates = pd.to_datetime(lineParts, errors='coerce')
        return [
            parsedDate.to_pydatetime()
            for parsedDate in parsedDates
            if not pd.isna(parsedDate)
        ]

    def _current_vline_values(self, xIsDate: bool) -> list:
        return (
            self._parse_date_line_values(self.vlineLineEdit.text())
            if xIsDate
            else self._parse_line_values(self.vlineLineEdit.text())
        )

    def _filter_data_by_x_vlines(
        self,
        dataFrame: pd.DataFrame,
        xColumn: str,
        xIsDate: bool,
        vLines: list,
    ) -> tuple[pd.DataFrame, str]:
        if len(vLines) != 2 or not xColumn or xColumn not in dataFrame.columns:
            return dataFrame, ''

        lowerValue, upperValue = sorted(vLines)
        if xIsDate:
            xSeries = pd.to_datetime(dataFrame[xColumn], errors='coerce')
            lowerValue = pd.Timestamp(lowerValue)
            upperValue = pd.Timestamp(upperValue)
            intervalLabel = (
                f'{xColumn} between '
                f'{self._format_date_label(lowerValue)} and {self._format_date_label(upperValue)}'
            )
        else:
            xSeries = pd.to_numeric(dataFrame[xColumn], errors='coerce')
            intervalLabel = (
                f'{xColumn} between '
                f'{self._format_number(lowerValue)} and {self._format_number(upperValue)}'
            )

        filteredData = dataFrame.loc[xSeries.between(lowerValue, upperValue, inclusive='both')]
        return filteredData, intervalLabel

    def _format_number(self, value: float) -> str:
        return f'{value:.6g}'

    def _format_line_values(self, values: list[float]) -> str:
        return ','.join(self._format_number(value) for value in values)

    def _decimal_places_for_series(self, series: pd.Series) -> int:
        maxDecimals = 0
        for value in series.dropna():
            valueText = str(value).strip()
            if 'e' in valueText.lower():
                valueText = f'{float(value):.12f}'.rstrip('0').rstrip('.')
            if '.' in valueText:
                maxDecimals = max(maxDecimals, len(valueText.split('.', 1)[1].rstrip('0')))
        return maxDecimals + 1

    def _format_auto_values(self, values: list[float], decimalPlaces: int) -> str:
        return ','.join(f'{value:.{decimalPlaces}f}' for value in values)

    def _is_date_series(self, series: pd.Series) -> bool:
        nonNullSeries = series.dropna()
        if nonNullSeries.empty:
            return False
        if is_datetime64_any_dtype(nonNullSeries):
            return True
        if pd.api.types.is_numeric_dtype(nonNullSeries):
            return False
        if self._has_excel_zero_date_text(nonNullSeries):
            return False
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")  # 確保會被捕捉
            parsedDates = pd.to_datetime(nonNullSeries, errors='coerce')
            if w:
                statusMsg = str(w[0].message)   
                self._set_status(f'something wrong while pd.to_datetime: {statusMsg}', error=True)

        return parsedDates.notna().mean() >= 0.8

    def _has_excel_zero_date_text(self, series: pd.Series) -> bool:
        textSeries = series.astype(str).str.strip()
        return bool(textSeries.str.match(self._excelZeroDatePattern).any())

    def _date_tick_format(self, formatText: str) -> str:
        formatText = formatText.strip() or 'mm/dd'
        if '%' in formatText:
            return formatText

        convertedParts = []
        index = 0
        while index < len(formatText):
            remainingText = formatText[index:]
            if remainingText.startswith(('yyyy', 'YYYY')):
                convertedParts.append('%Y')
                index += 4
            elif remainingText.startswith(('yy', 'YY')):
                convertedParts.append('%y')
                index += 2
            elif remainingText.startswith(('HH', 'hh')):
                convertedParts.append('%H')
                index += 2
            elif remainingText.startswith(('SS', 'ss')):
                convertedParts.append('%S')
                index += 2
            elif remainingText.startswith('MM'):
                previousChar = formatText[index - 1] if index > 0 else ''
                convertedParts.append('%M' if previousChar == ':' else '%m')
                index += 2
            elif remainingText.startswith('mm'):
                convertedParts.append('%m')
                index += 2
            elif remainingText.startswith(('dd', 'DD')):
                convertedParts.append('%d')
                index += 2
            else:
                convertedParts.append(formatText[index])
                index += 1
        return ''.join(convertedParts)

    def _format_date_label(self, value) -> str:
        parsedDate = pd.to_datetime(value, errors='coerce')
        if pd.isna(parsedDate):
            return str(value)
        return parsedDate.strftime(self._date_tick_format(self.xFormatLineEdit.text()))

    def _expanded_line_range(self, values: list[float]) -> list[float]:
        lowerValue = min(values)
        upperValue = max(values)
        if lowerValue == upperValue:
            padding = abs(lowerValue) * 0.1 or 1.0
            return [lowerValue - padding, upperValue + padding]
        if lowerValue >= 0:
            return [lowerValue * 0.9, upperValue * 1.1]
        if upperValue <= 0:
            return [lowerValue * 1.1, upperValue * 0.9]
        valueSpan = upperValue - lowerValue
        return [lowerValue - valueSpan * 0.1, upperValue + valueSpan * 0.1]

    def _format_line_label(self, axisTitle: str, value: float) -> str:
        return f'{axisTitle}={value:g}'

    def _format_date_line_label(self, axisTitle: str, value) -> str:
        return f'{axisTitle}={self._format_date_label(value)}'

    def _add_date_vline(
        self,
        fig,
        value,
        axisTitle: str,
        lineColor: str,
        lineWidth: float,
    ) -> None:
        fig.add_shape(
            type='line',
            x0=value,
            x1=value,
            y0=0,
            y1=1,
            xref='x',
            yref='paper',
            line=dict(
                color=lineColor,
                width=lineWidth,
                dash='dash',
            ),
        )
        fig.add_annotation(
            x=value,
            y=1,
            xref='x',
            yref='paper',
            text=self._format_date_line_label(axisTitle, value),
            showarrow=False,
            yanchor='bottom',
            xanchor='left',
            font=dict(color=lineColor),
        )

    def _line_color_with_opacity(self, opacity: float) -> str:
        opacity = max(0.0, min(1.0, opacity))
        if self.lineColor.startswith('#') and len(self.lineColor) == 7:
            red = int(self.lineColor[1:3], 16)
            green = int(self.lineColor[3:5], 16)
            blue = int(self.lineColor[5:7], 16)
            return f'rgba({red},{green},{blue},{opacity:.3g})'
        return self.lineColor

    def _auto_fill_plot_stats(self) -> None:
        if self.tabDataWidget is None:
            self._set_status('No data source attached to plot tab.', error=True)
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        if dataFrame.empty:
            self._set_status('No reshaped data available. Run wide_to_long first.', error=True)
            return

        xColumn = self.xComboBox.currentText().strip()
        yColumn = self.yComboBox.currentText().strip()
        if not xColumn or not yColumn:
            self._set_status('Choose both X and Y columns first.', error=True)
            return

        if xColumn not in dataFrame.columns or yColumn not in dataFrame.columns:
            self._set_status('Selected X or Y column not found in data.', error=True)
            return

        xIsDate = self._is_date_series(dataFrame[xColumn])
        if xIsDate:
            self.xFormatLineEdit.setText(self.xFormatLineEdit.text().strip() or 'mm/dd')
            xSeries = pd.Series(dtype='float64')
        else:
            xSeries = pd.to_numeric(dataFrame[xColumn], errors='coerce').dropna()
        ySeries = pd.to_numeric(dataFrame[yColumn], errors='coerce').dropna()
        if ySeries.empty:
            self._set_status('Y must contain numeric data for Auto.', error=True)
            return

        yAverage = float(ySeries.mean())
        yStdev = float(ySeries.std(ddof=1)) if len(ySeries) > 1 else 0.0
        hlineValues = [yAverage - 3 * yStdev, yAverage + 3 * yStdev]
        yDecimals = self._decimal_places_for_series(dataFrame[yColumn])

        if not xSeries.empty:
            xAverage = float(xSeries.mean())
            xStdev = float(xSeries.std(ddof=1)) if len(xSeries) > 1 else 0.0
            vlineValues = [xAverage - 3 * xStdev, xAverage + 3 * xStdev]
            xDecimals = self._decimal_places_for_series(dataFrame[xColumn])
            self.xRangeLineEdit.setText(
                self._format_auto_values(self._expanded_line_range(vlineValues), xDecimals)
            )
            self.vlineLineEdit.setText(self._format_auto_values(vlineValues, xDecimals))
        self.yRangeLineEdit.setText(
            self._format_auto_values(self._expanded_line_range(hlineValues), yDecimals)
        )
        self.hlineLineEdit.setText(self._format_auto_values(hlineValues, yDecimals))
        vLines = self._current_vline_values(xIsDate)
        statData, intervalLabel = self._filter_data_by_x_vlines(
            dataFrame,
            xColumn,
            xIsDate,
            vLines,
        )
        yStatSeries = pd.to_numeric(statData[yColumn], errors='coerce').dropna()
        if not yStatSeries.empty:
            yAverage = float(yStatSeries.mean())
            yStdev = float(yStatSeries.std(ddof=1)) if len(yStatSeries) > 1 else 0.0
        self._update_statistic_label(yAverage, yStdev, intervalLabel)
        self._draw_plot_when_xy_ready()
        if xSeries.empty:
            self._set_status('Auto updated Y statistics only; X is not numeric.')

    def _update_statistic_label(
        self,
        yAverage: float,
        yStdev: float,
        intervalLabel: str = '',
    ) -> None:
        yTitle = self.yTitleLineEdit.text().strip() or self.yComboBox.currentText().strip()
        parts = [
            f'{yTitle} average = {self._format_number(yAverage)}',
            f'stdev = {self._format_number(yStdev)}',
        ]

        specValues = self._parse_line_values(self.hlineLineEdit.text())
        if len(specValues) in [2, 3] and yStdev > 0:
            sortedSpecs = sorted(specValues)
            lowerSpec = sortedSpecs[0]
            upperSpec = sortedSpecs[-1]
            specRange = upperSpec - lowerSpec
            target = sortedSpecs[1] if len(sortedSpecs) == 3 else (lowerSpec + upperSpec) / 2
            if specRange > 0:
                ca = abs(yAverage - target) / (specRange / 2)
                cp = specRange / (6 * yStdev)
                cpk = min(upperSpec - yAverage, yAverage - lowerSpec) / (3 * yStdev)
                parts.extend([
                    f'target = {self._format_number(target)}',
                    f'range = {self._format_number(specRange)}',
                    f'Ca = {ca:.2f}',
                    f'Cp = {cp:.2f}',
                    f'Cpk = {cpk:.2f}',
                ])

        if intervalLabel:
            parts.append(f'interval = {intervalLabel}')
        self.statisticLabel.setText(', '.join(parts))

    def _show_pivot_table(self) -> None:
        if self.tabDataWidget is None:
            self._set_status('No data source attached to plot tab.', error=True)
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        xColumn = self.xComboBox.currentText().strip()
        yColumn = self.yComboBox.currentText().strip()
        if dataFrame.empty:
            self._set_status('No data available for pivot.', error=True)
            return
        if not yColumn or yColumn not in dataFrame.columns:
            self._set_status('Choose a valid Y column before pivot.', error=True)
            return
        if not xColumn or xColumn not in dataFrame.columns:
            self._set_status('Choose a valid X column before pivot.', error=True)
            return

        groupColumns = []
        for combo in [
            self.seriesComboBox,
            self.symbolComboBox,
            self.colorComboBox,
            self.sizeComboBox,
            self.opacityComboBox,
        ]:
            columnName = combo.currentText().strip()
            if columnName and columnName in dataFrame.columns and columnName not in groupColumns:
                groupColumns.append(columnName)

        xIsDate = self._is_date_series(dataFrame[xColumn])
        vLines = self._current_vline_values(xIsDate)
        pivotInputData, intervalLabel = self._filter_data_by_x_vlines(
            dataFrame,
            xColumn,
            xIsDate,
            vLines,
        )
        pivotData = build_pivot_table(pivotInputData, yColumn, groupColumns, xColumn)
        if pivotData.empty:
            self._set_status('No numeric Y data available for pivot.', error=True)
            return
        dialogTitle = 'Scatter pivot'
        if intervalLabel:
            dialogTitle = f'{dialogTitle} ({intervalLabel})'
        show_pivot_dialog(self.rootWidget, dialogTitle, pivotData)

    def _draw_plot(self) -> None:
        if self.tabDataWidget is None:
            self._set_status('No data source attached to plot tab.', error=True)
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        if dataFrame.empty:
            self._set_status('No reshaped data available. Run wide_to_long first.', error=True)
            return

        xColumn = self.xComboBox.currentText().strip()
        yColumn = self.yComboBox.currentText().strip()
        seriesColumn = self.seriesComboBox.currentText().strip() or None
        symbolColumn = self.symbolComboBox.currentText().strip() or None
        colorColumn = self.colorComboBox.currentText().strip() or None
        sizeColumn = self.sizeComboBox.currentText().strip() or None
        opacityColumn = self.opacityComboBox.currentText().strip() or None

        if not xColumn or not yColumn:
            self._set_status('Choose both X and Y columns first.', error=True)
            return

        if xColumn not in dataFrame.columns or yColumn not in dataFrame.columns:
            self._set_status('Selected X or Y column not found in data.', error=True)
            return

        plotTitle = self.plotTitleLineEdit.text().strip() or 'Plotly Scatter'
        xTitle = self.xTitleLineEdit.text().strip() or xColumn
        yTitle = self.yTitleLineEdit.text().strip() or yColumn
        plotlyTheme = self.plotlyThemeComboBox.currentText().strip()
        plotlyTheme = None if plotlyTheme == 'none' else plotlyTheme or 'plotly'
        xIsDate = self._is_date_series(dataFrame[xColumn])
        xRange = (
            self._parse_date_range(self.xRangeLineEdit.text())
            if xIsDate
            else self._parse_range(self.xRangeLineEdit.text())
        )
        yRange = self._parse_range(self.yRangeLineEdit.text())
        legendVisible = self.legendCheckBox.isChecked()
        hLines = self._parse_line_values(self.hlineLineEdit.text())
        vLines = self._current_vline_values(xIsDate)
        lineWidth = self.lineWidthSpinBox.value()
        lineColorWithOpacity = self._line_color_with_opacity(lineWidth)
        plotWidth = self.plotWidthSpinBox.value()
        plotHeight = self.plotHeightSpinBox.value()

        plotData = dataFrame.copy()
        if xIsDate:
            self.xFormatLineEdit.setText(self.xFormatLineEdit.text().strip() or 'mm/dd')
            plotData[xColumn] = pd.to_datetime(plotData[xColumn], errors='coerce')
        plotData[yColumn] = pd.to_numeric(plotData[yColumn], errors='coerce')
        plotData = plotData.dropna(subset=[xColumn, yColumn]).reset_index(drop=True)
        if plotData.empty:
            self._set_status('Y must contain numeric data for plotting.', error=True)
            return
        plotData[PLOT_ROW_ID] = plotData.index
        statData, intervalLabel = self._filter_data_by_x_vlines(
            plotData,
            xColumn,
            xIsDate,
            vLines,
        )
        yStatSeries = pd.to_numeric(statData[yColumn], errors='coerce').dropna()
        if not yStatSeries.empty:
            yStdev = float(yStatSeries.std(ddof=1)) if len(yStatSeries) > 1 else 0.0
            self._update_statistic_label(float(yStatSeries.mean()), yStdev, intervalLabel)

        try:
            colorArgument = colorColumn or seriesColumn
            symbolArgument = symbolColumn
            hoverData = []
            if seriesColumn and seriesColumn != colorArgument:
                hoverData.append(seriesColumn)

            fig = px.scatter(
                plotData,
                x=xColumn,
                y=yColumn,
                color=colorArgument,
                symbol=symbolArgument,
                size=sizeColumn if sizeColumn else None,
                title=plotTitle,
                labels={xColumn: xTitle, yColumn: yTitle},
                hover_data=hoverData,
                custom_data=[PLOT_ROW_ID],
                template=plotlyTheme,
            )

            if opacityColumn:
                opacitySeries = self._normalize_opacity(plotData[opacityColumn])
                for trace in fig.data:
                    if hasattr(trace, 'customdata') and trace.customdata is not None:
                        trace.marker.opacity = [
                            float(opacitySeries.iloc[int(rowData[0])])
                            for rowData in trace.customdata
                        ]
                    else:
                        trace.marker.opacity = float(opacitySeries.mean())

            if sizeColumn:
                self._add_reference_trace(fig, f'Size: {sizeColumn}')
            if opacityColumn:
                self._add_reference_trace(fig, f'Opacity: {opacityColumn}')

            fig.update_layout(
                legend=dict(
                    orientation='v',
                    y=1,
                    x=1.16,
                    title_text='Legend',
#                    bgcolor='rgba(255,255,255,0.92)',
                    bordercolor='rgba(0,0,0,0.15)',
                    borderwidth=1,
                ),
                coloraxis_colorbar=dict(x=1.02),
                margin={'t': 60, 'r': 220, 'l': 60, 'b': 60},
                showlegend=legendVisible,
            )
            fig.update_layout(width=plotWidth, height=plotHeight)

            if xRange is not None:
                fig.update_xaxes(range=xRange)
            if yRange is not None:
                fig.update_yaxes(range=yRange)
            xFormat = self.xFormatLineEdit.text().strip()
            yFormat = self.yFormatLineEdit.text().strip()
            if xFormat:
                fig.update_xaxes(tickformat=self._date_tick_format(xFormat) if xIsDate else xFormat)
            if yFormat:
                fig.update_yaxes(tickformat=yFormat)

            for hValue in hLines:
                fig.add_hline(
                    y=hValue,
                    line_dash='dash',
                    line_color=lineColorWithOpacity,
                    line_width=lineWidth,
                    annotation_text=self._format_line_label(yTitle, hValue),
                    annotation_position='top right',
                    annotation_font_color=lineColorWithOpacity,
                )
            for vValue in vLines:
                if xIsDate:
                    self._add_date_vline(
                        fig,
                        vValue,
                        xTitle,
                        lineColorWithOpacity,
                        lineWidth,
                    )
                else:
                    fig.add_vline(
                        x=vValue,
                        line_dash='dash',
                        line_color=lineColorWithOpacity,
                        line_width=lineWidth,
                        annotation_text=self._format_line_label(xTitle, vValue),
                        annotation_position='top right',
                        annotation_font_color=lineColorWithOpacity,
                    )

            self._render_figure(fig)
            self._set_status('Plot created successfully.')
        except Exception as exc:
            self._set_status(f'Failed to draw plot: {exc}', error=True)

    def _normalize_opacity(self, series: pd.Series) -> pd.Series:
        opacitySeries = pd.to_numeric(series, errors='coerce').fillna(1.0)
        minValue = opacitySeries.min()
        maxValue = opacitySeries.max()
        if maxValue > 1 or minValue < 0:
            valueSpan = maxValue - minValue
            if valueSpan == 0:
                opacitySeries = pd.Series(1.0, index=opacitySeries.index)
            else:
                opacitySeries = (opacitySeries - minValue) / valueSpan
        return 0.2 + 0.8 * opacitySeries.clip(0, 1)

    def _add_reference_trace(self, figure, traceName: str) -> None:
        figure.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode='markers',
                marker=dict(size=9, color='rgba(90,90,90,0.55)'),
                name=traceName,
                showlegend=True,
                hoverinfo='skip',
            )
        )

    def _render_figure(self, figure) -> None:
        self.currentPlotFigure = figure
        self.currentPlotHtml = local_plotly_html(figure, fullHtml=True)
        if not self.useExternalBrowser:
            assetsDir = Path(__file__).resolve().parent
            html = local_plotly_html(figure, fullHtml=False)
            try:
                baseUrl = QUrl.fromLocalFile(str(assetsDir)+'/')
                self.chartView.setHtml(html, baseUrl)
                return
            except Exception:
                self._switch_to_external_browser_view()

        self.currentPlotFilePath = str(Path(tempfile.gettempdir()) / 'p-chart-scatter.html')
        with open(self.currentPlotFilePath, 'w', encoding='utf-8') as htmlFile:
            htmlFile.write(self.currentPlotHtml)

        plotUri = Path(self.currentPlotFilePath).resolve().as_uri()
        self.currentViewerFilePath = str(Path(tempfile.gettempdir()) / 'p-chart-scatter-viewer.html')
        viewerUri = Path(self.currentViewerFilePath).resolve().as_uri()
        viewerHtml = f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>p-chart Scatter</title>
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
            '<p>Scatter plot is shown in the system browser.</p>'
            f'<p><a href="{viewerUri}">Open scatter browser viewer</a></p>'
            '<p>The viewer reloads the latest Plotly HTML automatically. Use Download HTML to save a copy.</p>'
            '<p>無法啟動 PySide6.WebEngine, 原因可能是 遠端桌面, 系統老舊沒有 GPU, 或者啟動時加了"--no-webengine"</p>'
            '<p>結果會是畫面因為字型大小跑掉很醜， 圖不能在這裡顯示, 要到瀏覽器看</p>'
            '<p><h1>不是我的錯！</h1></p>'
            '</div>'
        )

    def _download_html(self) -> None:
        if not self.currentPlotHtml:
            self._set_status('No plot HTML available. Draw a plot first.', error=True)
            return

        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Download Plotly HTML',
            'plot.html',
            'HTML Files (*.html);;All Files (*)',
        )
        if not selectedFile:
            return
        if not selectedFile.lower().endswith(('.html', '.htm')):
            selectedFile = f'{selectedFile}.html'

        try:
            with open(selectedFile, 'w', encoding='utf-8') as htmlFile:
                htmlFile.write(self.currentPlotHtml)
            self._set_status(f'Plot HTML saved to {selectedFile}.')
        except Exception as exc:
            self._set_status(f'Failed to save Plotly HTML: {exc}', error=True)

    def _download_png(self) -> None:
        if self.currentPlotFigure is None:
            self._set_status('No plot available. Draw a plot first.', error=True)
            return

        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Download Plotly PNG',
            'plot.png',
            'PNG Files (*.png);;All Files (*)',
        )
        if not selectedFile:
            return
        if not selectedFile.lower().endswith('.png'):
            selectedFile = f'{selectedFile}.png'

        try:
            self.currentPlotFigure.write_image(selectedFile, format='png')
            self._set_status(f'Plot PNG saved to {selectedFile}.')
        except Exception as exc:
            self._set_status(f'Failed to save Plotly PNG: {exc}', error=True)

    def _set_status(self, message: str, error: bool = False) -> None:
        self.statusLabel.setText(message)
        self.statusLabel.setStyleSheet('color: red;' if error else 'color: black;')
