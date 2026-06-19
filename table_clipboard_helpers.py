from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem


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
        self.tableWidget.editItem(self.tableWidget.item(nextRow, currentColumn))

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
        rowCount = len(rows)
        columnCount = max(len(row) for row in rows)
        requiredRows = startRow + rowCount
        requiredColumns = startColumn + columnCount

        if requiredRows > self.tableWidget.rowCount():
            self.tableWidget.setRowCount(requiredRows)
        if requiredColumns > self.tableWidget.columnCount():
            self.tableWidget.setColumnCount(requiredColumns)
        if self.onTableShapeChanged is not None:
            self.onTableShapeChanged()

        for rowOffset, rowValues in enumerate(rows):
            tableRow = startRow + rowOffset
            for columnOffset in range(columnCount):
                tableColumn = startColumn + columnOffset
                value = rowValues[columnOffset] if columnOffset < len(rowValues) else ''
                self.tableWidget.setItem(
                    tableRow,
                    tableColumn,
                    QTableWidgetItem(value),
                )

        self.tableWidget.setCurrentCell(startRow, startColumn)
        self.tableWidget.resizeColumnsToContents()
        if self.onPasteFinished is not None:
            self.onPasteFinished(rowCount, columnCount)

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
