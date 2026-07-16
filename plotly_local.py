import os
import sys
from pathlib import Path

import plotly.io as pio


PLOTLY_JS_FILENAME = 'plotly.min.js'
PLOTLY_FONT_FILENAME = 'CascadiaNextTC.wght.ttf'
PLOTLY_FONT_FAMILY = 'Cascadia Next TC'
PLOTLY_FONT_STACK = f'"{PLOTLY_FONT_FAMILY}", Arial, sans-serif'
PINNED_HOVER_ANNOTATION_NAME = 'pchart-pinned-hover'


def resource_path(filename: str) -> str:
    basePath = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
    return os.path.join(basePath, filename)


def local_plotly_html(figure, fullHtml: bool) -> str:
    assetsDir = Path(__file__).resolve().parent
    plotlyJsName = PLOTLY_JS_FILENAME
    plotlyJSPath = assetsDir / plotlyJsName
    if not plotlyJSPath.exists():
        raise FileNotFoundError(f'Plotly JS file not found: {plotlyJSPath}')


    html = pio.to_html(
        figure,
        full_html=fullHtml,
        include_plotlyjs=False,
        post_script=_pinned_hover_annotation_script(),
    )
    htmlHeader = '\n'.join(
        [
            _font_style_html(),
            f'<script src="{Path(resource_path(PLOTLY_JS_FILENAME)).resolve().as_uri()}"></script>',
        ]
    )
    if fullHtml:
        return html.replace('</head>', f'{htmlHeader}\n</head>')
    return f'{htmlHeader}\n{html}'


def _pinned_hover_annotation_script() -> str:
    return f'''
(function() {{
  const plot = document.getElementById('{{plot_id}}');
  if (!plot || plot.__pchartPinnedHoverEnabled) {{
    return;
  }}
  plot.__pchartPinnedHoverEnabled = true;

  const annotationName = {PINNED_HOVER_ANNOTATION_NAME!r};

  function escapeHtml(value) {{
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }}

  function formatValue(value) {{
    if (value === undefined || value === null) {{
      return '';
    }}
    if (typeof value === 'number' && Number.isFinite(value)) {{
      return value.toLocaleString(undefined, {{ maximumSignificantDigits: 8 }});
    }}
    return String(value);
  }}

  function pointAnnotationText(point) {{
    const lines = [];
    const traceName = point.fullData && point.fullData.name;
    if (traceName) {{
      lines.push('<b>' + escapeHtml(traceName) + '</b>');
    }}
    if (point.x !== undefined) {{
      lines.push('x: ' + escapeHtml(formatValue(point.x)));
    }}
    if (point.y !== undefined) {{
      lines.push('y: ' + escapeHtml(formatValue(point.y)));
    }}
    if (point.z !== undefined) {{
      lines.push('z: ' + escapeHtml(formatValue(point.z)));
    }}
    return lines.join('<br>');
  }}

  plot.on('plotly_click', function(eventData) {{
    const point = eventData && eventData.points && eventData.points[0];
    if (!point || point.x === undefined || point.y === undefined) {{
      return;
    }}

    const fullLayout = plot._fullLayout || {{}};
    const layoutFont = fullLayout.font || {{}};
    const annotations = (plot.layout.annotations || []).slice();
    annotations.push({{
      name: annotationName,
      x: point.x,
      y: point.y,
      xref: point.xaxis && point.xaxis._id ? point.xaxis._id : 'x',
      yref: point.yaxis && point.yaxis._id ? point.yaxis._id : 'y',
      text: pointAnnotationText(point),
      showarrow: true,
      arrowhead: 2,
      ax: 30,
      ay: -35,
      bgcolor: fullLayout.paper_bgcolor || 'rgba(255,255,255,0.92)',
      bordercolor: layoutFont.color || '#2a3f5f',
      borderwidth: 1,
      borderpad: 4,
      font: {{ color: layoutFont.color || '#2a3f5f' }},
      captureevents: true
    }});
    Plotly.relayout(plot, {{ annotations: annotations }});
  }});

  plot.on('plotly_clickannotation', function(eventData) {{
    const annotations = (plot.layout.annotations || []).slice();
    const annotationIndex = eventData && eventData.index;
    if (
      !Number.isInteger(annotationIndex) ||
      !annotations[annotationIndex] ||
      annotations[annotationIndex].name !== annotationName
    ) {{
      return;
    }}
    annotations.splice(annotationIndex, 1);
    Plotly.relayout(plot, {{ annotations: annotations }});
  }});
}})();
'''


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
