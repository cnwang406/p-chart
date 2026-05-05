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
    QMessageBox,
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
        self.previewFilterColumnComboBox = require_child(
            rootWidget,
            QComboBox,
            'previewFilterColumnComboBox',
        )
        self.previewFilterLineEdit = require_child(rootWidget, QLineEdit, 'previewFilterLineEdit')
        self.previewFilterClearButton = require_child(
            rootWidget,
            QPushButton,
            'previewFilterClearButton',
        )
        self.previewTableWidget = require_child(rootWidget, QTableWidget, 'previewTableWidget')
        self.statusLabel = require_child(rootWidget, QLabel, 'statusLabelTab1')

        self.loadedFilePath = ''
        self.loadedDataFrame = pd.DataFrame()
        self.meltedDataFrame = pd.DataFrame()
        self.previewSourceDataFrame = pd.DataFrame()
        self.previewMaxRows = 1000
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
        self.previewFilterLineEdit.textChanged.connect(self._refresh_preview_filter)
        self.previewFilterColumnComboBox.currentIndexChanged.connect(self._refresh_preview_filter)
        self.previewFilterClearButton.clicked.connect(self.previewFilterLineEdit.clear)

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
        self.previewFilterColumnComboBox.addItem('All columns')

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
                normalizedColumns = self._normalize_loaded_datetime_formats(self.loadedDataFrame)
                hasDataWarning = self._show_loaded_data_warnings(self.loadedDataFrame)
                statusText = 'CSV loaded successfully.'
                if normalizedColumns:
                    statusText += f' Converted {len(normalizedColumns)} AM/PM date column(s).'
                if hasDataWarning:
                    statusText += ' Possible date format issue found.'
                self._set_status(statusText, error=hasDataWarning)
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
            normalizedColumns = self._normalize_loaded_datetime_formats(self.loadedDataFrame)
            hasDataWarning = self._show_loaded_data_warnings(self.loadedDataFrame)
            self._append_detected_stubnames()
            self._populate_columns()
            self._show_preview(self.loadedDataFrame)
            self._notify_data_changed()
            statusText = f'Sheet "{sheetName}" loaded successfully.'
            if normalizedColumns:
                statusText += f' Converted {len(normalizedColumns)} AM/PM date column(s).'
            if hasDataWarning:
                statusText += ' Possible date format issue found.'
            self._set_status(statusText, error=hasDataWarning)
        except Exception as exc:
            self._set_status(f'Failed to load sheet: {exc}', error=True)

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
        warnings = [
            *self._detect_excel_zero_date_columns(dataFrame),
            *self._detect_suspicious_date_columns(dataFrame),
        ]
        if not warnings:
            return False

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
        return True

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
        self.previewSourceDataFrame = dataFrame
        self.previewMaxRows = maxRows
        self._populate_preview_filter_columns(dataFrame)
        self._refresh_preview_filter()

    def _populate_preview_filter_columns(self, dataFrame: pd.DataFrame) -> None:
        selectedColumn = self.previewFilterColumnComboBox.currentData()
        self.previewFilterColumnComboBox.blockSignals(True)
        self.previewFilterColumnComboBox.clear()
        self.previewFilterColumnComboBox.addItem('All columns', '')
        for columnName in dataFrame.columns:
            self.previewFilterColumnComboBox.addItem(str(columnName), columnName)
        if selectedColumn not in (None, ''):
            selectedIndex = self.previewFilterColumnComboBox.findData(selectedColumn)
            if selectedIndex >= 0:
                self.previewFilterColumnComboBox.setCurrentIndex(selectedIndex)
        self.previewFilterColumnComboBox.blockSignals(False)

    def _refresh_preview_filter(self, *_args) -> None:
        dataFrame = self.previewSourceDataFrame
        self.previewTableWidget.clear()
        self.previewTableWidget.setRowCount(0)
        self.previewTableWidget.setColumnCount(0)
        if dataFrame.empty:
            return

        filteredDataFrame = self._filtered_preview_data(dataFrame)
        columns = list(dataFrame.columns)
        self.previewTableWidget.setColumnCount(len(columns))
        self.previewTableWidget.setHorizontalHeaderLabels([str(column) for column in columns])
        for rowIndex, (_, row) in enumerate(filteredDataFrame.head(self.previewMaxRows).iterrows()):
            self.previewTableWidget.insertRow(rowIndex)
            for colIndex, columnName in enumerate(columns):
                item = QTableWidgetItem(self._format_preview_value(row[columnName]))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.previewTableWidget.setItem(rowIndex, colIndex, item)

        filterText = self.previewFilterLineEdit.text().strip()
        if filterText:
            shownRows = min(len(filteredDataFrame), self.previewMaxRows)
            self._set_status(
                f'Preview filter: {len(filteredDataFrame)}/{len(dataFrame)} rows matched, '
                f'showing {shownRows}.'
            )

    def _filtered_preview_data(self, dataFrame: pd.DataFrame) -> pd.DataFrame:
        filterText = self.previewFilterLineEdit.text().strip()
        if not filterText:
            return dataFrame

        selectedColumn = self.previewFilterColumnComboBox.currentData()
        if selectedColumn not in (None, ''):
            if selectedColumn not in dataFrame.columns:
                return dataFrame.iloc[0:0]
            series = dataFrame[selectedColumn].dropna()
            matchedIndex = series.astype(str).str.contains(
                filterText,
                case=False,
                regex=False,
                na=False,
            ).index
            return dataFrame.loc[matchedIndex]

        matchedMask = pd.Series(False, index=dataFrame.index)
        for columnName in dataFrame.columns:
            series = dataFrame[columnName].dropna()
            if series.empty:
                continue
            columnMask = series.astype(str).str.contains(
                filterText,
                case=False,
                regex=False,
                na=False,
            )
            matchedMask.loc[columnMask.index] = matchedMask.loc[columnMask.index] | columnMask
        return dataFrame.loc[matchedMask]

    def _format_preview_value(self, value) -> str:
        if pd.isna(value):
            return ''
        return str(value)

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
