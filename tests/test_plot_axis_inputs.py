from __future__ import annotations

import unittest

import pandas as pd
import plotly.graph_objects as go

from plot_axis_inputs import (
    MILLISECONDS_PER_DAY,
    AxisRange,
    ReferenceLine,
    minor_grid_options,
    parse_date_axis_range,
    parse_date_reference_lines,
    parse_numeric_axis_range,
    parse_numeric_reference_lines,
)
from tabBoxplot import TabBoxplotWidget
from tabScatter import TabScatterWidget


class ReferenceLineParserTests(unittest.TestCase):
    def test_numeric_lines_keep_bare_values_and_custom_labels(self) -> None:
        self.assertEqual(
            parse_numeric_reference_lines('100, LSL = 200,USL=300'),
            [
                ReferenceLine(100.0, None),
                ReferenceLine(200.0, 'LSL'),
                ReferenceLine(300.0, 'USL'),
            ],
        )

    def test_numeric_lines_ignore_empty_and_invalid_items(self) -> None:
        self.assertEqual(
            parse_numeric_reference_lines('bad,=100,label=,nan,ok=12.5'),
            [ReferenceLine(12.5, 'ok')],
        )

    def test_date_lines_support_bare_values_and_custom_labels(self) -> None:
        lines = parse_date_reference_lines('2026-01-01, Start = 2026-01-03')

        self.assertEqual([line.label for line in lines], [None, 'Start'])
        self.assertEqual(
            [pd.Timestamp(line.value) for line in lines],
            [pd.Timestamp('2026-01-01'), pd.Timestamp('2026-01-03')],
        )


class AxisRangeParserTests(unittest.TestCase):
    def test_numeric_range_supports_optional_minor_spacing(self) -> None:
        self.assertEqual(
            parse_numeric_axis_range('0,100,10'),
            AxisRange((0.0, 100.0), 10.0),
        )
        self.assertEqual(
            parse_numeric_axis_range('0,100'),
            AxisRange((0.0, 100.0), None),
        )

    def test_invalid_spacing_keeps_valid_numeric_range(self) -> None:
        for text in ['0,100,0', '0,100,-1', '0,100,bad', '0,100,']:
            with self.subTest(text=text):
                self.assertEqual(
                    parse_numeric_axis_range(text),
                    AxisRange((0.0, 100.0), None),
                )

    def test_invalid_range_uses_automatic_axis_range(self) -> None:
        for text in ['bad,100,10', '0,100,10,20', '0', '']:
            with self.subTest(text=text):
                self.assertIsNone(parse_numeric_axis_range(text))

    def test_date_range_converts_spacing_days_to_milliseconds(self) -> None:
        parsedRange = parse_date_axis_range('2026-01-01,2026-01-31,0.5')

        self.assertIsNotNone(parsedRange)
        self.assertEqual(
            [pd.Timestamp(value) for value in parsedRange.values],
            [pd.Timestamp('2026-01-01'), pd.Timestamp('2026-01-31')],
        )
        self.assertEqual(parsedRange.minorDtick, 0.5 * MILLISECONDS_PER_DAY)

    def test_invalid_date_spacing_keeps_valid_date_range(self) -> None:
        parsedRange = parse_date_axis_range('2026-01-01,2026-01-31,bad')

        self.assertIsNotNone(parsedRange)
        self.assertIsNone(parsedRange.minorDtick)

    def test_minor_grid_options_are_plotly_compatible(self) -> None:
        parsedRange = parse_numeric_axis_range('5,25,10')
        figure = go.Figure()
        figure.update_xaxes(minor=minor_grid_options(parsedRange))

        self.assertTrue(figure.layout.xaxis.minor.showgrid)
        self.assertEqual(figure.layout.xaxis.minor.dtick, 10.0)
        self.assertEqual(figure.layout.xaxis.minor.tick0, 5.0)


class ReferenceLineFigureTests(unittest.TestCase):
    def test_boxplot_annotations_keep_legacy_and_custom_labels(self) -> None:
        widget = TabBoxplotWidget.__new__(TabBoxplotWidget)
        figure = go.Figure()

        widget._add_reference_lines(
            figure,
            parse_numeric_reference_lines('100,USL=200'),
            'Thickness',
            '#ff0000',
            1.0,
        )

        self.assertEqual(
            [annotation.text for annotation in figure.layout.annotations],
            ['Thickness=100', 'USL'],
        )

    def test_controller_value_wrappers_ignore_custom_labels(self) -> None:
        scatterWidget = TabScatterWidget.__new__(TabScatterWidget)
        boxplotWidget = TabBoxplotWidget.__new__(TabBoxplotWidget)

        self.assertEqual(
            scatterWidget._parse_line_values('LSL=100,Target=150,USL=200'),
            [100.0, 150.0, 200.0],
        )
        self.assertEqual(
            boxplotWidget._parse_line_values('LSL=100,Target=150,USL=200'),
            [100.0, 150.0, 200.0],
        )


if __name__ == '__main__':
    unittest.main()
