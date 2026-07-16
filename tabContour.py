import math
import os
import re
import sys
import tempfile
import webbrowser
from pathlib import Path

import matplotlib.tri as mtri
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from matplotlib.path import Path as MplPath
from plotly.subplots import make_subplots
from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt, QUrl
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from async_helpers import BackgroundTaskMixin
from loading_overlay import LoadingOverlay
from pivot_helpers import build_pivot_table, show_pivot_dialog
from plot_export_helpers import (
    copy_png_bytes_to_clipboard,
    render_plotly_png,
    shift_click_requests_png_file,
)
from plotly_local import local_plotly_html
from qt_helpers import require_child
from wafermap_core import build_effective_outline, build_wafer_outline, nearest_value_lookup

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEB_ENGINE_AVAILABLE = True
except ImportError:
    QWebEngineView = None
    WEB_ENGINE_AVAILABLE = False


FILE_PATH_ROLE = int(Qt.ItemDataRole.UserRole)
PRIMARY_FILE_ROLE = FILE_PATH_ROLE + 1
DATAFRAME_ROLE = PRIMARY_FILE_ROLE + 1
COLUMNS_ROLE = DATAFRAME_ROLE + 1
CONTOUR_FILE_EXTENSIONS = ('.csv', '.txt')
CONTOUR_NONE_TEXT = 'none'
CONTOUR_STYLE_LINEAR = 'Linear triangulation'
CONTOUR_STYLE_CUBIC = 'Cubic triangulation'
CONTOUR_STYLE_IDW = 'IDW, inverse distance weighting'
CONTOUR_STYLE_GAUSSIAN = 'Kriging / Gaussian process'
CONTOUR_STYLE_OPTIONS = [
    CONTOUR_STYLE_LINEAR,
    CONTOUR_STYLE_CUBIC,
    CONTOUR_STYLE_IDW,
    CONTOUR_STYLE_GAUSSIAN,
]
VIRTUAL_COORD_OPTIONS = {
    '49點': ('coord-49.csv', 49),
    '81點': ('coord-81.csv', 81),
}


class ContourBuildSuperseded(RuntimeError):
    pass


class ContourFileDropFilter(QObject):
    def __init__(self, onFilesDropped, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.onFilesDropped = onFilesDropped

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self._supported_files(event.mimeData()):
                event.acceptProposedAction()
                return True
        if event.type() == QEvent.Type.Drop:
            filePaths = self._supported_files(event.mimeData())
            if filePaths:
                self.onFilesDropped(filePaths)
                event.acceptProposedAction()
                return True
        return super().eventFilter(watched, event)

    def _supported_files(self, mimeData) -> list[str]:
        if not mimeData.hasUrls():
            return []
        return [
            url.toLocalFile()
            for url in mimeData.urls()
            if url.isLocalFile()
            and url.toLocalFile().lower().endswith(CONTOUR_FILE_EXTENSIONS)
        ]


class TabContourWidget(BackgroundTaskMixin):
    _waferColumnPattern = re.compile(r'wafer|wafer_?id|waferid', re.IGNORECASE)

    def __init__(self, rootWidget: QWidget, preferWebEngine: bool = True) -> None:
        self.rootWidget = rootWidget
        self.tabDataWidget = None
        self.preferWebEngine = preferWebEngine
        self.useExternalBrowser = True

        self.tabWidget = require_child(rootWidget, QWidget, 'tabContour')
        self.xComboBox = require_child(rootWidget, QComboBox, 'contourXComboBox')
        self.yComboBox = require_child(rootWidget, QComboBox, 'contourYComboBox')
        self.zComboBox = require_child(rootWidget, QComboBox, 'contourZComboBox')
        self.waferIDComboBox = require_child(rootWidget, QComboBox, 'contourWaferIDComboBox')
        self.waferColComboBox = require_child(rootWidget, QComboBox, 'contourWaferColComboBox')
        self.styleComboBox = require_child(rootWidget, QComboBox, 'contourStyleComboBox')
        self.titleLineEdit = require_child(rootWidget, QLineEdit, 'contourTitleLineEdit')
        self.limitHighLineEdit = require_child(rootWidget, QLineEdit, 'contourLimitHighLineEdit')
        self.limitLowLineEdit = require_child(rootWidget, QLineEdit, 'contourLimitLowLineEdit')
        self.fillCheckBox = require_child(rootWidget, QCheckBox, 'contourFillCheckBox')
        self.lineSpaceLineEdit = require_child(rootWidget, QLineEdit, 'contourLineSpaceLineEdit')
        self.limitHighColorLabel = require_child(rootWidget, QLabel, 'contourLimitHighColorLabel')
        self.limitLowColorLabel = require_child(rootWidget, QLabel, 'contourLimitLowColorLabel')
        self.singleRadioButton = require_child(rootWidget, QRadioButton, 'contourChartsSingleRadioButton')
        self.gridRadioButton = require_child(rootWidget, QRadioButton, 'contourChartsGridRadioButton')
        self.cleanFilesButton = require_child(rootWidget, QPushButton, 'contourCleanFilesButton')
        self.filesList = require_child(rootWidget, QListWidget, 'contourFilesList')
        self.plotAreaWidget = require_child(rootWidget, QWidget, 'contourPlotAreaWidget')
        self.statusLabel = require_child(rootWidget, QLabel, 'contourStatusLabel')
        self.edgeColorLabel = require_child(rootWidget, QLabel, 'contourEdgeColorLabel')
        self.excludeColorLabel = require_child(rootWidget, QLabel, 'contourExcludeColorLabel')
        self.excludeSpinBox = require_child(rootWidget, QWidget, 'contourExcludeLSpinBox')
        self.waferDiameterComboBox = require_child(rootWidget, QComboBox, 'contourWaferDiameterComboBox')
        self.waferFlatComboBox = require_child(rootWidget, QComboBox, 'contourWaferFlatComboBox')
        self.showDetailCheckBox = require_child(rootWidget, QCheckBox, 'contourShowDetailCheckBox')
        self.showValueCheckBox = require_child(rootWidget, QCheckBox, 'contourShowValueCheckBox')
        self.fontSizeSpinBox = require_child(rootWidget, QSpinBox, 'contourFontSizeSpinBox')
        self.plotButton = require_child(rootWidget, QPushButton, 'contourPlotButton')
        self.pivotPushButton = require_child(rootWidget, QPushButton, 'pivotPushButton')
        self.downloadPngButton = require_child(rootWidget, QPushButton, 'contourDownloadPngButton')
        self.downloadHtmlButton = require_child(rootWidget, QPushButton, 'contourDownloadHtmlButton')

        self.currentPlotHtml = ''
        self.currentPlotFigure = None
        self.currentPlotFilePath = ''
        self.currentViewerFilePath = ''
        self.hasDrawRequest = False
        self.primaryFilePath = ''
        self._browserViewerOpened = False
        self._updatingControls = False
        self._updatingFilesList = False
        self._checkableWaferMode = False
        self._updatingWaferChecks = False
        self._lastPolarWarning = ''
        self.isActiveTab = False
        self._pendingPrimarySync = False
        self.cancelledVirtualCoordinateKey = None
        self._contourGridCache = {}
        self._contourBuildGeneration = 0
        self._activeBuildTaskId = None
        self._activeRenderTaskId = None
        self._virtualCoordinateDialogOpen = False
        self._applyingVirtualCoordinates = False

        self.waferIdModel = QStandardItemModel(self.waferIDComboBox)
        self.waferIDComboBox.setModel(self.waferIdModel)
        self.waferIDComboBox.view().pressed.connect(self._on_wafer_item_pressed)
        self.waferIdModel.itemChanged.connect(self._on_wafer_item_changed)

        self.fileDropFilter = ContourFileDropFilter(self._add_external_files, self.rootWidget)

        self._configure_plot_area()
        self.loadingOverlay = LoadingOverlay(self.plotAreaWidget)
        self.redrawTimer = QTimer(self.rootWidget)
        self.redrawTimer.setSingleShot(True)
        self.redrawTimer.setInterval(800)
        self.redrawTimer.timeout.connect(self._draw_plot)
        self._configure_defaults()
        self._configure_signals()
        self._apply_chart_mode()

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
        self.waferDiameterComboBox.clear()
        self.waferDiameterComboBox.addItems(['150', '200'])
        self.waferDiameterComboBox.setCurrentText('150')
        self.waferFlatComboBox.clear()
        self.waferFlatComboBox.addItems(['47.5 mm', '57.5 mm', 'notch-135', 'notch-180'])
        self.waferFlatComboBox.setCurrentText('57.5 mm')
        self.styleComboBox.clear()
        self.styleComboBox.addItems(CONTOUR_STYLE_OPTIONS)
        self.styleComboBox.setCurrentText(CONTOUR_STYLE_LINEAR)
        if not self.titleLineEdit.text().strip():
            self.titleLineEdit.setText('Contour Map')
        self._update_contour_fill_controls()
        self.filesList.setAcceptDrops(True)
        self.filesList.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.filesList.installEventFilter(self.fileDropFilter)
        self.filesList.viewport().setAcceptDrops(True)
        self.filesList.viewport().installEventFilter(self.fileDropFilter)

    def _configure_signals(self) -> None:
        self.plotButton.clicked.connect(self._draw_plot)
        self.cleanFilesButton.clicked.connect(self._clean_external_files)
        self.pivotPushButton.clicked.connect(self._show_pivot_table)
        self.downloadHtmlButton.clicked.connect(self._download_html)
        self.downloadPngButton.clicked.connect(self._download_png)
        self.filesList.itemSelectionChanged.connect(self._on_file_selection_changed)
        self.singleRadioButton.toggled.connect(self._on_chart_mode_toggled)
        self.gridRadioButton.toggled.connect(self._on_chart_mode_toggled)
        self.waferColComboBox.currentTextChanged.connect(self._on_wafer_column_changed)
        self.styleComboBox.currentTextChanged.connect(self._on_contour_style_changed)
        self.xComboBox.currentTextChanged.connect(self._on_column_selection_changed)
        self.yComboBox.currentTextChanged.connect(self._on_column_selection_changed)
        self.zComboBox.currentTextChanged.connect(self._on_z_column_changed)
        self.waferIDComboBox.currentTextChanged.connect(self._draw_plot_when_ready)
        for lineEdit in [
            self.titleLineEdit,
            self.limitHighLineEdit,
            self.limitLowLineEdit,
            self.lineSpaceLineEdit,
        ]:
            lineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.fillCheckBox.stateChanged.connect(self._on_contour_fill_changed)
        self.waferDiameterComboBox.currentTextChanged.connect(self._draw_plot_when_ready)
        self.waferFlatComboBox.currentTextChanged.connect(self._draw_plot_when_ready)
        self.excludeSpinBox.valueChanged.connect(self._draw_plot_when_ready)
        self.showDetailCheckBox.stateChanged.connect(self._draw_plot_when_ready)
        self.showValueCheckBox.stateChanged.connect(self._draw_plot_when_ready)
        self.fontSizeSpinBox.valueChanged.connect(self._draw_plot_when_ready)

    def set_tab_data(self, tabDataWidget) -> None:
        self.tabDataWidget = tabDataWidget
        if hasattr(self.tabDataWidget, 'add_data_changed_callback'):
            self.tabDataWidget.add_data_changed_callback(self._sync_primary_data)
        self._sync_primary_data()

    def set_active_tab(self, isActive: bool) -> None:
        wasActive = self.isActiveTab
        self.isActiveTab = isActive
        if isActive and not wasActive:
            if self._pendingPrimarySync:
                self._sync_primary_data()
            self.cancelledVirtualCoordinateKey = None
            self._draw_plot_when_ready()

    def _sync_primary_data(self) -> None:
        if self.tabDataWidget is None:
            return
        if self._applyingVirtualCoordinates:
            return
        if not self.isActiveTab:
            self._pendingPrimarySync = True
            return
        self._pendingPrimarySync = False
        self._clear_virtual_coordinate_cancel()
        self._clear_contour_cache()
        dataFrame = self.tabDataWidget.get_plot_data()
        normalizedPath = self._normalized_path(self.tabDataWidget.loadedFilePath) if self.tabDataWidget.loadedFilePath else ''
        primaryKey = normalizedPath if normalizedPath else '<tabdata-current>'

        self._updatingFilesList = True
        primaryItem = self._primary_item()
        if dataFrame.empty:
            if primaryItem is not None:
                self.filesList.takeItem(self.filesList.row(primaryItem))
            self.primaryFilePath = ''
        else:
            displayName = Path(normalizedPath).name if normalizedPath else 'TabData current data'
            if primaryItem is None:
                primaryItem = self._make_file_item(primaryKey, displayName, dataFrame, primary=True)
                self.filesList.insertItem(0, primaryItem)
            else:
                primaryItem.setText(displayName)
                primaryItem.setData(FILE_PATH_ROLE, primaryKey)
                primaryItem.setData(DATAFRAME_ROLE, dataFrame.copy())
                primaryItem.setData(COLUMNS_ROLE, list(dataFrame.columns.astype(str)))
                primaryItem.setToolTip(normalizedPath or 'TabData current processed data')
            self.primaryFilePath = primaryKey
            if not self.filesList.selectedItems():
                primaryItem.setSelected(True)
        self._updatingFilesList = False
        self._select_first_if_needed()
        self._refresh_column_options()

    def _primary_item(self) -> QListWidgetItem | None:
        for rowIndex in range(self.filesList.count()):
            item = self.filesList.item(rowIndex)
            if bool(item.data(PRIMARY_FILE_ROLE)):
                return item
        return None

    def _make_file_item(
        self,
        filePath: str,
        displayName: str,
        dataFrame: pd.DataFrame,
        primary: bool,
    ) -> QListWidgetItem:
        item = QListWidgetItem(displayName)
        item.setData(FILE_PATH_ROLE, filePath)
        item.setData(PRIMARY_FILE_ROLE, primary)
        item.setData(DATAFRAME_ROLE, dataFrame.copy())
        item.setData(COLUMNS_ROLE, list(dataFrame.columns.astype(str)))
        item.setToolTip(filePath if filePath and filePath != '<tabdata-current>' else 'TabData current processed data')
        return item

    def _add_external_files(self, filePaths: list[str]) -> None:
        if self.tabDataWidget is None:
            self._set_status('Load data tab once before adding contour files.', error=True)
            return
        self._clear_virtual_coordinate_cancel()
        self._clear_contour_cache()

        existingPaths = {
            str(self.filesList.item(rowIndex).data(FILE_PATH_ROLE) or '')
            for rowIndex in range(self.filesList.count())
        }
        addedCount = 0
        errors = []
        self._updatingFilesList = True
        for filePath in filePaths:
            normalizedPath = self._normalized_path(filePath)
            if (
                not normalizedPath.lower().endswith(CONTOUR_FILE_EXTENSIONS)
                or not os.path.exists(normalizedPath)
                or normalizedPath in existingPaths
            ):
                continue
            try:
                dataFrame = self._read_external_data(normalizedPath)
            except Exception as exc:
                errors.append(f'{Path(normalizedPath).name}: {exc}')
                continue
            item = self._make_file_item(
                normalizedPath,
                Path(normalizedPath).name,
                dataFrame,
                primary=False,
            )
            self.filesList.addItem(item)
            item.setSelected(True)
            existingPaths.add(normalizedPath)
            addedCount += 1
        self._updatingFilesList = False
        self._apply_chart_mode()
        self._refresh_column_options()
        messageParts = []
        if addedCount:
            messageParts.append(f'Added {addedCount} contour file(s).')
        if errors:
            messageParts.append('Skipped: ' + '; '.join(errors))
        if messageParts:
            self._set_status(' '.join(messageParts), error=bool(errors))

    def _read_external_data(self, filePath: str) -> pd.DataFrame:
        skipRows = self.tabDataWidget._detect_skip_rows(
            filePath,
            fallbackSkipRows=self.tabDataWidget.get_skip_rows(),
        )
        csvOptions = self.tabDataWidget._detect_csv_read_options(filePath)
        dataFrame = pd.read_csv(
            filePath,
            skiprows=skipRows,
            encoding=csvOptions['encoding'],
            sep=csvOptions['delimiter'],
        )
        dataFrame = self.tabDataWidget._strip_csv_dataframe_spaces(dataFrame)
        self.tabDataWidget._normalize_loaded_datetime_formats(dataFrame)
        return dataFrame

    def _clean_external_files(self) -> None:
        self._clear_virtual_coordinate_cancel()
        self._clear_contour_cache()
        self._updatingFilesList = True
        for rowIndex in range(self.filesList.count() - 1, -1, -1):
            item = self.filesList.item(rowIndex)
            if not bool(item.data(PRIMARY_FILE_ROLE)):
                self.filesList.takeItem(rowIndex)
        self._updatingFilesList = False
        self._select_first_if_needed()
        self._refresh_column_options()
        self._draw_plot_when_ready()

    def _normalized_path(self, filePath: str) -> str:
        return os.path.normcase(os.path.abspath(filePath))

    def _on_file_selection_changed(self) -> None:
        if self._updatingFilesList:
            return
        self._clear_virtual_coordinate_cancel()
        self._apply_chart_mode()
        self._refresh_column_options()
        self._draw_plot_when_ready()

    def _on_chart_mode_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._clear_virtual_coordinate_cancel()
        self._apply_chart_mode()
        self._refresh_wafer_id_values()
        self._draw_plot_when_ready()

    def _apply_chart_mode(self) -> None:
        if self._single_mode():
            self.filesList.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self._keep_first_selected_file()
        else:
            self.filesList.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._set_wafer_combo_checkable(not self._single_mode())
        self._select_first_if_needed()

    def _single_mode(self) -> bool:
        return self.singleRadioButton.isChecked() or not self.gridRadioButton.isChecked()

    def _keep_first_selected_file(self) -> None:
        selectedItems = self.filesList.selectedItems()
        if len(selectedItems) <= 1:
            return
        firstItem = selectedItems[0]
        blocker = QSignalBlocker(self.filesList)
        self.filesList.clearSelection()
        firstItem.setSelected(True)
        del blocker

    def _select_first_if_needed(self) -> None:
        if self.filesList.count() <= 0 or self.filesList.selectedItems():
            return
        self.filesList.item(0).setSelected(True)

    def _set_wafer_combo_checkable(self, enabled: bool) -> None:
        if self._checkableWaferMode == enabled:
            return
        self._checkableWaferMode = enabled
        self.waferIDComboBox.setEditable(enabled)
        if enabled and self.waferIDComboBox.lineEdit() is not None:
            self.waferIDComboBox.lineEdit().setReadOnly(True)
            self.waferIDComboBox.lineEdit().setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _selected_file_items(self) -> list[QListWidgetItem]:
        return self.filesList.selectedItems()

    def _selected_sources(self) -> list[dict[str, object]]:
        sources = []
        for item in self._selected_file_items():
            dataFrame = item.data(DATAFRAME_ROLE)
            if not isinstance(dataFrame, pd.DataFrame):
                continue
            sources.append({
                'name': item.text(),
                'path': str(item.data(FILE_PATH_ROLE) or ''),
                'dataFrame': dataFrame,
            })
        return sources

    def _active_source(self) -> dict[str, object] | None:
        sources = self._selected_sources()
        return sources[0] if sources else None

    def _active_columns(self) -> list[str]:
        source = self._active_source()
        if source is None:
            return []
        dataFrame = source['dataFrame']
        return list(dataFrame.columns.astype(str)) if isinstance(dataFrame, pd.DataFrame) else []

    def _refresh_column_options(self) -> None:
        if self._updatingControls:
            return
        columnNames = self._active_columns()
        self._updatingControls = True
        xyColumnNames = [CONTOUR_NONE_TEXT, *columnNames]
        self._populate_combo(self.xComboBox, xyColumnNames, self._default_xy_column(columnNames, ('x', 'dx', 'rx')))
        self._populate_combo(self.yComboBox, xyColumnNames, self._default_xy_column(columnNames, ('y', 'dy', 'ry')))
        selectedXY = {
            columnName
            for columnName in [self.xComboBox.currentText().strip(), self.yComboBox.currentText().strip()]
            if not self._is_none_column(columnName)
        }
        currentZ = self.zComboBox.currentText().strip()
        defaultZ = (
            currentZ
            if currentZ in columnNames and currentZ not in selectedXY
            else self._default_z_column(columnNames)
        )
        self._populate_combo(self.zComboBox, columnNames, defaultZ, preserveCurrent=False)
        waferColumnNames = [CONTOUR_NONE_TEXT, *columnNames]
        self._populate_combo(self.waferColComboBox, waferColumnNames, self._default_wafer_column(columnNames))
        self._updatingControls = False
        self._refresh_wafer_id_values()
        self._update_limits_from_z_column()

    def _populate_combo(
        self,
        comboBox: QComboBox,
        columnNames: list[str],
        defaultText: str,
        preserveCurrent: bool = True,
    ) -> None:
        currentText = comboBox.currentText().strip()
        selectedText = currentText if preserveCurrent and currentText in columnNames else defaultText
        comboBox.blockSignals(True)
        comboBox.clear()
        comboBox.addItems(columnNames)
        if selectedText:
            comboBox.setCurrentText(selectedText)
        comboBox.blockSignals(False)

    def _default_xy_column(self, columnNames: list[str], candidates: tuple[str, ...]) -> str:
        for candidate in candidates:
            columnName = self._column_by_upper_name(columnNames, candidate)
            if columnName:
                return columnName
        return CONTOUR_NONE_TEXT

    def _default_z_column(self, columnNames: list[str]) -> str:
        source = self._active_source()
        if source is None:
            return columnNames[0] if columnNames else ''
        dataFrame = source['dataFrame']
        selectedXY = {
            columnName
            for columnName in [self.xComboBox.currentText().strip(), self.yComboBox.currentText().strip()]
            if not self._is_none_column(columnName)
        }
        for columnName in columnNames:
            if columnName in selectedXY:
                continue
            numericSeries = pd.to_numeric(dataFrame[columnName], errors='coerce')
            if numericSeries.notna().any():
                return columnName
        return columnNames[0] if columnNames else ''

    def _default_wafer_column(self, columnNames: list[str]) -> str:
        normalizedMap = {self._normalize_name(columnName): columnName for columnName in columnNames}
        for candidate in ('waferid', 'wafer', 'id'):
            if candidate in normalizedMap:
                return normalizedMap[candidate]
        for columnName in columnNames:
            if self._is_wafer_column(columnName):
                return columnName
        return CONTOUR_NONE_TEXT

    def _is_wafer_column(self, columnName: str) -> bool:
        normalizedText = self._normalize_name(columnName)
        return 'wafer' in normalizedText or 'waferid' in normalizedText

    def _normalize_name(self, columnName: str) -> str:
        return re.sub(r'[\s_-]+', '', str(columnName).strip().lower())

    def _column_by_upper_name(self, columnNames: list[str], targetName: str) -> str:
        targetUpper = str(targetName).strip().upper()
        for columnName in columnNames:
            if str(columnName).strip().upper() == targetUpper:
                return columnName
        return ''

    def _is_none_column(self, columnName: str) -> bool:
        return self._normalize_name(columnName) == CONTOUR_NONE_TEXT

    def _on_wafer_column_changed(self, *_args) -> None:
        if self._updatingControls:
            return
        self._clear_virtual_coordinate_cancel()
        self._clear_contour_cache()
        self._refresh_wafer_id_values()
        self._draw_plot_when_ready()

    def _on_column_selection_changed(self, *_args) -> None:
        if self._updatingControls:
            return
        self._clear_virtual_coordinate_cancel()
        self._clear_contour_cache()
        self._update_limits_from_z_column()
        self._draw_plot_when_ready()

    def _on_z_column_changed(self, *_args) -> None:
        if self._updatingControls:
            return
        self._clear_virtual_coordinate_cancel()
        self._clear_contour_cache()
        self._update_limits_from_z_column()
        self._draw_plot_when_ready()

    def _on_contour_style_changed(self, *_args) -> None:
        self._clear_contour_cache()
        self._draw_plot_when_ready()

    def _on_contour_fill_changed(self, *_args) -> None:
        self._update_contour_fill_controls()
        self._draw_plot_when_ready()

    def _update_contour_fill_controls(self) -> None:
        fillEnabled = self.fillCheckBox.isChecked()
        for widget in (
            self.limitHighLineEdit,
            self.limitLowLineEdit,
            self.limitHighColorLabel,
            self.limitLowColorLabel,
        ):
            widget.setEnabled(fillEnabled)
        self.lineSpaceLineEdit.setEnabled(not fillEnabled)

    def _refresh_wafer_id_values(self) -> None:
        source = self._active_source()
        columnName = self.waferColComboBox.currentText().strip()
        values = ['1'] if self._is_none_column(columnName) else []
        seenValues = set()
        if source is not None and not self._is_none_column(columnName):
            dataFrame = source['dataFrame']
            if columnName in dataFrame.columns:
                for value in dataFrame[columnName]:
                    valueText = self._format_wafer_value(value)
                    if not valueText or valueText in seenValues:
                        continue
                    seenValues.add(valueText)
                    values.append(valueText)

        previousSingleText = self.waferIDComboBox.currentText().strip()
        previousChecked = set(self._checked_wafer_items())
        self._updatingWaferChecks = True
        self.waferIdModel.clear()
        items = ['all', *values]
        for itemText in items:
            item = QStandardItem(itemText)
            if self._checkableWaferMode:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                shouldCheck = (
                    not previousChecked
                    or 'all' in previousChecked
                    or itemText in previousChecked
                )
                item.setCheckState(
                    Qt.CheckState.Checked
                    if shouldCheck
                    else Qt.CheckState.Unchecked
                )
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if itemText == 'all' and values:
                    item.setEnabled(False)
            self.waferIdModel.appendRow(item)
        self._updatingWaferChecks = False
        if self._checkableWaferMode:
            self._sync_all_wafer_check_state()
            self._update_wafer_combo_display()
            return

        preferredText = previousSingleText if previousSingleText in values else ''
        if not preferredText and values:
            preferredText = values[0]
        self.waferIDComboBox.blockSignals(True)
        self.waferIDComboBox.setCurrentText(preferredText or 'all')
        self.waferIDComboBox.blockSignals(False)

    def _format_wafer_value(self, value) -> str:
        if pd.isna(value):
            return ''
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _on_wafer_item_pressed(self, index) -> None:
        if not self._checkableWaferMode:
            return
        item = self.waferIdModel.itemFromIndex(index)
        if item is None:
            return
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def _on_wafer_item_changed(self, item: QStandardItem) -> None:
        if self._updatingWaferChecks or not self._checkableWaferMode:
            return
        self._clear_virtual_coordinate_cancel()
        self._updatingWaferChecks = True
        if item.row() == 0:
            targetState = item.checkState()
            for rowIndex in range(1, self.waferIdModel.rowCount()):
                self.waferIdModel.item(rowIndex).setCheckState(targetState)
        else:
            self._sync_all_wafer_check_state()
        self._updatingWaferChecks = False
        self._update_wafer_combo_display()
        self._draw_plot_when_ready()

    def _sync_all_wafer_check_state(self) -> None:
        if self.waferIdModel.rowCount() <= 0:
            return
        allItem = self.waferIdModel.item(0)
        if self.waferIdModel.rowCount() == 1:
            allItem.setCheckState(Qt.CheckState.Checked)
            return
        allChecked = all(
            self.waferIdModel.item(rowIndex).checkState() == Qt.CheckState.Checked
            for rowIndex in range(1, self.waferIdModel.rowCount())
        )
        allItem.setCheckState(Qt.CheckState.Checked if allChecked else Qt.CheckState.Unchecked)

    def _checked_wafer_items(self) -> list[str]:
        if not self._checkableWaferMode:
            currentText = self.waferIDComboBox.currentText().strip()
            return [currentText] if currentText else []
        checkedItems = []
        for rowIndex in range(self.waferIdModel.rowCount()):
            item = self.waferIdModel.item(rowIndex)
            if item.checkState() == Qt.CheckState.Checked:
                checkedItems.append(item.text())
        return checkedItems

    def _selected_wafer_ids(self) -> list[str]:
        if self._is_none_column(self.waferColComboBox.currentText()):
            return ['1']
        if self._checkableWaferMode:
            checkedItems = self._checked_wafer_items()
            if 'all' in checkedItems:
                return [
                    self.waferIdModel.item(rowIndex).text()
                    for rowIndex in range(1, self.waferIdModel.rowCount())
                ]
            return [itemText for itemText in checkedItems if itemText != 'all']
        currentText = self.waferIDComboBox.currentText().strip()
        return [] if currentText == 'all' else [currentText]

    def _update_wafer_combo_display(self) -> None:
        if not self._checkableWaferMode or self.waferIDComboBox.lineEdit() is None:
            return
        selectedWaferIds = self._selected_wafer_ids()
        if not selectedWaferIds:
            displayText = 'Select wafer IDs'
        elif len(selectedWaferIds) == self.waferIdModel.rowCount() - 1:
            displayText = 'all'
        elif len(selectedWaferIds) == 1:
            displayText = selectedWaferIds[0]
        else:
            displayText = f'{len(selectedWaferIds)} selected'
        self.waferIDComboBox.lineEdit().setText(displayText)

    def _update_limits_from_z_column(self) -> None:
        source = self._active_source()
        zColumn = self.zComboBox.currentText().strip()
        if source is None or not zColumn:
            return
        dataFrame = source['dataFrame']
        if zColumn not in dataFrame.columns:
            return
        numericSeries = pd.to_numeric(dataFrame[zColumn], errors='coerce').dropna()
        if numericSeries.empty:
            return
        zRange = float(numericSeries.max() - numericSeries.min())
        self.limitHighLineEdit.blockSignals(True)
        self.limitLowLineEdit.blockSignals(True)
        self.lineSpaceLineEdit.blockSignals(True)
        self.limitHighLineEdit.setText(self._format_limit(float(numericSeries.max())))
        self.limitLowLineEdit.setText(self._format_limit(float(numericSeries.min())))
        if zRange > 0:
            self.lineSpaceLineEdit.setText(self._format_limit(zRange / 10.0))
        self.limitHighLineEdit.blockSignals(False)
        self.limitLowLineEdit.blockSignals(False)
        self.lineSpaceLineEdit.blockSignals(False)

    def _format_limit(self, value: float) -> str:
        return f'{value:.6g}'

    def _draw_plot_when_ready(self, *_args) -> None:
        if self._virtualCoordinateDialogOpen or self._applyingVirtualCoordinates:
            return
        if not self._has_minimum_plot_inputs():
            self._set_status('Contour settings ready. Load data and choose Z/wafer columns.')
            return
        if not self.isActiveTab:
            self._set_status('Contour settings ready. Switch to Contour tab to draw.')
            return
        self._schedule_draw()

    def _schedule_draw(self) -> None:
        self._set_status('Contour settings changed. Redrawing shortly...')
        self.redrawTimer.start()

    def _has_minimum_plot_inputs(self) -> bool:
        return bool(
            self._selected_sources()
            and self.xComboBox.currentText().strip()
            and self.yComboBox.currentText().strip()
            and self.zComboBox.currentText().strip()
        )

    def _show_pivot_table(self) -> None:
        sources = self._selected_sources()
        if not sources:
            self._set_status('No contour source available for pivot.', error=True)
            return

        zColumn = self.zComboBox.currentText().strip()
        if not zColumn:
            self._set_status('Choose a valid Z column before pivot.', error=True)
            return

        xColumn = self.xComboBox.currentText().strip()
        xColumn = '' if self._is_none_column(xColumn) else xColumn
        waferColumn = self.waferColComboBox.currentText().strip()
        hasWaferColumn = bool(waferColumn and not self._is_none_column(waferColumn))
        selectedWaferIds = self._selected_wafer_ids() if hasWaferColumn else []
        if hasWaferColumn and not selectedWaferIds:
            self._set_status('Choose at least one wafer ID before pivot.', error=True)
            return

        existingColumns = set()
        for source in sources:
            dataFrame = source['dataFrame']
            if isinstance(dataFrame, pd.DataFrame):
                existingColumns.update(dataFrame.columns.astype(str))
        sourceGroupColumn = self._unique_column_name('source', existingColumns)
        waferGroupColumn = self._unique_column_name('wafer', existingColumns | {sourceGroupColumn})

        pivotFrames = []
        skippedSources = []
        for source in sources:
            sourceName = str(source.get('name') or 'source')
            dataFrame = source['dataFrame']
            if not isinstance(dataFrame, pd.DataFrame) or dataFrame.empty:
                skippedSources.append(f'{sourceName}: empty')
                continue
            if zColumn not in dataFrame.columns:
                skippedSources.append(f'{sourceName}: missing {zColumn}')
                continue

            frameColumns = [zColumn]
            activeXColumn = xColumn if xColumn and xColumn in dataFrame.columns else ''
            if activeXColumn:
                frameColumns.append(activeXColumn)
            if hasWaferColumn:
                if waferColumn not in dataFrame.columns:
                    skippedSources.append(f'{sourceName}: missing {waferColumn}')
                    continue
                frameColumns.append(waferColumn)

            pivotFrame = dataFrame[frameColumns].copy()
            if hasWaferColumn:
                pivotFrame[waferGroupColumn] = pivotFrame[waferColumn].map(self._format_wafer_value)
                pivotFrame = pivotFrame.loc[pivotFrame[waferGroupColumn].isin(selectedWaferIds)]
            if len(sources) > 1:
                pivotFrame[sourceGroupColumn] = sourceName
            if pivotFrame.empty:
                skippedSources.append(f'{sourceName}: no selected rows')
                continue
            pivotFrames.append(pivotFrame)

        if not pivotFrames:
            message = 'No data available for contour pivot.'
            if skippedSources:
                message += ' ' + '; '.join(skippedSources)
            self._set_status(message, error=True)
            return

        pivotInputData = pd.concat(pivotFrames, ignore_index=True)
        groupColumns = []
        if len(sources) > 1:
            groupColumns.append(sourceGroupColumn)
        if hasWaferColumn:
            groupColumns.append(waferGroupColumn)

        pivotData = build_pivot_table(pivotInputData, zColumn, groupColumns, xColumn)
        if pivotData.empty:
            self._set_status('No numeric Z data available for contour pivot.', error=True)
            return

        show_pivot_dialog(self.rootWidget, 'Contour pivot', pivotData)
        message = f'Contour pivot ready. rows={len(pivotData)}'
        if skippedSources:
            message += ' skipped: ' + '; '.join(skippedSources)
        self._set_status(message, error=bool(skippedSources))

    def _unique_column_name(self, baseName: str, existingColumns: set[str]) -> str:
        columnName = baseName
        suffix = 2
        while columnName in existingColumns:
            columnName = f'{baseName}_{suffix}'
            suffix += 1
        return columnName

    def _draw_plot(self) -> None:
        self.redrawTimer.stop()
        if not self.isActiveTab:
            self._set_status('Contour settings ready. Switch to Contour tab to draw.')
            return
        buildGeneration = self._next_contour_build_generation()
        self._activeBuildTaskId = None
        self._activeRenderTaskId = None
        self.hasDrawRequest = True
        if not self._has_minimum_plot_inputs():
            self._clear_plot_area('No usable contour data selected.')
            return
        if self._needs_virtual_coordinates() and not self._apply_virtual_coordinates_from_dialog():
            return
        try:
            snapshot = self._contour_build_snapshot(buildGeneration)
        except Exception as exc:
            self.loadingOverlay.hide()
            self._set_status(f'Contour error: {exc}', error=True)
            return

        self.loadingOverlay.show('Building...')
        self._set_status('Building contour figure...')
        self._activeBuildTaskId = self._start_background_task(
            lambda: self._build_figure(snapshot),
            self._on_build_figure_finished,
            self._on_build_figure_failed,
        )

    def _on_build_figure_finished(
        self,
        taskId: int,
        result: tuple[go.Figure | None, str, str],
    ) -> None:
        if taskId != self._activeBuildTaskId:
            return
        figure, statusText, warningText = result
        if figure is None:
            self._clear_plot_area(statusText or 'No contour plot created.')
            return
        self.currentPlotFigure = figure
        self._render_figure(figure, statusText, warningText)

    def _on_build_figure_failed(self, taskId: int, errorText: str) -> None:
        if taskId != self._activeBuildTaskId:
            return
        if errorText == 'Contour build superseded':
            self.loadingOverlay.hide()
            self._set_status('Contour build superseded by newer settings.')
            return
        self.loadingOverlay.hide()
        self._set_status(f'Contour error: {errorText}', error=True)

    def _next_contour_build_generation(self) -> int:
        self._contourBuildGeneration += 1
        return self._contourBuildGeneration

    def _is_current_contour_build(self, snapshot: dict[str, object]) -> bool:
        return int(snapshot.get('buildGeneration', -1)) == self._contourBuildGeneration

    def _raise_if_stale_contour_build(self, snapshot: dict[str, object]) -> None:
        if not self._is_current_contour_build(snapshot):
            raise ContourBuildSuperseded('Contour build superseded')

    def _needs_virtual_coordinates(self) -> bool:
        xColumn = self.xComboBox.currentText().strip()
        yColumn = self.yComboBox.currentText().strip()
        missingX = self._is_none_column(xColumn)
        missingY = self._is_none_column(yColumn)
        if not missingX and not missingY:
            return False

        columnNames = self._active_columns()
        resolvedX = self._column_by_upper_name(columnNames, 'x') if missingX else xColumn
        resolvedY = self._column_by_upper_name(columnNames, 'y') if missingY else yColumn
        if (missingX and resolvedX) or (missingY and resolvedY):
            self._set_xy_combo_text(resolvedX or xColumn, resolvedY or yColumn)
            xColumn = self.xComboBox.currentText().strip()
            yColumn = self.yComboBox.currentText().strip()

        return self._is_none_column(xColumn) or self._is_none_column(yColumn)

    def _apply_virtual_coordinates_from_dialog(self) -> bool:
        zColumn = self.zComboBox.currentText().strip()
        activeSource = self._active_source()
        if activeSource is None or zColumn not in activeSource['dataFrame'].columns:
            self._set_status('Choose a valid Z column before generating virtual coordinates.', error=True)
            return False

        promptKey = self._virtual_coordinate_prompt_key(zColumn)
        if promptKey == self.cancelledVirtualCoordinateKey:
            self._cancel_virtual_coordinate_generation()
            return False

        zRowCount = int(pd.to_numeric(activeSource['dataFrame'][zColumn], errors='coerce').notna().sum())
        self._virtualCoordinateDialogOpen = True
        try:
            selectedOption = self._ask_virtual_coordinate_option(zColumn, zRowCount)
        finally:
            self._virtualCoordinateDialogOpen = False
        if selectedOption is None:
            self.cancelledVirtualCoordinateKey = promptKey
            self._cancel_virtual_coordinate_generation()
            return False

        csvFilename, expectedRows = selectedOption
        sourceSnapshots = []
        try:
            self._applyingVirtualCoordinates = True
            coordDataFrame = self._load_virtual_coordinate_file(csvFilename, expectedRows)
            sourceSnapshots = self._virtual_coordinate_source_snapshots()
            updatedCount = self._apply_virtual_coordinates_to_selected_sources(
                coordDataFrame,
                zColumn,
            )
            if updatedCount > 0:
                self._set_xy_combo_text('x', 'y')
        except Exception as exc:
            self._restore_virtual_coordinate_sources(sourceSnapshots)
            QMessageBox.warning(self.rootWidget, 'Contour', str(exc))
            self._set_status(f'Virtual coordinate error: {exc}', error=True)
            return False
        finally:
            self._applyingVirtualCoordinates = False

        if updatedCount <= 0:
            self._set_status('No selected source can use the virtual coordinates.', error=True)
            return False

        self._clear_virtual_coordinate_cancel()
        self._clear_contour_cache()
        self._set_status(f'Virtual coordinates added from {csvFilename}. sources={updatedCount}')
        return True

    def _virtual_coordinate_prompt_key(self, zColumn: str) -> tuple[str, tuple[tuple[str, int], ...]]:
        sourceKeys = []
        for source in self._selected_sources():
            dataFrame = source['dataFrame']
            if zColumn not in dataFrame.columns:
                continue
            usableCount = int(pd.to_numeric(dataFrame[zColumn], errors='coerce').notna().sum())
            sourceKeys.append((str(source['path'] or source['name']), usableCount))
        return zColumn, tuple(sourceKeys)

    def _clear_virtual_coordinate_cancel(self) -> None:
        self.cancelledVirtualCoordinateKey = None

    def _virtual_coordinate_source_snapshots(self) -> list[tuple[QListWidgetItem, pd.DataFrame, list[str]]]:
        snapshots = []
        for item in self._selected_file_items():
            dataFrame = item.data(DATAFRAME_ROLE)
            if isinstance(dataFrame, pd.DataFrame):
                snapshots.append((item, dataFrame.copy(), list(dataFrame.columns.astype(str))))
        return snapshots

    def _restore_virtual_coordinate_sources(
        self,
        snapshots: list[tuple[QListWidgetItem, pd.DataFrame, list[str]]],
    ) -> None:
        for item, dataFrame, columns in snapshots:
            item.setData(DATAFRAME_ROLE, dataFrame)
            item.setData(COLUMNS_ROLE, columns)

    def _cancel_virtual_coordinate_generation(
        self,
        sourceSnapshots: list[tuple[QListWidgetItem, pd.DataFrame, list[str]]] | None = None,
    ) -> None:
        if sourceSnapshots:
            self._restore_virtual_coordinate_sources(sourceSnapshots)
        self.hasDrawRequest = False
        self._clear_plot_area('Virtual coordinate generation cancelled. No contour plot created.')

    def _clear_contour_cache(self) -> None:
        self._contourGridCache.clear()

    def _ask_virtual_coordinate_option(
        self,
        zColumn: str,
        zRowCount: int,
    ) -> tuple[str, int] | None:
        messageBox = QMessageBox(self.rootWidget)
        messageBox.setWindowTitle('Contour')
        messageBox.setIcon(QMessageBox.Icon.Question)
        messageBox.setText(
            '沒有看到x y的可能欄位，你要我幫你產生虛擬座標？\n\n'
            f'Z({zColumn}) 目前有 {zRowCount} 列資料'
        )
        optionButtons = {}
        for buttonText, option in VIRTUAL_COORD_OPTIONS.items():
            optionButtons[messageBox.addButton(buttonText, QMessageBox.ButtonRole.AcceptRole)] = option
        cancelButton = messageBox.addButton(QMessageBox.StandardButton.Cancel)
        messageBox.setDefaultButton(cancelButton)
        messageBox.exec()
        return optionButtons.get(messageBox.clickedButton())

    def _load_virtual_coordinate_file(
        self,
        csvFilename: str,
        expectedRows: int,
    ) -> pd.DataFrame:
        csvPath = self._resource_path(csvFilename)
        if not csvPath.exists():
            raise ValueError(f'Missing virtual coordinate file: {csvFilename}')

        coordDataFrame = pd.read_csv(csvPath)
        normalizedColumns = {
            self._normalize_name(columnName): columnName
            for columnName in coordDataFrame.columns.astype(str)
        }
        if 'x' not in normalizedColumns or 'y' not in normalizedColumns:
            raise ValueError(f'{csvFilename} must include x,y headers.')

        coordDataFrame = coordDataFrame[[normalizedColumns['x'], normalizedColumns['y']]].copy()
        coordDataFrame.columns = ['x', 'y']
        coordDataFrame = coordDataFrame.apply(pd.to_numeric, errors='coerce')
        coordDataFrame = coordDataFrame.dropna(subset=['x', 'y']).reset_index(drop=True)
        if len(coordDataFrame) != expectedRows:
            raise ValueError(
                f'{csvFilename} has {len(coordDataFrame)} coordinate data rows; '
                f'expected {expectedRows}. CSV header is not counted.'
            )
        return coordDataFrame

    def _resource_path(self, filename: str) -> Path:
        basePath = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
        return Path(basePath) / filename

    def _apply_virtual_coordinates_to_selected_sources(
        self,
        coordDataFrame: pd.DataFrame,
        zColumn: str,
    ) -> int:
        coordValues = coordDataFrame[['x', 'y']].to_numpy(dtype=float)
        updates = []
        for item in self._selected_file_items():
            dataFrame = item.data(DATAFRAME_ROLE)
            if not isinstance(dataFrame, pd.DataFrame) or zColumn not in dataFrame.columns:
                continue
            if dataFrame.empty:
                continue
            usableMask = pd.to_numeric(dataFrame[zColumn], errors='coerce').notna()
            usableCount = int(usableMask.sum())
            if usableCount <= 0 or usableCount % len(coordValues) != 0:
                raise ValueError(
                    f'{item.text()}: Z({zColumn}) has {usableCount} numeric data rows; '
                    f'expected a multiple of {len(coordValues)}. CSV header is not counted.'
                )
            updates.append((item, dataFrame, usableMask, usableCount))

        for item, dataFrame, usableMask, usableCount in updates:
            sourceDataFrame = dataFrame.copy()
            coordIndexes = np.arange(usableCount) % len(coordValues)
            sourceDataFrame.loc[usableMask, 'x'] = coordValues[coordIndexes, 0]
            sourceDataFrame.loc[usableMask, 'y'] = coordValues[coordIndexes, 1]
            item.setData(DATAFRAME_ROLE, sourceDataFrame)
            item.setData(COLUMNS_ROLE, list(sourceDataFrame.columns.astype(str)))
            if bool(item.data(PRIMARY_FILE_ROLE)) and self.tabDataWidget is not None:
                if hasattr(self.tabDataWidget, 'apply_virtual_coordinates'):
                    self.tabDataWidget.apply_virtual_coordinates(sourceDataFrame)
        return len(updates)

    def _set_xy_combo_text(self, xColumn: str, yColumn: str) -> None:
        self._updatingControls = True
        blockers = []
        try:
            for comboBox, columnName in ((self.xComboBox, xColumn), (self.yComboBox, yColumn)):
                blockers.append(QSignalBlocker(comboBox))
                if comboBox.findText(columnName) < 0:
                    comboBox.addItem(columnName)
                comboBox.setCurrentText(columnName)
        finally:
            blockers.clear()
            self._updatingControls = False

    def _contour_build_snapshot(self, buildGeneration: int) -> dict[str, object]:
        fillContour = self.fillCheckBox.isChecked()
        limitLow = None
        limitHigh = None
        lineSpace = None
        if fillContour:
            limitLow, limitHigh = self._color_limits()
        else:
            lineSpace = self._contour_line_space()

        waferDiameter = self._wafer_diameter()
        excludeMm = float(self.excludeSpinBox.value())
        waferOutline = build_wafer_outline(waferDiameter, self._flat_option())
        effectiveOutline = build_effective_outline(
            waferOutline=waferOutline,
            edgeExcludeMm=excludeMm,
        )
        if len(effectiveOutline) < 3:
            raise ValueError('edge exclude 太大，已無可用 wafer 區域。')

        return {
            'buildGeneration': buildGeneration,
            'sources': self._selected_sources(),
            'selectedWaferIds': self._selected_wafer_ids(),
            'singleMode': self._single_mode(),
            'xColumn': self.xComboBox.currentText().strip(),
            'yColumn': self.yComboBox.currentText().strip(),
            'zColumn': self.zComboBox.currentText().strip(),
            'waferColumn': self.waferColComboBox.currentText().strip(),
            'contourStyle': self._contour_style(),
            'waferDiameter': waferDiameter,
            'flatOption': self._flat_option(),
            'excludeMm': excludeMm,
            'waferOutline': waferOutline,
            'effectiveOutline': effectiveOutline,
            'fillContour': fillContour,
            'limitLow': limitLow,
            'limitHigh': limitHigh,
            'lineSpace': lineSpace,
            'colorScale': [
                [0.0, self._label_color(self.limitLowColorLabel, '#2171ff')],
                [1.0, self._label_color(self.limitHighColorLabel, '#ff540e')],
            ],
            'edgeColor': self._label_color(self.edgeColorLabel, '#ff7ccd'),
            'excludeColor': self._label_color(self.excludeColorLabel, '#000000'),
            'showValue': self.showValueCheckBox.isChecked(),
            'showDetail': self.showDetailCheckBox.isChecked(),
            'fontSizes': self._font_sizes(),
            'title': self.titleLineEdit.text().strip() or 'Contour Map',
            'plotWidth': max(500, self.plotAreaWidget.width()),
            'plotHeight': max(500, self.plotAreaWidget.height()),
        }

    def _build_figure(self, snapshot: dict[str, object]) -> tuple[go.Figure | None, str, str]:
        self._raise_if_stale_contour_build(snapshot)

        sources = list(snapshot['sources'])
        selectedWaferIds = list(snapshot['selectedWaferIds'])
        singleMode = bool(snapshot['singleMode'])
        if singleMode:
            if not selectedWaferIds:
                return None, 'Single mode needs one wafer ID, not all.', ''
            sources = sources[:1]
            selectedWaferIds = selectedWaferIds[:1]
        elif not selectedWaferIds:
            return None, 'Grid mode needs at least one wafer ID.', ''

        subplotJobs = []
        if singleMode:
            subplotJobs.append((sources[0], selectedWaferIds[0]))
        else:
            for source in sources:
                for waferId in selectedWaferIds:
                    subplotJobs.append((source, waferId))

        if not subplotJobs:
            return None, 'No selected contour source.', ''

        rowCount, columnCount = self._best_subplot_grid(len(subplotJobs))
        titles = [self._subplot_title(source['name'], waferId) for source, waferId in subplotJobs]
        figure = make_subplots(
            rows=rowCount,
            cols=columnCount,
            subplot_titles=titles,
            horizontal_spacing=0.05,
            vertical_spacing=0.08,
        )
        waferOutline = snapshot['waferOutline']
        effectiveOutline = snapshot['effectiveOutline']
        fillContour = bool(snapshot['fillContour'])
        limitLow = snapshot['limitLow']
        limitHigh = snapshot['limitHigh']
        colorScale = list(snapshot['colorScale'])
        warnings = []
        plottedCount = 0
        emptyCount = 0

        for subplotIndex, (source, waferId) in enumerate(subplotJobs):
            self._raise_if_stale_contour_build(snapshot)

            rowIndex = subplotIndex // columnCount + 1
            columnIndex = subplotIndex % columnCount + 1
            cacheKey = self._contour_cache_key(source, waferId, snapshot)
            cachedContour = self._contourGridCache.get(cacheKey)
            if cachedContour is None:
                preparedData, dataWarning = self._prepared_source_data(source, waferId, effectiveOutline, snapshot)
                contourGrid = None if preparedData.empty else self._build_contour_grid(
                    preparedData,
                    effectiveOutline,
                    str(snapshot['contourStyle']),
                    snapshot,
                )
                siteData = preparedData[['x', 'y', 'z']].copy() if not preparedData.empty else pd.DataFrame()
                self._contourGridCache[cacheKey] = (dataWarning, contourGrid, siteData)
            else:
                dataWarning, contourGrid, siteData = cachedContour
            self._raise_if_stale_contour_build(snapshot)

            if dataWarning:
                warnings.append(f'{source["name"]} #{waferId}: {dataWarning}')
            if contourGrid is None:
                self._add_empty_subplot(figure, rowIndex, columnIndex, waferOutline, effectiveOutline, snapshot)
                if not dataWarning:
                    warnings.append(f'{source["name"]} #{waferId}: insufficient contour points')
                emptyCount += 1
            else:
                self._add_contour_subplot(
                    figure,
                    rowIndex,
                    columnIndex,
                    contourGrid,
                    waferOutline,
                    effectiveOutline,
                    colorScale,
                    limitLow,
                    limitHigh,
                    fillContour,
                    showScale=plottedCount == 0,
                    snapshot=snapshot,
                )
                self._add_site_markers(figure, rowIndex, columnIndex, siteData, snapshot)
                plottedCount += 1
            self._apply_subplot_axes(figure, rowIndex, columnIndex, columnCount, waferOutline, snapshot)

        if plottedCount <= 0:
            statusText = 'No usable contour points in selected wafer area.'
        else:
            statusText = f'Contour plot updated. maps={plottedCount}'
            if emptyCount:
                statusText += f', empty={emptyCount}'
        warningText = '; '.join(dict.fromkeys([warning for warning in warnings if warning]))
        self._apply_common_layout(figure, rowCount, columnCount, snapshot)
        return figure, statusText, warningText

    def _contour_cache_key(
        self,
        source: dict[str, object],
        waferId: str,
        snapshot: dict[str, object],
    ) -> tuple[object, ...]:
        return (
            str(source.get('path') or source.get('name') or ''),
            waferId,
            str(snapshot['xColumn']),
            str(snapshot['yColumn']),
            str(snapshot['zColumn']),
            str(snapshot['waferColumn']),
            str(snapshot['contourStyle']),
            float(snapshot['waferDiameter']),
            str(snapshot['flatOption']),
            float(snapshot['excludeMm']),
        )

    def _subplot_title(self, sourceName: str, waferId: str) -> str:
        return f'{sourceName} #{waferId}' if waferId else sourceName

    def _best_subplot_grid(self, subplotCount: int) -> tuple[int, int]:
        columnCount = math.ceil(math.sqrt(max(1, subplotCount)))
        rowCount = math.ceil(max(1, subplotCount) / columnCount)
        return rowCount, columnCount

    def _wafer_outlines(self) -> tuple[np.ndarray, np.ndarray]:
        diameterMm = self._wafer_diameter()
        waferOutline = build_wafer_outline(diameterMm, self._flat_option())
        effectiveOutline = build_effective_outline(
            waferOutline=waferOutline,
            edgeExcludeMm=float(self.excludeSpinBox.value()),
        )
        if len(effectiveOutline) < 3:
            raise ValueError('edge exclude 太大，已無可用 wafer 區域。')
        return waferOutline, effectiveOutline

    def _wafer_diameter(self) -> float:
        try:
            return float(self.waferDiameterComboBox.currentText().strip())
        except ValueError:
            return 150.0

    def _flat_option(self) -> str:
        text = self.waferFlatComboBox.currentText().strip().lower()
        if 'notch' in text and '135' in text:
            return 'notch-135'
        if 'notch' in text:
            return 'notch-180'
        if '57.5' in text:
            return '57.5 mm'
        return '47.5 mm'

    def _color_limits(self) -> tuple[float, float]:
        try:
            limitHigh = float(self.limitHighLineEdit.text().strip())
            limitLow = float(self.limitLowLineEdit.text().strip())
        except ValueError:
            raise ValueError('Contour high/low limits must be numeric.')
        if limitHigh <= limitLow:
            raise ValueError('Contour high limit must be greater than low limit.')
        return limitLow, limitHigh

    def _contour_line_space(self) -> float:
        try:
            lineSpace = float(self.lineSpaceLineEdit.text().strip())
        except ValueError:
            raise ValueError('Contour line space must be numeric.')
        if lineSpace <= 0:
            raise ValueError('Contour line space must be greater than zero.')
        return lineSpace

    def _contour_line_range(
        self,
        gridZ: np.ndarray,
        lineSpace: float | None = None,
    ) -> tuple[float, float]:
        finiteValues = gridZ[np.isfinite(gridZ)]
        if finiteValues.size <= 0:
            raise ValueError('No finite contour values for line contour.')
        if lineSpace is None:
            lineSpace = self._contour_line_space()
        valueMin = float(finiteValues.min())
        valueMax = float(finiteValues.max())
        lineStart = math.floor(valueMin / lineSpace) * lineSpace
        lineEnd = math.ceil(valueMax / lineSpace) * lineSpace
        if lineEnd <= lineStart:
            lineEnd = lineStart + lineSpace
        return lineStart, lineEnd

    def _prepared_source_data(
        self,
        source: dict[str, object],
        waferId: str,
        effectiveOutline: np.ndarray,
        snapshot: dict[str, object],
    ) -> tuple[pd.DataFrame, str]:
        dataFrame = source['dataFrame']
        xColumn = str(snapshot['xColumn'])
        yColumn = str(snapshot['yColumn'])
        zColumn = str(snapshot['zColumn'])
        waferColumn = str(snapshot['waferColumn'])
        if self._is_none_column(xColumn) or self._is_none_column(yColumn):
            return pd.DataFrame(), 'missing X/Y coordinates'
        requiredColumns = [xColumn, yColumn, zColumn]
        if waferColumn and not self._is_none_column(waferColumn):
            requiredColumns.append(waferColumn)
        missingColumns = [column for column in requiredColumns if column not in dataFrame.columns]
        if missingColumns:
            return pd.DataFrame(), f'missing {", ".join(missingColumns)}'

        filteredData = dataFrame
        if waferId and not self._is_none_column(waferColumn) and waferColumn in filteredData.columns:
            waferSeries = filteredData[waferColumn].map(self._format_wafer_value)
            filteredData = filteredData.loc[waferSeries == waferId]
        if filteredData.empty:
            return pd.DataFrame(), 'wafer not found'

        if self._is_polar_selection(xColumn, yColumn):
            plotData, warningText = self._polar_to_xy(
                filteredData,
                xColumn,
                yColumn,
                zColumn,
                snapshot,
            )
        else:
            plotData = filteredData[[xColumn, yColumn, zColumn]].copy()
            plotData.columns = ['x', 'y', 'z']
            plotData = plotData.apply(pd.to_numeric, errors='coerce')
            warningText = ''
        plotData = plotData.dropna(subset=['x', 'y', 'z']).reset_index(drop=True)
        if plotData.empty:
            return pd.DataFrame(), warningText or 'no numeric X/Y/Z data'

        outlinePath = MplPath(effectiveOutline)
        inside = outlinePath.contains_points(plotData[['x', 'y']].to_numpy(dtype=float))
        plotData = plotData.loc[inside].reset_index(drop=True)
        if plotData.empty:
            return pd.DataFrame(), warningText or 'all points outside effective wafer'
        return plotData, warningText

    def _is_polar_selection(self, xColumn: str, yColumn: str) -> bool:
        normalizedX = self._normalize_name(xColumn)
        normalizedY = self._normalize_name(yColumn)
        return {normalizedX, normalizedY} == {'r', 'theta'}

    def _polar_to_xy(
        self,
        dataFrame: pd.DataFrame,
        xColumn: str,
        yColumn: str,
        zColumn: str,
        snapshot: dict[str, object],
    ) -> tuple[pd.DataFrame, str]:
        normalizedX = self._normalize_name(xColumn)
        radiusColumn = xColumn if normalizedX == 'r' else yColumn
        thetaColumn = yColumn if radiusColumn == xColumn else xColumn
        radiusValues = pd.to_numeric(dataFrame[radiusColumn], errors='coerce')
        thetaValues = pd.to_numeric(dataFrame[thetaColumn], errors='coerce')
        zValues = pd.to_numeric(dataFrame[zColumn], errors='coerce')
        effectiveRadius = max(0.0, float(snapshot['waferDiameter']) / 2.0 - float(snapshot['excludeMm']))
        warningText = ''
        if radiusValues.dropna().gt(1.0).any():
            scaledRadius = radiusValues
            warningText = 'Alarm: R > 1 detected, treating R as radius (mm).'
        else:
            scaledRadius = radiusValues * effectiveRadius
        thetaRadians = np.deg2rad(thetaValues)
        return pd.DataFrame({
            'x': scaledRadius * np.sin(thetaRadians),
            'y': scaledRadius * np.cos(thetaRadians),
            'z': zValues,
        }), warningText

    def _effective_radius(self) -> float:
        return max(0.0, self._wafer_diameter() / 2.0 - float(self.excludeSpinBox.value()))

    def _contour_style(self) -> str:
        styleText = self.styleComboBox.currentText().strip()
        return styleText if styleText in CONTOUR_STYLE_OPTIONS else CONTOUR_STYLE_LINEAR

    def _build_contour_grid(
        self,
        plotData: pd.DataFrame,
        effectiveOutline: np.ndarray,
        styleText: str,
        snapshot: dict[str, object],
        gridSize: int = 160,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        self._raise_if_stale_contour_build(snapshot)
        points = plotData[['x', 'y']].to_numpy(dtype=float)
        values = plotData['z'].to_numpy(dtype=float)
        if len(points) < 3:
            return None
        centeredPoints = points - points.mean(axis=0, keepdims=True)
        if np.linalg.matrix_rank(centeredPoints) < 2:
            return None

        xMin, yMin = effectiveOutline.min(axis=0)
        xMax, yMax = effectiveOutline.max(axis=0)
        gridX, gridY = np.meshgrid(
            np.linspace(xMin, xMax, gridSize),
            np.linspace(yMin, yMax, gridSize),
        )
        if styleText == CONTOUR_STYLE_CUBIC:
            gridZ = self._triangulated_grid(points, values, gridX, gridY, cubic=True)
        elif styleText == CONTOUR_STYLE_IDW:
            gridZ = self._idw_grid(points, values, gridX, gridY, snapshot)
        elif styleText == CONTOUR_STYLE_GAUSSIAN:
            gridZ = self._gaussian_process_grid(points, values, gridX, gridY, snapshot)
        else:
            gridZ = self._triangulated_grid(points, values, gridX, gridY, cubic=False)

        self._raise_if_stale_contour_build(snapshot)
        missingMask = np.isnan(gridZ)
        if np.any(missingMask):
            missingPoints = np.column_stack((gridX[missingMask], gridY[missingMask]))
            gridZ[missingMask] = nearest_value_lookup(points, values, missingPoints)

        outlinePath = MplPath(effectiveOutline)
        inside = outlinePath.contains_points(
            np.column_stack((gridX.ravel(), gridY.ravel()))
        ).reshape(gridX.shape)
        gridZ = np.where(inside, gridZ, np.nan)
        return gridX, gridY, gridZ

    def _triangulated_grid(
        self,
        points: np.ndarray,
        values: np.ndarray,
        gridX: np.ndarray,
        gridY: np.ndarray,
        cubic: bool,
    ) -> np.ndarray:
        triangulation = mtri.Triangulation(points[:, 0], points[:, 1])
        interpolator = (
            mtri.CubicTriInterpolator(triangulation, values)
            if cubic
            else mtri.LinearTriInterpolator(triangulation, values)
        )
        linearGrid = interpolator(gridX, gridY)
        if np.ma.isMaskedArray(linearGrid):
            return linearGrid.filled(np.nan)
        return np.asarray(linearGrid, dtype=float)

    def _idw_grid(
        self,
        points: np.ndarray,
        values: np.ndarray,
        gridX: np.ndarray,
        gridY: np.ndarray,
        snapshot: dict[str, object],
        power: float = 2.0,
        chunkSize: int = 4096,
    ) -> np.ndarray:
        queryPoints = np.column_stack((gridX.ravel(), gridY.ravel()))
        gridValues = np.empty(len(queryPoints), dtype=float)
        for startIndex in range(0, len(queryPoints), chunkSize):
            self._raise_if_stale_contour_build(snapshot)
            chunk = queryPoints[startIndex:startIndex + chunkSize]
            distances = np.linalg.norm(chunk[:, None, :] - points[None, :, :], axis=2)
            exactMask = distances <= 1e-12
            weights = 1.0 / np.maximum(distances, 1e-12) ** power
            chunkValues = (weights @ values) / weights.sum(axis=1)
            if np.any(exactMask):
                exactRows = np.where(exactMask.any(axis=1))[0]
                nearestIndexes = exactMask[exactRows].argmax(axis=1)
                chunkValues[exactRows] = values[nearestIndexes]
            gridValues[startIndex:startIndex + len(chunk)] = chunkValues
        return gridValues.reshape(gridX.shape)

    def _gaussian_process_grid(
        self,
        points: np.ndarray,
        values: np.ndarray,
        gridX: np.ndarray,
        gridY: np.ndarray,
        snapshot: dict[str, object],
        chunkSize: int = 4096,
    ) -> np.ndarray:
        self._raise_if_stale_contour_build(snapshot)
        center = points.mean(axis=0, keepdims=True)
        scale = max(float(np.ptp(points[:, 0])), float(np.ptp(points[:, 1])), 1.0)
        scaledPoints = (points - center) / scale
        pairwiseDistances = np.linalg.norm(
            scaledPoints[:, None, :] - scaledPoints[None, :, :],
            axis=2,
        )
        nonZeroDistances = pairwiseDistances[pairwiseDistances > 0]
        lengthScale = float(np.median(nonZeroDistances)) if nonZeroDistances.size else 1.0
        lengthScale = max(lengthScale, 1e-3)
        kernelMatrix = np.exp(-0.5 * (pairwiseDistances / lengthScale) ** 2)
        kernelMatrix += np.eye(len(points)) * 0.05
        valueMean = float(values.mean())
        valueStd = float(values.std())
        if valueStd <= 0:
            return np.full(gridX.shape, valueMean, dtype=float)
        centeredValues = (values - valueMean) / valueStd
        try:
            alpha = np.linalg.solve(kernelMatrix, centeredValues)
        except np.linalg.LinAlgError:
            alpha = np.linalg.lstsq(kernelMatrix, centeredValues, rcond=None)[0]

        queryPoints = np.column_stack((gridX.ravel(), gridY.ravel()))
        gridValues = np.empty(len(queryPoints), dtype=float)
        for startIndex in range(0, len(queryPoints), chunkSize):
            self._raise_if_stale_contour_build(snapshot)
            chunk = (queryPoints[startIndex:startIndex + chunkSize] - center) / scale
            distances = np.linalg.norm(chunk[:, None, :] - scaledPoints[None, :, :], axis=2)
            queryKernel = np.exp(-0.5 * (distances / lengthScale) ** 2)
            gridValues[startIndex:startIndex + len(chunk)] = queryKernel @ alpha * valueStd + valueMean
        return gridValues.reshape(gridX.shape)

    def _add_contour_subplot(
        self,
        figure: go.Figure,
        rowIndex: int,
        columnIndex: int,
        contourGrid: tuple[np.ndarray, np.ndarray, np.ndarray],
        waferOutline: np.ndarray,
        effectiveOutline: np.ndarray,
        colorScale: list[list[float | str]],
        limitLow: float | None,
        limitHigh: float | None,
        fillContour: bool,
        showScale: bool,
        snapshot: dict[str, object],
    ) -> None:
        gridX, gridY, gridZ = contourGrid
        if fillContour:
            contours = dict(coloring='heatmap', showlines=True)
            traceOptions = {
                'colorscale': colorScale,
                'zmin': limitLow,
                'zmax': limitHigh,
                'colorbar': dict(title=str(snapshot['zColumn'])) if showScale else None,
                'showscale': showScale,
            }
            lineOptions = dict(width=0.4, color='rgba(0,0,0,0.45)')
        else:
            lineStart, lineEnd = self._contour_line_range(gridZ, float(snapshot['lineSpace']))
            contours = dict(
                coloring='none',
                showlines=True,
                start=lineStart,
                end=lineEnd,
                size=float(snapshot['lineSpace']),
            )
            traceOptions = {
                'colorscale': [[0.0, '#202020'], [1.0, '#202020']],
                'showscale': False,
            }
            lineOptions = dict(width=1.0, color='rgba(0,0,0,0.80)')
        figure.add_trace(
            go.Contour(
                x=gridX[0],
                y=gridY[:, 0],
                z=gridZ,
                contours=contours,
                line=lineOptions,
                **traceOptions,
            ),
            row=rowIndex,
            col=columnIndex,
        )
        self._add_wafer_outlines(figure, rowIndex, columnIndex, waferOutline, effectiveOutline, snapshot)

    def _add_site_markers(
        self,
        figure: go.Figure,
        rowIndex: int,
        columnIndex: int,
        siteData: pd.DataFrame,
        snapshot: dict[str, object],
    ) -> None:
        if siteData.empty:
            return
        showValue = bool(snapshot['showValue'])
        markerFontSize = dict(snapshot['fontSizes'])['value']
        figure.add_trace(
            go.Scatter(
                x=siteData['x'],
                y=siteData['y'],
                mode='markers+text' if showValue else 'markers',
                marker=dict(
                    symbol='circle-open',
                    size=max(5, markerFontSize - 2),
                    color='rgba(0,0,0,0.90)',
                    line=dict(width=1.2, color='rgba(0,0,0,0.90)'),
                ),
                text=[
                    self._format_site_value(value)
                    for value in siteData['z']
                ] if showValue else None,
                textposition='top center',
                textfont=dict(size=markerFontSize, color='rgba(0,0,0,0.88)'),
                name='site',
                showlegend=False,
                hovertemplate='x=%{x:.3g}<br>y=%{y:.3g}<br>value=%{customdata:.6g}<extra></extra>',
                customdata=siteData['z'],
            ),
            row=rowIndex,
            col=columnIndex,
        )

    def _format_site_value(self, value) -> str:
        try:
            return f'{float(value):.6g}'
        except (TypeError, ValueError):
            return str(value)

    def _add_empty_subplot(
        self,
        figure: go.Figure,
        rowIndex: int,
        columnIndex: int,
        waferOutline: np.ndarray,
        effectiveOutline: np.ndarray,
        snapshot: dict[str, object],
    ) -> None:
        self._add_wafer_outlines(figure, rowIndex, columnIndex, waferOutline, effectiveOutline, snapshot)
        figure.add_annotation(
            text='empty',
            x=0,
            y=0,
            showarrow=False,
            font=dict(size=dict(snapshot['fontSizes'])['base'], color='#888888'),
            row=rowIndex,
            col=columnIndex,
        )

    def _add_wafer_outlines(
        self,
        figure: go.Figure,
        rowIndex: int,
        columnIndex: int,
        waferOutline: np.ndarray,
        effectiveOutline: np.ndarray,
        snapshot: dict[str, object],
    ) -> None:
        figure.add_trace(
            go.Scatter(
                x=waferOutline[:, 0],
                y=waferOutline[:, 1],
                mode='lines',
                line=dict(color=str(snapshot['edgeColor']), width=2),
                name='wafer edge',
                showlegend=False,
                hoverinfo='skip',
            ),
            row=rowIndex,
            col=columnIndex,
        )
        figure.add_trace(
            go.Scatter(
                x=effectiveOutline[:, 0],
                y=effectiveOutline[:, 1],
                mode='lines',
                line=dict(color=str(snapshot['excludeColor']), width=1.5),
                name='effective edge',
                showlegend=False,
                hoverinfo='skip',
            ),
            row=rowIndex,
            col=columnIndex,
        )

    def _apply_subplot_axes(
        self,
        figure: go.Figure,
        rowIndex: int,
        columnIndex: int,
        columnCount: int,
        waferOutline: np.ndarray,
        snapshot: dict[str, object],
    ) -> None:
        margin = max(float(snapshot['waferDiameter']) * 0.03, 3.0)
        fontSizes = dict(snapshot['fontSizes'])
        xAxisRef = self._subplot_x_axis_ref(rowIndex, columnIndex, columnCount)
        figure.update_xaxes(
            range=[float(waferOutline[:, 0].min()) - margin, float(waferOutline[:, 0].max()) + margin],
            title_text='X (mm)',
            title_font=dict(size=fontSizes['axis']),
            tickfont=dict(size=fontSizes['tick']),
            showgrid=False,
            zeroline=False,
            row=rowIndex,
            col=columnIndex,
        )
        figure.update_yaxes(
            range=[float(waferOutline[:, 1].min()) - margin, float(waferOutline[:, 1].max()) + margin],
            title_text='Y (mm)',
            title_font=dict(size=fontSizes['axis']),
            tickfont=dict(size=fontSizes['tick']),
            showgrid=False,
            zeroline=False,
            scaleanchor=xAxisRef,
            scaleratio=1,
            row=rowIndex,
            col=columnIndex,
        )

    def _subplot_x_axis_ref(self, rowIndex: int, columnIndex: int, columnCount: int) -> str:
        subplotIndex = (rowIndex - 1) * columnCount + columnIndex
        return 'x' if subplotIndex == 1 else f'x{subplotIndex}'

    def _apply_common_layout(
        self,
        figure: go.Figure,
        rowCount: int,
        columnCount: int,
        snapshot: dict[str, object],
    ) -> None:
        width = int(snapshot['plotWidth'])
        height = int(snapshot['plotHeight'])
        fontSizes = dict(snapshot['fontSizes'])
        figure.update_layout(
            title=dict(
                text=str(snapshot['title']),
                x=0.5,
                font=dict(size=fontSizes['title']),
            ),
            width=width,
            height=height,
            template='plotly_white',
            margin=dict(t=80, r=80, b=60, l=60),
            font=dict(size=fontSizes['base']),
            showlegend=False,
        )
        figure.update_annotations(font=dict(size=fontSizes['subplot']))
        if bool(snapshot['showDetail']):
            figure.add_annotation(
                text='<br>'.join([
                    f'diameter: {float(snapshot["waferDiameter"]):.1f} mm',
                    f'flat: {snapshot["flatOption"]}',
                    f'exclude: {float(snapshot["excludeMm"]):.1f} mm',
                    f'mode: {"single" if bool(snapshot["singleMode"]) else "grid"}',
                    f'subplots: {rowCount}x{columnCount}',
                ]),
                xref='paper',
                yref='paper',
                x=1.0,
                y=0.0,
                showarrow=False,
                xanchor='right',
                yanchor='bottom',
                align='left',
                bgcolor='rgba(255,255,255,0.8)',
                bordercolor='rgba(0,0,0,0.2)',
                font=dict(size=fontSizes['detail']),
            )

    def _font_sizes(self) -> dict[str, int]:
        baseSize = max(8, int(self.fontSizeSpinBox.value()) + 4)
        return {
            'base': baseSize,
            'title': baseSize + 8,
            'subplot': baseSize + 2,
            'axis': baseSize + 1,
            'tick': max(7, baseSize - 1),
            'value': max(7, baseSize - 2),
            'detail': max(7, baseSize - 1),
        }

    def _label_color(self, label: QLabel, fallback: str) -> str:
        styleSheet = label.styleSheet()
        rgbMatch = re.search(r'rgb\((\d+),\s*(\d+),\s*(\d+)\)', styleSheet)
        if rgbMatch:
            red, green, blue = (int(value) for value in rgbMatch.groups())
            return f'#{red:02x}{green:02x}{blue:02x}'
        hexMatch = re.search(r'#[0-9a-fA-F]{6}', styleSheet)
        if hexMatch:
            return hexMatch.group(0)
        return fallback

    def _clear_plot_area(self, statusText: str) -> None:
        self.loadingOverlay.hide()
        self._activeBuildTaskId = None
        self._activeRenderTaskId = None
        self.hasDrawRequest = False
        self.currentPlotHtml = ''
        self.currentPlotFigure = None
        self.currentPlotFilePath = ''
        self.currentViewerFilePath = ''
        try:
            self.chartView.setHtml('')
        except Exception:
            if isinstance(self.chartView, QTextBrowser):
                self.chartView.clear()
        self._set_status(statusText)

    def _render_figure(self, figure: go.Figure, statusText: str, warningText: str) -> None:
        self.currentPlotHtml = ''
        self.loadingOverlay.show('Rendering...')
        self._set_status('Rendering contour HTML...')

        def work() -> str:
            return local_plotly_html(
                figure,
                fullHtml=True,
                annotationNamespace='contour',
            )

        self._activeRenderTaskId = self._start_background_task(
            work,
            lambda taskId, result: self._on_render_figure_finished(
                taskId,
                result,
                statusText,
                warningText,
            ),
            self._on_render_figure_failed,
        )

    def _on_render_figure_finished(
        self,
        taskId: int,
        result: str,
        statusText: str,
        warningText: str,
    ) -> None:
        if taskId != self._activeRenderTaskId:
            return
        self.currentPlotHtml = result
        try:
            if not self.useExternalBrowser:
                assetsDir = Path(__file__).resolve().parent
                try:
                    baseUrl = QUrl.fromLocalFile(str(assetsDir) + '/')
                    self.chartView.setHtml(self.currentPlotHtml, baseUrl)
                    self.loadingOverlay.hide()
                    self._set_status(self._status_with_warning(statusText, warningText), error=bool(warningText))
                    return
                except Exception:
                    self._switch_to_external_browser_view()

            self.currentPlotFilePath = str(Path(tempfile.gettempdir()) / 'p-chart-contour.html')
            with open(self.currentPlotFilePath, 'w', encoding='utf-8') as htmlFile:
                htmlFile.write(self.currentPlotHtml)
            plotUri = Path(self.currentPlotFilePath).resolve().as_uri()
            self.currentViewerFilePath = str(Path(tempfile.gettempdir()) / 'p-chart-contour-viewer.html')
            viewerUri = Path(self.currentViewerFilePath).resolve().as_uri()
            viewerHtml = f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>p-chart Contour</title>
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
                '<p>Contour plot is shown in the system browser.</p>'
                f'<p><a href="{viewerUri}">Open contour browser viewer</a></p>'
                '<p>The viewer reloads the latest Plotly HTML automatically. Use Download HTML to save a copy.</p>'
                '</div>'
            )
            self.loadingOverlay.hide()
            self._set_status(self._status_with_warning(statusText, warningText), error=bool(warningText))
        except Exception as exc:
            self.loadingOverlay.hide()
            self._set_status(f'Failed to render contour HTML: {exc}', error=True)

    def _on_render_figure_failed(self, taskId: int, errorText: str) -> None:
        if taskId != self._activeRenderTaskId:
            return
        self.loadingOverlay.hide()
        self._set_status(f'Failed to render contour HTML: {errorText}', error=True)

    def _status_with_warning(self, statusText: str, warningText: str) -> str:
        return f'{statusText} Warning: {warningText}' if warningText else statusText

    def _download_html(self) -> None:
        if not self.currentPlotHtml:
            self._set_status('No contour HTML available. Draw a plot first.', error=True)
            return
        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Download Contour Plot HTML',
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
            self._set_status(f'Contour HTML saved to {selectedFile}')
        except Exception as exc:
            self._set_status(f'Failed to save contour HTML: {exc}', error=True)

    def _download_png(self) -> None:
        if self.currentPlotFigure is None:
            self._set_status('No contour plot available. Draw a plot first.', error=True)
            return
        selectedFile = ''
        if shift_click_requests_png_file():
            selectedFile, _ = QFileDialog.getSaveFileName(
                self.rootWidget,
                'Save Contour Plot PNG',
                self._default_export_filename('.png'),
                'PNG Files (*.png);;All Files (*)',
            )
            if not selectedFile:
                return
            if not selectedFile.lower().endswith('.png'):
                selectedFile = f'{selectedFile}.png'

        self.downloadPngButton.setEnabled(False)
        self._set_status('Creating Contour PNG...')
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
            self._set_status(f'Contour PNG saved to {selectedFile}.')
            return
        try:
            copy_png_bytes_to_clipboard(pngBytes)
            self._set_status('Contour PNG copied to clipboard.')
        except Exception as exc:
            self._set_status(f'Failed to copy contour PNG: {exc}', error=True)

    def _on_png_export_failed(self, taskId: int, errorText: str) -> None:
        if taskId != getattr(self, '_activePngTaskId', None):
            return
        self.downloadPngButton.setEnabled(True)
        self._set_status(f'Failed to create contour PNG: {errorText}', error=True)

    def _default_export_filename(self, suffix: str) -> str:
        title = self.titleLineEdit.text().strip() or 'contour_plot'
        safeTitle = re.sub(r'[^A-Za-z0-9._-]+', '_', title).strip('._') or 'contour_plot'
        return str(Path.home() / f'{safeTitle}{suffix}')

    def _set_status(self, message: str, error: bool = False) -> None:
        self.statusLabel.setText(message)
        self.statusLabel.setStyleSheet('color: red;' if error else 'color: black;')
