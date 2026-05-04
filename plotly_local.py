import os
import sys
from pathlib import Path

import plotly.io as pio


PLOTLY_JS_FILENAME = 'plotly.min.js'
PLOTLY_FONT_FILENAME = 'CascadiaNextTC.wght.ttf'
PLOTLY_FONT_FAMILY = 'Cascadia Next TC'
PLOTLY_FONT_STACK = f'"{PLOTLY_FONT_FAMILY}", Arial, sans-serif'


def resource_path(filename: str) -> str:
    basePath = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
    return os.path.join(basePath, filename)


def local_plotly_html(figure, fullHtml: bool) -> str:
    assetsDir = Path(__file__).resolve().parent
    plotlyJsName = PLOTLY_JS_FILENAME
    plotlyJSPath = assetsDir / plotlyJsName
    if not plotlyJSPath.exists():
        raise FileNotFoundError(f'Plotly JS file not found: {plotlyJSPath}')


    html = pio.to_html(figure, full_html=fullHtml, include_plotlyjs=plotlyJsName)
    htmlHeader = '\n'.join(
        [
            _font_style_html(),
            f'<script src="{Path(resource_path(PLOTLY_JS_FILENAME)).resolve().as_uri()}"></script>',
        ]
    )
    if fullHtml:
        return html.replace('</head>', f'{htmlHeader}\n</head>')
    return f'{htmlHeader}\n{html}'


def _font_style_html() -> str:
    fontPath = resource_path(PLOTLY_FONT_FILENAME)
    if not os.path.exists(fontPath):
        return ''

    fontUri = Path(fontPath).resolve().as_uri()
    return f'''<style>
@font-face {{
  font-family: "{PLOTLY_FONT_FAMILY}";
  src: url("{fontUri}") format("truetype");
  font-weight: 100 900;
}}
body, .plotly, .js-plotly-plot {{
  font-family: {PLOTLY_FONT_STACK};
}}
</style>'''
