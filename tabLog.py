import math
import os
import re
import tempfile
import webbrowser
from io import StringIO
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from pandas.api.types import is_datetime64_any_dtype
from plotly.subplots import make_subplots
from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QUrl
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
    QPushButton,
    QRadioButton,
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


FILE_PATH_ROLE = int(Qt.ItemDataRole.UserRole)
PRIMARY_FILE_ROLE = FILE_PATH_ROLE + 1
LOG_EXTRA_INFO_ROLE = PRIMARY_FILE_ROLE + 1
LOG_FILE_EXTENSIONS = ('.csv', '.txt')


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
        self.comboBox.lineEdit().setText(self.placeholder)

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


class LogFileDropFilter(QObject):
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
            and url.toLocalFile().lower().endswith(LOG_FILE_EXTENSIONS)
        ]


class TabLogWidget(BackgroundTaskMixin):
    _dateColumnPattern = re.compile(r'(date|timestamp|time)', re.IGNORECASE)
    _emptyDataHint = '可直接將 .csv or .txt（同一編碼形式）拉到 files'

    def __init__(self, rootWidget: QWidget, preferWebEngine: bool = True) -> None:
        self.rootWidget = rootWidget
        self.tabDataWidget = None
        self.preferWebEngine = preferWebEngine
        self.useExternalBrowser = True

        self.xComboBox = require_child(rootWidget, QComboBox, 'logXComboBox')
        self.y1ComboBox = require_child(rootWidget, QComboBox, 'logY1ComboBox')
        self.y2ComboBox = require_child(rootWidget, QComboBox, 'logY2ComboBox')
        self.plotTitleLineEdit = require_child(rootWidget, QLineEdit, 'logTitleLineEdit')
        self.subTitleLineEdit = require_child(rootWidget, QLineEdit, 'logSubTitleLineEdit')
        self.y1TitleLineEdit = require_child(rootWidget, QLineEdit, 'logY1TitleLineEdit')
        self.y2TitleLineEdit = require_child(rootWidget, QLineEdit, 'logY2TitleLineEdit')
        self.xFormatLineEdit = self._optional_child(QLineEdit, 'logXFormatLineEdit')
        self.y1FormatLineEdit = self._optional_child(QLineEdit, 'logY1FormatLineEdit')
        self.y2FormatLineEdit = self._optional_child(QLineEdit, 'logY2FormatLineEdit')
        self.plotButton = require_child(rootWidget, QPushButton, 'logPlotButton')
        self.downloadHtmlButton = require_child(rootWidget, QPushButton, 'logDownloadHtmlButton')
        self.downloadPngButton = require_child(rootWidget, QPushButton, 'logDownloadPngButton')
        self.cleanFilesButton = require_child(rootWidget, QPushButton, 'logCleanFilesButton')
        self.plotlyThemeComboBox = require_child(rootWidget, QComboBox, 'logPlotlyThemeComboBox')
        self.plotWidthSpinBox = require_child(rootWidget, QSpinBox, 'logPlotWidthSpinBox')
        self.plotHeightSpinBox = require_child(rootWidget, QSpinBox, 'logPlotHeightSpinBox')
        self.legendFontSizeSpinBox = require_child(rootWidget, QSpinBox, 'logLegendFontSizeSpinButton')
        self.lineWidthSpinBox = require_child(rootWidget, QSpinBox, 'logLineWidthSpinBox')
        self.fontSizeSpinBox = require_child(rootWidget, QSpinBox, 'logFontSizeSpinButton')
        self.horizontalSpaceSpinBox = require_child(rootWidget, QSpinBox, 'logChartsHSpaceSpinButton')
        self.verticalSpaceSpinBox = require_child(rootWidget, QSpinBox, 'logChartsVSpaceSpinButton')
        self.symbolCheckBox = require_child(rootWidget, QCheckBox, 'logSymbolCheckBox')
        self.lineCheckBox = require_child(rootWidget, QCheckBox, 'logLineCheckBox')
        self.y1LogCheckBox = self._optional_child(
            QCheckBox,
            'logY1LogCheckBox',
            'Y1LogCheckBox',
        )
        self.y2LogCheckBox = self._optional_child(
            QCheckBox,
            'logY2LogCheckBox',
            'Y2LogCheckBox',
        )
        self.overlapRadioButton = require_child(rootWidget, QRadioButton, 'logOverlapChartsRadioButton')
        self.subplotRadioButton = require_child(rootWidget, QRadioButton, 'logSubplotChartsRadioButton')
        self.filesList = require_child(rootWidget, QListWidget, 'logFilesList')
        self.plotAreaWidget = require_child(rootWidget, QWidget, 'logPlotAreaWidget')
        self.statusLabel = require_child(rootWidget, QLabel, 'logStatusLabel')

        self.currentPlotHtml = ''
        self.currentPlotFigure = None
        self.currentPlotFilePath = ''
        self.currentViewerFilePath = ''
        self.pendingRenderStatus = ''
        self.pendingRenderStatusError = False
        self.primaryFilePath = ''
        self.subtitleEdited = False
        self.hasDrawRequest = False
        self._browserViewerOpened = False
        self._updatingFilesList = False
        self.isActiveTab = False
        self._pendingDataRefresh = False
        self._pendingPrimarySync = False
        self._pendingRedraw = False

        self.y1ColumnCombo = CheckableColumnCombo(
            self.y1ComboBox,
            'Select Y1 columns',
            self._on_y1_columns_changed,
        )
        self.y2ColumnCombo = CheckableColumnCombo(
            self.y2ComboBox,
            'Select Y2 columns',
            self._on_y2_columns_changed,
        )
        self.fileDropFilter = LogFileDropFilter(self._add_external_files, self.rootWidget)

        self._configure_plot_area()
        self.loadingOverlay = LoadingOverlay(self.plotAreaWidget)
        self._configure_defaults()
        self._configure_signals()
        self._apply_chart_mode()

    def _optional_child(self, childType, *objectNames):
        for objectName in objectNames:
            child = self.rootWidget.findChild(childType, objectName)
            if child is not None:
                return child
        return None

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
        initialLineWidth = self.lineWidthSpinBox.value()
        self.lineWidthSpinBox.setRange(1, 20)
        if initialLineWidth <= 0:
            self.lineWidthSpinBox.setValue(2)
        self.fontSizeSpinBox.setRange(-8, 8)
        self.fontSizeSpinBox.setValue(0)
        self.overlapRadioButton.setChecked(
            self.overlapRadioButton.isChecked() or not self.subplotRadioButton.isChecked()
        )
        self.filesList.setAcceptDrops(True)
        self.filesList.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.filesList.installEventFilter(self.fileDropFilter)
        self.filesList.viewport().setAcceptDrops(True)
        self.filesList.viewport().installEventFilter(self.fileDropFilter)

    def _configure_signals(self) -> None:
        self.plotButton.clicked.connect(self._draw_plot)
        self.downloadHtmlButton.clicked.connect(self._download_html)
        self.downloadPngButton.clicked.connect(self._download_png)
        self.cleanFilesButton.clicked.connect(self._clean_external_files)
        self.filesList.itemSelectionChanged.connect(self._on_file_selection_changed)
        self.plotlyThemeComboBox.currentTextChanged.connect(self._mark_plot_pending)
        self.plotWidthSpinBox.valueChanged.connect(self._mark_plot_pending)
        self.plotHeightSpinBox.valueChanged.connect(self._mark_plot_pending)
        self.legendFontSizeSpinBox.valueChanged.connect(self._mark_plot_pending)
        self.lineWidthSpinBox.valueChanged.connect(self._mark_plot_pending)
        self.fontSizeSpinBox.valueChanged.connect(self._mark_plot_pending)
        self.horizontalSpaceSpinBox.valueChanged.connect(self._mark_plot_pending)
        self.verticalSpaceSpinBox.valueChanged.connect(self._mark_plot_pending)
        self.symbolCheckBox.stateChanged.connect(self._on_trace_style_changed)
        self.lineCheckBox.stateChanged.connect(self._on_trace_style_changed)
        self.overlapRadioButton.toggled.connect(self._on_presentation_changed)
        self.subplotRadioButton.toggled.connect(self._on_presentation_changed)
        self.xComboBox.currentTextChanged.connect(self._update_plot_title)
        self.xComboBox.currentTextChanged.connect(self._mark_plot_pending)
        self.plotTitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.subTitleLineEdit.textEdited.connect(self._mark_subtitle_edited)
        self.subTitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.y1TitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        self.y2TitleLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        for formatLineEdit in (
            self.xFormatLineEdit,
            self.y1FormatLineEdit,
            self.y2FormatLineEdit,
        ):
            if formatLineEdit is not None:
                formatLineEdit.editingFinished.connect(self._draw_plot_when_ready)
        for logCheckBox in (self.y1LogCheckBox, self.y2LogCheckBox):
            if logCheckBox is not None:
                logCheckBox.stateChanged.connect(self._mark_plot_pending)

    def set_tab_data(self, tabDataWidget) -> None:
        self.tabDataWidget = tabDataWidget
        if hasattr(self.tabDataWidget, 'add_data_changed_callback'):
            self.tabDataWidget.add_data_changed_callback(self._refresh_column_options)
        if hasattr(self.tabDataWidget, 'filePathLineEdit'):
            self.tabDataWidget.filePathLineEdit.textChanged.connect(self._sync_primary_file)
        self._refresh_column_options()

    def set_active_tab(self, isActive: bool) -> None:
        wasActive = self.isActiveTab
        self.isActiveTab = isActive
        if not isActive or wasActive:
            return
        if self._pendingDataRefresh:
            self._refresh_column_options()
            return
        if self._pendingPrimarySync:
            self._sync_primary_file()
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
        self._sync_primary_file()
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
        self._update_plot_title()
        self._draw_plot_when_ready()

    def _default_x_column(self, columnNames: list[str]) -> str:
        for columnName in columnNames:
            if self._dateColumnPattern.search(columnName):
                return columnName
        return columnNames[0] if columnNames else ''

    def _on_y1_columns_changed(self) -> None:
        self._sync_axis_title(
            self.y1TitleLineEdit,
            self.y1ColumnCombo.checked_items(),
            'Y1',
        )
        self._apply_chart_mode()
        self._mark_plot_pending()

    def _on_y2_columns_changed(self) -> None:
        self._sync_axis_title(
            self.y2TitleLineEdit,
            self.y2ColumnCombo.checked_items(),
            'Y2',
        )
        self._apply_chart_mode()
        self._mark_plot_pending()

    def _sync_axis_title(
        self,
        titleLineEdit: QLineEdit,
        selectedColumns: list[str],
        defaultTitle: str,
    ) -> None:
        titleLineEdit.setText(', '.join(selectedColumns) or defaultTitle)

    def _build_auto_plot_title(self) -> str:
        xColumn = self.xComboBox.currentText().strip()
        return f'{xColumn} log plot' if xColumn else 'Log Plot'

    def _update_plot_title(self, *_args) -> None:
        currentTitle = self.plotTitleLineEdit.text().strip()
        if not currentTitle or currentTitle == 'log Title':
            self.plotTitleLineEdit.setText(self._build_auto_plot_title())

    def _sync_primary_file(self, *_args) -> None:
        if self.tabDataWidget is None:
            return
        if not self.isActiveTab:
            self._pendingPrimarySync = True
            return
        self._pendingPrimarySync = False
        filePath = self.tabDataWidget.filePathLineEdit.text().strip()
        normalizedPath = self._normalized_path(filePath) if filePath else ''
        hasPrimaryData = self.tabDataWidget.has_loaded_data()
        primaryKey = normalizedPath if normalizedPath else '<tabdata-current>'
        if primaryKey == self.primaryFilePath and (normalizedPath or not hasPrimaryData):
            return

        externalEntries = []
        selectedPaths = set()
        for rowIndex in range(self.filesList.count()):
            item = self.filesList.item(rowIndex)
            itemPath = str(item.data(FILE_PATH_ROLE) or '')
            if item.isSelected():
                selectedPaths.add(itemPath)
            if not bool(item.data(PRIMARY_FILE_ROLE)) and itemPath != normalizedPath:
                externalEntries.append(itemPath)

        self.primaryFilePath = primaryKey if normalizedPath or hasPrimaryData else ''
        self._updatingFilesList = True
        self.filesList.clear()
        if self.primaryFilePath:
            displayName = Path(normalizedPath).name if normalizedPath else 'TabData current data'
            self._add_file_item(normalizedPath, displayName, primary=True, selected=True)
        for externalPath in externalEntries:
            self._add_file_item(
                externalPath,
                Path(externalPath).name,
                primary=False,
                selected=externalPath in selectedPaths,
            )
        self._updatingFilesList = False
        self._apply_chart_mode()
        self._update_auto_subtitle()

    def _add_external_files(self, filePaths: list[str]) -> None:
        normalizedPaths = []
        for filePath in filePaths:
            normalizedPath = self._normalized_path(filePath)
            if (
                normalizedPath.lower().endswith(LOG_FILE_EXTENSIONS)
                and os.path.exists(normalizedPath)
                and normalizedPath not in normalizedPaths
            ):
                normalizedPaths.append(normalizedPath)

        if (
            normalizedPaths
            and self.tabDataWidget is not None
            and not self.tabDataWidget.filePathLineEdit.text().strip()
        ):
            primaryPath = normalizedPaths.pop(0)
            self.tabDataWidget._load_dropped_file(primaryPath)

        existingPaths = {
            str(self.filesList.item(rowIndex).data(FILE_PATH_ROLE) or '')
            for rowIndex in range(self.filesList.count())
        }
        addedCount = 0
        self._updatingFilesList = True
        for normalizedPath in normalizedPaths:
            if normalizedPath in existingPaths:
                continue
            self._add_file_item(
                normalizedPath,
                Path(normalizedPath).name,
                primary=False,
                selected=True,
            )
            existingPaths.add(normalizedPath)
            addedCount += 1
        self._updatingFilesList = False
        self._apply_chart_mode()
        self._update_auto_subtitle()
        if addedCount:
            self._mark_plot_pending()

    def _add_file_item(
        self,
        filePath: str,
        displayName: str,
        primary: bool,
        selected: bool,
    ) -> None:
        item = QListWidgetItem(displayName)
        item.setData(FILE_PATH_ROLE, filePath)
        item.setData(PRIMARY_FILE_ROLE, primary)
        item.setData(LOG_EXTRA_INFO_ROLE, self._build_log_file_extra_info(filePath, displayName))
        item.setToolTip(filePath or 'TabData current processed data')
        self.filesList.addItem(item)
        item.setSelected(selected)

    def _clean_external_files(self) -> None:
        self._updatingFilesList = True
        for rowIndex in range(self.filesList.count() - 1, -1, -1):
            item = self.filesList.item(rowIndex)
            if not bool(item.data(PRIMARY_FILE_ROLE)):
                self.filesList.takeItem(rowIndex)
        self._updatingFilesList = False
        self._apply_chart_mode()
        self._update_auto_subtitle()
        self._mark_plot_pending()

    def _normalized_path(self, filePath: str) -> str:
        return os.path.normcase(os.path.abspath(filePath))

    def _on_file_selection_changed(self) -> None:
        if self._updatingFilesList:
            return
        self._ensure_valid_file_selection()
        self._update_auto_subtitle()
        self._set_mode_status()
        self._mark_plot_pending()

    def _ensure_valid_file_selection(self) -> None:
        return

    def _single_chart_mode(self) -> bool:
        return False

    def _apply_chart_mode(self) -> None:
        self.filesList.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.overlapRadioButton.setEnabled(True)
        self.subplotRadioButton.setEnabled(True)
        self._ensure_valid_file_selection()
        self._update_auto_subtitle()
        self._set_mode_status()

    def _mode_text(self) -> str:
        selectedCount = len(self._selected_file_entries())
        if selectedCount <= 0:
            return 'no file selected'
        presentation = 'subplot' if self.subplotRadioButton.isChecked() else 'overlap'
        if selectedCount == 1:
            return f'1 file: {presentation}'
        return f'{selectedCount} files: {presentation}'

    def _set_mode_status(self) -> None:
        if (
            self.tabDataWidget is not None
            and hasattr(self.tabDataWidget, 'filePathLineEdit')
            and not self.tabDataWidget.filePathLineEdit.text().strip()
        ):
            self._set_status(self._emptyDataHint)
            return
        self._set_status(self._mode_text())

    def _selected_file_entries(self) -> list[dict[str, object]]:
        entries = []
        for rowIndex in range(self.filesList.count()):
            item = self.filesList.item(rowIndex)
            if item.isSelected():
                entries.append({
                    'name': item.text(),
                    'path': str(item.data(FILE_PATH_ROLE) or ''),
                    'primary': bool(item.data(PRIMARY_FILE_ROLE)),
                    'extraInfo': str(item.data(LOG_EXTRA_INFO_ROLE) or ''),
                })
        return entries

    def _mark_subtitle_edited(self, *_args) -> None:
        self.subtitleEdited = True

    def _update_auto_subtitle(self) -> None:
        if self.subtitleEdited:
            return
        subtitleParts = [
            str(entry.get('extraInfo') or entry['name'])
            for entry in self._selected_file_entries()
        ]
        self.subTitleLineEdit.setText(', '.join(subtitleParts))

    def _on_trace_style_changed(self, *_args) -> None:
        if not self.lineCheckBox.isChecked() and not self.symbolCheckBox.isChecked():
            blocker = QSignalBlocker(self.lineCheckBox)
            self.lineCheckBox.setChecked(True)
            del blocker
        self._mark_plot_pending()

    def _on_presentation_changed(self, checked: bool) -> None:
        if not checked:
            return
        self._set_mode_status()
        self._mark_plot_pending()

    def _redraw_existing_plot(self, *_args) -> None:
        if not self.isActiveTab:
            self._pendingRedraw = True
            return
        if self.hasDrawRequest:
            self._draw_plot()

    def _mark_plot_pending(self, *_args) -> None:
        if not self.isActiveTab:
            self._pendingRedraw = True
            return
        if self.hasDrawRequest:
            self._set_status(f'Plot settings changed. Click Refresh Plot. {self._mode_text()}')
            return
        self._set_mode_status()

    def _draw_plot_when_ready(self, *_args) -> None:
        if not self.isActiveTab:
            self._pendingRedraw = True
            return
        if self.hasDrawRequest:
            self._draw_plot()

    def _build_log_file_extra_info(self, filePath: str, displayName: str) -> str:
        profile = self._detect_log_file_profile(filePath)
        if not profile or not profile.get('extraFields'):
            return displayName

        rawLines = self._read_log_file_raw_lines(
            filePath,
            int(profile.get('extraMaxLine', 0)),
        )
        encoding = self._detect_log_file_encoding(filePath)
        extraParts = []
        for lineNumber, fieldIndexes in profile['extraFields']:
            lineIndex = int(lineNumber) - 1
            if lineIndex < 0 or lineIndex >= len(rawLines):
                continue
            lineText = self._decode_log_line(rawLines[lineIndex], encoding).strip()
            fields = self._split_log_info_line(lineText)
            for fieldIndex in fieldIndexes:
                if fieldIndex < len(fields) and fields[fieldIndex]:
                    extraParts.append(fields[fieldIndex])

        return ', '.join([*extraParts, displayName]) if extraParts else displayName

    def _detect_log_file_profile(self, filePath: str) -> dict[str, object] | None:
        if not filePath or not filePath.lower().endswith(LOG_FILE_EXTENSIONS):
            return None

        rawLines = self._read_log_file_raw_lines(filePath, 1)
        if not rawLines:
            return None

        encoding = self._detect_log_file_encoding(filePath)
        firstLineCandidates = self._decode_log_line_candidates(rawLines[0], encoding)
        for firstLine in firstLineCandidates:
            normalizedLine = firstLine.strip().lstrip('\ufeff')
            if normalizedLine == 'Run#,ProcIdent,StartDate,StartTime,Version':
                return {
                    'type': 'Evatek',
                    'skipLines': {4},
                    'headerLine': 3,
                    'dataStartLine': 6,
                    'extraFields': [(2, [0, 1])],
                    'extraMaxLine': 2,
                    'implemented': True,
                }
            if normalizedLine.startswith('RunNumber:') or normalizedLine.startswith('RunNumber：'):
                return {
                    'type': 'AST',
                    'skipLines': {4},
                    'headerLine': 8,
                    'dataStartLine': 9,
                    'extraFields': [(3, [1]), (5, [1])],
                    'extraMaxLine': 5,
                    'implemented': True,
                }
            if normalizedLine == 'BEGIN RECIPE,Date,Time,':
                return {
                    'type': 'Temescal',
                    'implemented': False,
                }
        return None

    def _read_profiled_log_data(
        self,
        filePath: str,
        profile: dict[str, object],
    ) -> pd.DataFrame:
        rawLines = self._read_log_file_raw_lines(filePath)
        headerLine = int(profile['headerLine'])
        dataStartLine = int(profile['dataStartLine'])
        skipLines = set(profile.get('skipLines', set()))
        encoding = self._detect_log_file_encoding(filePath)

        decodedLines = []
        for lineNumber, rawLine in enumerate(rawLines, start=1):
            if lineNumber in skipLines:
                continue
            if lineNumber == headerLine or lineNumber >= dataStartLine:
                decodedLines.append(self._decode_log_line(rawLine, encoding))

        if not decodedLines:
            raise ValueError(f'{profile.get("type", "log")} profile has no readable rows.')

        csvText = ''.join(decodedLines)
        delimiter = self.tabDataWidget._detect_csv_delimiter(csvText)
        dataFrame = pd.read_csv(StringIO(csvText), sep=delimiter)
        return dataFrame

    def _read_log_file_raw_lines(
        self,
        filePath: str,
        maxRows: int | None = None,
    ) -> list[bytes]:
        rows = []
        with open(filePath, 'rb') as logFile:
            for rowIndex, rawLine in enumerate(logFile):
                if maxRows is not None and rowIndex >= maxRows:
                    break
                rows.append(rawLine)
        return rows

    def _detect_log_file_encoding(self, filePath: str) -> str:
        if self.tabDataWidget is not None:
            sampleBytes = self.tabDataWidget._read_csv_sample_bytes(filePath)
            return self.tabDataWidget._detect_csv_encoding(sampleBytes)
        return 'utf-8-sig'

    def _decode_log_line(self, rawLine: bytes, encoding: str) -> str:
        try:
            return rawLine.decode(encoding, errors='replace')
        except LookupError:
            return rawLine.decode('utf-8-sig', errors='replace')

    def _decode_log_line_candidates(self, rawLine: bytes, preferredEncoding: str) -> list[str]:
        candidates = []
        for encoding in (preferredEncoding, 'utf-8-sig', 'cp950', 'big5', 'latin1'):
            if not encoding or encoding in candidates:
                continue
            candidates.append(encoding)

        decodedLines = []
        for encoding in candidates:
            try:
                decodedLines.append(rawLine.decode(encoding, errors='ignore'))
            except LookupError:
                continue
        return decodedLines

    def _split_log_info_line(self, lineText: str) -> list[str]:
        return [
            field.strip()
            for field in re.split(r'[\t,：:]+', lineText)
        ]

    def _draw_plot(self) -> None:
        if self.tabDataWidget is None:
            self._set_status('No data source attached to log tab.', error=True)
            return

        xColumn = self.xComboBox.currentText().strip()
        y1Columns = self.y1ColumnCombo.checked_items()
        y2Columns = self.y2ColumnCombo.checked_items()
        selectedEntries = self._selected_file_entries()
        self.hasDrawRequest = True
        if not selectedEntries:
            self._clear_plot_area('No file selected. Plot area cleared.')
            return

        primaryDataFrame = self.tabDataWidget.get_plot_data()
        if primaryDataFrame.empty:
            self._set_status('No data available for log plot.', error=True)
            return

        if not xColumn:
            self._set_status('Choose a valid X column first.', error=True)
            return
        if not y1Columns and not y2Columns:
            self._set_status('Choose at least one Y1 or Y2 column first.', error=True)
            return

        skipRows = self.tabDataWidget.get_skip_rows()
        self._set_status(f'Loading selected files. {self._mode_text()}')
        self.loadingOverlay.show('Loading files...')

        def work() -> dict[str, object]:
            sources = []
            warnings = []
            for entry in selectedEntries:
                if entry['primary']:
                    dataFrame = primaryDataFrame
                else:
                    try:
                        dataFrame = self._read_external_data(str(entry['path']), skipRows)
                    except Exception as exc:
                        warnings.append(f'{entry["name"]}: {exc}')
                        continue
                sources.append({
                    'name': entry['name'],
                    'path': entry['path'],
                    'dataFrame': dataFrame,
                })
            return {
                'sources': sources,
                'warnings': warnings,
                'xColumn': xColumn,
                'y1Columns': y1Columns,
                'y2Columns': y2Columns,
            }

        self._activeLogDataTaskId = self._start_background_task(
            work,
            self._on_log_data_finished,
            self._on_log_data_failed,
        )

    def _read_external_data(self, filePath: str, fallbackSkipRows: int) -> pd.DataFrame:
        profile = self._detect_log_file_profile(filePath)
        if profile and profile.get('implemented'):
            dataFrame = self._read_profiled_log_data(filePath, profile)
            dataFrame = self.tabDataWidget._strip_csv_dataframe_spaces(dataFrame)
            self.tabDataWidget._normalize_loaded_datetime_formats(dataFrame)
            return dataFrame

        skipRows = self.tabDataWidget._detect_skip_rows(
            filePath,
            fallbackSkipRows=fallbackSkipRows,
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

    def _on_log_data_finished(self, taskId: int, result: dict[str, object]) -> None:
        if taskId != getattr(self, '_activeLogDataTaskId', None):
            return
        try:
            figure, warnings = self._build_figure(
                list(result['sources']),
                str(result['xColumn']),
                list(result['y1Columns']),
                list(result['y2Columns']),
                list(result['warnings']),
            )
        except Exception as exc:
            self.loadingOverlay.hide()
            self._set_status(f'Failed to draw log plot: {exc}', error=True)
            return
        if figure is None:
            self.loadingOverlay.hide()
            warningText = '; '.join(warnings) or 'No selected files contain usable plot data.'
            self._set_status(warningText, error=True)
            return

        statusText = f'Log plot created successfully. {self._mode_text()}'
        if warnings:
            statusText += f' Skipped: {"; ".join(warnings)}'
        self.pendingRenderStatus = statusText
        self.pendingRenderStatusError = bool(warnings)
        self._render_figure(figure)

    def _on_log_data_failed(self, taskId: int, errorText: str) -> None:
        if taskId != getattr(self, '_activeLogDataTaskId', None):
            return
        self.loadingOverlay.hide()
        self._set_status(f'Failed to load log files: {errorText}', error=True)

    def _build_figure(
        self,
        sources: list[dict[str, object]],
        xColumn: str,
        y1Columns: list[str],
        y2Columns: list[str],
        warnings: list[str],
    ) -> tuple[go.Figure | None, list[str]]:
        preparedSources = []
        requiredColumns = list(dict.fromkeys([xColumn, *y1Columns, *y2Columns]))
        for source in sources:
            dataFrame = source['dataFrame']
            missingColumns = [
                columnName for columnName in requiredColumns
                if columnName not in dataFrame.columns
            ]
            if missingColumns:
                warnings.append(
                    f'{source["name"]}: missing {", ".join(missingColumns)}'
                )
                continue
            plotData = dataFrame[requiredColumns].copy()
            xIsDate = self._is_date_series(plotData[xColumn])
            if xIsDate:
                plotData[xColumn] = pd.to_datetime(plotData[xColumn], errors='coerce')
            for columnName in [*y1Columns, *y2Columns]:
                plotData[columnName] = pd.to_numeric(plotData[columnName], errors='coerce')
            plotData = plotData.dropna(subset=[xColumn])
            if plotData.empty or all(
                plotData[columnName].dropna().empty
                for columnName in [*y1Columns, *y2Columns]
            ):
                warnings.append(f'{source["name"]}: no usable plot data')
                continue
            preparedSources.append({
                'name': source['name'],
                'dataFrame': plotData,
                'xIsDate': xIsDate,
            })

        if not preparedSources:
            return None, warnings
        if len(preparedSources) == 1:
            if self.subplotRadioButton.isChecked():
                return self._build_y_item_subplot_figure(
                    preparedSources,
                    xColumn,
                    y1Columns,
                    y2Columns,
                ), warnings
            return self._build_overlap_figure(
                preparedSources,
                xColumn,
                y1Columns,
                y2Columns,
            ), warnings
        if self.subplotRadioButton.isChecked():
            return self._build_source_subplot_figure(
                preparedSources,
                xColumn,
                y1Columns,
                y2Columns,
            ), warnings
        return self._build_y_item_subplot_figure(
            preparedSources,
            xColumn,
            y1Columns,
            y2Columns,
        ), warnings

    def _build_overlap_figure(
        self,
        sources: list[dict[str, object]],
        xColumn: str,
        y1Columns: list[str],
        y2Columns: list[str],
    ) -> go.Figure:
        figure = go.Figure()
        includeFileName = len(sources) > 1
        for source in sources:
            for columnName in y1Columns:
                self._add_trace(
                    figure,
                    source,
                    xColumn,
                    columnName,
                    yAxis='y',
                    includeFileName=includeFileName,
                )
            for columnName in y2Columns:
                self._add_trace(
                    figure,
                    source,
                    xColumn,
                    columnName,
                    yAxis='y2',
                    includeFileName=includeFileName,
                )

        self._apply_common_layout(
            figure,
            xColumn,
            y1Columns,
            y2Columns,
            xIsDate=any(bool(source['xIsDate']) for source in sources),
        )
        return figure

    def _build_source_subplot_figure(
        self,
        sources: list[dict[str, object]],
        xColumn: str,
        y1Columns: list[str],
        y2Columns: list[str],
    ) -> go.Figure:
        rowCount, columnCount = self._best_subplot_grid(len(sources))
        figure = make_subplots(
            rows=rowCount,
            cols=columnCount,
            specs=self._subplot_specs(len(sources), rowCount, columnCount, bool(y2Columns)),
            subplot_titles=[str(source['name']) for source in sources],
            horizontal_spacing=self._subplot_spacing(self.horizontalSpaceSpinBox, columnCount),
            vertical_spacing=self._subplot_spacing(self.verticalSpaceSpinBox, rowCount),
        )
        for sourceIndex, source in enumerate(sources):
            rowIndex = sourceIndex // columnCount + 1
            columnIndex = sourceIndex % columnCount + 1
            for yColumn in y1Columns:
                trace = self._make_trace(source, xColumn, yColumn, includeFileName=False)
                figure.add_trace(trace, row=rowIndex, col=columnIndex, secondary_y=False)
            for yColumn in y2Columns:
                trace = self._make_trace(source, xColumn, yColumn, includeFileName=False)
                figure.add_trace(trace, row=rowIndex, col=columnIndex, secondary_y=True)
            self._apply_subplot_axes(
                figure,
                rowIndex,
                columnIndex,
                xColumn,
                bool(source['xIsDate']),
                y1Columns,
                y2Columns,
                sourceIndex > 0,
            )
        self._apply_common_layout(
            figure,
            xColumn,
            y1Columns,
            y2Columns,
            applyAxes=False,
        )
        return figure

    def _build_y_item_subplot_figure(
        self,
        sources: list[dict[str, object]],
        xColumn: str,
        y1Columns: list[str],
        y2Columns: list[str],
    ) -> go.Figure:
        subplotKeys = y1Columns or ['Y2']
        rowCount, columnCount = self._best_subplot_grid(len(subplotKeys))
        figure = make_subplots(
            rows=rowCount,
            cols=columnCount,
            specs=self._subplot_specs(len(subplotKeys), rowCount, columnCount, bool(y2Columns)),
            subplot_titles=subplotKeys,
            horizontal_spacing=self._subplot_spacing(self.horizontalSpaceSpinBox, columnCount),
            vertical_spacing=self._subplot_spacing(self.verticalSpaceSpinBox, rowCount),
        )
        includeFileName = len(sources) > 1
        anyXIsDate = any(bool(source['xIsDate']) for source in sources)
        for subplotIndex, y1Column in enumerate(subplotKeys):
            rowIndex = subplotIndex // columnCount + 1
            columnIndex = subplotIndex % columnCount + 1
            activeY1Columns = [] if not y1Columns else [y1Column]
            for source in sources:
                for yColumn in activeY1Columns:
                    trace = self._make_trace(source, xColumn, yColumn, includeFileName)
                    figure.add_trace(trace, row=rowIndex, col=columnIndex, secondary_y=False)
                for yColumn in y2Columns:
                    trace = self._make_trace(source, xColumn, yColumn, includeFileName)
                    figure.add_trace(trace, row=rowIndex, col=columnIndex, secondary_y=True)
            self._apply_subplot_axes(
                figure,
                rowIndex,
                columnIndex,
                xColumn,
                anyXIsDate,
                activeY1Columns,
                y2Columns,
                subplotIndex > 0,
            )
        self._apply_common_layout(
            figure,
            xColumn,
            y1Columns,
            y2Columns,
            applyAxes=False,
        )
        return figure

    def _best_subplot_grid(self, subplotCount: int) -> tuple[int, int]:
        columnCount = math.ceil(math.sqrt(max(1, subplotCount)))
        rowCount = math.ceil(max(1, subplotCount) / columnCount)
        return rowCount, columnCount

    def _subplot_specs(
        self,
        subplotCount: int,
        rowCount: int,
        columnCount: int,
        hasSecondaryY: bool,
    ) -> list[list[dict[str, bool] | None]]:
        specs = []
        for rowIndex in range(rowCount):
            rowSpecs = []
            for columnIndex in range(columnCount):
                subplotIndex = rowIndex * columnCount + columnIndex
                rowSpecs.append(
                    {'secondary_y': hasSecondaryY}
                    if subplotIndex < subplotCount
                    else None
                )
            specs.append(rowSpecs)
        return specs

    def _apply_subplot_axes(
        self,
        figure: go.Figure,
        rowIndex: int,
        columnIndex: int,
        xColumn: str,
        xIsDate: bool,
        y1Columns: list[str],
        y2Columns: list[str],
        lockToFirst: bool,
    ) -> None:
        xAxisOptions = {'title_text': xColumn}
        xTickFormat = self._x_tick_format(xIsDate)
        if xTickFormat:
            xAxisOptions['tickformat'] = xTickFormat
        figure.update_xaxes(**xAxisOptions, row=rowIndex, col=columnIndex)
        if y1Columns:
            y1AxisOptions = self._y_axis_options(
                self.y1TitleLineEdit.text().strip() or 'Y1',
                self.y1FormatLineEdit,
                self.y1LogCheckBox,
            )
            if lockToFirst:
                y1AxisOptions['matches'] = 'y'
            figure.update_yaxes(**y1AxisOptions, row=rowIndex, col=columnIndex, secondary_y=False)
        if y2Columns:
            y2AxisOptions = self._y_axis_options(
                self.y2TitleLineEdit.text().strip() or 'Y2',
                self.y2FormatLineEdit,
                self.y2LogCheckBox,
            )
            if lockToFirst:
                y2AxisOptions['matches'] = 'y2'
            figure.update_yaxes(**y2AxisOptions, row=rowIndex, col=columnIndex, secondary_y=True)

    def _subplot_spacing(self, spinBox: QSpinBox, axisCount: int) -> float:
        if axisCount <= 1:
            return 0.0
        requestedSpacing = spinBox.value() / 100.0
        maximumSpacing = 0.99 / (axisCount - 1)
        return min(requestedSpacing, maximumSpacing)

    def _add_trace(
        self,
        figure: go.Figure,
        source: dict[str, object],
        xColumn: str,
        yColumn: str,
        yAxis: str,
        includeFileName: bool,
    ) -> None:
        trace = self._make_trace(source, xColumn, yColumn, includeFileName)
        trace.yaxis = yAxis
        figure.add_trace(trace)

    def _make_trace(
        self,
        source: dict[str, object],
        xColumn: str,
        yColumn: str,
        includeFileName: bool,
    ) -> go.Scatter:
        plotData = source['dataFrame'].dropna(subset=[yColumn])
        traceName = (
            f'{source["name"]}: {yColumn}'
            if includeFileName
            else yColumn
        )
        return go.Scatter(
            x=plotData[xColumn],
            y=plotData[yColumn],
            mode=self._trace_mode(),
            name=traceName,
            legendgroup=str(source['name']),
            line=dict(width=self.lineWidthSpinBox.value()),
        )

    def _trace_mode(self) -> str:
        if self.lineCheckBox.isChecked() and self.symbolCheckBox.isChecked():
            return 'lines+markers'
        if self.symbolCheckBox.isChecked():
            return 'markers'
        return 'lines'

    def _apply_common_layout(
        self,
        figure: go.Figure,
        xColumn: str,
        y1Columns: list[str],
        y2Columns: list[str],
        applyAxes: bool = True,
        xIsDate: bool = False,
    ) -> None:
        plotlyTheme = self.plotlyThemeComboBox.currentText().strip()
        plotlyTheme = None if plotlyTheme == 'none' else plotlyTheme or 'plotly'
        figure.update_layout(
            title=dict(text=self._plot_title_html(), x=0.5, font=dict(size=self._font_sizes()['title'])),
            template=plotlyTheme,
            width=self.plotWidthSpinBox.value(),
            height=self.plotHeightSpinBox.value(),
            legend=dict(
                orientation='v',
                y=1,
                x=1.12,
                title_text='Legend',
                font=dict(size=self.legendFontSizeSpinBox.value()),
                title_font=dict(size=self.legendFontSizeSpinBox.value()),
                bordercolor='rgba(0,0,0,0.15)',
                borderwidth=1,
            ),
            margin={'t': 90, 'r': 180 if y2Columns else 100, 'l': 80, 'b': 70},
        )
        if applyAxes:
            xAxisOptions = {'title_text': xColumn}
            xTickFormat = self._x_tick_format(xIsDate)
            if xTickFormat:
                xAxisOptions['tickformat'] = xTickFormat
            figure.update_xaxes(**xAxisOptions)
            figure.update_yaxes(
                **self._y_axis_options(
                    self.y1TitleLineEdit.text().strip() or 'Y1',
                    self.y1FormatLineEdit,
                    self.y1LogCheckBox,
                )
            )
        if applyAxes and y2Columns:
            y2AxisOptions = self._y_axis_layout(
                self.y2TitleLineEdit.text().strip() or 'Y2',
                self.y2FormatLineEdit,
                self.y2LogCheckBox,
            )
            y2AxisOptions.update(overlaying='y', side='right')
            figure.update_layout(yaxis2=y2AxisOptions)
        self._apply_non_legend_fonts(figure)

    def _line_edit_text(self, lineEdit: QLineEdit | None) -> str:
        return lineEdit.text().strip() if lineEdit is not None else ''

    def _check_box_checked(self, checkBox: QCheckBox | None) -> bool:
        return bool(checkBox is not None and checkBox.isChecked())

    def _x_tick_format(self, xIsDate: bool) -> str:
        formatText = self._line_edit_text(self.xFormatLineEdit)
        if formatText:
            return self._date_tick_format(formatText) if xIsDate else formatText
        return '%Y/%m/%d %H:%M' if xIsDate else ''

    def _y_axis_options(
        self,
        title: str,
        formatLineEdit: QLineEdit | None,
        logCheckBox: QCheckBox | None,
    ) -> dict[str, str]:
        axisOptions = {'title_text': title}
        tickFormat = self._line_edit_text(formatLineEdit)
        if tickFormat:
            axisOptions['tickformat'] = tickFormat
        if self._check_box_checked(logCheckBox):
            axisOptions['type'] = 'log'
        return axisOptions

    def _y_axis_layout(
        self,
        title: str,
        formatLineEdit: QLineEdit | None,
        logCheckBox: QCheckBox | None,
    ) -> dict[str, str]:
        axisLayout = {'title': title}
        tickFormat = self._line_edit_text(formatLineEdit)
        if tickFormat:
            axisLayout['tickformat'] = tickFormat
        if self._check_box_checked(logCheckBox):
            axisLayout['type'] = 'log'
        return axisLayout

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

    def _font_sizes(self) -> dict[str, int]:
        adjustment = self.fontSizeSpinBox.value()
        return {
            'title': 20 + adjustment,
            'subtitle': 14 + adjustment,
            'axisTitle': 14 + adjustment,
            'tick': 12 + adjustment,
        }

    def _apply_non_legend_fonts(self, figure: go.Figure) -> None:
        fontSizes = self._font_sizes()
        figure.update_xaxes(
            title_font=dict(size=fontSizes['axisTitle']),
            tickfont=dict(size=fontSizes['tick']),
        )
        figure.update_yaxes(
            title_font=dict(size=fontSizes['axisTitle']),
            tickfont=dict(size=fontSizes['tick']),
        )
        figure.update_annotations(font=dict(size=fontSizes['tick']))

    def _plot_title_html(self) -> str:
        title = self.plotTitleLineEdit.text().strip() or self._build_auto_plot_title()
        subtitle = self.subTitleLineEdit.text().strip()
        subtitleSize = self._font_sizes()['subtitle']
        return f'{title}<br><span style="font-size:{subtitleSize}px">{subtitle}</span>' if subtitle else title

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

    def _clear_plot_area(self, statusText: str) -> None:
        self.loadingOverlay.hide()
        self.currentPlotHtml = ''
        self.currentPlotFigure = None
        self.currentPlotFilePath = ''
        self.currentViewerFilePath = ''
        self.pendingRenderStatus = ''
        self.pendingRenderStatusError = False
        try:
            self.chartView.setHtml('')
        except Exception:
            if isinstance(self.chartView, QTextBrowser):
                self.chartView.clear()
        self._set_status(statusText)

    def _render_figure(self, figure) -> None:
        self.currentPlotFigure = figure
        self.currentPlotHtml = ''
        self._set_status(f'Rendering log HTML. {self._mode_text()}')
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
                self._set_status(
                    self.pendingRenderStatus or f'Log plot created successfully. {self._mode_text()}',
                    error=self.pendingRenderStatusError,
                )
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
        self._set_status(
            self.pendingRenderStatus or f'Log plot created successfully. {self._mode_text()}',
            error=self.pendingRenderStatusError,
        )

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
        if selectedFile and not selectedFile.lower().endswith('.png'):
            selectedFile = f'{selectedFile}.png'
        try:
            save_plotly_png_and_copy_to_clipboard(self.currentPlotFigure, selectedFile)
            if selectedFile:
                self._set_status(f'PNG saved to {selectedFile} and copied to clipboard.')
            else:
                self._set_status('PNG copied to clipboard.')
        except Exception as exc:
            self._set_status(f'Failed to save PNG: {exc}', error=True)

    def _default_export_filename(self, suffix: str) -> str:
        title = self.plotTitleLineEdit.text().strip() or 'log_plot'
        safeTitle = re.sub(r'[^A-Za-z0-9._-]+', '_', title).strip('._') or 'log_plot'
        return f'{safeTitle}{suffix}'

    def _set_status(self, message: str, error: bool = False) -> None:
        self.statusLabel.setText(message)
        self.statusLabel.setStyleSheet('color: red;' if error else 'color: black;')
