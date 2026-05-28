import csv
import os
import re
from html import escape

import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QSortFilterProxyModel,
    Signal,
    Qt,
)
from PySide6.QtGui import QColor, QBrush, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QDialog,
    QFileDialog,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QLabel,
    QMessageBox,
    QMenu,
    QInputDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidgetAction,
    QWidget,
    QGroupBox,
)

from qt_helpers import require_child
from async_helpers import BackgroundTaskMixin
from loading_overlay import LoadingOverlay


class DropFileFilter(QObject):
    def __init__(self, onFileDropped) -> None:
        super().__init__()
        self.onFileDropped = onFileDropped

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.DragEnter:
            if self._first_supported_drop_file(event.mimeData()):
                event.acceptProposedAction()
                return True
        if event.type() == QEvent.Type.Drop:
            filePath = self._first_supported_drop_file(event.mimeData())
            if filePath:
                self.onFileDropped(filePath)
                event.acceptProposedAction()
                return True
        return super().eventFilter(watched, event)

    def _first_supported_drop_file(self, mimeData) -> str:
        if not mimeData.hasUrls():
            return ''
        for url in mimeData.urls():
            if not url.isLocalFile():
                continue
            filePath = url.toLocalFile()
            if filePath.lower().endswith(('.csv', '.xls', '.xlsx')):
                return filePath
        return ''


class PreviewTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.dataFrame = pd.DataFrame()
        self.columns = []

    def set_data_frame(self, dataFrame: pd.DataFrame) -> None:
        self.beginResetModel()
        self.dataFrame = dataFrame.reset_index(drop=True)
        self.columns = list(self.dataFrame.columns)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.dataFrame)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.UserRole):
            return None

        value = self.raw_value(index.row(), index.column())
        if role == Qt.UserRole:
            return value
        return self._format_preview_value(value)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            return str(self.columns[section])
        if orientation == Qt.Vertical:
            return str(section + 1)
        return None

    def raw_value(self, rowIndex: int, columnIndex: int):
        if self.dataFrame.empty:
            return None
        return self.dataFrame.iat[rowIndex, columnIndex]

    def unique_display_values(self, columnIndex: int) -> list[str]:
        if self.dataFrame.empty or columnIndex < 0 or columnIndex >= len(self.columns):
            return []

        series = self.dataFrame.iloc[:, columnIndex]
        values = sorted({self._format_preview_value(value) for value in series}, key=str.lower)
        return values

    def _format_preview_value(self, value) -> str:
        if pd.isna(value):
            return ''
        return str(value)


class PreviewSortFilterProxyModel(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.columnFilters: dict[int, set[str]] = {}

    def set_column_filter(self, columnIndex: int, selectedValues: set[str]) -> None:
        self.columnFilters[columnIndex] = selectedValues
        self.invalidateFilter()

    def clear_column_filter(self, columnIndex: int) -> None:
        self.columnFilters.pop(columnIndex, None)
        self.invalidateFilter()

    def clear_filters(self) -> None:
        self.columnFilters.clear()
        self.invalidateFilter()

    def filterAcceptsRow(self, sourceRow: int, sourceParent: QModelIndex) -> bool:
        sourceModel = self.sourceModel()
        if sourceModel is None:
            return True

        for columnIndex, selectedValues in self.columnFilters.items():
            index = sourceModel.index(sourceRow, columnIndex, sourceParent)
            valueText = sourceModel.data(index, Qt.DisplayRole)
            if valueText not in selectedValues:
                return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        leftValue = left.data(Qt.UserRole)
        rightValue = right.data(Qt.UserRole)

        if pd.isna(leftValue):
            return False
        if pd.isna(rightValue):
            return True
        try:
            return leftValue < rightValue
        except TypeError:
            return str(leftValue).lower() < str(rightValue).lower()


class CheckableComboBox(QComboBox):
    checkedItemsChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.itemModel = QStandardItemModel(self)
        self.setModel(self.itemModel)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self._skipNextHide = False
        self._updatingItems = False
        self.itemModel.itemChanged.connect(self._on_item_changed)
        self.view().pressed.connect(self._toggle_item)
        self.activated.connect(lambda _index: self._update_summary())
        self._update_summary()

    def add_check_item(self, text: str, checked: bool = False, enabled: bool = True) -> None:
        item = QStandardItem(text)
        flags = Qt.ItemIsUserCheckable
        if enabled:
            flags |= Qt.ItemIsEnabled
        item.setFlags(flags)
        item.setData(Qt.Checked if checked else Qt.Unchecked, Qt.CheckStateRole)
        self.itemModel.appendRow(item)
        self._update_summary()

    def set_check_items(
        self,
        itemStates: list[tuple[str, bool, bool]],
        emitChanged: bool = True,
    ) -> None:
        self._updatingItems = True
        super().clear()
        for text, checked, enabled in itemStates:
            item = QStandardItem(text)
            flags = Qt.ItemIsUserCheckable
            if enabled:
                flags |= Qt.ItemIsEnabled
            item.setFlags(flags)
            item.setData(Qt.Checked if checked else Qt.Unchecked, Qt.CheckStateRole)
            self.itemModel.appendRow(item)
        self._updatingItems = False
        self._update_summary()
        if emitChanged:
            self.checkedItemsChanged.emit()

    def checked_items(self) -> list[str]:
        checkedItems = []
        for rowIndex in range(self.itemModel.rowCount()):
            item = self.itemModel.item(rowIndex)
            if item is not None and item.checkState() == Qt.Checked:
                checkedItems.append(item.text())
        return checkedItems

    def clear(self) -> None:
        self._updatingItems = True
        super().clear()
        self._updatingItems = False
        self._update_summary()

    def hidePopup(self) -> None:
        if self._skipNextHide:
            self._skipNextHide = False
            return
        super().hidePopup()

    def _toggle_item(self, index: QModelIndex) -> None:
        item = self.model().itemFromIndex(index)
        if item is None or not item.isEnabled():
            return
        self._skipNextHide = True
        item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def _on_item_changed(self, _item: QStandardItem) -> None:
        if self._updatingItems:
            return
        self._update_summary()
        self.checkedItemsChanged.emit()

    def _update_summary(self) -> None:
        if self.lineEdit() is None:
            return
        checkedItems = self.checked_items()
        if not checkedItems:
            self.lineEdit().setText('No values selected')
        elif len(checkedItems) <= 2:
            self.lineEdit().setText(', '.join(checkedItems))
        else:
            self.lineEdit().setText(f'{len(checkedItems)} selected')


class TabDataWidget(BackgroundTaskMixin):
    _dateColumnNamePattern = re.compile(
        r'(date|time|datetime|timestamp|dt|日期|時間|日付|年月日)',
        re.IGNORECASE,
    )
    _chineseMeridiemDatePattern = re.compile(
        r'^\s*'
        r'(?P<date>\d{4}[/-]\d{1,2}[/-]\d{1,2})'
        r'\s*(?P<meridiem>上午|下午)\s*'
        r'(?P<hour>\d{1,2}):(?P<minute>\d{1,2})'
        r'(?::(?P<second>\d{1,2}))?'
        r'\s*$'
    )
    _excelZeroDatePattern = re.compile(
        r'^\s*\d{4}[/-](?:0[/-]\d{1,2}|\d{1,2}[/-]0)'
        r'(?:\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)?\s*$'
    )
    _suspiciousDateValues = {
        pd.Timestamp(1899, 12, 30).date(),
        pd.Timestamp(1899, 12, 31).date(),
        pd.Timestamp(1900, 1, 1).date(),
    }

    def __init__(self, rootWidget: QWidget):
        self.rootWidget = rootWidget
        self.tabDataWidget = require_child(rootWidget, QWidget, 'tabData')
        self.filePathLineEdit = require_child(rootWidget, QLineEdit, 'filePathLineEdit')
        self.browseFileButton = require_child(rootWidget, QPushButton, 'browseFileButton')
        self.loadButton = require_child(rootWidget, QPushButton, 'loadButton')
        self.skipRowsSpinBox = require_child(rootWidget, QSpinBox, 'skipRowsSpinBox')
        self.sheetComboBox = require_child(rootWidget, QComboBox, 'sheetComboBox')
        self.sheetGroupBox = require_child(rootWidget, QGroupBox, 'sheetGroupBox')
        self.columnsListWidget = require_child(rootWidget, QListWidget, 'columnsListWidget')
        self.stubnamesLineEdit = require_child(rootWidget, QLineEdit, 'stubnamesLineEdit')
        self.suffixLineEdit = require_child(rootWidget, QLineEdit, 'suffixLineEdit')
        self.suffixNameLineEdit = require_child(rootWidget, QLineEdit, 'suffixNameLineEdit')
        self.attachComboBox = require_child(rootWidget, QComboBox, 'attachComboBox')
        self.sheetNameLineEdit = require_child(rootWidget, QLineEdit, 'sheetNameLineEdit')
        self.savePathLineEdit = require_child(rootWidget, QLineEdit, 'savePathLineEdit')
        self.browseSaveButton = require_child(rootWidget, QPushButton, 'browseSaveButton')
        self.meltButton = require_child(rootWidget, QPushButton, 'meltButton')
        self.melt2Button = require_child(rootWidget, QPushButton, 'melt2Button')
        self.saveButton = require_child(rootWidget, QPushButton, 'saveButton')
        self.infoButton = require_child(rootWidget, QPushButton, 'infoButton')
        self.convertColumnButton = require_child(rootWidget, QPushButton, 'convertColumnButton')
        self.previewTableWidget = require_child(rootWidget, QTableView, 'previewTableWidget')
        self.statusLabel = require_child(rootWidget, QLabel, 'statusLabelTab1')

        self.loadedFilePath = ''
        self.loadedDataFrame = pd.DataFrame()
        self.meltedDataFrame = pd.DataFrame()
        self.previewSourceDataFrame = pd.DataFrame()
        self.previewTableModel = PreviewTableModel()
        self.previewProxyModel = PreviewSortFilterProxyModel()
        self.loadingOverlay = LoadingOverlay(self.previewTableWidget)
        self.previewMaxRows = 1000
        self.dataChangedCallbacks = []
        self.matchChangedCallbacks = []
        self.matchingColumnCount = 0
        self.totalColumnCount = 0
        self.autoDetectedSkipRows = None
        self.settingSkipRowsFromAutoDetect = False
        self._matchedColumnColor = QBrush(QColor(208, 245, 216))
        self.dropFileFilter = DropFileFilter(self._load_dropped_file)

        self._configure_widgets()

    def _configure_widgets(self) -> None:
        self.browseFileButton.clicked.connect(self._browse_file)
        self.loadButton.clicked.connect(self._load_selected_sheet)
        self.browseSaveButton.clicked.connect(self._browse_save_path)
        self.meltButton.clicked.connect(self._melt_dataframe)
        self.melt2Button.clicked.connect(self._show_long_to_wide_dialog)
        self.saveButton.clicked.connect(self._export_data_frame)
        self.infoButton.clicked.connect(self._show_wide_to_long_info)
        self.convertColumnButton.clicked.connect(self._convert_prefixed_columns)

        self.columnsListWidget.setSelectionMode(QAbstractItemView.NoSelection)
        self.tabDataWidget.setAcceptDrops(True)
        self.tabDataWidget.installEventFilter(self.dropFileFilter)
        self.filePathLineEdit.setAcceptDrops(True)
        self.filePathLineEdit.installEventFilter(self.dropFileFilter)
        previewFont = self.previewTableWidget.font()
        previewFont.setPointSize(12)
        self.previewTableWidget.setFont(previewFont)
        self.previewTableWidget.horizontalHeader().setFont(previewFont)
        self.previewTableWidget.verticalHeader().setFont(previewFont)
        self.previewTableWidget.setStyleSheet('QTableView { color: rgba(0, 0, 0, 204); }')
        self.previewProxyModel.setSourceModel(self.previewTableModel)
        self.previewTableWidget.setModel(self.previewProxyModel)
        self.previewTableWidget.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.previewTableWidget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.previewTableWidget.setAlternatingRowColors(True)
        self.previewTableWidget.horizontalHeader().setSectionsClickable(True)
        self.previewTableWidget.horizontalHeader().setSortIndicatorShown(True)
        self.previewTableWidget.horizontalHeader().sectionClicked.connect(
            self._show_preview_column_menu
        )
        self.previewTableWidget.horizontalHeader().sectionDoubleClicked.connect(
            self._rename_preview_column
        )

        self.filePathLineEdit.textChanged.connect(self._invalidate_melted_data)
        self.skipRowsSpinBox.valueChanged.connect(self._on_skip_rows_changed)
        self.stubnamesLineEdit.textChanged.connect(self._mark_matching_columns)
        self.stubnamesLineEdit.textChanged.connect(self._invalidate_melted_data)
        self.suffixLineEdit.textChanged.connect(self._mark_matching_columns)
        self.suffixLineEdit.textChanged.connect(self._invalidate_melted_data)
        self.suffixNameLineEdit.textChanged.connect(self._invalidate_melted_data)

        self.attachComboBox.addItems(['new workbook/file', 'attach new sheet to workbook'])

        self.sheetNameLineEdit.setText('wide_to_long')
        self.suffixNameLineEdit.setText('site')
        self.suffixLineEdit.setText('[TCBLR]')

    def _load_dropped_file(self, filePath: str) -> None:
        self._invalidate_melted_data()
        self.filePathLineEdit.setText(filePath)
        self.autoDetectedSkipRows = None
        self._load_file()

    def _browse_file(self) -> None:
        selectedFile, _ = QFileDialog.getOpenFileName(
            self.rootWidget,
            'Open Excel or CSV',
            os.getcwd(),
            'Excel Files (*.xlsx *.xls);;CSV Files (*.csv);;All Files (*)',
        )
        if selectedFile:
            self._invalidate_melted_data()
            self.filePathLineEdit.setText(selectedFile)
            self.autoDetectedSkipRows = None
            self._load_file()

    def _on_skip_rows_changed(self, *_args) -> None:
        if not self.settingSkipRowsFromAutoDetect:
            self.autoDetectedSkipRows = None
        self._invalidate_melted_data()

    def _browse_save_path(self) -> None:
        defaultPath = self._default_export_path()
        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Export DataFrame',
            defaultPath,
            'Excel Files (*.xlsx);;CSV Files (*.csv);;All Files (*)',
        )
        if selectedFile:
            self.savePathLineEdit.setText(selectedFile)

    def _default_export_path(self) -> str:
        if self.loadedFilePath:
            baseName, _extension = os.path.splitext(self.loadedFilePath)
            return f'{baseName}_export.xlsx'
        return os.path.join(os.getcwd(), 'pchart_export.xlsx')

    def _show_wide_to_long_info(self) -> None:
        imagePath = os.path.join(os.path.dirname(__file__), 'w2l.png')
        pixmap = QPixmap(imagePath)
        if pixmap.isNull():
            self._set_status(f'Cannot load info image: {imagePath}', error=True)
            return

        imageLabel = QLabel()
        imageLabel.setPixmap(pixmap)
        imageLabel.setAlignment(Qt.AlignCenter)

        scrollArea = QScrollArea()
        scrollArea.setWidget(imageLabel)
        scrollArea.setWidgetResizable(False)

        infoTextEdit = QTextEdit()
        infoFont = infoTextEdit.font()
        infoFont.setPointSize(10)
        infoTextEdit.setFont(infoFont)
        infoTextEdit.setReadOnly(True)
        infoTextEdit.setStyleSheet('QTextEdit { color: rgba(0, 0, 0, 204); }')
        infoTextEdit.setPlainText(
            """
1. 這個app 會轉換 excel or csv, 利用 pandas 的 wide_to_long 功能, 把寬格式的資料轉換成長格式. 這對於後續的分析和繪圖很有幫助.
(我也不知道這麼基本的功能 excel 為什麼沒有, 不過應該有人寫過巨集或 UEDA 可能也有. 我還沒測試, 反正 我寫這個比摸清楚 UEDA 還快)

2. 但是必須先修改欄位, 要合併的必須都長得像 WAT_T, WAT_C....Thickness_T, Thickness_C, inlineRS_T....
也就是說, 這些欄位的名稱都要有一個共同的前綴 (stubname), 和一個共同的後綴 (suffix), 這樣 wide_to_long 才知道要怎麼合併.
我知道會很麻煩, 所以可以用 [T_AVG --> AVG_T] 這個按鈕來幫忙轉換一次. 但是如果欄位名稱太亂, 可能還是需要手動改一下比較快.

3. 有些情況不需要, 比如 AVG -> Y, 時間 --> X, 打算用機台, product 當作 category, 這時候就不需要轉換了, 直接用原來的寬格式資料就好.

4. 這個 app 輸出用 HTML + JavaScript 功能來顯示互動式圖表, 這樣就算是複雜的交互式圖表也能在app裡面直接看, 不需要再開一個 Excel 或 Python 的視窗.
但是如果電腦環境不支援 PySide6.WebEngine, 就會退回到在系統瀏覽器裡面看圖, 這時候app 的字體可能會變得很大很醜,
這 不 是 我 的 錯
, 是 PySide6.WebEngine 的問題. 但是產生的 HTML 應該還是正常的,可以放大縮小移動 balabala

5. V2.6 新增功能, 把 long-->wide. 是對付 eFAB/WAT 這種有 PARAMETER 欄位, 分別對應不同片好的不同 parameter 的 AVG, MIN,MAX 等等, 但是又想要把這些 parameter 分別當作不同的欄位來分析的情況. 這樣就可以先用 wide_to_long 把資料轉成長格式, 加工完之後再用 long_to_wide 把它轉回寬格式, 這樣就可以直接拿去畫圖了.

6. WaferMap 可以畫 frames, dies, 還可以畫出 mapping. 但無法把 eFAB/WAT mapping 對應過來. 因為 WAT/mapping 的座標根本亂來. 但是其他 app 如 KGD, OP 之類的就沒問題了
""".strip()
        )
        infoTextEdit.setMinimumHeight(160)

        dialog = QDialog(self.rootWidget)
        dialog.setWindowTitle('如 何 用 火')
        dialogLayout = QVBoxLayout(dialog)
        dialogLayout.addWidget(scrollArea)
        dialogLayout.addWidget(infoTextEdit)
        dialog.resize(min(pixmap.width() + 40, 1100), min(pixmap.height() + 400, 900))
        dialog.exec()

    def _load_file(self) -> None:
        filePath = self.filePathLineEdit.text().strip()
        if not filePath:
            self._set_status('Please choose a file first.', error=True)
            return

        if not os.path.exists(filePath):
            self._set_status(f'File does not exist: {filePath}', error=True)
            return

        self.loadedFilePath = filePath
        self.loadButton.setEnabled(False)
        self.browseFileButton.setEnabled(False)
        self._set_status('Loading file...')
        self.loadingOverlay.show('Loading...')
        skipRowsRequest = self._skip_rows_request()

        def work() -> dict[str, object]:
            if filePath.lower().endswith('.csv'):
                skipRows = self._resolve_skip_rows_for_worker(
                    filePath,
                    skipRowsRequest,
                )
                dataFrame = pd.read_csv(filePath, skiprows=skipRows)
                normalizedColumns = self._normalize_loaded_datetime_formats(dataFrame)
                warnings = self._loaded_data_warnings(dataFrame)
                return {
                    'kind': 'csv',
                    'filePath': filePath,
                    'dataFrame': dataFrame,
                    'skipRows': skipRows,
                    'normalizedColumns': normalizedColumns,
                    'warnings': warnings,
                }

            with pd.ExcelFile(filePath) as excelReader:
                sheetNames = excelReader.sheet_names
            return {'kind': 'workbook', 'filePath': filePath, 'sheetNames': sheetNames}

        self._activeLoadTaskId = self._start_background_task(
            work,
            self._on_load_file_finished,
            self._on_load_file_failed,
        )

    def _on_load_file_finished(self, taskId: int, result: dict[str, object]) -> None:
        if taskId != getattr(self, '_activeLoadTaskId', None):
            return
        self.browseFileButton.setEnabled(True)
        if result.get('filePath') != self.loadedFilePath:
            return

        if result.get('kind') == 'csv':
            self.loadedDataFrame = result['dataFrame']
            self._apply_resolved_skip_rows(int(result.get('skipRows', 0)))
            warnings = list(result.get('warnings', []))
            self._append_detected_stubnames()
            self._populate_columns()
            self._warn_if_no_reshape_columns()
            self._show_preview(self.loadedDataFrame)
            self._notify_data_changed()
            self.sheetComboBox.clear()
            self.sheetComboBox.addItem('csv')
            self.sheetComboBox.setEnabled(False)
            self.loadButton.setEnabled(False)
            statusText = 'CSV loaded successfully.'
            skipRows = int(result.get('skipRows', 0))
            normalizedColumns = list(result.get('normalizedColumns', []))
            if skipRows:
                statusText += f' skiprows={skipRows}.'
            if normalizedColumns:
                statusText += f' Converted {len(normalizedColumns)} AM/PM date column(s).'
            if warnings:
                statusText += ' Possible date format issue found.'
            self._set_status(statusText, error=bool(warnings))
            self.loadingOverlay.hide()
            if warnings:
                self._show_loaded_data_warning_messages(warnings)
            return

        sheetNames = list(result.get('sheetNames', []))
        self.loadingOverlay.hide()
        self.sheetComboBox.clear()
        self.sheetComboBox.addItems(sheetNames)
        self.sheetComboBox.setEnabled(True)
        self.loadButton.setEnabled(True)
        self._set_status('Excel workbook loaded. Choose a sheet and press Load.')

    def _on_load_file_failed(self, taskId: int, errorText: str) -> None:
        if taskId != getattr(self, '_activeLoadTaskId', None):
            return
        self.browseFileButton.setEnabled(True)
        self.loadButton.setEnabled(True)
        self.loadingOverlay.hide()
        self._set_status(f'Error loading file: {errorText}', error=True)

    def _load_selected_sheet(self) -> None:
        filePath = self.filePathLineEdit.text().strip()
        if not filePath or not os.path.exists(filePath):
            self._set_status('Choose a valid file before loading a sheet.', error=True)
            return

        if filePath.lower().endswith('.csv'):
            self._load_file()
            return

        sheetName = self.sheetComboBox.currentText().strip()
        if not sheetName:
            self._set_status('Pick a sheet name before loading.', error=True)
            return

        self.loadedFilePath = filePath
        self._load_sheet_data(sheetName)

    def _load_sheet_data(self, sheetName: str) -> None:
        self._invalidate_melted_data()
        filePath = self.loadedFilePath
        self.loadButton.setEnabled(False)
        self.browseFileButton.setEnabled(False)
        self._set_status(f'Loading sheet "{sheetName}"...')
        self.loadingOverlay.show('Loading...')
        skipRowsRequest = self._skip_rows_request()

        def work() -> dict[str, object]:
            skipRows = self._resolve_skip_rows_for_worker(
                filePath,
                skipRowsRequest,
                sheetName=sheetName,
            )
            with pd.ExcelFile(filePath) as excelReader:
                dataFrame = pd.read_excel(
                    excelReader,
                    sheet_name=sheetName,
                    skiprows=skipRows,
                )
            normalizedColumns = self._normalize_loaded_datetime_formats(dataFrame)
            warnings = self._loaded_data_warnings(dataFrame)
            return {
                'filePath': filePath,
                'sheetName': sheetName,
                'dataFrame': dataFrame,
                'skipRows': skipRows,
                'normalizedColumns': normalizedColumns,
                'warnings': warnings,
            }

        self._activeSheetTaskId = self._start_background_task(
            work,
            self._on_load_sheet_finished,
            self._on_load_sheet_failed,
        )

    def _on_load_sheet_finished(self, taskId: int, result: dict[str, object]) -> None:
        if taskId != getattr(self, '_activeSheetTaskId', None):
            return
        self.browseFileButton.setEnabled(True)
        self.loadButton.setEnabled(True)
        if result.get('filePath') != self.loadedFilePath:
            return
        self.loadedDataFrame = result['dataFrame']
        self._apply_resolved_skip_rows(int(result.get('skipRows', 0)))
        warnings = list(result.get('warnings', []))
        self._append_detected_stubnames()
        self._populate_columns()
        self._warn_if_no_reshape_columns()
        self._show_preview(self.loadedDataFrame)
        self._notify_data_changed()
        sheetName = str(result.get('sheetName', ''))
        statusText = f'Sheet "{sheetName}" loaded successfully.'
        skipRows = int(result.get('skipRows', 0))
        normalizedColumns = list(result.get('normalizedColumns', []))
        if skipRows:
            statusText += f' skiprows={skipRows}.'
        if normalizedColumns:
            statusText += f' Converted {len(normalizedColumns)} AM/PM date column(s).'
        if warnings:
            statusText += ' Possible date format issue found.'
        self._set_status(statusText, error=bool(warnings))
        self.loadingOverlay.hide()
        if warnings:
            self._show_loaded_data_warning_messages(warnings)

    def _on_load_sheet_failed(self, taskId: int, errorText: str) -> None:
        if taskId != getattr(self, '_activeSheetTaskId', None):
            return
        self.browseFileButton.setEnabled(True)
        self.loadButton.setEnabled(True)
        self.loadingOverlay.hide()
        self._set_status(f'Failed to load sheet: {errorText}', error=True)

    def _normalize_loaded_datetime_formats(self, dataFrame: pd.DataFrame) -> list[str]:
        normalizedColumns = []
        for columnName in dataFrame.columns:
            series = dataFrame[columnName]
            if not isinstance(series, pd.Series):
                continue
            normalizedSeries = self._normalize_chinese_meridiem_datetime_series(series)
            if normalizedSeries is None:
                continue
            dataFrame[columnName] = normalizedSeries
            normalizedColumns.append(str(columnName))
        return normalizedColumns

    def _normalize_chinese_meridiem_datetime_series(
        self,
        series: pd.Series,
    ) -> pd.Series | None:
        if not (
            pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)
        ):
            return None

        nonEmptyText = series.dropna().astype(str).str.strip()
        nonEmptyText = nonEmptyText[nonEmptyText != '']
        if nonEmptyText.empty:
            return None

        matchedRows = nonEmptyText.str.extract(self._chineseMeridiemDatePattern)
        matchedMask = matchedRows['date'].notna()
        if int(matchedMask.sum()) == 0:
            return None

        normalizedText = nonEmptyText.copy()
        normalizedText.loc[matchedMask] = [
            self._format_chinese_meridiem_datetime(row)
            for _, row in matchedRows.loc[matchedMask].iterrows()
        ]
        parsedDates = pd.to_datetime(normalizedText, errors='coerce')
        if int(parsedDates.notna().sum()) != len(nonEmptyText):
            return None

        normalizedSeries = pd.Series(pd.NaT, index=series.index, dtype='datetime64[ns]')
        normalizedSeries.loc[parsedDates.index] = parsedDates
        return normalizedSeries

    def _format_chinese_meridiem_datetime(self, matchedRow: pd.Series) -> str:
        hour = int(matchedRow['hour'])
        if matchedRow['meridiem'] == '上午':
            hour = 0 if hour == 12 else hour
        else:
            hour = hour if hour == 12 else hour + 12

        second = matchedRow.get('second')
        timeText = f'{hour:02d}:{int(matchedRow["minute"]):02d}'
        if pd.notna(second):
            timeText += f':{int(second):02d}'
        return f'{matchedRow["date"]} {timeText}'

    def _show_loaded_data_warnings(self, dataFrame: pd.DataFrame) -> bool:
        warnings = self._loaded_data_warnings(dataFrame)
        if not warnings:
            return False
        self._show_loaded_data_warning_messages(warnings)
        return True

    def _loaded_data_warnings(self, dataFrame: pd.DataFrame) -> list[str]:
        return [
            *self._detect_excel_zero_date_columns(dataFrame),
            *self._detect_suspicious_date_columns(dataFrame),
        ]

    def _show_loaded_data_warning_messages(self, warnings: list[str]) -> None:
        warningText = '\n'.join(warnings[:8])
        if len(warnings) > 8:
            warningText += f'\n...and {len(warnings) - 8} more columns.'

        QMessageBox.warning(
            self.rootWidget,
            'Data format warning',
            (
                'Some loaded values look like suspicious date/time formats.\n\n'
                f'{warningText}\n\n'
                'Please check whether blank dates, invalid dates, or decimal/time '
                'values were converted by Excel or pandas.'
            ),
        )

    def _detect_excel_zero_date_columns(self, dataFrame: pd.DataFrame) -> list[str]:
        warnings = []
        for columnName in dataFrame.columns:
            columnText = str(columnName)
            series = dataFrame[columnName]
            if not isinstance(series, pd.Series):
                continue
            if not (
                pd.api.types.is_object_dtype(series)
                or pd.api.types.is_string_dtype(series)
            ):
                continue

            textSeries = series.dropna().astype(str).str.strip()
            textSeries = textSeries[textSeries != '']
            if textSeries.empty:
                continue

            suspiciousMask = textSeries.str.match(self._excelZeroDatePattern)
            suspiciousCount = int(suspiciousMask.sum())
            if suspiciousCount == 0:
                continue

            examples = sorted(set(textSeries[suspiciousMask].head(3)))
            warnings.append(
                f'- {columnText}: {suspiciousCount} invalid zero-date value(s), '
                f'e.g. {", ".join(examples)}. This may be a decimal/time value, not a date.'
            )
        return warnings

    def _detect_suspicious_date_columns(self, dataFrame: pd.DataFrame) -> list[str]:
        warnings = []
        for columnName in dataFrame.columns:
            columnText = str(columnName)
            series = dataFrame[columnName]
            if not isinstance(series, pd.Series):
                continue
            if series.empty or not self._is_date_candidate_column(columnText, series):
                continue

            parsedDates = self._parse_date_series(series)
            if parsedDates.empty:
                continue

            dateValues = parsedDates.dt.date
            suspiciousMask = dateValues.isin(self._suspiciousDateValues)
            suspiciousCount = int(suspiciousMask.sum())
            if suspiciousCount == 0:
                continue

            examples = sorted({str(value) for value in dateValues[suspiciousMask].dropna()})
            warnings.append(
                f'- {columnText}: {suspiciousCount} suspicious value(s), '
                f'e.g. {", ".join(examples[:3])}'
            )
        return warnings

    def _is_date_candidate_column(self, columnName: str, series: pd.Series) -> bool:
        if pd.api.types.is_datetime64_any_dtype(series):
            return True

        if not self._dateColumnNamePattern.search(columnName):
            return False

        return (
            pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)
            or pd.api.types.is_datetime64_any_dtype(series)
        )

    def _parse_date_series(self, series: pd.Series) -> pd.Series:
        nonEmptySeries = series.dropna()
        if nonEmptySeries.empty:
            return pd.Series(dtype='datetime64[ns]')

        parsedDates = pd.to_datetime(nonEmptySeries, errors='coerce')
        return parsedDates.dropna()

    def _populate_columns(self) -> None:
        self.columnsListWidget.clear()
        for columnName in self.loadedDataFrame.columns.astype(str):
            self.columnsListWidget.addItem(QListWidgetItem(columnName))
        self._mark_matching_columns()

    def _convert_prefixed_columns(self) -> None:
        if self.loadedDataFrame.empty:
            self._set_status('Load data before converting column names.', error=True)
            return

        convertedColumns = []
        convertedCount = 0
        usedColumnNames = set()
        for columnName in self.loadedDataFrame.columns.astype(str):
            convertedName = self._convert_prefixed_column_name(columnName)
            if convertedName != columnName:
                convertedCount += 1
            uniqueName = self._unique_column_name(convertedName, usedColumnNames)
            usedColumnNames.add(uniqueName)
            convertedColumns.append(uniqueName)

        if convertedCount == 0:
            self._set_status('No T_/L_/B_/C_/R_ column names found to convert.')
            return

        self._invalidate_melted_data()
        self.loadedDataFrame.columns = convertedColumns
        self._append_detected_stubnames()
        self._populate_columns()
        self._show_preview(self.loadedDataFrame)
        self._notify_data_changed()
        self._set_status(f'Converted {convertedCount} column names for wide_to_long.')

    def _convert_prefixed_column_name(self, columnName: str) -> str:
        matchedColumn = re.match(r'^([TLBCR])_(.+)$', columnName.strip())
        if not matchedColumn:
            return columnName
        suffixName, stubName = matchedColumn.groups()
        return f'{stubName}_{suffixName}'

    def _unique_column_name(self, columnName: str, usedColumnNames: set[str]) -> str:
        if columnName not in usedColumnNames:
            return columnName

        suffixIndex = 2
        while f'{columnName}_{suffixIndex}' in usedColumnNames:
            suffixIndex += 1
        return f'{columnName}_{suffixIndex}'

    def _append_detected_stubnames(self) -> None:
        detectedStubnames = self._detect_suffix_stubnames()
        if not detectedStubnames:
            return

        existingStubnames = self._parse_stubnames()
        existingStubnameSet = set(existingStubnames)
        newStubnames = [
            stubname
            for stubname in detectedStubnames
            if stubname not in existingStubnameSet
        ]
        if not newStubnames:
            return

        self.stubnamesLineEdit.setText(','.join([*existingStubnames, *newStubnames]))

    def _detect_suffix_stubnames(self) -> list[str]:
        detectedStubnames = []
        detectedStubnameSet = set()
        for columnName in self.loadedDataFrame.columns.astype(str):
            matchedColumn = re.match(r'^(.+_)([TLBCR])$', columnName.strip())
            if not matchedColumn:
                continue
            stubname = matchedColumn.group(1)
            if stubname in detectedStubnameSet:
                continue
            detectedStubnameSet.add(stubname)
            detectedStubnames.append(stubname)
        return detectedStubnames

    def _parse_stubnames(self) -> list[str]:
        rawStubnames = self.stubnamesLineEdit.text().strip()
        return [stubname.strip() for stubname in rawStubnames.split(',') if stubname.strip()]

    def _matching_columns(self) -> list[str]:
        stubnames = self._parse_stubnames()
        suffixPattern = self.suffixLineEdit.text().strip()
        if not stubnames or not suffixPattern:
            return []

        try:
            columnPattern = re.compile(
                r'^(?:' + '|'.join(re.escape(stubname) for stubname in stubnames) + r')'
                + r'(?:' + suffixPattern + r')$'
            )
        except re.error:
            return []

        return [
            columnName
            for columnName in self.loadedDataFrame.columns.astype(str)
            if columnPattern.match(columnName)
        ]

    def _mark_matching_columns(self) -> None:
        matchingColumns = set(self._matching_columns())
        self.matchingColumnCount = len(matchingColumns)
        self.totalColumnCount = self.columnsListWidget.count()
        self._update_sheet_group_title()
        for rowIndex in range(self.columnsListWidget.count()):
            item = self.columnsListWidget.item(rowIndex)
            if item is None:
                continue
            columnName = item.text().removeprefix('[MATCH] ')
            item.setText(columnName)
            item.setBackground(QBrush())
            item.setToolTip('')
            if columnName in matchingColumns:
                item.setText(f'[MATCH] {columnName}')
                item.setBackground(self._matchedColumnColor)
                item.setToolTip('Matched by stubnames and suffix regex.')
        self._notify_match_changed()

    def _update_sheet_group_title(self) -> None:
        self.sheetGroupBox.setTitle(
            f'Sheet & columns ({self.matchingColumnCount}/{self.totalColumnCount})'
        )

    def _warn_if_no_reshape_columns(self) -> None:
        if self.loadedDataFrame.empty or self.matchingColumnCount > 0:
            return
        QMessageBox.warning(self.rootWidget, 'p-chart', '無可以 reshape 欄位')

    def _ordered_reshaped_columns(
        self,
        dataFrame: pd.DataFrame,
        matchedColumns: list[str],
        suffixName: str,
        stubnames: list[str],
    ) -> list[str]:
        matchedColumnSet = set(matchedColumns)
        sourceColumns = [
            columnName
            for columnName in self.loadedDataFrame.columns.astype(str)
            if columnName not in matchedColumnSet and columnName in dataFrame.columns
        ]
        appendedColumns = [
            columnName
            for columnName in [suffixName, *stubnames]
            if columnName in dataFrame.columns and columnName not in sourceColumns
        ]
        remainingColumns = [
            columnName
            for columnName in dataFrame.columns
            if columnName not in sourceColumns and columnName not in appendedColumns
        ]
        return [*sourceColumns, *appendedColumns, *remainingColumns]

    def _melt_dataframe(self) -> None:
        if self.loadedDataFrame.empty:
            self._set_status('No data loaded; import an Excel or CSV file first.', error=True)
            return

        stubnames = self._parse_stubnames()
        suffixPattern = self.suffixLineEdit.text().strip()
        suffixName = self.suffixNameLineEdit.text().strip()
        suffixName = suffixName or 'suffix'

        if not stubnames:
            self._set_status('Please enter at least one stubname.', error=True)
            return

        if not suffixPattern:
            self._set_status('Please enter a suffix regex.', error=True)
            return

        try:
            re.compile(suffixPattern)
        except re.error as exc:
            self._set_status(f'Invalid suffix regex: {exc}', error=True)
            return

        matchedColumns = self._matching_columns()
        if not matchedColumns:
            self._set_status('No columns match the stubnames and suffix regex.', error=True)
            return

        rowIdColumn = '__wideToLongRowId'
        while rowIdColumn in self.loadedDataFrame.columns:
            rowIdColumn = f'_{rowIdColumn}'

        try:
            workingDataFrame = self.loadedDataFrame.copy()
            workingDataFrame.insert(0, rowIdColumn, range(len(workingDataFrame)))
            self.meltedDataFrame = pd.wide_to_long(
                workingDataFrame,
                stubnames=stubnames,
                i=rowIdColumn,
                j=suffixName,
                sep='',
                suffix=suffixPattern,
            )
            self.meltedDataFrame = self.meltedDataFrame.reset_index().drop(columns=[rowIdColumn])
            orderedColumns = self._ordered_reshaped_columns(
                self.meltedDataFrame,
                matchedColumns,
                suffixName,
                stubnames,
            )
            self.meltedDataFrame = self.meltedDataFrame.loc[:, orderedColumns]
            self._show_preview(self.meltedDataFrame)
            self._notify_data_changed()
            self._set_status(
                f'Data reshaped with wide_to_long. Matched columns: {len(matchedColumns)}.'
            )
        except Exception as exc:
            self._set_status(f'Error reshaping data: {exc}', error=True)

    def _show_long_to_wide_dialog(self) -> None:
        sourceDataFrame = self._long_to_wide_source_data_frame()
        if sourceDataFrame.empty:
            self._set_status('No data loaded; import an Excel or CSV file first.', error=True)
            return

        sourceDataFrame = sourceDataFrame.copy()
        sourceDataFrame.columns = sourceDataFrame.columns.astype(str)
        columnNames = list(sourceDataFrame.columns)
        if len(set(columnNames)) != len(columnNames):
            self._set_status('Long-to-wide requires unique column names.', error=True)
            return

        dialog = QDialog(self.rootWidget)
        dialog.setWindowTitle('Long-to-wide 轉換')
        dialog.resize(600, 260)
        dialogLayout = QVBoxLayout(dialog)
        dialogLayout.addWidget(QLabel('選擇放各 parameter 的 column name', dialog))

        parameterComboBox = QComboBox(dialog)
        parameterComboBox.addItems(columnNames)
        parameterComboBox.setMinimumWidth(420)
        defaultParameterIndex = self._default_parameter_column_index(columnNames)
        if defaultParameterIndex >= 0:
            parameterComboBox.setCurrentIndex(defaultParameterIndex)

        parameterLabel = QLabel('Select patameter', dialog)
        dialogLayout.addWidget(parameterLabel)
        dialogLayout.addWidget(parameterComboBox)

        indexComboBox = CheckableComboBox(dialog)
        indexComboBox.setMinimumWidth(420)
        valueComboBox = CheckableComboBox(dialog)
        valueComboBox.setMinimumWidth(420)

        indexOptionsInitialized = False
        valueOptionsInitialized = False

        def rebuild_index_options() -> None:
            nonlocal indexOptionsInitialized
            selectedParameter = parameterComboBox.currentText()
            checkedColumns = set(indexComboBox.checked_items())
            itemStates = []
            for columnName in columnNames:
                enabled = columnName != selectedParameter
                if indexOptionsInitialized:
                    checked = columnName in checkedColumns
                else:
                    checked = self._is_default_long_to_wide_index_column(columnName)
                itemStates.append((columnName, checked and enabled, enabled))
            indexComboBox.set_check_items(itemStates, emitChanged=False)
            indexOptionsInitialized = True

        def rebuild_value_options() -> None:
            nonlocal valueOptionsInitialized
            selectedParameter = parameterComboBox.currentText()
            selectedIndexColumns = set(indexComboBox.checked_items())
            checkedColumns = set(valueComboBox.checked_items())
            itemStates = []
            for columnName in columnNames:
                enabled = (
                    columnName != selectedParameter
                    and columnName not in selectedIndexColumns
                )
                if valueOptionsInitialized:
                    checked = columnName in checkedColumns
                else:
                    checked = '_' in columnName
                itemStates.append((columnName, checked and enabled, enabled))
            valueComboBox.set_check_items(itemStates, emitChanged=False)
            valueOptionsInitialized = True

        def on_parameter_changed(_selectedParameter: str) -> None:
            rebuild_index_options()
            rebuild_value_options()

        rebuild_index_options()
        rebuild_value_options()
        parameterComboBox.currentTextChanged.connect(on_parameter_changed)
        indexComboBox.checkedItemsChanged.connect(rebuild_value_options)

        indexLabel = QLabel('index', dialog)
        dialogLayout.addWidget(indexLabel)
        dialogLayout.addWidget(indexComboBox)

        valueLabel = QLabel('values', dialog)
        dialogLayout.addWidget(valueLabel)
        dialogLayout.addWidget(valueComboBox)

        buttonLayout = QHBoxLayout()
        buttonLayout.addStretch()
        cancelButton = QPushButton('CANCEL', dialog)
        convertButton = QPushButton('OK', dialog)
        cancelButton.clicked.connect(dialog.reject)
        convertButton.clicked.connect(dialog.accept)
        buttonLayout.addWidget(cancelButton)
        buttonLayout.addWidget(convertButton)
        dialogLayout.addLayout(buttonLayout)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        parameterColumn = parameterComboBox.currentText()
        indexColumns = indexComboBox.checked_items()
        valueColumns = valueComboBox.checked_items()
        selectedColumns = {parameterColumn, *indexColumns, *valueColumns}
        droppedColumns = [
            columnName
            for columnName in columnNames
            if columnName not in selectedColumns
        ]
        if not self._confirm_long_to_wide_dropped_columns(droppedColumns):
            return
        self._long_to_wide_dataframe(
            sourceDataFrame,
            parameterColumn,
            indexColumns,
            valueColumns,
        )

    def _long_to_wide_source_data_frame(self) -> pd.DataFrame:
        if not self.meltedDataFrame.empty:
            return self.meltedDataFrame.copy()
        return self.loadedDataFrame.copy()

    def _default_parameter_column_index(self, columnNames: list[str]) -> int:
        exactNames = {'PARAMETER', 'PARAMETERS'}
        for columnIndex, columnName in enumerate(columnNames):
            if columnName.upper() in exactNames:
                return columnIndex
        for columnIndex, columnName in enumerate(columnNames):
            upperColumnName = columnName.upper()
            if 'PARAMETER' in upperColumnName or 'PARAMETERS' in upperColumnName:
                return columnIndex
        return 0 if columnNames else -1

    def _is_default_long_to_wide_index_column(self, columnName: str) -> bool:
        normalizedName = columnName.strip().lower()
        compactName = re.sub(r'[^a-z0-9]+', '', normalizedName)
        tokens = [token for token in re.split(r'[^a-z0-9]+', normalizedName) if token]
        return (
            'waferid' in compactName
            or 'lotno' in compactName
            or 'processunit' in compactName
            or 'wafer' in tokens
            or 'lot' in tokens
            or 'tool' in tokens
            or 'pu' in tokens
        )

    def _confirm_long_to_wide_dropped_columns(self, droppedColumns: list[str]) -> bool:
        droppedColumnText = ', '.join(droppedColumns) if droppedColumns else '無'
        message = (
            f'沒有被選擇的欄位：{droppedColumnText}\n'
            '會被消失不見，請注意 save 也會消失掉這些欄位'
        )
        answer = QMessageBox.warning(
            self.rootWidget,
            'Long-to-wide 欄位確認',
            message,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _long_to_wide_dataframe(
        self,
        dataFrame: pd.DataFrame,
        parameterColumn: str,
        indexColumns: list[str],
        valueColumns: list[str],
    ) -> None:
        if not parameterColumn or parameterColumn not in dataFrame.columns:
            self._set_status('Choose a valid parameter column.', error=True)
            return

        indexColumns = [
            columnName
            for columnName in indexColumns
            if columnName in dataFrame.columns and columnName != parameterColumn
        ]
        valueColumns = [
            columnName
            for columnName in valueColumns
            if columnName in dataFrame.columns
        ]
        if not valueColumns:
            self._set_status('Choose at least one value column for long-to-wide.', error=True)
            return

        if parameterColumn in valueColumns:
            self._set_status('Parameter column cannot also be a value column.', error=True)
            return

        overlappingColumns = sorted(set(indexColumns) & set(valueColumns))
        if overlappingColumns:
            self._set_status(
                'Index columns cannot also be value columns: '
                f'{", ".join(overlappingColumns)}.',
                error=True,
            )
            return

        try:
            convertedDataFrame, parameterCount, duplicateRowCount = (
                self._build_long_to_wide_dataframe(
                    dataFrame,
                    parameterColumn,
                    indexColumns,
                    valueColumns,
                )
            )
        except Exception as exc:
            self._set_status(f'Error reshaping data with long-to-wide: {exc}', error=True)
            return

        self.meltedDataFrame = convertedDataFrame
        self._show_preview(self.meltedDataFrame)
        self._notify_data_changed()
        statusText = (
            f'Data reshaped with long-to-wide. '
            f'Parameters: {parameterCount}; indexes: {len(indexColumns)}; '
            f'values: {len(valueColumns)}.'
        )
        if duplicateRowCount:
            statusText += f' Duplicate rows merged with first value: {duplicateRowCount}.'
        self._set_status(statusText)

    def _build_long_to_wide_dataframe(
        self,
        dataFrame: pd.DataFrame,
        parameterColumn: str,
        indexColumns: list[str],
        valueColumns: list[str],
    ) -> tuple[pd.DataFrame, int, int]:
        workingDataFrame = dataFrame.copy()
        workingDataFrame[parameterColumn] = workingDataFrame[parameterColumn].map(
            self._format_long_to_wide_parameter_value
        )
        workingDataFrame = workingDataFrame.loc[workingDataFrame[parameterColumn] != ''].copy()
        if workingDataFrame.empty:
            raise ValueError('parameter column has no non-empty values')

        effectiveIndexColumns = list(indexColumns)
        occurrenceColumn = ''
        if not effectiveIndexColumns:
            occurrenceColumn = self._unique_column_name(
                '__longToWideRowId',
                set(workingDataFrame.columns.astype(str)),
            )
            workingDataFrame[occurrenceColumn] = (
                workingDataFrame.groupby(parameterColumn, sort=False).cumcount()
            )
            effectiveIndexColumns = [occurrenceColumn]

        parameterValues = list(dict.fromkeys(workingDataFrame[parameterColumn].tolist()))
        duplicateRowCount = int(
            workingDataFrame.duplicated(
                [*effectiveIndexColumns, parameterColumn],
                keep=False,
            ).sum()
        )
        dedupedDataFrame = workingDataFrame.drop_duplicates(
            subset=[*effectiveIndexColumns, parameterColumn],
            keep='first',
        )
        wideDataFrame = (
            dedupedDataFrame[effectiveIndexColumns]
            .drop_duplicates(keep='first')
            .copy()
        )

        usedColumnNames = set(wideDataFrame.columns.astype(str))
        for parameterValue in parameterValues:
            parameterDataFrame = dedupedDataFrame.loc[
                dedupedDataFrame[parameterColumn] == parameterValue,
                [*effectiveIndexColumns, *valueColumns],
            ].copy()
            renamedColumns = {}
            for valueColumn in valueColumns:
                wideColumnName = self._unique_column_name(
                    f'{parameterValue}_{valueColumn}',
                    usedColumnNames,
                )
                usedColumnNames.add(wideColumnName)
                renamedColumns[valueColumn] = wideColumnName
            parameterDataFrame = parameterDataFrame.rename(columns=renamedColumns)
            wideDataFrame = wideDataFrame.merge(
                parameterDataFrame,
                on=effectiveIndexColumns,
                how='left',
                sort=False,
            )

        if occurrenceColumn:
            wideDataFrame = wideDataFrame.drop(columns=[occurrenceColumn])
        return wideDataFrame.reset_index(drop=True), len(parameterValues), duplicateRowCount

    def _format_long_to_wide_parameter_value(self, value) -> str:
        if pd.isna(value):
            return ''
        return str(value).strip()

    def _show_preview(
        self,
        dataFrame: pd.DataFrame,
        maxRows: int = 1000,
        clearFilters: bool = True,
    ) -> None:
        self.previewMaxRows = maxRows
        self.previewSourceDataFrame = dataFrame
        if clearFilters:
            self.previewProxyModel.clear_filters()
        if dataFrame.empty:
            self.previewTableModel.set_data_frame(pd.DataFrame())
            return

        previewDataFrame = dataFrame.head(maxRows).copy()
        self.previewTableModel.set_data_frame(previewDataFrame)
        self.previewTableWidget.resizeColumnsToContents()
        shownRows = len(previewDataFrame)
        if len(dataFrame) > shownRows:
            self._set_status(f'Preview showing first {shownRows}/{len(dataFrame)} rows.')

    def _show_preview_column_menu(self, columnIndex: int) -> None:
        if self.previewTableModel.columnCount() == 0:
            return

        menu = QMenu(self.previewTableWidget)
        columnName = self.previewTableModel.headerData(
            columnIndex,
            Qt.Horizontal,
            Qt.DisplayRole,
        )
        menu.addAction(f'Filter: {columnName}').setEnabled(False)
        menu.addSeparator()

        renameAction = menu.addAction('Rename column')
        menu.addSeparator()

        sortAscendingAction = menu.addAction('Sort ascending')
        sortDescendingAction = menu.addAction('Sort descending')
        menu.addSeparator()

        clearColumnAction = menu.addAction('Clear this filter')
        clearAllAction = menu.addAction('Clear all filters')
        menu.addSeparator()

        valueListWidget = QListWidget()
        valueListWidget.setMinimumWidth(260)
        valueListWidget.setMaximumHeight(320)
        selectedValues = self.previewProxyModel.columnFilters.get(columnIndex)
        allValues = self._unique_preview_filter_values(columnIndex)
        if selectedValues is None:
            selectedValues = set(allValues)
        for valueText in allValues:
            item = QListWidgetItem(valueText if valueText else '(blank)')
            item.setData(Qt.UserRole, valueText)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if valueText in selectedValues else Qt.Unchecked)
            valueListWidget.addItem(item)

        valueListAction = QWidgetAction(menu)
        valueListAction.setDefaultWidget(valueListWidget)
        menu.addAction(valueListAction)

        buttonWidget = QWidget()
        buttonLayout = QHBoxLayout(buttonWidget)
        buttonLayout.setContentsMargins(6, 6, 6, 6)
        selectAllButton = QPushButton('All')
        selectNoneButton = QPushButton('None')
        applyButton = QPushButton('Apply')
        buttonLayout.addWidget(selectAllButton)
        buttonLayout.addWidget(selectNoneButton)
        buttonLayout.addWidget(applyButton)

        buttonAction = QWidgetAction(menu)
        buttonAction.setDefaultWidget(buttonWidget)
        menu.addAction(buttonAction)

        selectAllButton.clicked.connect(
            lambda: self._set_filter_list_check_state(valueListWidget, Qt.Checked)
        )
        selectNoneButton.clicked.connect(
            lambda: self._set_filter_list_check_state(valueListWidget, Qt.Unchecked)
        )
        applyButton.clicked.connect(
            lambda: self._apply_preview_column_filter(columnIndex, valueListWidget, menu)
        )

        header = self.previewTableWidget.horizontalHeader()
        menuPosition = header.mapToGlobal(
            QPoint(header.sectionViewportPosition(columnIndex), header.height())
        )
        selectedAction = menu.exec(menuPosition)

        if selectedAction == renameAction:
            self._rename_preview_column(columnIndex)
        elif selectedAction == sortAscendingAction:
            self.previewTableWidget.horizontalHeader().setSortIndicator(columnIndex, Qt.AscendingOrder)
            self.previewProxyModel.sort(columnIndex, Qt.AscendingOrder)
        elif selectedAction == sortDescendingAction:
            self.previewTableWidget.horizontalHeader().setSortIndicator(columnIndex, Qt.DescendingOrder)
            self.previewProxyModel.sort(columnIndex, Qt.DescendingOrder)
        elif selectedAction == clearColumnAction:
            self.previewProxyModel.clear_column_filter(columnIndex)
            self._update_preview_filter_status()
            self._notify_data_changed()
        elif selectedAction == clearAllAction:
            self.previewProxyModel.clear_filters()
            self._update_preview_filter_status()
            self._notify_data_changed()

    def _set_filter_list_check_state(self, valueListWidget: QListWidget, checkState: Qt.CheckState) -> None:
        for rowIndex in range(valueListWidget.count()):
            valueListWidget.item(rowIndex).setCheckState(checkState)

    def _apply_preview_column_filter(
        self,
        columnIndex: int,
        valueListWidget: QListWidget,
        menu: QMenu,
    ) -> None:
        selectedValues = set()
        for rowIndex in range(valueListWidget.count()):
            item = valueListWidget.item(rowIndex)
            if item.checkState() == Qt.Checked:
                selectedValues.add(item.data(Qt.UserRole))

        allValues = set(self._unique_preview_filter_values(columnIndex))
        if selectedValues == allValues:
            self.previewProxyModel.clear_column_filter(columnIndex)
        else:
            self.previewProxyModel.set_column_filter(columnIndex, selectedValues)
        self._update_preview_filter_status()
        self._notify_data_changed()
        menu.close()

    def _update_preview_filter_status(self) -> None:
        totalRows = self.previewTableModel.rowCount()
        visibleRows = self.previewProxyModel.rowCount()
        filterCount = len(self.previewProxyModel.columnFilters)
        if filterCount:
            sourceTotalRows = len(self.previewSourceDataFrame)
            sourceVisibleRows = len(self.get_plot_data())
            self._set_status(
                f'Preview filter active: {sourceVisibleRows}/{sourceTotalRows} source rows '
                f'used for plots, {visibleRows}/{totalRows} preview rows shown, '
                f'{filterCount} column filter(s).'
            )
        else:
            self._set_status(f'Preview filter cleared: {totalRows} rows shown.')

    def _rename_preview_column(self, columnIndex: int) -> None:
        if self.previewSourceDataFrame.empty or columnIndex >= len(self.previewSourceDataFrame.columns):
            return

        oldColumnName = str(self.previewSourceDataFrame.columns[columnIndex])
        newColumnName, accepted = QInputDialog.getText(
            self.rootWidget,
            'Rename preview column',
            'Column name:',
            text=oldColumnName,
        )
        if not accepted:
            return

        newColumnName = newColumnName.strip()
        if not newColumnName:
            self._set_status('Column name cannot be empty.', error=True)
            return

        existingColumns = [str(columnName) for columnName in self.previewSourceDataFrame.columns]
        if newColumnName != oldColumnName and newColumnName in existingColumns:
            self._set_status(f'Column "{newColumnName}" already exists.', error=True)
            return

        renamedColumns = list(self.previewSourceDataFrame.columns)
        renamedColumns[columnIndex] = newColumnName
        self.previewSourceDataFrame.columns = renamedColumns

        if self.previewSourceDataFrame is self.loadedDataFrame:
            self._invalidate_melted_data()
            self._populate_columns()

        self._show_preview(self.previewSourceDataFrame, self.previewMaxRows, clearFilters=False)
        self._notify_data_changed()
        self._set_status(f'Renamed column "{oldColumnName}" to "{newColumnName}".')

    def _unique_preview_filter_values(self, columnIndex: int) -> list[str]:
        if (
            self.previewSourceDataFrame.empty
            or columnIndex < 0
            or columnIndex >= len(self.previewSourceDataFrame.columns)
        ):
            return []

        series = self.previewSourceDataFrame.iloc[:, columnIndex]
        return sorted(
            {self.previewTableModel._format_preview_value(value) for value in series},
            key=str.lower,
        )

    def _filter_dataframe_by_preview_filters(self, dataFrame: pd.DataFrame) -> pd.DataFrame:
        if dataFrame.empty or not self.previewProxyModel.columnFilters:
            return dataFrame.copy()

        filteredDataFrame = dataFrame
        for columnIndex, selectedValues in self.previewProxyModel.columnFilters.items():
            if columnIndex < 0 or columnIndex >= len(filteredDataFrame.columns):
                continue

            seriesText = filteredDataFrame.iloc[:, columnIndex].map(
                self.previewTableModel._format_preview_value
            )
            filteredDataFrame = filteredDataFrame.loc[seriesText.isin(selectedValues)]
        return filteredDataFrame.copy()

    def _export_data_frame(self) -> None:
        dataFrame = self.get_plot_data()
        if dataFrame.empty:
            self._set_status('No data to export. Load data first.', error=True)
            return

        attachMode = self.attachComboBox.currentText()
        savePath = self.savePathLineEdit.text().strip()
        if attachMode == 'attach new sheet to workbook' and not savePath:
            savePath = self.loadedFilePath
        if not savePath:
            self._set_status('Choose export path first.', error=True)
            return

        try:
            if attachMode == 'attach new sheet to workbook':
                if not savePath.lower().endswith('.xlsx'):
                    self._set_status('Attach mode works only with .xlsx Excel files.', error=True)
                    return
                sheetName = self.sheetNameLineEdit.text().strip() or 'export'
                with pd.ExcelWriter(savePath, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                    dataFrame.to_excel(writer, sheet_name=sheetName, index=False)
                self._set_status(f'DataFrame attached to {savePath} as sheet {sheetName}.')
            else:
                savePath = self._normalized_export_path(savePath)
                if savePath.lower().endswith('.csv'):
                    dataFrame.to_csv(savePath, index=False)
                else:
                    with pd.ExcelWriter(savePath, engine='openpyxl') as writer:
                        sheetName = self.sheetNameLineEdit.text().strip() or 'export'
                        dataFrame.to_excel(writer, sheet_name=sheetName, index=False)
                self._set_status(f'DataFrame exported to {savePath}.')
        except Exception as exc:
            self._set_status(f'Error exporting DataFrame: {exc}', error=True)

    def _normalized_export_path(self, savePath: str) -> str:
        if savePath.lower().endswith(('.csv', '.xlsx')):
            return savePath
        return f'{savePath}.xlsx'

    def get_melted_data(self) -> pd.DataFrame:
        if self.meltedDataFrame.empty:
            return self.loadedDataFrame.copy()
        return self.meltedDataFrame.copy()

    def get_plot_data(self) -> pd.DataFrame:
        if self.meltedDataFrame.empty:
            return self._filter_dataframe_by_preview_filters(self.loadedDataFrame)
        return self._filter_dataframe_by_preview_filters(self.meltedDataFrame)

    def get_skip_rows(self) -> int:
        return int(self.skipRowsSpinBox.value())

    def _skip_rows_request(self) -> dict[str, int | bool | None]:
        currentSkipRows = self.get_skip_rows()
        canAutoDetect = (
            currentSkipRows == 0
            or self.autoDetectedSkipRows is not None
            and currentSkipRows == self.autoDetectedSkipRows
        )
        return {
            'currentSkipRows': currentSkipRows,
            'canAutoDetect': canAutoDetect,
        }

    def _resolve_skip_rows_for_worker(
        self,
        filePath: str,
        skipRowsRequest: dict[str, int | bool | None],
        sheetName: str | None = None,
    ) -> int:
        currentSkipRows = int(skipRowsRequest['currentSkipRows'] or 0)
        if not bool(skipRowsRequest['canAutoDetect']):
            return currentSkipRows

        return self._detect_skip_rows(
            filePath,
            sheetName=sheetName,
            fallbackSkipRows=currentSkipRows,
        )

    def _apply_resolved_skip_rows(self, skipRows: int) -> None:
        currentSkipRows = self.get_skip_rows()
        canAutoDetect = (
            currentSkipRows == 0
            or self.autoDetectedSkipRows is not None
            and currentSkipRows == self.autoDetectedSkipRows
        )
        if not canAutoDetect:
            return

        if skipRows != currentSkipRows:
            self.settingSkipRowsFromAutoDetect = True
            try:
                self.skipRowsSpinBox.setValue(skipRows)
            finally:
                self.settingSkipRowsFromAutoDetect = False
        self.autoDetectedSkipRows = skipRows

    def _resolve_skip_rows_for_file(self, filePath: str, sheetName: str | None = None) -> int:
        currentSkipRows = self.get_skip_rows()
        canAutoDetect = (
            currentSkipRows == 0
            or self.autoDetectedSkipRows is not None
            and currentSkipRows == self.autoDetectedSkipRows
        )
        if not canAutoDetect:
            return currentSkipRows

        detectedSkipRows = self._detect_skip_rows(filePath, sheetName=sheetName)
        if detectedSkipRows != currentSkipRows:
            self.settingSkipRowsFromAutoDetect = True
            try:
                self.skipRowsSpinBox.setValue(detectedSkipRows)
            finally:
                self.settingSkipRowsFromAutoDetect = False
        self.autoDetectedSkipRows = detectedSkipRows
        return detectedSkipRows

    def _detect_skip_rows(
        self,
        filePath: str,
        sheetName: str | None = None,
        fallbackSkipRows: int | None = None,
    ) -> int:
        try:
            if filePath.lower().endswith('.csv'):
                previewRows = self._read_csv_preview_rows(filePath)
                previewDf = pd.DataFrame(previewRows)
            else:
                previewDf = pd.read_excel(
                    filePath,
                    sheet_name=sheetName,
                    header=None,
                    nrows=30,
                    dtype=object,
                    keep_default_na=False,
                )
        except Exception:
            if fallbackSkipRows is not None:
                return fallbackSkipRows
            return self.get_skip_rows()

        if previewDf.empty:
            return 0

        bestRowIndex = 0
        bestScore = float('-inf')
        maxCandidateRows = min(len(previewDf), 20)
        for rowIndex in range(maxCandidateRows):
            score = self._score_header_row(previewDf, rowIndex)
            if score > bestScore:
                bestScore = score
                bestRowIndex = rowIndex

        return int(bestRowIndex) if bestScore >= 4.0 else 0

    def _read_csv_preview_rows(self, filePath: str, maxRows: int = 30) -> list[list[str]]:
        rows = []
        with open(filePath, newline='', encoding='utf-8-sig') as csvFile:
            reader = csv.reader(csvFile)
            for rowIndex, row in enumerate(reader):
                if rowIndex >= maxRows:
                    break
                rows.append(row)
        return rows

    def _score_header_row(self, previewDf: pd.DataFrame, rowIndex: int) -> float:
        rowTexts = self._row_text_values(previewDf.iloc[rowIndex])
        nonEmptyTexts = [text for text in rowTexts if text]
        nonEmptyCount = len(nonEmptyTexts)
        if nonEmptyCount < 2:
            return -10.0

        uniqueCount = len({text.lower() for text in nonEmptyTexts})
        duplicatePenalty = max(0, nonEmptyCount - uniqueCount) * 1.5
        textLikeCount = sum(1 for text in nonEmptyTexts if self._looks_like_header_text(text))
        numericLikeCount = sum(1 for text in nonEmptyTexts if self._looks_like_number(text))
        nextRowsScore = self._score_rows_after_header(previewDf, rowIndex, nonEmptyCount)
        leadingPenalty = rowIndex * 0.15

        return (
            nonEmptyCount * 0.8
            + textLikeCount * 1.2
            + nextRowsScore
            - numericLikeCount * 0.7
            - duplicatePenalty
            - leadingPenalty
        )

    def _score_rows_after_header(
        self,
        previewDf: pd.DataFrame,
        rowIndex: int,
        headerNonEmptyCount: int,
    ) -> float:
        nextRows = previewDf.iloc[rowIndex + 1:rowIndex + 6]
        if nextRows.empty:
            return -2.0

        score = 0.0
        usedRows = 0
        for _, row in nextRows.iterrows():
            rowTexts = self._row_text_values(row)
            nonEmptyCount = sum(1 for text in rowTexts if text)
            if nonEmptyCount == 0:
                continue
            usedRows += 1
            fillRatio = min(nonEmptyCount / max(headerNonEmptyCount, 1), 1.0)
            numericCount = sum(1 for text in rowTexts if text and self._looks_like_number(text))
            score += fillRatio * 1.3 + min(numericCount, headerNonEmptyCount) * 0.2

        return score if usedRows else -2.0

    def _row_text_values(self, row: pd.Series) -> list[str]:
        return [
            '' if pd.isna(value) else str(value).strip()
            for value in row.tolist()
        ]

    def _looks_like_header_text(self, text: str) -> bool:
        if not text or self._looks_like_number(text):
            return False
        return any(char.isalpha() for char in text) or '_' in text or '-' in text

    def _looks_like_number(self, text: str) -> bool:
        try:
            float(text.replace(',', ''))
            return True
        except ValueError:
            return False

    def preview_filter_annotation_text(self) -> str:
        if not self.previewProxyModel.columnFilters:
            return ''

        sourceTotalRows = len(self.previewSourceDataFrame)
        sourceVisibleRows = len(self.get_plot_data())
        filterLines = [
            f'Preview filter: {sourceVisibleRows}/{sourceTotalRows} rows used'
        ]
        for columnIndex, selectedValues in self.previewProxyModel.columnFilters.items():
            if columnIndex < 0 or columnIndex >= len(self.previewSourceDataFrame.columns):
                continue

            columnName = escape(str(self.previewSourceDataFrame.columns[columnIndex]))
            allValues = self._unique_preview_filter_values(columnIndex)
            selectedValueTexts = sorted(selectedValues, key=str.lower)
            shownValues = ', '.join(
                escape(valueText.strip() if valueText else '(blank)')
                for valueText in selectedValueTexts[:5]
            )
            hiddenCount = max(0, len(selectedValueTexts) - 5)
            if hiddenCount:
                shownValues = f'{shownValues}, ... +{hiddenCount}'
            filterLines.append(
                f'{columnName}: {shownValues} ({len(selectedValues)}/{len(allValues)} selected)'
            )
        return '<br>'.join(filterLines)

    def has_loaded_data(self) -> bool:
        return not self.loadedDataFrame.empty

    def has_reshaped_data(self) -> bool:
        return not self.meltedDataFrame.empty

    def has_reshape_columns(self) -> bool:
        return self.matchingColumnCount > 0

    def add_data_changed_callback(self, callback) -> None:
        self.dataChangedCallbacks.append(callback)

    def _invalidate_melted_data(self, *_args) -> None:
        if self.meltedDataFrame.empty:
            return
        self.meltedDataFrame = pd.DataFrame()
        self._notify_data_changed()

    def add_match_changed_callback(self, callback) -> None:
        self.matchChangedCallbacks.append(callback)
        callback(self.matchingColumnCount, self.totalColumnCount)

    def _notify_data_changed(self) -> None:
        for callback in self.dataChangedCallbacks:
            callback()

    def _notify_match_changed(self) -> None:
        for callback in self.matchChangedCallbacks:
            callback(self.matchingColumnCount, self.totalColumnCount)

    def _set_status(self, message: str, error: bool = False) -> None:
        self.statusLabel.setText(message)
        if error:
            self.statusLabel.setStyleSheet('color: red;')
        else:
            self.statusLabel.setStyleSheet('color: black;')
