import os
import re

import pandas as pd
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QBrush, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QGroupBox,
)

from qt_helpers import require_child


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


class TabDataWidget:
    def __init__(self, rootWidget: QWidget):
        self.rootWidget = rootWidget
        self.tabDataWidget = require_child(rootWidget, QWidget, 'tabData')
        self.filePathLineEdit = require_child(rootWidget, QLineEdit, 'filePathLineEdit')
        self.browseFileButton = require_child(rootWidget, QPushButton, 'browseFileButton')
        self.loadButton = require_child(rootWidget, QPushButton, 'loadButton')
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
        self.saveButton = require_child(rootWidget, QPushButton, 'saveButton')
        self.infoButton = require_child(rootWidget, QPushButton, 'infoButton')
        self.convertColumnButton = require_child(rootWidget, QPushButton, 'convertColumnButton')
        self.previewTableWidget = require_child(rootWidget, QTableWidget, 'previewTableWidget')
        self.statusLabel = require_child(rootWidget, QLabel, 'statusLabelTab1')

        self.loadedFilePath = ''
        self.loadedDataFrame = pd.DataFrame()
        self.meltedDataFrame = pd.DataFrame()
        self.dataChangedCallbacks = []
        self.matchChangedCallbacks = []
        self.matchingColumnCount = 0
        self.totalColumnCount = 0
        self._matchedColumnColor = QBrush(QColor(208, 245, 216))
        self.dropFileFilter = DropFileFilter(self._load_dropped_file)

        self._configure_widgets()

    def _configure_widgets(self) -> None:
        self.browseFileButton.clicked.connect(self._browse_file)
        self.loadButton.clicked.connect(self._load_selected_sheet)
        self.browseSaveButton.clicked.connect(self._browse_save_path)
        self.meltButton.clicked.connect(self._melt_dataframe)
        self.saveButton.clicked.connect(self._save_melted_data)
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
        self.previewTableWidget.setStyleSheet('QTableWidget { color: rgba(0, 0, 0, 204); }')

        self.filePathLineEdit.textChanged.connect(self._invalidate_melted_data)
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
            self._load_file()

    def _browse_save_path(self) -> None:
        defaultPath = self.loadedFilePath if self.loadedFilePath else os.getcwd()
        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Save reshaped worksheet',
            defaultPath,
            'Excel Files (*.xlsx);;CSV Files (*.csv);;All Files (*)',
        )
        if selectedFile:
            self.savePathLineEdit.setText(selectedFile)

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
        infoFont.setPointSize(16)
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
        try:
            if filePath.lower().endswith('.csv'):
                self.loadedDataFrame = pd.read_csv(filePath)
                self._set_status('CSV loaded successfully.')
                self._append_detected_stubnames()
                self._populate_columns()
                self._show_preview(self.loadedDataFrame)
                self._notify_data_changed()
                self.sheetComboBox.clear()
                self.sheetComboBox.addItem('csv')
                self.sheetComboBox.setEnabled(False)
                self.loadButton.setEnabled(False)
            else:
                excelReader = pd.ExcelFile(filePath)
                self.sheetComboBox.clear()
                self.sheetComboBox.addItems(excelReader.sheet_names)
                self.sheetComboBox.setEnabled(True)
                self.loadButton.setEnabled(True)
                self._set_status('Excel workbook loaded. Choose a sheet and press Load.')
        except Exception as exc:
            self._set_status(f'Error loading file: {exc}', error=True)

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
        try:
            self._invalidate_melted_data()
            self.loadedDataFrame = pd.read_excel(self.loadedFilePath, sheet_name=sheetName)
            self._append_detected_stubnames()
            self._populate_columns()
            self._show_preview(self.loadedDataFrame)
            self._notify_data_changed()
            self._set_status(f'Sheet "{sheetName}" loaded successfully.')
        except Exception as exc:
            self._set_status(f'Failed to load sheet: {exc}', error=True)

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

    def _show_preview(self, dataFrame: pd.DataFrame, maxRows: int = 1000) -> None:
        self.previewTableWidget.clear()
        self.previewTableWidget.setRowCount(0)
        self.previewTableWidget.setColumnCount(0)
        if dataFrame.empty:
            return

        columns = list(dataFrame.columns)
        self.previewTableWidget.setColumnCount(len(columns))
        self.previewTableWidget.setHorizontalHeaderLabels(columns)
        for rowIndex, (_, row) in enumerate(dataFrame.head(maxRows).iterrows()):
            self.previewTableWidget.insertRow(rowIndex)
            for colIndex, columnName in enumerate(columns):
                item = QTableWidgetItem(str(row[columnName]))
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                self.previewTableWidget.setItem(rowIndex, colIndex, item)

    def _save_melted_data(self) -> None:
        if self.meltedDataFrame.empty:
            self._set_status('No reshaped data to save. Run wide_to_long first.', error=True)
            return

        attachMode = self.attachComboBox.currentText()
        savePath = self.savePathLineEdit.text().strip()
        if attachMode == 'attach new sheet to workbook' and not savePath:
            savePath = self.loadedFilePath
        if not savePath:
            self._set_status('Choose save path first.', error=True)
            return

        try:
            if attachMode == 'attach new sheet to workbook':
                if not savePath.lower().endswith(('.xlsx', '.xls')):
                    self._set_status('Attach mode works only with Excel files.', error=True)
                    return
                sheetName = self.sheetNameLineEdit.text().strip() or 'wide_to_long'
                with pd.ExcelWriter(savePath, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                    self.meltedDataFrame.to_excel(writer, sheet_name=sheetName, index=False)
                self._set_status(f'Reshaped worksheet attached to {savePath} as sheet {sheetName}.')
            else:
                if savePath.lower().endswith('.csv'):
                    self.meltedDataFrame.to_csv(savePath, index=False)
                else:
                    with pd.ExcelWriter(savePath, engine='openpyxl') as writer:
                        sheetName = self.sheetNameLineEdit.text().strip() or 'wide_to_long'
                        self.meltedDataFrame.to_excel(writer, sheet_name=sheetName, index=False)
                self._set_status(f'Reshaped worksheet saved to {savePath}.')
        except Exception as exc:
            self._set_status(f'Error saving reshaped data: {exc}', error=True)

    def get_melted_data(self) -> pd.DataFrame:
        if self.meltedDataFrame.empty:
            return self.loadedDataFrame.copy()
        return self.meltedDataFrame.copy()

    def get_plot_data(self) -> pd.DataFrame:
        return self.get_melted_data()

    def has_loaded_data(self) -> bool:
        return not self.loadedDataFrame.empty

    def has_reshaped_data(self) -> bool:
        return not self.meltedDataFrame.empty

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
