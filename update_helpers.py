from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid


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
