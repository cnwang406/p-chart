from __future__ import annotations

import re


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
