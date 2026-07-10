#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gc
import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tabData import TabDataWidget


def _write_fixture(path: Path, rowCount: int, columnCount: int) -> None:
    headers = [f'value{columnIndex}' for columnIndex in range(columnCount)]
    with path.open('w', newline='', encoding='utf-8') as csvFile:
        writer = csv.writer(csvFile)
        writer.writerow(headers)
        for rowIndex in range(rowCount):
            writer.writerow(
                rowIndex * (columnIndex + 1)
                if columnIndex % 3
                else f'lot-{rowIndex % 250}'
                for columnIndex in range(columnCount)
            )


def _measure_parser(path: Path, repeatCount: int) -> dict[str, object]:
    parser = TabDataWidget.__new__(TabDataWidget)
    elapsedValues = []
    peakValues = []
    resultShape = None

    for _ in range(repeatCount):
        gc.collect()
        tracemalloc.start()
        startedAt = time.perf_counter()
        dataFrame = parser._read_delimited_text_dataframe(
            str(path),
            skipRows=0,
            encoding='utf-8',
            delimiter=',',
        )
        elapsedValues.append(time.perf_counter() - startedAt)
        _, peakBytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peakValues.append(peakBytes)
        resultShape = list(dataFrame.shape)
        del dataFrame

    return {
        'elapsedSecondsMedian': statistics.median(elapsedValues),
        'peakBytesMedian': int(statistics.median(peakValues)),
        'peakMiBMedian': statistics.median(peakValues) / (1024 * 1024),
        'resultShape': resultShape,
        'elapsedSeconds': elapsedValues,
        'peakBytes': peakValues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Benchmark p-chart delimited-text parsing.')
    parser.add_argument('--rows', type=int, default=100_000)
    parser.add_argument('--columns', type=int, default=12)
    parser.add_argument('--repeat', type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rows <= 0 or args.columns <= 0 or args.repeat <= 0:
        raise ValueError('rows, columns, and repeat must be positive integers.')

    with tempfile.TemporaryDirectory(prefix='pchart-performance-') as tempDir:
        fixturePath = Path(tempDir) / 'parser-fixture.csv'
        _write_fixture(fixturePath, args.rows, args.columns)
        result = {
            'rows': args.rows,
            'columns': args.columns,
            'repeat': args.repeat,
            'parser': _measure_parser(fixturePath, args.repeat),
        }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
