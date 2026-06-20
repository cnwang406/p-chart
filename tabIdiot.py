import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QPoint, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QWidget,
)

from qt_helpers import require_child
from table_clipboard_helpers import TableClipboardHelper


IDIOT_SHEET_NAME = 'idiotData'


class TabIdiotWidget:
    def __init__(self, rootWidget: QWidget, tabDataWidget, tabWidget: QTabWidget):
        self.rootWidget = rootWidget
        self.tabDataWidget = tabDataWidget
        self.tabWidget = tabWidget

        self.tabIdiotWidget = require_child(rootWidget, QWidget, 'tabIdiot')
        self.dataTableWidget = require_child(rootWidget, QTableWidget, 'idiotDataTableWidget')
        self.rowsSpinBox = require_child(rootWidget, QSpinBox, 'idiotRowsSpinBox')
        self.colsSpinBox = require_child(rootWidget, QSpinBox, 'idiotColsSpinBox')
        self.addRowsButton = require_child(rootWidget, QPushButton, 'idiotAddRowsPushButton')
        self.addColsButton = require_child(rootWidget, QPushButton, 'idiotAddColsPushButton')
        self.transferButton = require_child(rootWidget, QPushButton, 'idiotTransferPushButton')
        self.insert49Button = require_child(rootWidget, QPushButton, 'idiotInsert49PushButton')
        self.insert81Button = require_child(rootWidget, QPushButton, 'idiotInsert81PushButton')
        self.statusLabel = require_child(rootWidget, QLabel, 'idiotStatusLabelTab')
        self.clipboardHelper = None
        self.isActiveTab = False

        self._configure_widgets()

    def set_active_tab(self, isActive: bool) -> None:
        wasActive = self.isActiveTab
        self.isActiveTab = isActive
        if isActive and not wasActive:
            self._copy_from_tab_data_when_entering()

    def _configure_widgets(self) -> None:
        self.rowsSpinBox.setRange(1, 100000)
        self.colsSpinBox.setRange(1, 1000)
        self.dataTableWidget.setSortingEnabled(False)
        self.dataTableWidget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.dataTableWidget.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.dataTableWidget.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self._configure_context_menus()
        self.clipboardHelper = TableClipboardHelper(
            self.dataTableWidget,
            on_table_shape_changed=self._sync_table_shape_controls,
            on_paste_finished=self._on_clipboard_paste_finished,
            on_copy_finished=self._on_clipboard_copy_finished,
        )

        self._sync_spins_to_table()
        self._ensure_default_headers()

        self.rowsSpinBox.valueChanged.connect(self._set_row_count)
        self.colsSpinBox.valueChanged.connect(self._set_column_count)
        self.addRowsButton.clicked.connect(self._add_row)
        self.addColsButton.clicked.connect(self._add_column)
        self.insert49Button.clicked.connect(lambda: self._insert_coord_file('coord-49.csv'))
        self.insert81Button.clicked.connect(lambda: self._insert_coord_file('coord-81.csv'))
        self.transferButton.clicked.connect(self._transfer_to_tab_data)
        self._set_status('Ready.')

    def _configure_context_menus(self) -> None:
        self.dataTableWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.dataTableWidget.customContextMenuRequested.connect(
            self._show_table_context_menu
        )

        horizontalHeader = self.dataTableWidget.horizontalHeader()
        horizontalHeader.sectionDoubleClicked.connect(self._rename_header)
        horizontalHeader.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        horizontalHeader.customContextMenuRequested.connect(
            self._show_column_header_context_menu
        )

        verticalHeader = self.dataTableWidget.verticalHeader()
        verticalHeader.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        verticalHeader.customContextMenuRequested.connect(
            self._show_row_header_context_menu
        )

    def _sync_spins_to_table(self) -> None:
        rowsBlocker = QSignalBlocker(self.rowsSpinBox)
        colsBlocker = QSignalBlocker(self.colsSpinBox)
        self.rowsSpinBox.setValue(max(1, self.dataTableWidget.rowCount()))
        self.colsSpinBox.setValue(max(1, self.dataTableWidget.columnCount()))
        del rowsBlocker
        del colsBlocker

    def _sync_table_shape_controls(self) -> None:
        self._ensure_default_headers()
        self._sync_spins_to_table()

    def _on_clipboard_paste_finished(self, rowCount: int, columnCount: int) -> None:
        self._set_status(
            f'Pasted {rowCount} rows x {columnCount} cols. '
            f'Table: {self.dataTableWidget.rowCount()} rows x '
            f'{self.dataTableWidget.columnCount()} cols.'
        )

    def _on_clipboard_copy_finished(self, rowCount: int, columnCount: int) -> None:
        self._set_status(f'Copied {rowCount} rows x {columnCount} cols.')

    def _set_row_count(self, rowCount: int) -> None:
        self.dataTableWidget.setRowCount(max(1, rowCount))
        self._set_status(f'Rows: {self.dataTableWidget.rowCount()}, cols: {self.dataTableWidget.columnCount()}.')

    def _set_column_count(self, columnCount: int) -> None:
        self.dataTableWidget.setColumnCount(max(1, columnCount))
        self._ensure_default_headers()
        self._set_status(f'Rows: {self.dataTableWidget.rowCount()}, cols: {self.dataTableWidget.columnCount()}.')

    def _add_row(self) -> None:
        self.rowsSpinBox.setValue(self.rowsSpinBox.value() + 1)

    def _add_column(self) -> None:
        self.colsSpinBox.setValue(self.colsSpinBox.value() + 1)

    def _rename_header(self, columnIndex: int) -> None:
        currentName = self._header_text(columnIndex) or f'Column{columnIndex + 1}'
        newName, accepted = QInputDialog.getText(
            self.rootWidget,
            'Rename column',
            'Column name:',
            text=currentName,
        )
        if not accepted:
            return
        self.dataTableWidget.setHorizontalHeaderItem(
            columnIndex,
            QTableWidgetItem(newName.strip()),
        )

    def _show_table_context_menu(self, position: QPoint) -> None:
        modelIndex = self.dataTableWidget.indexAt(position)
        rowIndex = modelIndex.row() if modelIndex.isValid() else self.dataTableWidget.currentRow()
        columnIndex = modelIndex.column() if modelIndex.isValid() else self.dataTableWidget.currentColumn()
        if modelIndex.isValid() and not self._cell_is_selected(rowIndex, columnIndex):
            self.dataTableWidget.setCurrentCell(rowIndex, columnIndex)

        menu = QMenu(self.dataTableWidget)
        markRowAction = menu.addAction(f'Mark row {rowIndex + 1}')
        markColumnAction = menu.addAction(f'Mark column {columnIndex + 1}')
        renameColumnAction = menu.addAction('Rename column header')
        menu.addSeparator()
        deleteRowsAction = menu.addAction(self._delete_rows_action_text(rowIndex))
        deleteColumnsAction = menu.addAction(self._delete_columns_action_text(columnIndex))

        hasRow = rowIndex >= 0
        hasColumn = columnIndex >= 0
        markRowAction.setEnabled(hasRow)
        deleteRowsAction.setEnabled(hasRow or bool(self._marked_row_indexes()))
        markColumnAction.setEnabled(hasColumn)
        renameColumnAction.setEnabled(hasColumn)
        deleteColumnsAction.setEnabled(hasColumn or bool(self._marked_column_indexes()))

        selectedAction = menu.exec(self.dataTableWidget.viewport().mapToGlobal(position))
        if selectedAction == markRowAction and hasRow:
            self._mark_row(rowIndex)
        elif selectedAction == markColumnAction and hasColumn:
            self._mark_column(columnIndex)
        elif selectedAction == renameColumnAction and hasColumn:
            self._rename_header(columnIndex)
        elif selectedAction == deleteRowsAction:
            self._delete_rows(self._marked_row_indexes(rowIndex if hasRow else None))
        elif selectedAction == deleteColumnsAction:
            self._delete_columns(self._marked_column_indexes(columnIndex if hasColumn else None))

    def _show_column_header_context_menu(self, position: QPoint) -> None:
        columnIndex = self.dataTableWidget.horizontalHeader().logicalIndexAt(position)
        if columnIndex < 0:
            return

        menu = QMenu(self.dataTableWidget)
        markColumnAction = menu.addAction(f'Mark column {columnIndex + 1}')
        renameColumnAction = menu.addAction('Rename column header')
        deleteColumnsAction = menu.addAction(self._delete_columns_action_text(columnIndex))

        selectedAction = menu.exec(
            self.dataTableWidget.horizontalHeader().mapToGlobal(position)
        )
        if selectedAction == markColumnAction:
            self._mark_column(columnIndex)
        elif selectedAction == renameColumnAction:
            self._rename_header(columnIndex)
        elif selectedAction == deleteColumnsAction:
            self._delete_columns(self._marked_column_indexes(columnIndex))

    def _show_row_header_context_menu(self, position: QPoint) -> None:
        rowIndex = self.dataTableWidget.verticalHeader().logicalIndexAt(position)
        if rowIndex < 0:
            return

        menu = QMenu(self.dataTableWidget)
        markRowAction = menu.addAction(f'Mark row {rowIndex + 1}')
        deleteRowsAction = menu.addAction(self._delete_rows_action_text(rowIndex))

        selectedAction = menu.exec(
            self.dataTableWidget.verticalHeader().mapToGlobal(position)
        )
        if selectedAction == markRowAction:
            self._mark_row(rowIndex)
        elif selectedAction == deleteRowsAction:
            self._delete_rows(self._marked_row_indexes(rowIndex))

    def _cell_is_selected(self, rowIndex: int, columnIndex: int) -> bool:
        return any(
            selectedIndex.row() == rowIndex and selectedIndex.column() == columnIndex
            for selectedIndex in self.dataTableWidget.selectedIndexes()
        )

    def _mark_row(self, rowIndex: int) -> None:
        if rowIndex < 0:
            return
        self.dataTableWidget.clearSelection()
        self.dataTableWidget.selectRow(rowIndex)
        self.dataTableWidget.setCurrentCell(rowIndex, max(0, self.dataTableWidget.currentColumn()))
        self._set_status(f'Marked row {rowIndex + 1}.')

    def _mark_column(self, columnIndex: int) -> None:
        if columnIndex < 0:
            return
        self.dataTableWidget.clearSelection()
        self.dataTableWidget.selectColumn(columnIndex)
        self.dataTableWidget.setCurrentCell(max(0, self.dataTableWidget.currentRow()), columnIndex)
        self._set_status(f'Marked column {columnIndex + 1}.')

    def _delete_rows_action_text(self, fallbackRow: int | None = None) -> str:
        rowIndexes = self._marked_row_indexes(fallbackRow)
        if len(rowIndexes) <= 1:
            rowText = f'row {rowIndexes[0] + 1}' if rowIndexes else 'row'
            return f'Delete {rowText}'
        return f'Delete {len(rowIndexes)} marked rows'

    def _delete_columns_action_text(self, fallbackColumn: int | None = None) -> str:
        columnIndexes = self._marked_column_indexes(fallbackColumn)
        if len(columnIndexes) <= 1:
            columnText = f'column {columnIndexes[0] + 1}' if columnIndexes else 'column'
            return f'Delete {columnText}'
        return f'Delete {len(columnIndexes)} marked columns'

    def _marked_row_indexes(self, fallbackRow: int | None = None) -> list[int]:
        rowIndexes = self._fully_selected_row_indexes()
        if not rowIndexes and fallbackRow is not None and fallbackRow >= 0:
            rowIndexes = [fallbackRow]
        return sorted({
            rowIndex
            for rowIndex in rowIndexes
            if 0 <= rowIndex < self.dataTableWidget.rowCount()
        })

    def _marked_column_indexes(self, fallbackColumn: int | None = None) -> list[int]:
        columnIndexes = self._fully_selected_column_indexes()
        if not columnIndexes and fallbackColumn is not None and fallbackColumn >= 0:
            columnIndexes = [fallbackColumn]
        return sorted({
            columnIndex
            for columnIndex in columnIndexes
            if 0 <= columnIndex < self.dataTableWidget.columnCount()
        })

    def _fully_selected_row_indexes(self) -> list[int]:
        if self.dataTableWidget.columnCount() <= 0:
            return []
        rowIndexes = set()
        lastColumn = self.dataTableWidget.columnCount() - 1
        for selectedRange in self.dataTableWidget.selectedRanges():
            if selectedRange.leftColumn() != 0 or selectedRange.rightColumn() != lastColumn:
                continue
            rowIndexes.update(range(selectedRange.topRow(), selectedRange.bottomRow() + 1))
        return sorted(rowIndexes)

    def _fully_selected_column_indexes(self) -> list[int]:
        if self.dataTableWidget.rowCount() <= 0:
            return []
        columnIndexes = set()
        lastRow = self.dataTableWidget.rowCount() - 1
        for selectedRange in self.dataTableWidget.selectedRanges():
            if selectedRange.topRow() != 0 or selectedRange.bottomRow() != lastRow:
                continue
            columnIndexes.update(range(selectedRange.leftColumn(), selectedRange.rightColumn() + 1))
        return sorted(columnIndexes)

    def _delete_rows(self, rowIndexes: list[int]) -> None:
        if not rowIndexes:
            return

        deletedCount = len(rowIndexes)
        if deletedCount >= self.dataTableWidget.rowCount():
            self.dataTableWidget.setRowCount(0)
            self.dataTableWidget.setRowCount(1)
        else:
            for rowIndex in sorted(rowIndexes, reverse=True):
                self.dataTableWidget.removeRow(rowIndex)

        self._sync_table_after_shape_change()
        self._set_status(
            f'Deleted {deletedCount} row(s). '
            f'Table: {self.dataTableWidget.rowCount()} rows x '
            f'{self.dataTableWidget.columnCount()} cols.'
        )

    def _delete_columns(self, columnIndexes: list[int]) -> None:
        if not columnIndexes:
            return

        deletedCount = len(columnIndexes)
        if deletedCount >= self.dataTableWidget.columnCount():
            self.dataTableWidget.setColumnCount(0)
            self.dataTableWidget.setColumnCount(1)
        else:
            for columnIndex in sorted(columnIndexes, reverse=True):
                self.dataTableWidget.removeColumn(columnIndex)

        self._sync_table_after_shape_change()
        self._set_status(
            f'Deleted {deletedCount} column(s). '
            f'Table: {self.dataTableWidget.rowCount()} rows x '
            f'{self.dataTableWidget.columnCount()} cols.'
        )

    def _sync_table_after_shape_change(self) -> None:
        self.dataTableWidget.clearSelection()
        self._ensure_default_headers()
        self._sync_spins_to_table()
        if self.dataTableWidget.rowCount() > 0 and self.dataTableWidget.columnCount() > 0:
            self.dataTableWidget.setCurrentCell(0, 0)

    def _insert_coord_file(self, filename: str) -> None:
        filePath = self._resource_path(filename)
        try:
            dataFrame = pd.read_csv(filePath)
        except Exception as exc:
            self._set_status(f'Failed to load {filename}: {exc}', error=True)
            return

        if [str(columnName).strip() for columnName in dataFrame.columns] != ['x', 'y']:
            self._set_status(f'{filename} must have x,y columns.', error=True)
            return

        appendedColumns = self._append_data_frame_columns(dataFrame)
        self._set_status(f'{filename} appended: {appendedColumns} columns, {len(dataFrame)} rows.')

    def _append_data_frame_columns(self, dataFrame: pd.DataFrame) -> int:
        if dataFrame.empty:
            return 0

        startColumn = self.dataTableWidget.columnCount()
        requiredRows = max(self.dataTableWidget.rowCount(), len(dataFrame), 1)
        requiredColumns = startColumn + len(dataFrame.columns)

        self.dataTableWidget.setRowCount(requiredRows)
        self.dataTableWidget.setColumnCount(requiredColumns)
        for dataColumnIndex, columnName in enumerate(dataFrame.columns):
            self.dataTableWidget.setHorizontalHeaderItem(
                startColumn + dataColumnIndex,
                QTableWidgetItem(str(columnName)),
            )
        self._ensure_default_headers()

        for dataRowIndex in range(len(dataFrame)):
            for dataColumnIndex in range(len(dataFrame.columns)):
                tableColumnIndex = startColumn + dataColumnIndex
                cellValue = dataFrame.iat[dataRowIndex, dataColumnIndex]
                value = '' if pd.isna(cellValue) else str(cellValue)
                self.dataTableWidget.setItem(
                    dataRowIndex,
                    tableColumnIndex,
                    QTableWidgetItem(value),
                )

        self._sync_spins_to_table()
        self.dataTableWidget.resizeColumnsToContents()
        return len(dataFrame.columns)

    def _copy_from_tab_data_when_entering(self) -> None:
        if self.tabDataWidget is None or not self.tabDataWidget.has_loaded_data():
            return

        dataFrame = self.tabDataWidget.get_plot_data()
        if dataFrame.empty:
            return

        if self._has_non_empty_data() and not self._confirm_tab_data_overwrite():
            self._set_status('Kept existing IDIOT table data.')
            return

        self._set_table_from_data_frame(dataFrame)
        self._set_status(f'Copied from TabData: {len(dataFrame)} rows x {len(dataFrame.columns)} cols.')

    def _confirm_tab_data_overwrite(self) -> bool:
        result = QMessageBox.question(
            self.rootWidget,
            'Overwrite IDIOT table?',
            'IDIOT table already has data. Overwrite it with current TabData?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    def _set_table_from_data_frame(self, dataFrame: pd.DataFrame) -> None:
        rowCount = max(1, len(dataFrame))
        columnCount = max(1, len(dataFrame.columns))
        self.dataTableWidget.setRowCount(rowCount)
        self.dataTableWidget.setColumnCount(columnCount)
        self.dataTableWidget.setHorizontalHeaderLabels([
            str(columnName) for columnName in dataFrame.columns
        ])

        for rowIndex in range(rowCount):
            for columnIndex in range(columnCount):
                value = ''
                if rowIndex < len(dataFrame) and columnIndex < len(dataFrame.columns):
                    cellValue = dataFrame.iat[rowIndex, columnIndex]
                    value = '' if pd.isna(cellValue) else str(cellValue)
                self.dataTableWidget.setItem(
                    rowIndex,
                    columnIndex,
                    QTableWidgetItem(value),
                )

        self._sync_spins_to_table()
        self.dataTableWidget.resizeColumnsToContents()

    def _transfer_to_tab_data(self) -> None:
        dataFrame = self._table_to_data_frame()
        if dataFrame.empty:
            self._set_status('Nothing to transfer.', error=True)
            return

        outputPath = self._transfer_output_path()
        try:
            outputPath.parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(outputPath, engine='openpyxl') as writer:
                dataFrame.to_excel(writer, sheet_name=IDIOT_SHEET_NAME, index=False)
        except Exception as exc:
            self._set_status(f'Failed to save transfer file: {exc}', error=True)
            return

        loaded = self.tabDataWidget.load_workbook_sheet(
            str(outputPath),
            sheetName=IDIOT_SHEET_NAME,
        )
        if not loaded:
            self._set_status(f'Saved {outputPath}, but TabData did not load it.', error=True)
            return

        self._switch_to_tab_data()
        self._set_status(f'Transferred to TabData: {outputPath}')

    def _table_to_data_frame(self) -> pd.DataFrame:
        lastRow = self._last_non_empty_row()
        lastColumn = self._last_non_empty_column()
        if lastRow < 0 or lastColumn < 0:
            return pd.DataFrame()

        rows = []
        for rowIndex in range(lastRow + 1):
            rows.append([
                self._cell_text(rowIndex, columnIndex)
                for columnIndex in range(lastColumn + 1)
            ])
        return pd.DataFrame(rows, columns=self._export_headers(lastColumn + 1))

    def _last_non_empty_row(self) -> int:
        for rowIndex in range(self.dataTableWidget.rowCount() - 1, -1, -1):
            for columnIndex in range(self.dataTableWidget.columnCount()):
                if self._cell_text(rowIndex, columnIndex):
                    return rowIndex
        return -1

    def _last_non_empty_column(self) -> int:
        for columnIndex in range(self.dataTableWidget.columnCount() - 1, -1, -1):
            for rowIndex in range(self.dataTableWidget.rowCount()):
                if self._cell_text(rowIndex, columnIndex):
                    return columnIndex
        return -1

    def _export_headers(self, columnCount: int) -> list[str]:
        usedCounts = {}
        headers = []
        for columnIndex in range(columnCount):
            baseName = self._header_text(columnIndex).strip() or f'Column{columnIndex + 1}'
            count = usedCounts.get(baseName, 0) + 1
            usedCounts[baseName] = count
            headers.append(baseName if count == 1 else f'{baseName}_{count}')
        return headers

    def _ensure_default_headers(self) -> None:
        for columnIndex in range(self.dataTableWidget.columnCount()):
            if self.dataTableWidget.horizontalHeaderItem(columnIndex) is not None:
                continue
            self.dataTableWidget.setHorizontalHeaderItem(
                columnIndex,
                QTableWidgetItem(f'Column{columnIndex + 1}'),
            )

    def _has_non_empty_data(self) -> bool:
        return self._last_non_empty_row() >= 0

    def _cell_text(self, rowIndex: int, columnIndex: int) -> str:
        item = self.dataTableWidget.item(rowIndex, columnIndex)
        return '' if item is None else item.text().strip()

    def _header_text(self, columnIndex: int) -> str:
        item = self.dataTableWidget.horizontalHeaderItem(columnIndex)
        return '' if item is None else item.text().strip()

    def _switch_to_tab_data(self) -> None:
        for tabIndex in range(self.tabWidget.count()):
            tabWidget = self.tabWidget.widget(tabIndex)
            if tabWidget is not None and tabWidget.objectName() == 'tabData':
                self.tabWidget.setCurrentIndex(tabIndex)
                return

    def _transfer_output_path(self) -> Path:
        filename = f'{datetime.now():%m%d%H%M}.xlsx'
        if os.name == 'nt':
            return Path(r'C:\temp') / filename
        return Path('/tmp') / filename

    def _resource_path(self, filename: str) -> Path:
        basePath = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        return basePath / filename

    def _set_status(self, message: str, error: bool = False) -> None:
        self.statusLabel.setText(message)
        self.statusLabel.setStyleSheet('color: red;' if error else 'color: black;')
