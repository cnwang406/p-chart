import json
import os
from pathlib import Path
import sys
import importlib.util
# 找 PySide6 path（還沒 import Qt）
spec = importlib.util.find_spec("PySide6")
print (f'PySide6 found at: {spec.origin}')
baseDir = os.path.dirname(spec.origin)
qtDir = os.path.join(baseDir, "Qt")

pluginPath = os.path.join(qtDir, "plugins")
frameworkPath = os.path.join(qtDir, "lib")
QT_PLUGIN_PATH = pluginPath
QT_PLATFORM_PLUGIN_PATH = os.path.join(QT_PLUGIN_PATH, 'platforms')
print (f'QT_PLUGIN_PATH: {pluginPath}')
print (f'QT_FRAMEWORK_PATH: {frameworkPath}')   
# for key in [
#     "QT_PLUGIN_PATH",
#     "QT_QPA_PLATFORM_PLUGIN_PATH",
#     "DYLD_LIBRARY_PATH",
#     "DYLD_FRAMEWORK_PATH",
# ]:
#     os.environ.pop(key, None)

# macOS/PySide6 can fail to load the cocoa platform plugin when a parent
# process leaves blank or stale Qt paths behind. VSCode integrated terminals are
# especially prone to inheriting those values across launches.


def _path_matches(pathText: str, expectedPath: str) -> bool:
    if not pathText:
        return False

    try:
        return Path(pathText).expanduser().resolve() == Path(expectedPath).resolve()
    except OSError:
        return False


def _qt_path_env_matches(key: str, expectedPath: str) -> bool:
    value = os.environ.get(key)
    if value is None:
        return True

    pathParts = [part for part in value.split(os.pathsep) if part]
    return bool(pathParts) and all(_path_matches(part, expectedPath) for part in pathParts)


def configure_qt_runtime_environment() -> None:
    if not os.path.isdir(QT_PLATFORM_PLUGIN_PATH):
        return

    if not _qt_path_env_matches('QT_PLUGIN_PATH', QT_PLUGIN_PATH):
        os.environ.pop('QT_PLUGIN_PATH', None)

    # Use assignment rather than setdefault: an empty inherited value makes Qt
    # search for the cocoa plugin in "" and abort before the app starts.
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = QT_PLATFORM_PLUGIN_PATH

    for key in ('DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH'):
        if os.environ.get(key) == '':
            os.environ.pop(key, None)

# os.environ["PYSIDE_DESIGNER_PLUGINS"] = ""


import PySide6
configure_qt_runtime_environment()
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QFile,
    QObject,
    QSignalBlocker,
    QTimer,
)
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
from tabContour import TabContourWidget
from tabData import TabDataWidget
from tabIdiot import TabIdiotWidget
from tabLog import TabLogWidget
from tabScatter import WEB_ENGINE_AVAILABLE, TabScatterWidget
from tabWafermap import TabWafermapWidget
from update_helpers import (
    is_newer_release,
    is_newer_version,
    normalize_build_number,
)

APP_NAME = 'p-chart'
APP_VERSION = 'v3.0'
APP_AUTHOR = 'cnwang'
APP_DATE = 'build 0720'
WINDOW_TITLE = f'{APP_NAME} {APP_VERSION} {APP_DATE} by {APP_AUTHOR}'
UI_FONT_STYLESHEET = '''
QPushButton,
QLabel,
QCheckBox,
QRadioButton,
QGroupBox,
QTabBar {
    font-family: "Cascadia Code";
}
QTableView,
QTableWidget,
QHeaderView,
QListView,
QListWidget,
QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QTextEdit,
QPlainTextEdit {
    font-family: "Cascadia Next TC";
}
'''
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


def read_launch_info() -> dict[str, str] | None:
    recDirectory = find_existing_rec_directory()
    if recDirectory is None:
        return None

    infoPath = recDirectory / REC_INFO_FILENAME
    defaultInfo = {
        'version': APP_VERSION,
        'build': normalize_build_number(APP_DATE),
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

    currentBuild = normalize_build_number(APP_DATE)
    savedVersion = str(launchInfo.get('version') or APP_VERSION)
    savedBuild = normalize_build_number(launchInfo.get('build'))
    versionsMatch = (
        not is_newer_version(APP_VERSION, savedVersion)
        and not is_newer_version(savedVersion, APP_VERSION)
    )
    if not savedBuild and versionsMatch:
        savedBuild = currentBuild

    if is_newer_release(APP_VERSION, currentBuild, savedVersion, savedBuild):
        launchInfo['version'] = APP_VERSION
        launchInfo['build'] = currentBuild
    else:
        launchInfo['version'] = savedVersion
        launchInfo['build'] = savedBuild
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


class ResponsiveUiResizer(QObject):
    def __init__(self, rootWidget: QWidget, plotControllers: dict[str, object] | None = None) -> None:
        super().__init__(rootWidget)
        self.rootWidget = rootWidget
        self.plotControllers = plotControllers or {}
        self.anchorRules = []
        self.rulesByParentId = {}
        self.parentWidgets = {}
        self.pendingPlotControllers = set()
        self.plotResizeTimer = QTimer(self)
        self.plotResizeTimer.setSingleShot(True)
        self.plotResizeTimer.setInterval(250)
        self.plotResizeTimer.timeout.connect(self._redraw_resized_plots)
        self._register_default_anchors()

    def _register_default_anchors(self) -> None:
        self._add_anchor('tabWidget', stretchWidth=True, stretchHeight=True)

        for widgetName in (
            'previewTableWidget',
            'plotAreaWidget',
            'boxPlotAreaWidget',
            'contourPlotAreaWidget',
            'idiotDataTableWidget',
            'logPlotAreaWidget',
            'waferMapPlotAreaWidget',
        ):
            self._add_anchor(widgetName, stretchWidth=True, stretchHeight=True)

        for widgetName in (
            'statisticLabel',
            'boxStatisticLabel',
        ):
            self._add_anchor(widgetName, stretchWidth=True)

        for widgetName in (
            'statusLabelTab1',
            'statusLabelTab2',
            'boxStatusLabel',
            'contourStatusLabel',
            'idiotStatusLabelTab',
            'logStatusLabel',
            'waferMapStatusLabelx',
        ):
            self._add_anchor(widgetName, stretchWidth=True, moveY=True)

    def _add_anchor(
        self,
        widgetName: str,
        stretchWidth: bool = False,
        stretchHeight: bool = False,
        moveY: bool = False,
    ) -> None:
        widget = self.rootWidget.findChild(QWidget, widgetName)
        if widget is None or widget.parentWidget() is None:
            return

        parentWidget = widget.parentWidget()
        rule = {
            'widget': widget,
            'parent': parentWidget,
            'geometry': widget.geometry(),
            'stretchWidth': stretchWidth,
            'stretchHeight': stretchHeight,
            'moveY': moveY,
            'rightMargin': max(0, parentWidget.width() - widget.geometry().right() - 1),
            'bottomMargin': max(0, parentWidget.height() - widget.geometry().bottom() - 1),
            'minWidth': min(widget.width(), 200),
            'minHeight': min(widget.height(), 120) if stretchHeight else widget.height(),
        }
        self.anchorRules.append(rule)
        parentId = id(parentWidget)
        self.rulesByParentId.setdefault(parentId, []).append(rule)
        if parentId not in self.parentWidgets:
            self.parentWidgets[parentId] = parentWidget
            parentWidget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        parentId = id(watched)
        if (
            event.type() in (
                QEvent.Type.Resize,
                QEvent.Type.Show,
                QEvent.Type.ShowToParent,
            )
            and parentId in self.rulesByParentId
        ):
            self._apply_parent_rules(watched)
        return super().eventFilter(watched, event)

    def _apply_parent_rules(self, parentWidget: QWidget) -> None:
        for rule in self.rulesByParentId.get(id(parentWidget), []):
            self._apply_rule(rule)
        self._enforce_vertical_gap(
            parentWidget,
            'idiotDataTableWidget',
            'idiotStatusLabelTab',
            16,
        )

    def _enforce_vertical_gap(
        self,
        parentWidget: QWidget,
        upperWidgetName: str,
        lowerWidgetName: str,
        minimumGap: int,
    ) -> None:
        upperWidget = parentWidget.findChild(QWidget, upperWidgetName)
        lowerWidget = parentWidget.findChild(QWidget, lowerWidgetName)
        if upperWidget is None or lowerWidget is None:
            return
        if (
            upperWidget.parentWidget() is not parentWidget
            or lowerWidget.parentWidget() is not parentWidget
        ):
            return

        maxHeight = lowerWidget.y() - minimumGap - upperWidget.y()
        if maxHeight <= 0 or upperWidget.height() <= maxHeight:
            return
        upperWidget.setGeometry(
            upperWidget.x(),
            upperWidget.y(),
            upperWidget.width(),
            maxHeight,
        )

    def _apply_rule(self, rule: dict[str, object]) -> None:
        widget = rule['widget']
        parentWidget = rule['parent']
        baseGeometry = rule['geometry']

        newX = baseGeometry.x()
        newY = baseGeometry.y()
        newWidth = baseGeometry.width()
        newHeight = baseGeometry.height()
        if rule['stretchWidth']:
            newWidth = max(
                int(rule['minWidth']),
                parentWidget.width() - newX - int(rule['rightMargin']),
            )
        if rule['stretchHeight']:
            newHeight = max(
                int(rule['minHeight']),
                parentWidget.height() - newY - int(rule['bottomMargin']),
            )
        if rule['moveY']:
            newY = max(
                0,
                parentWidget.height() - int(rule['bottomMargin']) - newHeight,
            )
        sizeChanged = widget.width() != newWidth or widget.height() != newHeight
        widget.setGeometry(newX, newY, newWidth, newHeight)
        if sizeChanged:
            self._sync_plot_size(widget)

    def _sync_plot_size(self, plotAreaWidget: QWidget) -> None:
        plotController = self.plotControllers.get(plotAreaWidget.objectName())
        if plotController is None:
            return

        if hasattr(plotController, 'plotWidthSpinBox') and hasattr(plotController, 'plotHeightSpinBox'):
            widthBlocker = QSignalBlocker(plotController.plotWidthSpinBox)
            heightBlocker = QSignalBlocker(plotController.plotHeightSpinBox)
            plotController.plotWidthSpinBox.setValue(max(200, plotAreaWidget.width()))
            plotController.plotHeightSpinBox.setValue(max(200, plotAreaWidget.height()))
            del widthBlocker, heightBlocker

        if plotController.currentPlotFigure is not None:
            self.pendingPlotControllers.add(plotController)
            self.plotResizeTimer.start()

    def _redraw_resized_plots(self) -> None:
        plotControllers = list(self.pendingPlotControllers)
        self.pendingPlotControllers.clear()
        for plotController in plotControllers:
            plotController._draw_plot()

    def apply_all(self) -> None:
        for parentWidget in self.parentWidgets.values():
            self._apply_parent_rules(parentWidget)


class AppMain(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.runtimeOptions = remove_runtime_args()
        if os.path.exists(QT_PLUGIN_PATH):
            QCoreApplication.addLibraryPath(QT_PLUGIN_PATH)
        if os.path.exists(QT_PLATFORM_PLUGIN_PATH):
            os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', QT_PLATFORM_PLUGIN_PATH)
        self.app = QApplication(sys.argv)
        self._configure_application_metadata()
        self._configure_application_icon()
        self._load_application_fonts()
        self.launchInfo = read_launch_info()
        self.ui = self._load_ui(self._platform_ui_filename())
        self.ui.setWindowTitle(WINDOW_TITLE)
        if not self.app.windowIcon().isNull():
            self.ui.setWindowIcon(self.app.windowIcon())
        self.tabWidget = require_child(self.ui, QTabWidget, 'tabWidget')
        self.noWebengineLabel = require_child(self.ui, QLabel, 'noWebengineLabel')
        self.aboutButton = require_child(self.ui, QPushButton, 'aboutButton')
        self.tabDataWidget = TabDataWidget(self.ui)
        self.tabIdiotWidget = TabIdiotWidget(self.ui, self.tabDataWidget, self.tabWidget)
        preferWebEngine = (
            not self.runtimeOptions['no_webengine']
            and not is_remote_desktop_session()
            and WEB_ENGINE_AVAILABLE
        )
        self.noWebengineLabel.setText('' if preferWebEngine else 'no webEngine')
        self.tabScatterWidget = TabScatterWidget(self.ui, preferWebEngine=preferWebEngine)
        self.tabBoxplotWidget = TabBoxplotWidget(self.ui, preferWebEngine=preferWebEngine)
        self.tabWafermapWidget = TabWafermapWidget(self.ui)
        self.tabContourWidget = TabContourWidget(self.ui, preferWebEngine=preferWebEngine)
        self.tabLogWidget = TabLogWidget(self.ui, preferWebEngine=preferWebEngine)
        self.tabControllersByObjectName = {
            'tabData': self.tabDataWidget,
            'tabScatter': self.tabScatterWidget,
            'tabBoxplot': self.tabBoxplotWidget,
            'tabWafermap': self.tabWafermapWidget,
            'tabContour': self.tabContourWidget,
            'logTab': self.tabLogWidget,
            'tabIdiot': self.tabIdiotWidget,
        }
        self.tabScatterWidget.set_tab_data(self.tabDataWidget)
        self.tabBoxplotWidget.set_tab_data(self.tabDataWidget)
        self.tabWafermapWidget.set_tab_data(self.tabDataWidget)
        self.tabContourWidget.set_tab_data(self.tabDataWidget)
        self.tabLogWidget.set_tab_data(self.tabDataWidget)
        self.tabWidget.currentChanged.connect(self._on_tab_changed)
        self.aboutButton.clicked.connect(self._show_about_dialog)
        self.tabWidget.setCurrentIndex(0)
        self._on_tab_changed(self.tabWidget.currentIndex())
        self._center_window()
        self.ui.show()
        self.app.processEvents()
        self.responsiveUiResizer = ResponsiveUiResizer(
            self.ui,
            {
                'plotAreaWidget': self.tabScatterWidget,
                'boxPlotAreaWidget': self.tabBoxplotWidget,
                'contourPlotAreaWidget': self.tabContourWidget,
                'logPlotAreaWidget': self.tabLogWidget,
            },
        )
        self.responsiveUiResizer.apply_all()
        self._show_new_version_notice()

    def _on_tab_changed(self, tabIndex: int) -> None:
        self._warn_if_plotting_loaded_data(tabIndex)
        currentWidget = self.tabWidget.widget(tabIndex)
        currentObjectName = currentWidget.objectName() if currentWidget is not None else ''
        for objectName, controller in self.tabControllersByObjectName.items():
            if hasattr(controller, 'set_active_tab'):
                controller.set_active_tab(objectName == currentObjectName)

    def _configure_application_metadata(self) -> None:
        self.app.setApplicationName(APP_NAME)
        self.app.setApplicationVersion(APP_VERSION)
        self.app.setOrganizationName(APP_AUTHOR)

    def _configure_application_icon(self) -> None:
        iconPath = resource_path(APP_ICON_FILENAME)
        if os.path.exists(iconPath):
            self.app.setWindowIcon(QIcon(iconPath))

    def _load_application_fonts(self) -> None:
        loadedFamilies = {}
        for fontFilename in ('CascadiaNextTC.wght.ttf', 'CascadiaCode.ttf'):
            fontPath = resource_path(fontFilename)
            if not os.path.exists(fontPath):
                continue

            fontId = QFontDatabase.addApplicationFont(fontPath)
            if fontId < 0:
                continue

            fontFamilies = QFontDatabase.applicationFontFamilies(fontId)
            if fontFamilies:
                loadedFamilies[fontFilename] = fontFamilies

        nextTcFamilies = loadedFamilies.get('CascadiaNextTC.wght.ttf', [])
        if nextTcFamilies:
            self.app.setFont(QFont(nextTcFamilies[0], 10))
        self.app.setStyleSheet(UI_FONT_STYLESHEET)

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
        if tabIndex not in [1, 2, 3, 4, 5]:
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
        elif tabIndex == 4:
            self.tabContourWidget._set_status(warningText, error=True)
        elif tabIndex == 5:
            self.tabLogWidget._set_status(warningText, error=True)
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
        latestBuild = normalize_build_number(self.launchInfo.get('build'))
        currentBuild = normalize_build_number(APP_DATE)
        if not is_newer_release(
            latestVersion,
            latestBuild,
            APP_VERSION,
            currentBuild,
        ):
            return

        releaseNote = str(self.launchInfo.get('release_note') or '').strip()
        latestRelease = latestVersion
        if latestBuild:
            latestRelease = f'{latestRelease}, build {latestBuild}'
        message = (
            f'目前版本: {APP_VERSION}, {APP_DATE}\n'
            f'發現新版本: {latestRelease}\n\n'
            r'請到 Z:\9630 下載最新版。'
        )
        if releaseNote:
            message = f'{message}\n\nRelease note:\n{releaseNote}'

        QMessageBox.information(
            self.ui,
            'New Version',
            message,
            QMessageBox.StandardButton.Ok,
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
