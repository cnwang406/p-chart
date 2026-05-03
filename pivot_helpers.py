import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


PIVOT_VALUE_COLUMNS = [
    'n',
    'min',
    'max',
    'average',
    'sigma',
    'average - 3 sigma',
    'average + 3 sigma',
    'range',
]


def build_pivot_table(dataFrame: pd.DataFrame, yColumn: str, groupColumns: list[str]) -> pd.DataFrame:
    workingData = dataFrame.copy()
    workingData[yColumn] = pd.to_numeric(workingData[yColumn], errors='coerce')
    subsetColumns = [yColumn, *groupColumns]
    workingData = workingData.dropna(subset=subsetColumns)
    if workingData.empty:
        return pd.DataFrame(columns=[*groupColumns, *PIVOT_VALUE_COLUMNS])

    rows = []
    if groupColumns:
        groupedData = workingData.groupby(groupColumns, dropna=False, sort=True)
        iterator = groupedData[yColumn]
    else:
        iterator = [('All', workingData[yColumn])]

    for groupKey, series in iterator:
        if not isinstance(groupKey, tuple):
            groupKey = (groupKey,)
        series = series.dropna()
        average = float(series.mean())
        sigma = float(series.std(ddof=1)) if len(series) > 1 else 0.0
        row = {
            'n': int(series.count()),
            'min': float(series.min()),
            'max': float(series.max()),
            'average': average,
            'sigma': sigma,
            'average - 3 sigma': average - 3 * sigma,
            'average + 3 sigma': average + 3 * sigma,
            'range': float(series.max() - series.min()),
        }
        for columnName, value in zip(groupColumns, groupKey):
            row[columnName] = value
        rows.append(row)

    return pd.DataFrame(rows, columns=[*groupColumns, *PIVOT_VALUE_COLUMNS])


def show_pivot_dialog(parent: QWidget, title: str, pivotData: pd.DataFrame) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(900, 520)
    pivotFont = QFont('Cascadia Next TC', 14)
    dialog.setFont(pivotFont)

    tableWidget = QTableWidget(dialog)
    tableWidget.setFont(pivotFont)
    tableWidget.horizontalHeader().setFont(pivotFont)
    tableWidget.verticalHeader().setFont(pivotFont)
    tableWidget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tableWidget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    tableWidget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tableWidget.setRowCount(len(pivotData))
    tableWidget.setColumnCount(len(pivotData.columns))
    tableWidget.setHorizontalHeaderLabels([str(column) for column in pivotData.columns])

    for rowIndex, (_, row) in enumerate(pivotData.iterrows()):
        for columnIndex, value in enumerate(row):
            item = QTableWidgetItem(_format_cell_value(value))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            tableWidget.setItem(rowIndex, columnIndex, item)
    tableWidget.resizeColumnsToContents()

    copyButton = QPushButton('Copy')
    closeButton = QPushButton('Close')
    copyButton.setFont(pivotFont)
    closeButton.setFont(pivotFont)

    def copy_table() -> None:
        QApplication.clipboard().setText(pivotData.to_csv(sep=',', index=False))

    copyButton.clicked.connect(copy_table)
    closeButton.clicked.connect(dialog.accept)

    buttonLayout = QHBoxLayout()
    buttonLayout.addStretch(1)
    buttonLayout.addWidget(copyButton)
    buttonLayout.addWidget(closeButton)

    dialogLayout = QVBoxLayout(dialog)
    dialogLayout.addWidget(tableWidget)
    dialogLayout.addLayout(buttonLayout)
    dialog.exec()


def _format_cell_value(value) -> str:
    if pd.isna(value):
        return ''
    if isinstance(value, float):
        return f'{value:.6g}'
    return str(value)
