import math
import re
import tempfile
import webbrowser
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from PySide6.QtCore import QSignalBlocker, QTimer, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from qt_helpers import require_child
from async_helpers import BackgroundTaskMixin
from loading_overlay import LoadingOverlay
from pivot_helpers import build_pivot_table, show_pivot_dialog
from plot_annotation_helpers import add_preview_filter_annotation
from plot_export_helpers import (
    copy_png_bytes_to_clipboard,
    render_plotly_png,
    shift_click_requests_png_file,
)
from plot_templates import CUSTOM_TEMPLATE_NAME, FOR_PPT_TEMPLATE_NAME
from plotly_local import local_plotly_html

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEB_ENGINE_AVAILABLE = True
except ImportError:
    QWebEngineView = None
    WEB_ENGINE_AVAILABLE = False


class TabBoxplotWidget(BackgroundTaskMixin):
    def __init__(self, rootWidget: QWidget, preferWebEngine: bool = True):
        self.tabDataWidget = None
        self.preferWebEngine = preferWebEngine
        self.useExternalBrowser = True
        self.currentPlotHtml = ''
        self.currentPlotFigure = None
        self.currentPlotFilePath = ''
        self.currentViewerFilePath = ''
        self._browserViewerOpened = False
        self.isActiveTab = False
        self._pendingDataRefresh = False
        self._pendingRedraw = False
        self.lineColor = '#ff0000'
        self.annotationColor = '#000000'
        self.groupSepLineSettings = {}
        self.activeGroupSepColumn = None
        self.currentSepPlotValues = []
        self.lastGroupSepStatus = ''
        self.largeGroupSepAcceptedColumn = None

        self.rootWidget = rootWidget
        self.yComboBox = require_child(rootWidget, QComboBox, 'boxYComboBox')
        self.group1ComboBox = require_child(rootWidget, QComboBox, 'boxGroup1ComboBox')
        self.group2ComboBox = require_child(rootWidget, QComboBox, 'boxGroup2ComboBox')
        self.groupSepComboBox = require_child(rootWidget, QComboBox, 'boxGroupSepComboBox')
        self.singleSelectComboBox = require_child(rootWidget, QComboBox, 'boxSingleSelectComboBox')
        self.pointsComboBox = require_child(rootWidget, QComboBox, 'boxPointsComboBox')
        self.annotationCheckBoxes = [
            ('N', require_child(rootWidget, QCheckBox, 'boxAnnotationNCheckBox')),
            ('max', require_child(rootWidget, QCheckBox, 'boxAnnotationMaxCheckBox')),
            ('q1', require_child(rootWidget, QCheckBox, 'boxAnnotationQ1CheckBox')),
            ('median', require_child(rootWidget, QCheckBox, 'boxAnnotationMedianCheckBox')),
            ('average', require_child(rootWidget, QCheckBox, 'boxAnnotationAverageCheckBox')),
            ('q3', require_child(rootWidget, QCheckBox, 'boxAnnotationQ3CheckBox')),
            ('min', require_child(rootWidget, QCheckBox, 'boxAnnotationMinCheckBox')),
            (
                'standard deviation',
                require_child(rootWidget, QCheckBox, 'boxAnnotationStdevCheckBox'),
            ),
            ('range', require_child(rootWidget, QCheckBox, 'boxAnnotationRangeCheckBox')),
        ]
        self.annotationSizeSpinBox = require_child(rootWidget, QSpinBox, 'boxAnnotationSizeSpinBox')
        self.annotationColorButton = require_child(rootWidget, QPushButton, 'boxAnnotationColorButton')
        self.annotationAlphaSpinBox = require_child(
            rootWidget,
            QDoubleSpinBox,
            'boxAnnotationAlphaSpinBox',
        )
        self.annotationFormatLineEdit = require_child(
            rootWidget,
            QLineEdit,
            'boxAnnotationFormatLineEdit',
        )
        self.plotTitleLineEdit = require_child(rootWidget, QLineEdit, 'boxPlotTitleLineEdit')
        self.yTitleLineEdit = require_child(rootWidget, QLineEdit, 'boxYTitleLineEdit')
        self.yRangeLineEdit = require_child(rootWidget, QLineEdit, 'boxYRangeLineEdit')
        self.hlineLineEdit = require_child(rootWidget, QLineEdit, 'boxYLinesLineEdit')
        self.legendCheckBox = require_child(rootWidget, QCheckBox, 'boxLegendCheckBox')
        self.gridsLinesPushButton = require_child(
            rootWidget,
            QPushButton,
            'boxGridsLinesPushButton',
        )
        self.filterAnnotationCheckBox = require_child(
            rootWidget,
            QCheckBox,
            'boxFilterAnnotationCheckBox',
        )
        self.autoStatsButton = require_child(rootWidget, QPushButton, 'boxAutoStatsButton')
        self.lineColorButton = require_child(rootWidget, QPushButton, 'boxLineColorButton')
        self.lineWidthSpinBox = require_child(rootWidget, QDoubleSpinBox, 'boxLineWidthSpinBox')
        self.plotlyThemeComboBox = require_child(rootWidget, QComboBox, 'boxPlotlyThemeComboBox')
        self.horizontalSpaceSpinBox = require_child(rootWidget, QSpinBox, 'boxChartHSpaceSpinBox')
        self.verticalSpaceSpinBox = require_child(rootWidget, QSpinBox, 'boxChartVSpaceSpinbox')
        self.plotWidthSpinBox = require_child(rootWidget, QSpinBox, 'boxPlotWidthSpinBox')
        self.plotHeightSpinBox = require_child(rootWidget, QSpinBox, 'boxPlotHeightSpinBox')
        self.legendFontSizeSpinBox = require_child(
            rootWidget,
            QSpinBox,
            'boxplotLegendFontSizeButton',
        )
        self.plotButton = require_child(rootWidget, QPushButton, 'boxPlotButton')
        self.boxplotPivotButton = require_child(rootWidget, QPushButton, 'boxplotPivotButton')
        self.downloadHtmlButton = require_child(rootWidget, QPushButton, 'boxDownloadHtmlButton')
        self.downloadPngButton = require_child(rootWidget, QPushButton, 'boxDownloadPngButton')
        self.statisticLabel = require_child(rootWidget, QLabel, 'boxStatisticLabel')
        self.plotAreaWidget = require_child(rootWidget, QWidget, 'boxPlotAreaWidget')
        self.statusLabel = require_child(rootWidget, QLabel, 'boxStatusLabel')

        self._configure_plot_area()
        self.loadingOverlay = LoadingOverlay(self.plotAreaWidget)
        self.redrawTimer = QTimer(self.rootWidget)
        self.redrawTimer.setSingleShot(True)
        self.redrawTimer.setInterval(300)
        self.redrawTimer.timeout.connect(self._draw_plot)
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

        plotLayout = QVBoxLayout(self.plotAreaWidget)
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
        self.boxplotPivotButton.clicked.connect(self._show_pivot_table)
        self.downloadHtmlButton.clicked.connect(self._download_html)
        self.downloadPngButton.clicked.connect(self._download_png)
        self.autoStatsButton.clicked.connect(self._auto_fill_plot_stats)
        self.lineColorButton.clicked.connect(self._pick_line_color)
        self.annotationColorButton.clicked.connect(self._pick_annotation_color)
        self.gridsLinesPushButton.clicked.connect(self._show_group_sep_lines_dialog)
        self.legendCheckBox.stateChanged.connect(self._redraw_existing_plot)
        self.filterAnnotationCheckBox.stateChanged.connect(self._redraw_existing_plot)
        self.pointsComboBox.currentTextChanged.connect(self._redraw_existing_plot)
        for _statName, annotationCheckBox in self.annotationCheckBoxes:
            annotationCheckBox.stateChanged.connect(self._redraw_existing_plot)
        self.annotationSizeSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.annotationAlphaSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.annotationFormatLineEdit.editingFinished.connect(self._redraw_existing_plot)
        self.plotlyThemeComboBox.currentTextChanged.connect(self._redraw_existing_plot)
        self.horizontalSpaceSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.verticalSpaceSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.lineWidthSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.plotWidthSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.plotHeightSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.legendFontSizeSpinBox.valueChanged.connect(self._redraw_existing_plot)
        self.plotTitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.yTitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.yRangeLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.hlineLineEdit.editingFinished.connect(self._update_stats_and_redraw)
        self.yComboBox.currentTextChanged.connect(self._sync_y_title_from_column)
        self.yComboBox.currentTextChanged.connect(self._update_plot_title)
        self.yComboBox.currentTextChanged.connect(self._draw_plot_when_ready)
        self.group1ComboBox.currentTextChanged.connect(self._update_plot_title)
        self.group1ComboBox.currentTextChanged.connect(self._draw_plot_when_ready)
        self.group2ComboBox.currentTextChanged.connect(self._update_plot_title)
        self.group2ComboBox.currentTextChanged.connect(self._draw_plot_when_ready)
        self.groupSepComboBox.currentTextChanged.connect(self._on_group_sep_changed)
        self.singleSelectComboBox.currentTextChanged.connect(self._draw_plot_when_ready)

    def _configure_defaults(self) -> None:
        self.legendCheckBox.setChecked(True)
        self.gridsLinesPushButton.setEnabled(False)
        self.horizontalSpaceSpinBox.setRange(4, 30)
        self.horizontalSpaceSpinBox.setValue(self.horizontalSpaceSpinBox.value() or 4)
        self.verticalSpaceSpinBox.setRange(4, 30)
        self.verticalSpaceSpinBox.setValue(self.verticalSpaceSpinBox.value() or 12)
        self._update_grid_controls_enabled(False)
        if self.groupSepComboBox.count() == 0:
            self.groupSepComboBox.addItem('none')
        self.groupSepComboBox.setCurrentText('none')
        self._refresh_single_select_options([])
        if self.pointsComboBox.count() == 0:
            self.pointsComboBox.addItems(['outliers', 'all', 'jitter', 'none'])
        self.pointsComboBox.setCurrentText('outliers')
        self._update_line_color_button()
        self._update_annotation_color_button()
        self.annotationSizeSpinBox.setRange(6, 32)
        self.annotationSizeSpinBox.setValue(11)
        self.annotationAlphaSpinBox.setMinimum(0.0)
        self.annotationAlphaSpinBox.setMaximum(1.0)
        self.annotationAlphaSpinBox.setSingleStep(0.1)
        self.annotationAlphaSpinBox.setDecimals(2)
        self.annotationAlphaSpinBox.setValue(0.72)
        if not self.annotationFormatLineEdit.text().strip():
            self.annotationFormatLineEdit.setText('.4f')
        self.annotationFormatLineEdit.setWhatsThis(
            'Format controls annotation numbers except N. Examples: .2f shows '
            '2 decimals, .3g keeps 3 significant digits, ,.1f adds thousands '
            'separators, and {value:.2f} is also accepted.'
        )
        self.lineWidthSpinBox.setMinimum(0.0)
        self.lineWidthSpinBox.setMaximum(1.0)
        self.lineWidthSpinBox.setSingleStep(0.1)
        self.lineWidthSpinBox.setDecimals(2)
        self.lineWidthSpinBox.setValue(0.5)
        self.plotWidthSpinBox.setRange(200, 5000)
        self.plotWidthSpinBox.setSingleStep(50)
        self.plotWidthSpinBox.setValue(900)
        self.plotHeightSpinBox.setRange(200, 5000)
        self.plotHeightSpinBox.setSingleStep(50)
        self.plotHeightSpinBox.setValue(500)
        self.legendFontSizeSpinBox.setRange(6, 32)
        self.legendFontSizeSpinBox.setValue(self.legendFontSizeSpinBox.value() or 12)
        self.plotlyThemeComboBox.addItems([
            'plotly',
            'plotly_white',
            'plotly_dark',
            'ggplot2',
            'seaborn',
            'simple_white',
            'presentation',
            'xgridoff',
            'ygridoff',
            'gridon',
            FOR_PPT_TEMPLATE_NAME,
            CUSTOM_TEMPLATE_NAME,
            'none',
        ])
        self.plotlyThemeComboBox.setCurrentText('plotly')

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
        self._redraw_existing_plot()

    def _pick_annotation_color(self) -> None:
        selectedColor = QColorDialog.getColor(
            QColor(self.annotationColor),
            self.rootWidget,
            'Pick annotation color',
        )
        if not selectedColor.isValid():
            return
        self.annotationColor = selectedColor.name()
        self._update_annotation_color_button()
        self._redraw_existing_plot()

    def _update_line_color_button(self) -> None:
        self.lineColorButton.setText('')
        self.lineColorButton.setToolTip(self.lineColor)
        self.lineColorButton.setWhatsThis(self.lineColor)
        self.lineColorButton.setStyleSheet(
            f'QPushButton {{ background-color: {self.lineColor}; }}'
        )

    def _update_annotation_color_button(self) -> None:
        self.annotationColorButton.setText('')
        self.annotationColorButton.setToolTip(self.annotationColor)
        self.annotationColorButton.setWhatsThis(self.annotationColor)
        self.annotationColorButton.setStyleSheet(
            f'QPushButton {{ background-color: {self.annotationColor}; }}'
        )

    def _sync_y_title_from_column(self, columnName: str) -> None:
        self.yTitleLineEdit.setText(columnName.strip().rstrip())

    def _current_combo_text(self, combo: QComboBox) -> str:
        return combo.currentText().strip()

    def _selected_group_sep_column(self) -> str | None:
        sepColumn = self.groupSepComboBox.currentText().strip()
        if not sepColumn or sepColumn == 'none':
            return None
        return sepColumn

    def _selected_single_sep_value(self) -> str | None:
        singleValue = self.singleSelectComboBox.currentText().strip()
        if not singleValue or singleValue == 'all':
            return None
        return singleValue

    def _group_sep_values(self, dataFrame: pd.DataFrame, sepColumn: str) -> list[str]:
        if sepColumn not in dataFrame.columns:
            return []
        return self._ordered_categories(dataFrame[sepColumn])

    def _refresh_single_select_options(self, sepValues: list[str], reset: bool = True) -> None:
        currentText = self.singleSelectComboBox.currentText().strip()
        blocker = QSignalBlocker(self.singleSelectComboBox)
        try:
            self.singleSelectComboBox.clear()
            self.singleSelectComboBox.addItem('all')
            self.singleSelectComboBox.addItems(sepValues)
            if not reset and currentText in sepValues:
                self.singleSelectComboBox.setCurrentText(currentText)
            else:
                self.singleSelectComboBox.setCurrentText('all')
        finally:
            del blocker

    def _update_grid_controls_enabled(self, isGrid: bool) -> None:
        self.horizontalSpaceSpinBox.setEnabled(isGrid)
        self.verticalSpaceSpinBox.setEnabled(isGrid)

    def _subplot_spacing(self, spinBox: QSpinBox, axisCount: int) -> float:
        if axisCount <= 1:
            return 0.0
        requestedSpacing = spinBox.value() / 100.0
        maximumSpacing = 0.99 / (axisCount - 1)
        return min(requestedSpacing, maximumSpacing)

    def _confirm_group_sep_count(self, sepColumn: str) -> list[str] | None:
        if self.tabDataWidget is None:
            return []
        dataFrame = self.tabDataWidget.get_plot_data()
        sepValues = self._group_sep_values(dataFrame, sepColumn)
        sepCount = len(sepValues)
        self.lastGroupSepStatus = f'{sepColumn} unique values: {sepCount}'
        self._set_status(self.lastGroupSepStatus)
        if sepCount <= 12:
            return sepValues

        if self.largeGroupSepAcceptedColumn == sepColumn:
            return sepValues

        answer = QMessageBox.warning(
            self.rootWidget,
            'Boxplot',
            '分太多圖了！',
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return None
        self.largeGroupSepAcceptedColumn = sepColumn
        return sepValues

    def _on_group_sep_changed(self, *_args) -> None:
        sepColumn = self._selected_group_sep_column()
        columnChanged = sepColumn != self.activeGroupSepColumn
        if columnChanged:
            self.groupSepLineSettings.clear()
            self.currentSepPlotValues = []
            self.largeGroupSepAcceptedColumn = None
            self.gridsLinesPushButton.setEnabled(False)
            self._update_grid_controls_enabled(False)
            self._refresh_single_select_options([])
        self.activeGroupSepColumn = sepColumn

        if not sepColumn:
            self.lastGroupSepStatus = ''
            self._refresh_single_select_options([])
            self._draw_plot_when_ready()
            return

        sepValues = self._confirm_group_sep_count(sepColumn)
        if sepValues is None:
            blocker = QSignalBlocker(self.groupSepComboBox)
            try:
                self.groupSepComboBox.setCurrentText('none')
            finally:
                del blocker
            self.activeGroupSepColumn = None
            self.groupSepLineSettings.clear()
            self.currentSepPlotValues = []
            self.largeGroupSepAcceptedColumn = None
            self.gridsLinesPushButton.setEnabled(False)
            self._update_grid_controls_enabled(False)
            self.lastGroupSepStatus = ''
            self._refresh_single_select_options([])
            self._set_status('Separate by canceled.')
            self._draw_plot_when_ready()
            return

        self.currentSepPlotValues = sepValues
        self._refresh_single_select_options(sepValues, reset=columnChanged)
        self._draw_plot_when_ready()

    def _build_auto_plot_title(self) -> str:
        yTitle = self.yTitleLineEdit.text().strip() or self._current_combo_text(self.yComboBox)
        group1 = self._current_combo_text(self.group1ComboBox)
        group2 = self._current_combo_text(self.group2ComboBox)
        if not yTitle:
            return ''
        title = f'{yTitle} boxplot'
        if group1:
            title = f'{title} by {group1}'
        if group2:
            title = f'{title} / {group2}'
        return title

    def _build_x_title(self, group1Column: str | None, group2Column: str | None) -> str:
        return ' / '.join(column for column in [group1Column, group2Column] if column)

    def _plotly_points_mode(self) -> tuple[str | bool, float | None]:
        pointsMode = self.pointsComboBox.currentText().strip().lower()
        if pointsMode == 'none':
            return False, None
        if pointsMode == 'jitter':
            return 'all', 0.35
        if pointsMode == 'all':
            return 'all', 0.0
        return 'outliers', None

    def _selected_annotation_stats(self) -> list[str]:
        return [
            statName
            for statName, annotationCheckBox in self.annotationCheckBoxes
            if annotationCheckBox.isChecked()
        ]

    def _update_plot_title(self, *_args) -> None:
        self.plotTitleLineEdit.setText(self._build_auto_plot_title())

    def _redraw_existing_plot(self, *_args) -> None:
        if self.currentPlotFigure is None and not self.currentPlotHtml:
            return
        if not self.isActiveTab:
            self._pendingRedraw = True
            return
        self._set_status('Boxplot settings changed. Redrawing shortly...')
        self.redrawTimer.start()

    def _draw_plot_when_ready(self, *_args) -> None:
        if not self.isActiveTab:
            self._pendingRedraw = True
            return
        if self.tabDataWidget is None:
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        yColumn = self.yComboBox.currentText().strip()
        if dataFrame.empty or not yColumn or yColumn not in dataFrame.columns:
            return

        self._draw_plot()

    def _update_stats_and_redraw(self, *_args) -> None:
        if self.tabDataWidget is None:
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        yColumn = self.yComboBox.currentText().strip()
        if dataFrame.empty or not yColumn or yColumn not in dataFrame.columns:
            return

        ySeries = pd.to_numeric(dataFrame[yColumn], errors='coerce').dropna()
        if ySeries.empty:
            return

        yStdev = float(ySeries.std(ddof=1)) if len(ySeries) > 1 else 0.0
        self._update_statistic_label(float(ySeries.mean()), yStdev)
        self._draw_plot_when_ready()

    def set_tab_data(self, tabDataWidget) -> None:
        self.tabDataWidget = tabDataWidget
        if hasattr(self.tabDataWidget, 'add_data_changed_callback'):
            self.tabDataWidget.add_data_changed_callback(self._refresh_column_options)
        self._refresh_column_options()

    def set_active_tab(self, isActive: bool) -> None:
        wasActive = self.isActiveTab
        self.isActiveTab = isActive
        if not isActive or wasActive:
            return
        if self._pendingDataRefresh:
            self._refresh_column_options()
            return
        if self._pendingRedraw:
            self._pendingRedraw = False
            self._draw_plot_when_ready()

    def _refresh_column_options(self) -> None:
        if self.tabDataWidget is None:
            return
        if not self.isActiveTab:
            self._pendingDataRefresh = True
            return
        self._pendingDataRefresh = False

        dataFrame = self.tabDataWidget.get_plot_data()
        columnNames = list(dataFrame.columns.astype(str))
        combos = [self.yComboBox, self.group1ComboBox, self.group2ComboBox, self.groupSepComboBox]
        blockers = [QSignalBlocker(combo) for combo in combos]
        try:
            for combo in [self.yComboBox, self.group1ComboBox, self.group2ComboBox]:
                currentText = combo.currentText()
                combo.clear()
                combo.addItem('')
                combo.addItems(columnNames)
                if currentText in columnNames:
                    combo.setCurrentText(currentText)
            currentSepText = self.groupSepComboBox.currentText()
            self.groupSepComboBox.clear()
            self.groupSepComboBox.addItem('none')
            self.groupSepComboBox.addItems(columnNames)
            if currentSepText in columnNames:
                self.groupSepComboBox.setCurrentText(currentSepText)
            else:
                self.groupSepComboBox.setCurrentText('none')
        finally:
            del blockers
        sepColumn = self._selected_group_sep_column()
        columnChanged = sepColumn != self.activeGroupSepColumn
        if columnChanged:
            self.groupSepLineSettings.clear()
            self.currentSepPlotValues = []
            self.largeGroupSepAcceptedColumn = None
            self.gridsLinesPushButton.setEnabled(False)
            self._update_grid_controls_enabled(False)
            self.activeGroupSepColumn = sepColumn
        if sepColumn:
            sepValues = self._group_sep_values(dataFrame, sepColumn)
            self.currentSepPlotValues = sepValues
            self._refresh_single_select_options(sepValues, reset=columnChanged)
        else:
            self.currentSepPlotValues = []
            self._refresh_single_select_options([])
        currentY = self.yComboBox.currentText().strip()
        if currentY:
            self._sync_y_title_from_column(currentY)
        self._update_plot_title()
        self._redraw_existing_plot()

    def _parse_range(self, value: str) -> tuple | None:
        if not value:
            return None

        rangeParts = [part.strip() for part in value.split(',') if part.strip()]
        if len(rangeParts) != 2:
            return None
        try:
            return float(rangeParts[0]), float(rangeParts[1])
        except ValueError:
            return None

    def _parse_line_values(self, value: str) -> list[float]:
        if not value:
            return []
        values = []
        for linePart in [item.strip() for item in value.split(',') if item.strip()]:
            try:
                values.append(float(linePart))
            except ValueError:
                continue
        return values

    def _parse_single_line_value(self, value: str) -> float | None:
        values = self._parse_line_values(value)
        if not values:
            return None
        return values[0]

    def _best_subplot_grid(self, plotCount: int) -> tuple[int, int]:
        if plotCount <= 1:
            return 1, 1
        columnCount = math.ceil(math.sqrt(plotCount))
        rowCount = math.ceil(plotCount / columnCount)
        return rowCount, columnCount

    def _group_line_setting(self, sepValue: str) -> dict[str, str]:
        setting = self.groupSepLineSettings.get(sepValue)
        if setting is None:
            setting = {'low': '', 'target': '', 'high': '', 'title': sepValue}
            self.groupSepLineSettings[sepValue] = setting
        if not setting.get('title'):
            setting['title'] = sepValue
        return setting

    def _setting_line_values(self, sepValue: str) -> list[float]:
        setting = self._group_line_setting(sepValue)
        values = []
        for key in ['low', 'target', 'high']:
            lineValue = self._parse_single_line_value(setting.get(key, ''))
            if lineValue is not None:
                values.append(lineValue)
        return values

    def _setting_y_range(self, sepValue: str) -> tuple[float, float] | None:
        setting = self._group_line_setting(sepValue)
        lowValue = self._parse_single_line_value(setting.get('low', ''))
        highValue = self._parse_single_line_value(setting.get('high', ''))
        if lowValue is None or highValue is None:
            return None
        lowerValue = min(lowValue, highValue)
        upperValue = max(lowValue, highValue)
        if lowerValue == upperValue:
            return tuple(self._expanded_line_range([lowerValue, upperValue]))
        if lowerValue >= 0:
            return lowerValue * 0.9, upperValue * 1.1
        if upperValue <= 0:
            return lowerValue * 1.1, upperValue * 0.9
        return tuple(self._expanded_line_range([lowerValue, upperValue]))

    def _show_group_sep_lines_dialog(self) -> None:
        sepColumn = self._selected_group_sep_column()
        if not sepColumn or not self.currentSepPlotValues:
            self.gridsLinesPushButton.setEnabled(False)
            return

        dialog = QDialog(self.rootWidget)
        dialog.setWindowTitle('Boxplot subplot lines')
        layout = QGridLayout(dialog)
        headers = ['group1 value', 'lines low', 'lines target', 'lines high', 'title']
        for columnIndex, headerText in enumerate(headers):
            layout.addWidget(QLabel(headerText), 0, columnIndex)

        rowEditors = {}
        for rowIndex, sepValue in enumerate(self.currentSepPlotValues, start=1):
            setting = self._group_line_setting(sepValue)
            layout.addWidget(QLabel(sepValue), rowIndex, 0)
            lowLineEdit = QLineEdit(setting.get('low', ''))
            targetLineEdit = QLineEdit(setting.get('target', ''))
            highLineEdit = QLineEdit(setting.get('high', ''))
            titleLineEdit = QLineEdit(setting.get('title', sepValue) or sepValue)
            layout.addWidget(lowLineEdit, rowIndex, 1)
            layout.addWidget(targetLineEdit, rowIndex, 2)
            layout.addWidget(highLineEdit, rowIndex, 3)
            layout.addWidget(titleLineEdit, rowIndex, 4)
            rowEditors[sepValue] = {
                'low': lowLineEdit,
                'target': targetLineEdit,
                'high': highLineEdit,
                'title': titleLineEdit,
            }

        buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttonRow = len(self.currentSepPlotValues) + 1
        layout.addWidget(buttonBox, buttonRow, 0, 1, len(headers))
        buttonBox.accepted.connect(dialog.accept)
        buttonBox.rejected.connect(dialog.reject)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        for sepValue, editors in rowEditors.items():
            self.groupSepLineSettings[sepValue] = {
                key: editor.text().strip()
                for key, editor in editors.items()
            }
            if not self.groupSepLineSettings[sepValue]['title']:
                self.groupSepLineSettings[sepValue]['title'] = sepValue
        self._draw_plot_when_ready()

    def _format_number(self, value: float) -> str:
        return f'{value:.6g}'

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

    def _line_color_with_opacity(self, opacity: float) -> str:
        opacity = max(0.0, min(1.0, opacity))
        if self.lineColor.startswith('#') and len(self.lineColor) == 7:
            red = int(self.lineColor[1:3], 16)
            green = int(self.lineColor[3:5], 16)
            blue = int(self.lineColor[5:7], 16)
            return f'rgba({red},{green},{blue},{opacity:.3g})'
        return self.lineColor

    def _annotation_color_with_alpha(self) -> str:
        alpha = max(0.0, min(1.0, self.annotationAlphaSpinBox.value()))
        if self.annotationColor.startswith('#') and len(self.annotationColor) == 7:
            red = int(self.annotationColor[1:3], 16)
            green = int(self.annotationColor[3:5], 16)
            blue = int(self.annotationColor[5:7], 16)
            return f'rgba({red},{green},{blue},{alpha:.3g})'
        return self.annotationColor

    def _auto_fill_plot_stats(self) -> None:
        if self.tabDataWidget is None:
            self._set_status('No data source attached to boxplot tab.', error=True)
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        yColumn = self.yComboBox.currentText().strip()
        if dataFrame.empty:
            self._set_status('No reshaped data available. Run wide_to_long first.', error=True)
            return
        if not yColumn or yColumn not in dataFrame.columns:
            self._set_status('Choose a valid Y column first.', error=True)
            return

        ySeries = pd.to_numeric(dataFrame[yColumn], errors='coerce').dropna()
        if ySeries.empty:
            self._set_status('Y must contain numeric data for Auto.', error=True)
            return

        yAverage = float(ySeries.mean())
        yStdev = float(ySeries.std(ddof=1)) if len(ySeries) > 1 else 0.0
        hlineValues = [yAverage - 3 * yStdev, yAverage + 3 * yStdev]
        yDecimals = self._decimal_places_for_series(dataFrame[yColumn])

        self.yRangeLineEdit.setText(
            self._format_auto_values(self._expanded_line_range(hlineValues), yDecimals)
        )
        self.hlineLineEdit.setText(self._format_auto_values(hlineValues, yDecimals))
        self._update_statistic_label(yAverage, yStdev)
        self._draw_plot_when_ready()

    def _update_statistic_label(self, yAverage: float, yStdev: float) -> None:
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

        self.statisticLabel.setText(', '.join(parts))

    def _show_pivot_table(self) -> None:
        if self.tabDataWidget is None:
            self._set_status('No data source attached to boxplot tab.', error=True)
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        yColumn = self.yComboBox.currentText().strip()
        if dataFrame.empty:
            self._set_status('No data available for pivot.', error=True)
            return
        if not yColumn or yColumn not in dataFrame.columns:
            self._set_status('Choose a valid Y column before pivot.', error=True)
            return

        groupColumns = []
        for combo in [self.group1ComboBox, self.group2ComboBox]:
            columnName = combo.currentText().strip()
            if columnName and columnName in dataFrame.columns and columnName not in groupColumns:
                groupColumns.append(columnName)

        pivotData = build_pivot_table(dataFrame, yColumn, groupColumns)
        if pivotData.empty:
            self._set_status('No numeric Y data available for pivot.', error=True)
            return
        show_pivot_dialog(self.rootWidget, 'Boxplot pivot', pivotData)

    def _ordered_categories(self, series: pd.Series) -> list:
        return sorted(
            list(pd.Series(series.dropna().unique()).astype(str)),
            key=self._natural_sort_key,
        )

    def _natural_sort_key(self, value: str) -> list:
        keyParts = []
        for part in re.split(r'(-?\d+(?:\.\d+)?)', str(value)):
            if not part:
                continue
            try:
                keyParts.append((0, float(part)))
            except ValueError:
                keyParts.append((1, part.lower()))
        return keyParts

    def _build_box_category(
        self,
        plotData: pd.DataFrame,
        group1Column: str | None,
        group2Column: str | None,
    ) -> tuple[str, list[str]]:
        categoryColumn = '_boxplot_category'
        group1Order = (
            self._ordered_categories(plotData[group1Column])
            if group1Column
            else ['All']
        )
        group2Order = self._ordered_categories(plotData[group2Column]) if group2Column else []

        if group1Column and group2Column:
            plotData[categoryColumn] = (
                plotData[group1Column].astype(str) + '<br>' + plotData[group2Column].astype(str)
            )
            categoryOrder = [
                f'{group1Value}<br>{group2Value}'
                for group1Value in group1Order
                for group2Value in group2Order
                if (
                    (plotData[group1Column].astype(str) == group1Value)
                    & (plotData[group2Column].astype(str) == group2Value)
                ).any()
            ]
        elif group1Column:
            plotData[categoryColumn] = plotData[group1Column].astype(str)
            categoryOrder = group1Order
        elif group2Column:
            plotData[categoryColumn] = plotData[group2Column].astype(str)
            categoryOrder = group2Order
        else:
            plotData[categoryColumn] = 'All'
            categoryOrder = ['All']

        return categoryColumn, categoryOrder

    def _format_annotation_number(self, value: float) -> str:
        formatText = self.annotationFormatLineEdit.text().strip() or '.6g'
        try:
            if '{value' in formatText:
                return formatText.format(value=value)
            return format(value, formatText)
        except (KeyError, IndexError, ValueError):
            return self._format_number(value)

    def _annotation_value(self, series: pd.Series, statName: str) -> str:
        if statName == 'N':
            return str(int(series.count()))
        if series.empty:
            return ''
        if statName == 'max':
            return self._format_annotation_number(float(series.max()))
        if statName == 'q1':
            return self._format_annotation_number(float(series.quantile(0.25)))
        if statName == 'min':
            return self._format_annotation_number(float(series.min()))
        if statName == 'median':
            return self._format_annotation_number(float(series.median()))
        if statName == 'average':
            return self._format_annotation_number(float(series.mean()))
        if statName == 'q3':
            return self._format_annotation_number(float(series.quantile(0.75)))
        if statName == 'standard deviation':
            stdev = float(series.std(ddof=1)) if len(series) > 1 else 0.0
            return self._format_annotation_number(stdev)
        if statName == 'range':
            return self._format_annotation_number(float(series.max() - series.min()))
        return ''

    def _annotation_label(self, statName: str) -> str:
        labels = {
            'N': 'N',
            'max': 'MAX',
            'q1': '1/4Q',
            'min': 'MIN',
            'median': 'MED',
            'average': 'AVG',
            'q3': '3/4Q',
            'standard deviation': 'STDEV',
            'range': 'RANGE',
        }
        return labels.get(statName, statName.upper())

    def _add_box_annotations(
        self,
        fig,
        plotData: pd.DataFrame,
        categoryColumn: str,
        categoryOrder: list[str],
        yColumn: str,
        selectedStats: list[str],
        xref: str = 'x',
        yref: str = 'paper',
        labelXref: str = 'paper',
    ) -> None:
        if not selectedStats:
            return

        annotationFont = dict(
            size=self.annotationSizeSpinBox.value(),
            color=self._annotation_color_with_alpha(),
        )
        groupedData = {
            str(categoryValue): group[yColumn].dropna()
            for categoryValue, group in plotData.groupby(categoryColumn, sort=False, observed =False)
        }
        for categoryValue in categoryOrder:
            series = groupedData.get(categoryValue)
            if series is None:
                continue
            lines = [
                self._annotation_value(series, statName)
                for statName in selectedStats
            ]
            fig.add_annotation(
                x=categoryValue,
                y=0,
                xref=xref,
                yref=yref,
                text='<br>'.join(lines),
                showarrow=False,
                yanchor='bottom',
                yshift=2,
                align='center',
                font=annotationFont,
            )
        fig.add_annotation(
            x=1,
            y=0,
            xref=labelXref,
            yref=yref,
            text='<br>'.join(self._annotation_label(statName) for statName in selectedStats),
            showarrow=False,
            yanchor='bottom',
            xanchor='left',
            xshift=12,
            yshift=2,
            align='left',
            font=annotationFont,
        )

    def _subplot_axis_ref(self, axisName: str, subplotIndex: int) -> str:
        return axisName if subplotIndex == 1 else f'{axisName}{subplotIndex}'

    def _subplot_domain_ref(self, axisName: str, subplotIndex: int) -> str:
        return f'{self._subplot_axis_ref(axisName, subplotIndex)} domain'

    def _add_reference_lines(
        self,
        fig,
        lineValues: list[float],
        yTitle: str,
        lineColor: str,
        lineWidth: float,
        row: int | None = None,
        col: int | None = None,
    ) -> None:
        for hValue in lineValues:
            kwargs = {}
            if row is not None and col is not None:
                kwargs.update({'row': row, 'col': col})
            fig.add_hline(
                y=hValue,
                line_dash='dash',
                line_color=lineColor,
                line_width=lineWidth,
                annotation_text=self._format_line_label(yTitle, hValue),
                annotation_position='top right',
                annotation_font_color=lineColor,
                annotation_font_size=10,
                **kwargs,
            )

    def _draw_group_sep_boxplot(
        self,
        plotData: pd.DataFrame,
        sepColumn: str,
        yColumn: str,
        group1Column: str | None,
        group2Column: str | None,
        plotTitle: str,
        yTitle: str,
        plotlyTheme: str | None,
        yRange: tuple | None,
        legendVisible: bool,
        hLines: list[float],
        lineWidth: float,
        lineColorWithOpacity: str,
        pointsMode: str | bool,
        jitterValue: float | None,
        selectedAnnotationStats: list[str],
        legendFontSize: int,
        leftMargin: int,
        rightMargin: int,
        bottomMargin: int,
    ) -> None:
        allSepValues = self._group_sep_values(plotData, sepColumn)
        sepCount = len(allSepValues)
        self.lastGroupSepStatus = f'{sepColumn} unique values: {sepCount}'
        if sepCount == 0:
            self._set_status(f'{sepColumn} has no values for separate plots.', error=True)
            return
        if sepCount > 12 and self.largeGroupSepAcceptedColumn != sepColumn:
            answer = QMessageBox.warning(
                self.rootWidget,
                'Boxplot',
                '分太多圖了！',
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                blocker = QSignalBlocker(self.groupSepComboBox)
                try:
                    self.groupSepComboBox.setCurrentText('none')
                finally:
                    del blocker
                self.activeGroupSepColumn = None
                self.groupSepLineSettings.clear()
                self.currentSepPlotValues = []
                self.largeGroupSepAcceptedColumn = None
                self.gridsLinesPushButton.setEnabled(False)
                self._update_grid_controls_enabled(False)
                self.lastGroupSepStatus = ''
                self._draw_plot_when_ready()
                return
            self.largeGroupSepAcceptedColumn = sepColumn

        self.currentSepPlotValues = allSepValues
        selectedSingleValue = self._selected_single_sep_value()
        if selectedSingleValue and selectedSingleValue in allSepValues:
            sepValues = [selectedSingleValue]
            self.lastGroupSepStatus = (
                f'{sepColumn} unique values: {sepCount}, showing: {selectedSingleValue}'
            )
        else:
            sepValues = allSepValues
        rowCount, columnCount = self._best_subplot_grid(len(sepValues))
        isGridPlot = len(sepValues) > 1
        subplotTitles = [
            self._group_line_setting(sepValue).get('title', sepValue) or sepValue
            for sepValue in sepValues
        ]
        fig = make_subplots(
            rows=rowCount,
            cols=columnCount,
            subplot_titles=subplotTitles,
            horizontal_spacing=self._subplot_spacing(self.horizontalSpaceSpinBox, columnCount),
            vertical_spacing=self._subplot_spacing(self.verticalSpaceSpinBox, rowCount),
        )
        xTitle = self._build_x_title(group1Column, group2Column)
        shownLegendNames = set()

        for sepIndex, sepValue in enumerate(sepValues):
            row = sepIndex // columnCount + 1
            col = sepIndex % columnCount + 1
            subPlotData = plotData[plotData[sepColumn].astype(str) == sepValue].copy()
            if subPlotData.empty:
                continue
            categoryColumn, categoryOrder = self._build_box_category(
                subPlotData,
                group1Column,
                group2Column,
            )
            subPlotData[categoryColumn] = pd.Categorical(
                subPlotData[categoryColumn],
                categories=categoryOrder,
                ordered=True,
            )
            subPlotData = subPlotData.sort_values(categoryColumn)
            subFig = px.box(
                subPlotData,
                x=categoryColumn,
                y=yColumn,
                color=group2Column,
                points=pointsMode,
                labels={yColumn: yTitle, categoryColumn: xTitle},
                category_orders={categoryColumn: categoryOrder},
                template=plotlyTheme,
            )
            if jitterValue is not None:
                subFig.update_traces(jitter=jitterValue, pointpos=0)
            for trace in subFig.data:
                legendName = trace.name or ''
                trace.showlegend = bool(legendVisible and legendName not in shownLegendNames)
                shownLegendNames.add(legendName)
                fig.add_trace(trace, row=row, col=col)
            fig.update_xaxes(
                title_text=xTitle,
                categoryorder='array',
                categoryarray=categoryOrder,
                row=row,
                col=col,
            )
            subplotYRange = self._setting_y_range(sepValue) or yRange
            fig.update_yaxes(
                title_text=yTitle,
                range=subplotYRange,
                row=row,
                col=col,
            )
            subplotAxisIndex = sepIndex + 1
            self._add_box_annotations(
                fig,
                subPlotData,
                categoryColumn,
                categoryOrder,
                yColumn,
                selectedAnnotationStats,
                xref=self._subplot_axis_ref('x', subplotAxisIndex),
                yref=self._subplot_domain_ref('y', subplotAxisIndex),
                labelXref=self._subplot_domain_ref('x', subplotAxisIndex),
            )
            self._add_reference_lines(
                fig,
                hLines,
                yTitle,
                lineColorWithOpacity,
                lineWidth,
                row=row,
                col=col,
            )
            self._add_reference_lines(
                fig,
                self._setting_line_values(sepValue),
                yTitle,
                lineColorWithOpacity,
                lineWidth,
                row=row,
                col=col,
            )

        plotHeight = max(self.plotHeightSpinBox.value(), 300 * rowCount)
        fig.update_layout(
            title_text=plotTitle,
            template=plotlyTheme,
            boxmode='group',
            legend=dict(
                orientation='v',
                y=1,
                x=1.02,
                title_text='Legend',
                font=dict(size=legendFontSize),
                title_font=dict(size=legendFontSize),
                bordercolor='rgba(0,0,0,0.15)',
                borderwidth=1,
            ),
            margin={'t': 80, 'r': rightMargin, 'l': leftMargin, 'b': bottomMargin},
            showlegend=legendVisible,
            width=self.plotWidthSpinBox.value(),
            height=plotHeight,
        )
        if self.filterAnnotationCheckBox.isChecked():
            add_preview_filter_annotation(
                fig,
                self.tabDataWidget.preview_filter_annotation_text(),
            )
        self._update_grid_controls_enabled(isGridPlot)
        self._render_figure(fig)

    def _draw_plot(self) -> None:
        self.redrawTimer.stop()
        self.gridsLinesPushButton.setEnabled(False)
        self._update_grid_controls_enabled(False)
        if self.tabDataWidget is None:
            self._set_status('No data source attached to boxplot tab.', error=True)
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        if dataFrame.empty:
            self._set_status('No reshaped data available. Run wide_to_long first.', error=True)
            return

        yColumn = self.yComboBox.currentText().strip()
        group1Column = self.group1ComboBox.currentText().strip() or None
        group2Column = self.group2ComboBox.currentText().strip() or None
        sepColumn = self._selected_group_sep_column()
        if not yColumn:
            self._set_status('Choose a Y column first.', error=True)
            return
        if yColumn not in dataFrame.columns:
            self._set_status('Selected Y column not found in data.', error=True)
            return
        for groupColumn in [group1Column, group2Column, sepColumn]:
            if groupColumn and groupColumn not in dataFrame.columns:
                self._set_status('Selected group column not found in data.', error=True)
                return

        plotTitle = self.plotTitleLineEdit.text().strip() or 'Plotly Boxplot'
        yTitle = self.yTitleLineEdit.text().strip() or yColumn
        plotlyTheme = self.plotlyThemeComboBox.currentText().strip()
        plotlyTheme = None if plotlyTheme == 'none' else plotlyTheme or 'plotly'
        yRange = self._parse_range(self.yRangeLineEdit.text())
        legendVisible = self.legendCheckBox.isChecked()
        hLines = self._parse_line_values(self.hlineLineEdit.text())
        lineWidth = self.lineWidthSpinBox.value()
        lineColorWithOpacity = self._line_color_with_opacity(lineWidth)
        pointsMode, jitterValue = self._plotly_points_mode()
        selectedAnnotationStats = self._selected_annotation_stats()
        legendFontSize = self.legendFontSizeSpinBox.value()

        requiredColumns = list(dict.fromkeys(
            columnName
            for columnName in [yColumn, group1Column, group2Column, sepColumn]
            if columnName
        ))
        plotData = dataFrame.loc[:, requiredColumns].copy()
        plotData[yColumn] = pd.to_numeric(plotData[yColumn], errors='coerce')
        dropColumns = [
            yColumn,
            *[column for column in [group1Column, group2Column, sepColumn] if column],
        ]
        plotData = plotData.dropna(subset=dropColumns)
        if plotData.empty:
            self._set_status('No numeric Y data available for boxplot.', error=True)
            return

        yStatSeries = plotData[yColumn].dropna()
        yStdev = float(yStatSeries.std(ddof=1)) if len(yStatSeries) > 1 else 0.0
        self._update_statistic_label(float(yStatSeries.mean()), yStdev)

        categoryColumn, categoryOrder = self._build_box_category(
            plotData,
            group1Column,
            group2Column,
        )
        plotData[categoryColumn] = pd.Categorical(
            plotData[categoryColumn],
            categories=categoryOrder,
            ordered=True,
        )
        plotData = plotData.sort_values(categoryColumn)
        bottomMargin = 80
        leftMargin = 60
        rightMargin = 230 if selectedAnnotationStats else 180

        try:
            if sepColumn:
                self._draw_group_sep_boxplot(
                    plotData,
                    sepColumn,
                    yColumn,
                    group1Column,
                    group2Column,
                    plotTitle,
                    yTitle,
                    plotlyTheme,
                    yRange,
                    legendVisible,
                    hLines,
                    lineWidth,
                    lineColorWithOpacity,
                    pointsMode,
                    jitterValue,
                    selectedAnnotationStats,
                    legendFontSize,
                    leftMargin,
                    rightMargin,
                    bottomMargin,
                )
                return

            self.currentSepPlotValues = []
            fig = px.box(
                plotData,
                x=categoryColumn,
                y=yColumn,
                color=group2Column,
                points=pointsMode,
                title=plotTitle,
                labels={yColumn: yTitle, categoryColumn: self._build_x_title(group1Column, group2Column)},
                category_orders={categoryColumn: categoryOrder},
                template=plotlyTheme,
            )
            if jitterValue is not None:
                fig.update_traces(jitter=jitterValue, pointpos=0)
            fig.update_layout(
                boxmode='group',
                legend=dict(
                    orientation='v',
                    y=1,
                    x=1.02,
                    title_text='Legend',
                    font=dict(size=legendFontSize),
                    title_font=dict(size=legendFontSize),
#                    bgcolor='rgba(255,255,255,0.92)',
                    bordercolor='rgba(0,0,0,0.15)',
                    borderwidth=1,
                ),
                margin={'t': 60, 'r': rightMargin, 'l': leftMargin, 'b': bottomMargin},
                showlegend=legendVisible,
                width=self.plotWidthSpinBox.value(),
                height=self.plotHeightSpinBox.value(),
            )
            fig.update_xaxes(
                title_text=self._build_x_title(group1Column, group2Column),
                categoryorder='array',
                categoryarray=categoryOrder,
            )
            fig.update_yaxes(title_text=yTitle)
            if yRange is not None:
                fig.update_yaxes(range=yRange)

            self._add_box_annotations(
                fig,
                plotData,
                categoryColumn,
                categoryOrder,
                yColumn,
                selectedAnnotationStats,
            )

            self._add_reference_lines(
                fig,
                hLines,
                yTitle,
                lineColorWithOpacity,
                lineWidth,
            )

            if self.filterAnnotationCheckBox.isChecked():
                add_preview_filter_annotation(
                    fig,
                    self.tabDataWidget.preview_filter_annotation_text(),
                )

            self._render_figure(fig)
        except Exception as exc:
            self._set_status(f'Failed to draw boxplot: {exc}', error=True)

    def _render_figure(self, figure) -> None:
        self.currentPlotFigure = figure
        self.currentPlotHtml = ''
        self._set_status('Rendering boxplot HTML...')
        self.loadingOverlay.show('Loading...')

        def work() -> str:
            return local_plotly_html(
                figure,
                fullHtml=True,
                annotationNamespace='boxplot',
            )

        self._activeRenderTaskId = self._start_background_task(
            work,
            self._on_render_figure_finished,
            self._on_render_figure_failed,
        )

    def _on_render_figure_finished(self, taskId: int, result: str) -> None:
        if taskId != getattr(self, '_activeRenderTaskId', None):
            return
        self.loadingOverlay.hide()
        self.currentPlotHtml = result
        isGroupSepPlot = bool(self._selected_group_sep_column() and self.currentSepPlotValues)
        self.gridsLinesPushButton.setEnabled(isGroupSepPlot)
        isGridPlot = bool(isGroupSepPlot and not self._selected_single_sep_value() and len(self.currentSepPlotValues) > 1)
        self._update_grid_controls_enabled(isGridPlot)
        statusText = 'Boxplot created successfully.'
        if isGroupSepPlot and self.lastGroupSepStatus:
            statusText = f'{statusText} {self.lastGroupSepStatus}.'
        if not self.useExternalBrowser:
            assetsDir = Path(__file__).resolve().parent
            try:
                baseUrl = QUrl.fromLocalFile(str(assetsDir)+'/')
                self.chartView.setHtml(self.currentPlotHtml, baseUrl)
                self._set_status(statusText)
                return
            except Exception:
                self._switch_to_external_browser_view()

        self.currentPlotFilePath = str(Path(tempfile.gettempdir()) / 'p-chart-boxplot.html')
        with open(self.currentPlotFilePath, 'w', encoding='utf-8') as htmlFile:
            htmlFile.write(self.currentPlotHtml)

        plotUri = Path(self.currentPlotFilePath).resolve().as_uri()
        self.currentViewerFilePath = str(Path(tempfile.gettempdir()) / 'p-chart-boxplot-viewer.html')
        viewerUri = Path(self.currentViewerFilePath).resolve().as_uri()
        viewerHtml = f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>p-chart Boxplot</title>
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
            '<p>Boxplot is shown in the system browser.</p>'
            f'<p><a href="{viewerUri}">Open boxplot browser viewer</a></p>'
            '<p>The viewer reloads the latest Plotly HTML automatically. Use Download HTML to save a copy.</p>'
            '<p>無法啟動 PySide6.WebEngine, 原因可能是 遠端桌面, 系統老舊沒有 GPU, 或者啟動時加了"--no-webengine"</p>'
            '<p>結果會是畫面因為字型大小跑掉很醜， 圖不能在這裡顯示, 要到瀏覽器看</p>'
            '<p><h1>不是我的錯！</h1></p>'
            '</div>'
        )
        self._set_status(statusText)

    def _on_render_figure_failed(self, taskId: int, errorText: str) -> None:
        if taskId != getattr(self, '_activeRenderTaskId', None):
            return
        self.loadingOverlay.hide()
        self.gridsLinesPushButton.setEnabled(False)
        self._update_grid_controls_enabled(False)
        self._set_status(f'Failed to render boxplot HTML: {errorText}', error=True)

    def _download_html(self) -> None:
        if not self.currentPlotHtml:
            self._set_status('No boxplot HTML available. Draw a boxplot first.', error=True)
            return

        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Download Plotly HTML',
            'boxplot.html',
            'HTML Files (*.html);;All Files (*)',
        )
        if not selectedFile:
            return
        if not selectedFile.lower().endswith(('.html', '.htm')):
            selectedFile = f'{selectedFile}.html'

        try:
            with open(selectedFile, 'w', encoding='utf-8') as htmlFile:
                htmlFile.write(self.currentPlotHtml)
            self._set_status(f'Boxplot HTML saved to {selectedFile}.')
        except Exception as exc:
            self._set_status(f'Failed to save Plotly HTML: {exc}', error=True)

    def _download_png(self) -> None:
        if self.currentPlotFigure is None:
            self._set_status('No boxplot available. Draw a boxplot first.', error=True)
            return

        selectedFile = ''
        if shift_click_requests_png_file():
            selectedFile, _ = QFileDialog.getSaveFileName(
                self.rootWidget,
                'Save Plotly PNG',
                'boxplot.png',
                'PNG Files (*.png);;All Files (*)',
            )
            if not selectedFile:
                return
            if not selectedFile.lower().endswith('.png'):
                selectedFile = f'{selectedFile}.png'

        self.downloadPngButton.setEnabled(False)
        self._set_status('Creating Boxplot PNG...')
        figure = self.currentPlotFigure
        self._activePngTaskId = self._start_background_task(
            lambda: render_plotly_png(figure, selectedFile),
            lambda taskId, pngBytes: self._on_png_export_finished(
                taskId, pngBytes, selectedFile
            ),
            self._on_png_export_failed,
        )

    def _on_png_export_finished(
        self, taskId: int, pngBytes: bytes, selectedFile: str
    ) -> None:
        if taskId != getattr(self, '_activePngTaskId', None):
            return
        self.downloadPngButton.setEnabled(True)
        if selectedFile:
            self._set_status(f'Boxplot PNG saved to {selectedFile}.')
            return
        try:
            copy_png_bytes_to_clipboard(pngBytes)
            self._set_status('Boxplot PNG copied to clipboard.')
        except Exception as exc:
            self._set_status(f'Failed to copy Boxplot PNG: {exc}', error=True)

    def _on_png_export_failed(self, taskId: int, errorText: str) -> None:
        if taskId != getattr(self, '_activePngTaskId', None):
            return
        self.downloadPngButton.setEnabled(True)
        self._set_status(f'Failed to create Boxplot PNG: {errorText}', error=True)

    def _set_status(self, message: str, error: bool = False) -> None:
        self.statusLabel.setText(message)
        self.statusLabel.setStyleSheet('color: red;' if error else 'color: black;')
