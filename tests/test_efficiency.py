from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import plotly.graph_objects as go

from plotly_local import local_plotly_html
from tabData import TabDataWidget
from tabLog import TabLogWidget


class DelimitedTextParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TabDataWidget.__new__(TabDataWidget)

    def test_trims_cells_drops_blank_rows_and_infers_numbers(self) -> None:
        dataFrame = self.parser._read_delimited_text_dataframe_from_lines(
            [
                'name,value,count,\n',
                ' A , 1.5 , 2 ,\n',
                'B,3.0,4,\n',
                ' , , ,\n',
            ],
            ',',
        )

        self.assertEqual(list(dataFrame.columns), ['name', 'value', 'count'])
        self.assertEqual(dataFrame.shape, (2, 3))
        self.assertEqual(dataFrame['name'].tolist(), ['A', 'B'])
        self.assertEqual(dataFrame['value'].tolist(), [1.5, 3.0])
        self.assertEqual(dataFrame['count'].tolist(), [2, 4])

    def test_handles_tab_bom_and_leading_blank_column(self) -> None:
        dataFrame = self.parser._read_delimited_text_dataframe_from_lines(
            ['\ufeff\tx\ty\n', '\t1\t2\n', '\t3\t4\n'],
            '\t',
        )

        self.assertEqual(list(dataFrame.columns), ['x', 'y'])
        self.assertEqual(dataFrame.to_dict(orient='list'), {'x': [1, 3], 'y': [2, 4]})

    def test_preserves_irregular_extra_columns(self) -> None:
        dataFrame = self.parser._read_delimited_text_dataframe_from_lines(
            ['a,b\n', '1,2,extra\n', '3,4\n'],
            ',',
        )

        self.assertEqual(list(dataFrame.columns), ['a', 'b', 'raw1'])
        self.assertEqual(dataFrame['raw1'].tolist(), ['extra', ''])

    def test_detects_cp950_and_invalidates_file_cache_after_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix='pchart-parser-test-') as tempDir:
            filePath = Path(tempDir) / 'data.csv'
            filePath.write_bytes('批號,數值\nA,1\n'.encode('cp950'))
            firstOptions = self.parser._detect_csv_read_options(str(filePath))
            cachedOptions = self.parser._detect_csv_read_options(str(filePath))
            self.assertEqual(firstOptions, cachedOptions)
            self.assertEqual(firstOptions['delimiter'], ',')

            filePath.write_bytes('\ufeff批號\t數值\nA\t1\n'.encode('utf-8'))
            changedOptions = self.parser._detect_csv_read_options(str(filePath))
            self.assertEqual(changedOptions['encoding'], 'utf-8-sig')
            self.assertEqual(changedOptions['delimiter'], '\t')


class LogPreparedDataCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logWidget = TabLogWidget.__new__(TabLogWidget)
        self.logWidget._externalPreparedDataCache = {}
        self.logWidget._externalPreparedDataCacheMaxBytes = 256 * 1024 * 1024
        self.signature = ('/tmp/log.csv', 100, 1)

        class FakeTabData:
            def __init__(fakeSelf, owner) -> None:
                fakeSelf.owner = owner

            def _file_signature(fakeSelf, _filePath):
                return fakeSelf.owner.signature

        self.logWidget.tabDataWidget = FakeTabData(self)
        self.readCount = 0

        def read_external_data(_filePath, _skipRows):
            self.readCount += 1
            return pd.DataFrame({'x': [1, 2], 'y': [3, 4], 'z': [5, 6]})

        self.logWidget._read_external_data = read_external_data

    def _load(self, skipRows: int = 0, columns: list[str] | None = None):
        requiredColumns = columns or ['x', 'y']
        return self.logWidget._cached_external_log_source(
            'log.csv',
            '/tmp/log.csv',
            skipRows,
            requiredColumns,
            'x',
            [requiredColumns[1]],
            [],
        )

    def test_reuses_same_file_skiprows_and_columns(self) -> None:
        firstSource, firstWarning = self._load()
        secondSource, secondWarning = self._load()

        self.assertEqual(firstWarning, '')
        self.assertEqual(secondWarning, '')
        self.assertEqual(self.readCount, 1)
        self.assertIs(firstSource['dataFrame'], secondSource['dataFrame'])

    def test_invalidates_for_file_skiprows_or_column_changes(self) -> None:
        self._load()
        self.signature = ('/tmp/log.csv', 101, 2)
        self._load()
        self._load(skipRows=1)
        self._load(columns=['x', 'z'])

        self.assertEqual(self.readCount, 4)


class PlotlyHtmlTests(unittest.TestCase):
    def test_serializes_once_and_injects_one_local_loader(self) -> None:
        figure = go.Figure(go.Scatter(x=[1, 2], y=[3, 4]))
        with patch('plotly_local.pio.to_html', return_value='<html><head></head><body></body></html>') as toHtml:
            html = local_plotly_html(figure, fullHtml=True)

        toHtml.assert_called_once_with(
            figure,
            full_html=True,
            include_plotlyjs=False,
        )
        self.assertEqual(html.count('<script src='), 1)
        self.assertEqual(html.count('plotly.min.js'), 1)


if __name__ == '__main__':
    unittest.main()
