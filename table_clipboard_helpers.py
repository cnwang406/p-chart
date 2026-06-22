from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidget, QTableWidgetItem


class TableClipboardHelper(QObject):
    def __init__(
        self,
        tableWidget: QTableWidget,
        on_table_shape_changed: Callable[[], None] | None = None,
        on_paste_finished: Callable[[int, int], None] | None = None,
        on_copy_finished: Callable[[int, int], None] | None = None,
    ) -> None:
        super().__init__(tableWidget)
        self.tableWidget = tableWidget
        self.onTableShapeChanged = on_table_shape_changed
        self.onPasteFinished = on_paste_finished
        self.onCopyFinished = on_copy_finished
        self.shortcuts = []
        self.tableWidget.installEventFilter(self)
        self.tableWidget.viewport().installEventFilter(self)
        self._configure_shortcuts()

    def _configure_shortcuts(self) -> None:
        self._add_shortcut(QKeySequence(QKeySequence.StandardKey.Copy), self.copy_selection)
        self._add_shortcut(QKeySequence(QKeySequence.StandardKey.Paste), self.paste_clipboard)
        self._add_shortcut(QKeySequence('Return'), self.move_to_next_row)
        self._add_shortcut(QKeySequence('Enter'), self.move_to_next_row)

    def _add_shortcut(self, keySequence: QKeySequence, slot) -> None:
        shortcut = QShortcut(keySequence, self.tableWidget)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(slot)
        self.shortcuts.append(shortcut)

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)

        if not self._event_targets_table(watched):
            return super().eventFilter(watched, event)

        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            return True

        if event.matches(QKeySequence.Paste):
            self.paste_clipboard()
            return True

        if self._is_enter_key(event):
            self.move_to_next_row()
            return True

        return super().eventFilter(watched, event)

    def move_to_next_row(self) -> None:
        currentRow = self.tableWidget.currentRow()
        currentColumn = self.tableWidget.currentColumn()
        if currentRow < 0:
            currentRow = 0
        if currentColumn < 0:
            currentColumn = 0

        nextRow = currentRow + 1
        if nextRow >= self.tableWidget.rowCount():
            self.tableWidget.setRowCount(nextRow + 1)
            if self.onTableShapeChanged is not None:
                self.onTableShapeChanged()

        if self.tableWidget.item(nextRow, currentColumn) is None:
            self.tableWidget.setItem(nextRow, currentColumn, QTableWidgetItem(''))
        self.tableWidget.setCurrentCell(nextRow, currentColumn)

    def copy_selection(self) -> None:
        copiedText, rowCount, columnCount = self._selected_text()
        if not copiedText and rowCount == 0 and columnCount == 0:
            return
        QApplication.clipboard().setText(copiedText)
        if self.onCopyFinished is not None:
            self.onCopyFinished(rowCount, columnCount)

    def paste_clipboard(self) -> None:
        rows = self._parse_clipboard_text(QApplication.clipboard().text())
        if not rows:
            return

        startRow, startColumn = self._paste_start_cell()
        columnCount = max(len(row) for row in rows)
        rows = self._rectangular_rows(rows, columnCount)
        headerRow = rows[0] if self._looks_like_header_paste(rows) else None
        dataRows = rows[1:] if headerRow is not None else rows
        rowCount = len(dataRows)
        requiredRows = startRow + rowCount
        requiredColumns = startColumn + columnCount
        nonNumericCells = self._non_numeric_data_cells(dataRows) if headerRow is not None else []
        nonNumericAction = 'leave'
        if nonNumericCells:
            nonNumericAction = self._ask_non_numeric_paste_action(len(nonNumericCells))
            if nonNumericAction == 'cancel':
                self._clear_paste_area(startRow, startColumn, rowCount, columnCount)
                return
            dataRows = self._apply_non_numeric_action(dataRows, nonNumericAction)

        if requiredRows > self.tableWidget.rowCount():
            self.tableWidget.setRowCount(requiredRows)
        if requiredColumns > self.tableWidget.columnCount():
            self.tableWidget.setColumnCount(requiredColumns)
        if self.onTableShapeChanged is not None:
            self.onTableShapeChanged()

        if headerRow is not None:
            for columnOffset, headerText in enumerate(headerRow):
                self.tableWidget.setHorizontalHeaderItem(
                    startColumn + columnOffset,
                    QTableWidgetItem(headerText.strip()),
                )

        for rowOffset, rowValues in enumerate(dataRows):
            tableRow = startRow + rowOffset
            for columnOffset in range(columnCount):
                tableColumn = startColumn + columnOffset
                value = rowValues[columnOffset]
                self.tableWidget.setItem(
                    tableRow,
                    tableColumn,
                    QTableWidgetItem(value),
                )

        self.tableWidget.setCurrentCell(startRow, startColumn)
        self.tableWidget.resizeColumnsToContents()
        if self.onPasteFinished is not None:
            self.onPasteFinished(max(0, rowCount), columnCount)

    def _paste_start_cell(self) -> tuple[int, int]:
        currentRow = self.tableWidget.currentRow()
        currentColumn = self.tableWidget.currentColumn()
        if currentRow >= 0 and currentColumn >= 0:
            return currentRow, currentColumn

        selectedRanges = self.tableWidget.selectedRanges()
        if selectedRanges:
            selectedRange = selectedRanges[0]
            return selectedRange.topRow(), selectedRange.leftColumn()

        return 0, 0

    def _selected_text(self) -> tuple[str, int, int]:
        selectedRanges = self.tableWidget.selectedRanges()
        if selectedRanges:
            selectedRange = selectedRanges[0]
            topRow = selectedRange.topRow()
            bottomRow = selectedRange.bottomRow()
            leftColumn = selectedRange.leftColumn()
            rightColumn = selectedRange.rightColumn()
        else:
            currentRow = self.tableWidget.currentRow()
            currentColumn = self.tableWidget.currentColumn()
            if currentRow < 0 or currentColumn < 0:
                return '', 0, 0
            topRow = bottomRow = currentRow
            leftColumn = rightColumn = currentColumn

        copiedRows = []
        for rowIndex in range(topRow, bottomRow + 1):
            copiedRows.append('\t'.join(
                self._cell_text(rowIndex, columnIndex)
                for columnIndex in range(leftColumn, rightColumn + 1)
            ))
        return (
            '\n'.join(copiedRows),
            bottomRow - topRow + 1,
            rightColumn - leftColumn + 1,
        )

    def _cell_text(self, rowIndex: int, columnIndex: int) -> str:
        item = self.tableWidget.item(rowIndex, columnIndex)
        return '' if item is None else item.text()

    def _is_enter_key(self, event) -> bool:
        return event.key() in (Qt.Key_Return, Qt.Key_Enter)

    def _event_targets_table(self, watched) -> bool:
        if watched in (self.tableWidget, self.tableWidget.viewport()):
            return True

        focusWidget = QApplication.focusWidget()
        while focusWidget is not None:
            if focusWidget in (self.tableWidget, self.tableWidget.viewport()):
                return True
            focusWidget = focusWidget.parentWidget()
        return False

    def _parse_clipboard_text(self, text: str) -> list[list[str]]:
        if not text:
            return []
        normalizedText = text.replace('\r\n', '\n').replace('\r', '\n')
        if normalizedText.endswith('\n'):
            normalizedText = normalizedText[:-1]
        if not normalizedText:
            return []
        return [row.split('\t') for row in normalizedText.split('\n')]

    def _rectangular_rows(self, rows: list[list[str]], columnCount: int) -> list[list[str]]:
        return [
            row + [''] * (columnCount - len(row))
            for row in rows
        ]

    def _looks_like_header_paste(self, rows: list[list[str]]) -> bool:
        if len(rows) < 2:
            return False

        headerValues = [value.strip() for value in rows[0] if value.strip()]
        if not headerValues:
            return False

        return all(not self._is_numeric_text(value) for value in headerValues)

    def _non_numeric_data_cells(self, rows: list[list[str]]) -> list[tuple[int, int]]:
        nonNumericCells = []
        for rowIndex, row in enumerate(rows):
            for columnIndex, value in enumerate(row):
                if not self._is_numeric_text(value):
                    nonNumericCells.append((rowIndex, columnIndex))
        return nonNumericCells

    def _is_numeric_text(self, value: str) -> bool:
        text = str(value).strip()
        if not text:
            return True
        try:
            float(text.replace(',', ''))
            return True
        except ValueError:
            return False

    def _ask_non_numeric_paste_action(self, nonNumericCount: int) -> str:
        messageBox = QMessageBox(self.tableWidget)
        messageBox.setIcon(QMessageBox.Icon.Warning)
        messageBox.setWindowTitle('Paste data')
        messageBox.setText(
            '應該只能是數字(第一列除外, 會作為 column name)，對於非數字要怎麼做?'
        )
        messageBox.setInformativeText(f'Found {nonNumericCount} non-numeric cell(s).')
        leaveButton = messageBox.addButton(
            'Leave as is. (This might crash)',
            QMessageBox.ButtonRole.AcceptRole,
        )
        blankButton = messageBox.addButton(
            'Change to space (This might crash)',
            QMessageBox.ButtonRole.ActionRole,
        )
        naButton = messageBox.addButton(
            "Change to 'N/A' (This might crash)",
            QMessageBox.ButtonRole.ActionRole,
        )
        cancelButton = messageBox.addButton(
            "I'll be back",
            QMessageBox.ButtonRole.RejectRole,
        )
        messageBox.setDefaultButton(cancelButton)
        messageBox.exec()

        clickedButton = messageBox.clickedButton()
        if clickedButton == leaveButton:
            return 'leave'
        if clickedButton == blankButton:
            return 'blank'
        if clickedButton == naButton:
            return 'na'
        return 'cancel'

    def _apply_non_numeric_action(self, rows: list[list[str]], action: str) -> list[list[str]]:
        if action == 'leave':
            return rows

        replacement = '' if action == 'blank' else 'N/A'
        cleanedRows = []
        for row in rows:
            cleanedRows.append([
                value if self._is_numeric_text(value) else replacement
                for value in row
            ])
        return cleanedRows

    def _clear_paste_area(
        self,
        startRow: int,
        startColumn: int,
        rowCount: int,
        columnCount: int,
    ) -> None:
        requiredRows = startRow + rowCount
        requiredColumns = startColumn + columnCount
        if requiredRows > self.tableWidget.rowCount():
            self.tableWidget.setRowCount(requiredRows)
        if requiredColumns > self.tableWidget.columnCount():
            self.tableWidget.setColumnCount(requiredColumns)

        for rowOffset in range(rowCount):
            tableRow = startRow + rowOffset
            for columnOffset in range(columnCount):
                self.tableWidget.setItem(
                    tableRow,
                    startColumn + columnOffset,
                    QTableWidgetItem(''),
                )

        if self.onTableShapeChanged is not None:
            self.onTableShapeChanged()
