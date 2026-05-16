import json
import os
from pathlib import Path
import re
import sys
import importlib.util
# 找 PySide6 path（還沒 import Qt）
spec = importlib.util.find_spec("PySide6")
print (f'PySide6 found at: {spec.origin}')
baseDir = os.path.dirname(spec.origin)
qtDir = os.path.join(baseDir, "Qt")

pluginPath = os.path.join(qtDir, "plugins")
frameworkPath = os.path.join(qtDir, "lib")
print (f'QT_PLUGIN_PATH: {pluginPath}')
print (f'QT_FRAMEWORK_PATH: {frameworkPath}')   
# for key in [
#     "QT_PLUGIN_PATH",
#     "QT_QPA_PLATFORM_PLUGIN_PATH",
#     "DYLD_LIBRARY_PATH",
#     "DYLD_FRAMEWORK_PATH",
# ]:
#     os.environ.pop(key, None)

# macOS/PySide6 can fail to load the cocoa platform plugin when Qt paths are
# forced before importing PySide6. Let PySide6 resolve its own plugin/runtime paths.
# os.environ["QT_PLUGIN_PATH"] = pluginPath
# os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(pluginPath, "platforms")
# os.environ["DYLD_FRAMEWORK_PATH"] = frameworkPath

# os.environ["PYSIDE_DESIGNER_PLUGINS"] = ""


import PySide6
from PySide6.QtCore import QCoreApplication, QFile
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QColor
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from qt_helpers import require_child
from tabBoxplot import TabBoxplotWidget
from tabData import TabDataWidget
from tabScatter import WEB_ENGINE_AVAILABLE, TabScatterWidget
from tabWafermap import TabWafermapWidget

QT_PLUGIN_PATH = os.path.join(os.path.dirname(PySide6.__file__), 'Qt', 'plugins')
QT_PLATFORM_PLUGIN_PATH = os.path.join(QT_PLUGIN_PATH, 'platforms')
APP_NAME = 'p-chart'
APP_VERSION = 'v2.4.1'
APP_AUTHOR = 'cnwang'
APP_DATE = '2024/04'
WINDOW_TITLE = f'{APP_NAME} {APP_VERSION} by {APP_AUTHOR}, {APP_DATE}'
APP_ICON_FILENAME = os.path.join(
    'AppIcon.appiconset',
    'icon-ios-marketing-1024x1024-1x.png',
)
NO_WEBENGINE_ARGS = {'--no-webengine', '-W'}
REC_DIRECTORY_CANDIDATES = [
    Path(r'z:\9630\pchart.rec'),
    Path.home() / 'Document' / 'py' / 'pchart_sa.rec',
    Path.home() / 'Documents' / 'py' / 'pchart_sa.rec',
]
REC_INFO_FILENAME = 'info.json'


def resource_path(filename: str) -> str:
    basePath = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
    return os.path.join(basePath, filename)


def remove_runtime_args() -> dict[str, bool]:
    runtimeOptions = {'no_webengine': False}
    qtArgs = [sys.argv[0]]
    for argument in sys.argv[1:]:
        if argument in NO_WEBENGINE_ARGS:
            runtimeOptions['no_webengine'] = True
            continue
        qtArgs.append(argument)
    sys.argv[:] = qtArgs
    return runtimeOptions


def is_remote_desktop_session() -> bool:
    if not sys.platform.startswith('win'):
        return False
    sessionName = os.environ.get('SESSIONNAME', '').lower()
    return sessionName.startswith('rdp-') or sessionName.startswith('rdp')


def find_existing_rec_directory() -> Path | None:
    for recDirectory in REC_DIRECTORY_CANDIDATES:
        if recDirectory.exists() and recDirectory.is_dir():
            return recDirectory
    return None


def version_key(versionText: str) -> tuple[int, ...]:
    versionNumbers = re.findall(r'\d+', str(versionText))
    return tuple(int(versionNumber) for versionNumber in versionNumbers)


def is_newer_version(candidateVersion: str, currentVersion: str) -> bool:
    candidateKey = version_key(candidateVersion)
    currentKey = version_key(currentVersion)
    if not candidateKey or not currentKey:
        return str(currentVersion) < str(candidateVersion)

    maxLength = max(len(candidateKey), len(currentKey))
    candidateKey += (0,) * (maxLength - len(candidateKey))
    currentKey += (0,) * (maxLength - len(currentKey))
    return currentKey < candidateKey


def read_launch_info() -> dict[str, str] | None:
    recDirectory = find_existing_rec_directory()
    if recDirectory is None:
        return None

    infoPath = recDirectory / REC_INFO_FILENAME
    defaultInfo = {
        'version': APP_VERSION,
        'launch': '1',
        'release_note': '',
    }
    if not infoPath.exists():
        write_launch_info(infoPath, defaultInfo)
        return defaultInfo

    try:
        launchInfo = json.loads(infoPath.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        launchInfo = {}
    if not isinstance(launchInfo, dict):
        launchInfo = {}

    launchCount = 0
    try:
        launchCount = int(launchInfo.get('launch', 0))
    except (TypeError, ValueError):
        launchCount = 0

    savedVersion = str(launchInfo.get('version') or APP_VERSION)
    launchInfo['version'] = (
        APP_VERSION
        if is_newer_version(APP_VERSION, savedVersion)
        else savedVersion
    )
    launchInfo['launch'] = str(launchCount + 1)
    launchInfo['release_note'] = str(launchInfo.get('release_note') or '')
    write_launch_info(infoPath, launchInfo)
    return launchInfo


def write_launch_info(infoPath: Path, launchInfo: dict[str, str]) -> None:
    try:
        infoPath.write_text(
            json.dumps(launchInfo, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except OSError:
        return


class AppMain:
    def __init__(self) -> None:
        self.runtimeOptions = remove_runtime_args()
        if os.path.exists(QT_PLUGIN_PATH):
            QCoreApplication.addLibraryPath(QT_PLUGIN_PATH)
        if os.path.exists(QT_PLATFORM_PLUGIN_PATH):
            os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', QT_PLATFORM_PLUGIN_PATH)
        self.app = QApplication(sys.argv)
        self._configure_application_metadata()
        self._configure_application_icon()
        self._load_application_font()
        self.launchInfo = read_launch_info()
        self.ui = self._load_ui(self._platform_ui_filename())
        self.ui.setWindowTitle(WINDOW_TITLE)
        if not self.app.windowIcon().isNull():
            self.ui.setWindowIcon(self.app.windowIcon())
        self.tabWidget = require_child(self.ui, QTabWidget, 'tabWidget')
        self.noWebengineLabel = require_child(self.ui, QLabel, 'noWebengineLabel')
        self.aboutButton = require_child(self.ui, QPushButton, 'aboutButton')
        self.tabDataWidget = TabDataWidget(self.ui)
        preferWebEngine = (
            not self.runtimeOptions['no_webengine']
            and not is_remote_desktop_session()
            and WEB_ENGINE_AVAILABLE
        )
        self.noWebengineLabel.setText('' if preferWebEngine else 'no webEngine')
        self.tabScatterWidget = TabScatterWidget(self.ui, preferWebEngine=preferWebEngine)
        self.tabBoxplotWidget = TabBoxplotWidget(self.ui, preferWebEngine=preferWebEngine)
        self.tabWafermapWidget = TabWafermapWidget(self.ui)
        self.tabScatterWidget.set_tab_data(self.tabDataWidget)
        self.tabBoxplotWidget.set_tab_data(self.tabDataWidget)
        self.tabWafermapWidget.set_tab_data(self.tabDataWidget)
        self.tabWidget.currentChanged.connect(self._warn_if_plotting_loaded_data)
        self.aboutButton.clicked.connect(self._show_about_dialog)
        self.tabWidget.setCurrentIndex(0)
        self._center_window()
        self.ui.show()
        self._show_new_version_notice()

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

    def _platform_ui_filename(self) -> str:
        if sys.platform.startswith('win'):
            return 'mainwindow-win.ui'
        if sys.platform == 'darwin':
            return 'mainwindow-mac.ui'
        return 'mainwindow-mac.ui'

    def _center_window(self) -> None:
        screen = self.app.primaryScreen()
        if screen is None:
            return

        # self.ui.adjustSize()
        windowFrame = self.ui.frameGeometry()
        windowFrame.moveCenter(screen.availableGeometry().center())
        self.ui.move(windowFrame.topLeft())

    def _warn_if_plotting_loaded_data(self, tabIndex: int) -> None:
        if tabIndex not in [1, 2, 3]:
            return
        if (
            not self.tabDataWidget.has_loaded_data()
            or self.tabDataWidget.has_reshaped_data()
            or not self.tabDataWidget.has_reshape_columns()
        ):
            return

        warningText = '尚未完成 reshape. 會以原本的資料進行畫圖'
        if tabIndex == 1:
            self.tabScatterWidget._set_status(warningText, error=True)
        elif tabIndex == 2:
            self.tabBoxplotWidget._set_status(warningText, error=True)
        elif tabIndex == 3:
            self.tabWafermapWidget._set_status(warningText, error=True)
        self._show_app_icon_warning(warningText)

    def _show_app_icon_warning(self, message: str) -> None:
        messageBox = QMessageBox(self.ui)
        messageBox.setWindowTitle('Warning')
        messageBox.setText(message)
        messageBox.setStandardButtons(QMessageBox.StandardButton.Ok)
        appIcon = self.app.windowIcon()
        if not appIcon.isNull():
            messageBox.setWindowIcon(appIcon)
            messageBox.setIconPixmap(appIcon.pixmap(64, 64))
        else:
            messageBox.setIcon(QMessageBox.Icon.Warning)
        messageBox.exec()

    def _show_new_version_notice(self) -> None:
        if not self.launchInfo:
            return

        latestVersion = self.launchInfo.get('version', '')
        if not is_newer_version(latestVersion, APP_VERSION):
            return

        releaseNote = str(self.launchInfo.get('release_note') or '').strip()
        message = f'好像有新版本, {latestVersion}, 可以問我看看有什麼新功能'
        if releaseNote:
            message = f'{message}\n\nRelease note:\n{releaseNote}'

        QMessageBox.information(
            self.ui,
            'New Version',
            message,
        )

    def _show_about_dialog(self) -> None:
        dialog = QDialog(self.ui)
        dialog.setWindowTitle(f'About {APP_NAME}')
        dialogLayout = QVBoxLayout(dialog)

        headerLayout = QHBoxLayout()
        iconLabel = QLabel()
        appIcon = self.app.windowIcon()
        if not appIcon.isNull():
            iconLabel.setPixmap(appIcon.pixmap(72, 72))
        headerLayout.addWidget(iconLabel)

        titleLabel = QLabel(f'{APP_NAME} {APP_VERSION}\nby {APP_AUTHOR}, {APP_DATE}')
        titleFont = titleLabel.font()
        titleFont.setPointSize(12)
        titleLabel.setFont(titleFont)
        headerLayout.addWidget(titleLabel, 1)
        dialogLayout.addLayout(headerLayout)

        acknowledgeTextEdit = QTextEdit()
        acknowledgeFont = acknowledgeTextEdit.font()
        acknowledgeFont.setPointSize(12)
        acknowledgeTextEdit.setFont(acknowledgeFont)
        acknowledgeTextEdit.setTextColor(QColor('darkblue'))
        acknowledgeTextEdit.setReadOnly(True)
        acknowledgeTextEdit.setText(
"""
    import packages and resources:
        - PySide6 for GUI
        - Plotly for plotting
        - Pandas for data manipulation
        - Cascadia Next TC font for Windows/macOS UI consistency
        - codex for code generation and debugging assistance

    if you found this tool useful, please consider giving it me a cup of coffee~

            畢竟 tokens ** 很燒錢的 **
                                    cnwang, 2026/05
""")
        acknowledgeTextEdit.setMinimumSize(520, 260)
        dialogLayout.addWidget(acknowledgeTextEdit)

        dialog.resize(640, 420)
        dialog.exec()

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
