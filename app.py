import os
import sys

import PySide6
from PySide6.QtCore import QCoreApplication, QFile
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget, QWidget

from qt_helpers import require_child
from tabBoxplot import TabBoxplotWidget
from tabData import TabDataWidget
from tabScatter import TabScatterWidget

QT_PLUGIN_PATH = os.path.join(os.path.dirname(PySide6.__file__), 'Qt', 'plugins')
QT_PLATFORM_PLUGIN_PATH = os.path.join(QT_PLUGIN_PATH, 'platforms')
APP_NAME = 'p-chart'
APP_VERSION = 'v2.1'
APP_AUTHOR = 'cnwang'
APP_DATE = '2024/04'
WINDOW_TITLE = f'{APP_NAME} {APP_VERSION} by {APP_AUTHOR}, {APP_DATE}'
APP_ICON_FILENAME = os.path.join(
    'AppIcon.appiconset',
    'icon-ios-marketing-1024x1024-1x.png',
)


def resource_path(filename: str) -> str:
    basePath = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
    return os.path.join(basePath, filename)


class AppMain:
    def __init__(self) -> None:
        if os.path.exists(QT_PLUGIN_PATH):
            QCoreApplication.addLibraryPath(QT_PLUGIN_PATH)
        if os.path.exists(QT_PLATFORM_PLUGIN_PATH):
            os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', QT_PLATFORM_PLUGIN_PATH)
        self.app = QApplication(sys.argv)
        self._configure_application_metadata()
        self._configure_application_icon()
        self._load_application_font()
        self.ui = self._load_ui('mainwindow.ui')
        self.ui.setWindowTitle(WINDOW_TITLE)
        if not self.app.windowIcon().isNull():
            self.ui.setWindowIcon(self.app.windowIcon())
        self.tabWidget = require_child(self.ui, QTabWidget, 'tabWidget')
        self.tabDataWidget = TabDataWidget(self.ui)
        self.tabScatterWidget = TabScatterWidget(self.ui)
        self.tabBoxplotWidget = TabBoxplotWidget(self.ui)
        self.tabScatterWidget.set_tab_data(self.tabDataWidget)
        self.tabBoxplotWidget.set_tab_data(self.tabDataWidget)
        self.tabDataWidget.add_data_changed_callback(self._update_plot_tab_enabled)
        self._update_plot_tab_enabled()
        self.tabWidget.setCurrentIndex(0)
        self.ui.show()

    def _configure_application_metadata(self) -> None:
        self.app.setApplicationName(APP_NAME)
        self.app.setApplicationVersion(APP_VERSION)
        self.app.setOrganizationName(APP_AUTHOR)

    def _configure_application_icon(self) -> None:
        iconPath = resource_path(APP_ICON_FILENAME)
        if os.path.exists(iconPath):
            self.app.setWindowIcon(QIcon(iconPath))

    def _load_application_font(self) -> None:
        fontPath = resource_path('CascadiaNextTC.wght.ttf')
        if not os.path.exists(fontPath):
            return

        fontId = QFontDatabase.addApplicationFont(fontPath)
        if fontId < 0:
            return

        fontFamilies = QFontDatabase.applicationFontFamilies(fontId)
        if not fontFamilies:
            return

        self.app.setFont(QFont(fontFamilies[0], 10))

    def _update_plot_tab_enabled(self) -> None:
        enabled = not self.tabDataWidget.get_melted_data().empty
        for plotTabIndex in [1, 2]:
            if self.tabWidget.count() <= plotTabIndex:
                continue
            self.tabWidget.setTabEnabled(plotTabIndex, enabled)
            self.tabWidget.setTabToolTip(
                plotTabIndex,
                '' if enabled else 'Run wide_to_long successfully before plotting.',
            )
        if not enabled and self.tabWidget.currentIndex() in [1, 2]:
            self.tabWidget.setCurrentIndex(0)

    def _load_ui(self, uiFilename: str) -> QWidget:
        uiPath = resource_path(uiFilename)
        uiFile = QFile(uiPath)
        if not uiFile.open(QFile.OpenModeFlag.ReadOnly):
            QMessageBox.critical(None, 'Error', f'Cannot open UI file: {uiPath}')
            sys.exit(1)
        loader = QUiLoader()
        ui = loader.load(uiFile)
        uiFile.close()
        if ui is None:
            QMessageBox.critical(None, 'Error', 'Failed to load UI file.')
            sys.exit(1)
        return ui

if __name__ == '__main__':
    appMain = AppMain()
    sys.exit(appMain.app.exec())
