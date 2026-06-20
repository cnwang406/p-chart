#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def log(message: str) -> None:
    print(f'[GUI-SMOKE] {message}', flush=True)


class GuiSmoke:
    def __init__(self, appMain, renderPlots: bool) -> None:
        self.appMain = appMain
        self.app = appMain.app
        self.tabWidget = appMain.tabWidget
        self.renderPlots = renderPlots

    def run(self) -> None:
        self.activate_all_tabs()
        if self.renderPlots:
            self.render_core_plots()
        log('smoke complete')

    def activate_all_tabs(self) -> None:
        for tabIndex in range(self.tabWidget.count()):
            tabWidget = self.tabWidget.widget(tabIndex)
            objectName = tabWidget.objectName() if tabWidget is not None else ''
            log(f'activate tab {tabIndex}: {objectName}')
            self.tabWidget.setCurrentIndex(tabIndex)
            self.wait(0.4)

    def render_core_plots(self) -> None:
        self.load_contour_demo_workbook()
        self.render_scatter()
        self.render_boxplot()
        self.render_wafermap()
        self.render_contour()
        self.render_log()
        self.activate_tab('tabIdiot')

    def load_contour_demo_workbook(self) -> None:
        demoPath = ROOT / 'demo' / '06191623.xlsx'
        log(f'load workbook: {demoPath}')
        loaded = self.appMain.tabDataWidget.load_workbook_sheet(str(demoPath), sheetName='idiotData')
        if not loaded:
            raise RuntimeError('TabData failed to load demo/06191623.xlsx')
        self.wait(0.5)

    def render_scatter(self) -> None:
        scatter = self.appMain.tabScatterWidget
        self.activate_tab('tabScatter')
        self.set_combo(scatter.xComboBox, 'x')
        self.set_combo(scatter.yComboBox, 'value')
        log('render scatter')
        scatter._draw_plot()
        self.wait(4.0)

    def render_boxplot(self) -> None:
        boxplot = self.appMain.tabBoxplotWidget
        self.activate_tab('tabBoxplot')
        self.set_combo(boxplot.yComboBox, 'value')
        log('render boxplot')
        boxplot._draw_plot()
        self.wait(4.0)

    def render_wafermap(self) -> None:
        wafermap = self.appMain.tabWafermapWidget
        self.activate_tab('tabWafermap')
        self.set_combo(wafermap.xComboBox, 'x')
        self.set_combo(wafermap.yComboBox, 'y')
        self.set_combo(wafermap.zComboBox, 'value')
        log('render wafermap')
        wafermap.draw_plot()
        self.wait(5.0)

    def render_contour(self) -> None:
        contour = self.appMain.tabContourWidget
        self.activate_tab('tabContour')
        self.set_combo(contour.xComboBox, 'x')
        self.set_combo(contour.yComboBox, 'y')
        self.set_combo(contour.zComboBox, 'value')
        log('render contour')
        contour._draw_plot()
        self.wait(6.0)

    def render_log(self) -> None:
        logWidget = self.appMain.tabLogWidget
        self.activate_tab('logTab')
        self.set_combo(logWidget.xComboBox, 'x')
        self.set_checked_column(logWidget.y1ColumnCombo, 'value')
        self.select_first_item(logWidget.filesList)
        log('render log')
        logWidget._draw_plot()
        self.wait(5.0)

    def activate_tab(self, objectName: str) -> None:
        for tabIndex in range(self.tabWidget.count()):
            tabWidget = self.tabWidget.widget(tabIndex)
            if tabWidget is not None and tabWidget.objectName() == objectName:
                log(f'activate tab: {objectName}')
                self.tabWidget.setCurrentIndex(tabIndex)
                self.wait(0.4)
                return
        raise RuntimeError(f'Tab not found: {objectName}')

    def set_combo(self, comboBox, text: str) -> None:
        index = comboBox.findText(text)
        if index < 0:
            raise RuntimeError(f'Combo value not found: {text}')
        comboBox.setCurrentIndex(index)
        self.wait(0.1)

    def set_checked_column(self, checkableCombo, text: str) -> None:
        from PySide6.QtCore import Qt

        found = False
        for rowIndex in range(checkableCombo.model.rowCount()):
            item = checkableCombo.model.item(rowIndex)
            if item is None:
                continue
            checked = item.text() == text
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            found = found or checked
        if not found:
            raise RuntimeError(f'Checkable combo value not found: {text}')
        self.wait(0.1)

    def select_first_item(self, listWidget) -> None:
        if listWidget.count() <= 0:
            raise RuntimeError('List widget has no selectable items')
        listWidget.clearSelection()
        listWidget.item(0).setSelected(True)
        self.wait(0.1)

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.05)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run p-chart GUI smoke checks.')
    parser.add_argument(
        '--no-webengine',
        action='store_true',
        help='Pass --no-webengine to the app for controller/data smoke.',
    )
    parser.add_argument(
        '--render',
        action='store_true',
        help='Render Scatter, Boxplot, Wafermap, Contour, and Log with demo data.',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ.setdefault('QT_ENABLE_HIGHDPI_SCALING', '0')
    sys.argv = ['gui_smoke.py']
    if args.no_webengine:
        sys.argv.append('--no-webengine')

    log('import app')
    from app import AppMain

    log('start app')
    appMain = AppMain()
    try:
        GuiSmoke(appMain, renderPlots=args.render).run()
    finally:
        appMain.ui.close()
        appMain.app.processEvents()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
