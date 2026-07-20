from __future__ import annotations

import math
from typing import NamedTuple

import pandas as pd


MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000


class ReferenceLine(NamedTuple):
    value: object
    label: str | None


class AxisRange(NamedTuple):
    values: tuple[object, object]
    minorDtick: float | None


def parse_numeric_reference_lines(text: str) -> list[ReferenceLine]:
    return _parse_reference_lines(text, _parse_finite_float)


def parse_date_reference_lines(text: str) -> list[ReferenceLine]:
    return _parse_reference_lines(text, _parse_date)


def parse_numeric_axis_range(text: str) -> AxisRange | None:
    return _parse_axis_range(text, _parse_finite_float, spacingMultiplier=1.0)


def parse_date_axis_range(text: str) -> AxisRange | None:
    return _parse_axis_range(
        text,
        _parse_date,
        spacingMultiplier=MILLISECONDS_PER_DAY,
    )


def minor_grid_options(axisRange: AxisRange | None) -> dict | None:
    if axisRange is None or axisRange.minorDtick is None:
        return None
    return {
        'showgrid': True,
        'dtick': axisRange.minorDtick,
        'tick0': axisRange.values[0],
    }


def _parse_reference_lines(text: str, valueParser) -> list[ReferenceLine]:
    if not text:
        return []

    lines = []
    for item in text.split(','):
        item = item.strip()
        if not item:
            continue

        label = None
        valueText = item
        if '=' in item:
            labelText, valueText = item.split('=', 1)
            label = labelText.strip()
            valueText = valueText.strip()
            if not label or not valueText:
                continue

        parsedValue = valueParser(valueText)
        if parsedValue is not None:
            lines.append(ReferenceLine(parsedValue, label))
    return lines


def _parse_axis_range(
    text: str,
    valueParser,
    spacingMultiplier: float,
) -> AxisRange | None:
    if not text:
        return None

    rangeParts = [part.strip() for part in text.split(',')]
    if len(rangeParts) not in (2, 3) or not all(rangeParts[:2]):
        return None

    fromValue = valueParser(rangeParts[0])
    toValue = valueParser(rangeParts[1])
    if fromValue is None or toValue is None:
        return None

    minorDtick = None
    if len(rangeParts) == 3 and rangeParts[2]:
        spacing = _parse_finite_float(rangeParts[2])
        if spacing is not None and spacing > 0:
            minorDtick = spacing * spacingMultiplier

    return AxisRange((fromValue, toValue), minorDtick)


def _parse_finite_float(text: str) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _parse_date(text: str):
    parsedDate = pd.to_datetime(text, errors='coerce')
    if pd.isna(parsedDate):
        return None
    return parsedDate.to_pydatetime()
