#!/usr/bin/env python3
from __future__ import annotations

import ast
import csv
import importlib.metadata
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL_PY = [
    'app.py',
    'tabData.py',
    'tabScatter.py',
    'tabBoxplot.py',
    'tabWafermap.py',
    'tabContour.py',
    'tabLog.py',
    'tabIdiot.py',
    'table_clipboard_helpers.py',
    'pivot_helpers.py',
    'qt_helpers.py',
    'plot_templates.py',
    'update_helpers.py',
]
UI_FILES = ['mainwindow-win.ui', 'mainwindow-mac.ui']
EMBEDDED_FONT_FILES = ['CascadiaNextTC.wght.ttf', 'CascadiaCode.ttf']
DEMO_FILES = [
    'demo/06191623.xlsx',
    'demo/demo_contour_1.csv',
    'demo/demo_contour_3.csv',
]
QT_PACKAGES = [
    'PySide6',
    'PySide6_Addons',
    'PySide6_Essentials',
    'shiboken6',
]


class ReleaseCheck:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f'[OK] {message}')

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f'[WARN] {message}')

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f'[FAIL] {message}')

    def run(self) -> int:
        checks = [
            self.check_python_arch,
            self.check_dependency_health,
            self.check_qt_versions,
            self.check_qt_import_smoke,
            self.check_py_compile,
            self.check_embedded_fonts,
            self.check_ui_xml,
            self.check_ui_object_contract,
            self.check_version_text,
            self.check_manual_update_notice,
            self.check_demo_data,
        ]
        for check in checks:
            try:
                check()
            except Exception as exc:
                checkName = check.__name__.removeprefix('check_').replace('_', ' ')
                self.fail(f'{checkName} check crashed: {exc}')

        print()
        if self.warnings:
            print(f'Warnings: {len(self.warnings)}')
        if self.failures:
            print(f'Failures: {len(self.failures)}')
            return 1
        print('Release check passed.')
        return 0

    def check_dependency_health(self) -> None:
        result = self.run_command([sys.executable, '-m', 'pip', 'check'])
        if result.returncode == 0:
            self.ok('pip check: no broken requirements')
            return

        output = (result.stdout or result.stderr or '').strip()
        self.fail(f'pip check failed: {output or "unknown dependency error"}')

    def check_python_arch(self) -> None:
        executable = Path(sys.executable)
        machine = platform.machine()
        if machine != 'arm64' and sys.platform == 'darwin':
            self.fail(f'Python architecture is {machine}, expected arm64 on macOS.')
        else:
            self.ok(f'Python architecture: {machine}')

        expectedVenv = (ROOT / '.venv').resolve()
        activePrefix = Path(sys.prefix).resolve()
        if activePrefix != expectedVenv:
            self.warn(f'Python is not from repo .venv: {executable} (prefix={activePrefix})')
        else:
            self.ok(f'Python executable: {executable}')

    def check_qt_versions(self) -> None:
        versions = {}
        for packageName in QT_PACKAGES:
            try:
                versions[packageName] = importlib.metadata.version(packageName)
            except importlib.metadata.PackageNotFoundError:
                self.fail(f'Missing package: {packageName}')
        if not versions:
            return

        uniqueVersions = set(versions.values())
        if len(uniqueVersions) == 1:
            version = next(iter(uniqueVersions))
            self.ok(f'Qt/PySide package versions match: {version}')
        else:
            self.fail(f'Qt/PySide package versions differ: {versions}')

    def check_qt_import_smoke(self) -> None:
        command = [
            sys.executable,
            '-c',
            (
                'from PySide6.QtCore import qVersion; '
                'from PySide6.QtWebEngineWidgets import QWebEngineView; '
                'print(qVersion()); print(QWebEngineView.__name__)'
            ),
        ]
        result = self.run_command(command, timeout=20)
        if result.returncode == 0:
            self.ok('PySide6 QtCore + QtWebEngine import smoke')
            return

        output = (result.stderr or result.stdout or '').strip()
        self.fail(f'PySide6 import smoke failed with code {result.returncode}: {output}')

    def check_py_compile(self) -> None:
        files = [str(ROOT / fileName) for fileName in TOP_LEVEL_PY if (ROOT / fileName).exists()]
        files.extend(str(path) for path in sorted((ROOT / 'scripts').glob('*.py')))
        result = self.run_command([sys.executable, '-m', 'py_compile', *files])
        if result.returncode == 0:
            self.ok(f'py_compile: {len(files)} files')
        else:
            self.fail(result.stderr.strip() or result.stdout.strip() or 'py_compile failed')

    def check_embedded_fonts(self) -> None:
        appText = (ROOT / 'app.py').read_text(encoding='utf-8')
        specText = (ROOT / 'p-chart.spec').read_text(encoding='utf-8')
        failures = []
        for fontFilename in EMBEDDED_FONT_FILES:
            if not (ROOT / fontFilename).is_file():
                failures.append(f'missing file {fontFilename}')
            if fontFilename not in appText:
                failures.append(f'app.py does not load {fontFilename}')
            if fontFilename not in specText:
                failures.append(f'p-chart.spec does not bundle {fontFilename}')

        if failures:
            self.fail(f'Embedded font contract: {"; ".join(failures)}')
        else:
            self.ok(f'Embedded fonts: {", ".join(EMBEDDED_FONT_FILES)}')

    def check_ui_xml(self) -> None:
        for uiFile in UI_FILES:
            path = ROOT / uiFile
            result = self.run_command(['xmllint', '--noout', str(path)])
            if result.returncode == 0:
                self.ok(f'xmllint: {uiFile}')
            else:
                self.fail(result.stderr.strip() or f'xmllint failed: {uiFile}')

    def check_ui_object_contract(self) -> None:
        requiredNames = self.required_ui_object_names()
        for uiFile in UI_FILES:
            uiText = (ROOT / uiFile).read_text(encoding='utf-8')
            uiNames = set(re.findall(r'name="([^"]+)"', uiText))
            missing = sorted(requiredNames - uiNames)
            if missing:
                self.fail(f'{uiFile} missing objectName(s): {", ".join(missing)}')
            else:
                self.ok(f'{uiFile} objectName contract: {len(requiredNames)} required')

    def required_ui_object_names(self) -> set[str]:
        requiredNames = set()
        for path in ROOT.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, 'id', '') != 'require_child':
                    continue
                if len(node.args) < 3:
                    continue
                objectNameNode = node.args[2]
                if isinstance(objectNameNode, ast.Constant) and isinstance(objectNameNode.value, str):
                    requiredNames.add(objectNameNode.value)
        return requiredNames

    def check_version_text(self) -> None:
        appText = (ROOT / 'app.py').read_text(encoding='utf-8')
        readmeText = (ROOT / 'README.md').read_text(encoding='utf-8')
        appVersion = self.regex_value(appText, r"APP_VERSION = '([^']+)'")
        appDate = self.regex_value(appText, r"APP_DATE = '([^']+)'")
        readmeVersion = self.regex_value(readmeText, r'Version: `([^`]+)`')

        if not appVersion or not appDate:
            self.fail('APP_VERSION or APP_DATE missing in app.py')
            return
        if appVersion != readmeVersion:
            self.fail(f'README version {readmeVersion} does not match app.py {appVersion}')
            return
        self.ok(f'Version text: {appVersion}, {appDate}')

    def check_manual_update_notice(self) -> None:
        appText = (ROOT / 'app.py').read_text(encoding='utf-8')
        forbiddenNames = [
            'copy_update_files',
            'stage_update_files',
            'start_windows_update_after_exit',
            'UPDATE_SOURCE_DIRECTORY',
            'QProgressDialog',
        ]
        foundNames = [name for name in forbiddenNames if name in appText]
        if foundNames:
            self.fail(
                'Automatic update code remains in app.py: '
                f'{", ".join(foundNames)}'
            )
            return
        if r'請到 Z:\9630 下載最新版。' not in appText:
            self.fail('New-version notice does not direct users to Z:\\9630')
            return
        self.ok('New-version check is notification-only; automatic copy is disabled')

    def check_demo_data(self) -> None:
        import pandas as pd

        for relativePath in DEMO_FILES:
            path = ROOT / relativePath
            if not path.exists():
                self.fail(f'Missing demo file: {relativePath}')
                continue

        xlsxPath = ROOT / 'demo' / '06191623.xlsx'
        if xlsxPath.exists():
            with pd.ExcelFile(xlsxPath) as excelReader:
                if 'idiotData' not in excelReader.sheet_names:
                    self.fail('demo/06191623.xlsx missing idiotData sheet')
                else:
                    dataFrame = pd.read_excel(excelReader, sheet_name='idiotData')
                    self.require_columns('demo/06191623.xlsx', dataFrame, {'x', 'y', 'value'})

        for csvPath in sorted((ROOT / 'demo').glob('demo_contour_*.csv')):
            dataFrame = pd.read_csv(csvPath)
            self.check_contour_demo_csv(csvPath, dataFrame)

        kmDataFiles = sorted((ROOT / 'demo').glob('kmdata2*.csv'))
        if not kmDataFiles:
            self.fail('Missing demo/kmdata2*.csv')
            return
        kmDataPath = kmDataFiles[0]
        headerRow = self.detect_csv_header_row(kmDataPath)
        dataFrame = pd.read_csv(kmDataPath, skiprows=headerRow)
        if dataFrame.empty:
            self.fail(f'{kmDataPath.name} loaded empty with skiprows={headerRow}')
        else:
            self.ok(f'{kmDataPath.name}: rows={len(dataFrame)}, cols={len(dataFrame.columns)}, skiprows={headerRow}')

    def require_columns(self, label: str, dataFrame, columns: set[str]) -> None:
        missing = columns - set(str(column) for column in dataFrame.columns)
        if missing:
            self.fail(f'{label} missing columns: {", ".join(sorted(missing))}')
        elif dataFrame.empty:
            self.fail(f'{label} loaded empty')
        else:
            self.ok(f'{label}: rows={len(dataFrame)}, cols={len(dataFrame.columns)}')

    def check_contour_demo_csv(self, path: Path, dataFrame) -> None:
        import pandas as pd

        label = str(path.relative_to(ROOT))
        if dataFrame.empty:
            self.fail(f'{label} loaded empty')
            return
        if 'value' not in dataFrame.columns:
            self.fail(f'{label} missing value column')
            return
        usableCount = int(pd.to_numeric(dataFrame['value'], errors='coerce').notna().sum())
        if int(usableCount) == 0:
            self.fail(f'{label} has no numeric value data')
            return
        if path.name == 'demo_contour_3.csv' and 'waferID' not in dataFrame.columns:
            self.fail(f'{label} missing waferID column')
            return
        self.ok(f'{label}: rows={len(dataFrame)}, cols={len(dataFrame.columns)}, numeric value rows={usableCount}')

    def detect_csv_header_row(self, path: Path) -> int:
        with path.open(newline='', encoding='utf-8-sig') as csvFile:
            rows = []
            reader = csv.reader(csvFile)
            for rowIndex, row in enumerate(reader):
                if rowIndex >= 30:
                    break
                rows.append([cell.strip() for cell in row])

        bestIndex = 0
        bestScore = -1
        for rowIndex, row in enumerate(rows):
            nonEmpty = [cell for cell in row if cell]
            score = len(nonEmpty)
            score += sum(1 for cell in nonEmpty if not self.looks_numeric(cell))
            score -= rowIndex * 0.1
            if score > bestScore:
                bestScore = score
                bestIndex = rowIndex
        return bestIndex

    def looks_numeric(self, value: str) -> bool:
        try:
            float(value.replace(',', ''))
            return True
        except ValueError:
            return False

    def regex_value(self, text: str, pattern: str) -> str:
        match = re.search(pattern, text)
        return match.group(1) if match else ''

    def run_command(self, command: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(command, 127, '', str(exc))
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(command, 124, exc.stdout or '', exc.stderr or 'timeout')


def main() -> int:
    return ReleaseCheck().run()


if __name__ == '__main__':
    raise SystemExit(main())
