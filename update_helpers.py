from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

RELEASE_MANIFEST_FILENAME = 'p-chart-release.json'


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


def normalize_build_number(buildText: object) -> str:
    buildNumbers = re.findall(r'\d+', str(buildText or ''))
    return buildNumbers[-1].zfill(4) if buildNumbers else ''


def is_newer_release(
    candidateVersion: str,
    candidateBuild: str,
    currentVersion: str,
    currentBuild: str,
) -> bool:
    if is_newer_version(candidateVersion, currentVersion):
        return True
    if is_newer_version(currentVersion, candidateVersion):
        return False
    return is_newer_version(
        normalize_build_number(candidateBuild),
        normalize_build_number(currentBuild),
    )


def resolve_update_package_directory(
    sourceDirectory: Path,
    executableName: str,
) -> Path:
    if not sourceDirectory.is_dir():
        raise FileNotFoundError(f'Update source directory not found: {sourceDirectory}')
    if not executableName:
        raise ValueError('The update executable name is empty.')

    if (sourceDirectory / executableName).is_file():
        return sourceDirectory

    nestedDirectories = sorted(
        (
            path
            for path in sourceDirectory.iterdir()
            if path.is_dir() and (path / executableName).is_file()
        ),
        key=lambda path: path.name.lower(),
    )
    if len(nestedDirectories) == 1:
        return nestedDirectories[0]
    if len(nestedDirectories) > 1:
        nestedText = ', '.join(str(path) for path in nestedDirectories)
        raise RuntimeError(
            f'Multiple update packages contain {executableName}: {nestedText}'
        )
    raise FileNotFoundError(
        f'Update package does not contain {executableName}: {sourceDirectory}'
    )


def read_package_release(packageDirectory: Path) -> dict[str, str] | None:
    manifestPaths = [
        packageDirectory / RELEASE_MANIFEST_FILENAME,
        packageDirectory / '_internal' / RELEASE_MANIFEST_FILENAME,
    ]
    manifestPath = next((path for path in manifestPaths if path.is_file()), None)
    if manifestPath is None:
        return None
    try:
        releaseInfo = json.loads(manifestPath.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(releaseInfo, dict):
        return None

    version = str(releaseInfo.get('version') or '')
    build = normalize_build_number(releaseInfo.get('build'))
    if not version or not build:
        return None
    return {'version': version, 'build': build}


def update_result_marker_path(executablePath: Path) -> Path:
    executableKey = os.path.normcase(str(executablePath.resolve())).encode('utf-8')
    executableHash = hashlib.sha256(executableKey).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f'p-chart-update-result-{executableHash}.json'


def write_update_result_marker(
    executablePath: Path,
    expectedVersion: str,
    expectedBuild: str,
    sourceDirectory: Path,
) -> Path:
    markerPath = update_result_marker_path(executablePath)
    markerInfo = {
        'expected_version': str(expectedVersion),
        'expected_build': normalize_build_number(expectedBuild),
        'source_directory': str(sourceDirectory),
    }
    markerPath.write_text(
        json.dumps(markerInfo, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return markerPath


def read_update_result_marker(executablePath: Path) -> dict[str, str] | None:
    markerPath = update_result_marker_path(executablePath)
    if not markerPath.is_file():
        return None
    try:
        markerInfo = json.loads(markerPath.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(markerInfo, dict):
        return None
    return {
        'expected_version': str(markerInfo.get('expected_version') or ''),
        'expected_build': normalize_build_number(markerInfo.get('expected_build')),
        'source_directory': str(markerInfo.get('source_directory') or ''),
    }


def clear_update_result_marker(executablePath: Path) -> None:
    update_result_marker_path(executablePath).unlink(missing_ok=True)


def update_target_paths(sourceDirectory: Path, targetDirectory: Path) -> list[Path]:
    targetPaths = []
    for sourcePath in sourceDirectory.rglob('*'):
        relativePath = sourcePath.relative_to(sourceDirectory)
        targetPath = targetDirectory / relativePath
        if targetPath.exists():
            targetPaths.append(targetPath)
    return targetPaths


UpdateProgressCallback = Callable[[int, int, str], None]


def copy_update_files(
    sourceDirectory: Path,
    targetDirectory: Path,
    progressCallback: UpdateProgressCallback | None = None,
) -> None:
    sourcePaths = sorted(sourceDirectory.iterdir(), key=lambda path: path.name.lower())
    totalPaths = len(sourcePaths)
    for pathIndex, sourcePath in enumerate(sourcePaths, start=1):
        if progressCallback is not None:
            progressCallback(pathIndex, totalPaths, sourcePath.name)
        targetPath = targetDirectory / sourcePath.name
        if sourcePath.is_dir():
            shutil.copytree(sourcePath, targetPath, dirs_exist_ok=True)
        else:
            shutil.copy2(sourcePath, targetPath)


def stage_update_files(
    sourceDirectory: Path,
    progressCallback: UpdateProgressCallback | None = None,
) -> Path:
    stageDirectory = Path(tempfile.mkdtemp(prefix='p-chart-update-'))
    try:
        copy_update_files(sourceDirectory, stageDirectory, progressCallback)
    except Exception:
        shutil.rmtree(stageDirectory, ignore_errors=True)
        raise
    return stageDirectory


def _batch_literal(value: object) -> str:
    return str(value).replace('%', '%%')


def windows_update_script(
    stageDirectory: Path,
    targetDirectory: Path,
    executablePath: Path,
    processId: int,
) -> str:
    return f'''@echo off
setlocal
set "UPDATE_SOURCE={_batch_literal(stageDirectory)}"
set "UPDATE_TARGET={_batch_literal(targetDirectory)}"
set "UPDATE_EXE={_batch_literal(executablePath)}"
set "UPDATE_PID={processId}"

:wait_for_app
tasklist /FI "PID eq %UPDATE_PID%" /NH 2>nul | findstr /R /C:"[ ]%UPDATE_PID%[ ]" >nul
if not errorlevel 1 (
    ping 127.0.0.1 -n 2 >nul
    goto wait_for_app
)

ping 127.0.0.1 -n 3 >nul
robocopy "%UPDATE_SOURCE%" "%UPDATE_TARGET%" /E /COPY:DAT /DCOPY:DAT /R:10 /W:2
set "UPDATE_RESULT=%ERRORLEVEL%"
if %UPDATE_RESULT% GEQ 8 goto update_failed

rmdir /S /Q "%UPDATE_SOURCE%"
start "" "%UPDATE_EXE%"
del "%~f0"
exit /b 0

:update_failed
set "UPDATE_LOG=%TEMP%\\p-chart-update-error.txt"
> "%UPDATE_LOG%" echo p-chart update failed. Robocopy exit code: %UPDATE_RESULT%
>> "%UPDATE_LOG%" echo Source: %UPDATE_SOURCE%
>> "%UPDATE_LOG%" echo Target: %UPDATE_TARGET%
start "" notepad.exe "%UPDATE_LOG%"
del "%~f0"
exit /b %UPDATE_RESULT%
'''


def start_windows_update_after_exit(
    stageDirectory: Path,
    targetDirectory: Path,
    executablePath: Path,
    processId: int | None = None,
) -> Path:
    if not sys.platform.startswith('win'):
        raise RuntimeError('The after-exit updater is available on Windows only.')

    updaterPath = Path(tempfile.gettempdir()) / f'p-chart-updater-{uuid.uuid4().hex}.cmd'
    updaterPath.write_text(
        windows_update_script(
            stageDirectory,
            targetDirectory,
            executablePath,
            processId or os.getpid(),
        ),
        encoding='mbcs',
    )
    creationFlags = (
        getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        | getattr(subprocess, 'DETACHED_PROCESS', 0)
        | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    )
    subprocess.Popen(
        [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(updaterPath)],
        close_fds=True,
        creationflags=creationFlags,
    )
    return updaterPath
