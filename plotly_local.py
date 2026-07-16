import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import plotly.io as pio
from plotly.utils import PlotlyJSONEncoder


PLOTLY_JS_FILENAME = 'plotly.min.js'
PLOTLY_FONT_FILENAME = 'CascadiaNextTC.wght.ttf'
PLOTLY_FONT_FAMILY = 'Cascadia Next TC'
PLOTLY_FONT_STACK = f'"{PLOTLY_FONT_FAMILY}", Arial, sans-serif'
PINNED_HOVER_ANNOTATION_NAME = 'pchart-pinned-hover'


def resource_path(filename: str) -> str:
    basePath = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
    return os.path.join(basePath, filename)


def local_plotly_html(
    figure,
    fullHtml: bool,
    annotationNamespace: str = 'plot',
    annotationTextMode: str = 'hover',
    clearPinnedAnnotations: bool = False,
) -> str:
    assetsDir = Path(__file__).resolve().parent
    plotlyJsName = PLOTLY_JS_FILENAME
    plotlyJSPath = assetsDir / plotlyJsName
    if not plotlyJSPath.exists():
        raise FileNotFoundError(f'Plotly JS file not found: {plotlyJSPath}')

    annotationStateKey = _annotation_state_key(figure, annotationNamespace)
    clearAnnotationsToken = uuid.uuid4().hex if clearPinnedAnnotations else ''
    html = pio.to_html(
        figure,
        full_html=fullHtml,
        include_plotlyjs=False,
        post_script=_pinned_hover_annotation_script(
            annotationStateKey,
            annotationTextMode,
            clearAnnotationsToken,
        ),
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


def _annotation_state_key(figure, annotationNamespace: str) -> str:
    figureJson = (
        figure.to_plotly_json()
        if hasattr(figure, 'to_plotly_json')
        else figure
    )
    traceData = figureJson.get('data', []) if isinstance(figureJson, dict) else []
    serializedTraceData = json.dumps(
        traceData,
        cls=PlotlyJSONEncoder,
        separators=(',', ':'),
        sort_keys=True,
    )
    dataFingerprint = hashlib.sha256(
        serializedTraceData.encode('utf-8')
    ).hexdigest()[:16]
    return (
        f'pchart-pinned-hover:{annotationNamespace.strip() or "plot"}:'
        f'{dataFingerprint}'
    )


def _pinned_hover_annotation_script(
    annotationStateKey: str,
    annotationTextMode: str = 'hover',
    clearAnnotationsToken: str = '',
) -> str:
    return f'''
(function() {{
  const plot = document.getElementById('{{plot_id}}');
  if (!plot || plot.__pchartPinnedHoverEnabled) {{
    return;
  }}
  plot.__pchartPinnedHoverEnabled = true;

  const annotationName = {PINNED_HOVER_ANNOTATION_NAME!r};
  const annotationStateKey = {annotationStateKey!r};
  const annotationStatePrefix = annotationStateKey.slice(
    0,
    annotationStateKey.lastIndexOf(':') + 1
  );
  const annotationTextMode = {annotationTextMode!r};
  const clearAnnotationsToken = {clearAnnotationsToken!r};
  const windowStatePrefix = 'pchart-pinned-hover-state:';
  let lastHoverText = '';

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

  function textElementLines(textElement) {{
    const lines = [];
    let directText = '';
    for (const childNode of textElement.childNodes || []) {{
      if (childNode.nodeType === 3) {{
        directText += childNode.textContent || '';
        continue;
      }}
      if (String(childNode.nodeName).toLowerCase() === 'tspan') {{
        const lineText = (childNode.textContent || '').trim();
        if (directText.trim()) {{
          lines.push(directText.trim());
          directText = '';
        }}
        if (lineText) {{
          lines.push(lineText);
        }}
      }}
    }}
    if (directText.trim()) {{
      lines.push(directText.trim());
    }}
    if (!lines.length && (textElement.textContent || '').trim()) {{
      lines.push(textElement.textContent.trim());
    }}
    return lines;
  }}

  function currentPlotlyHoverText() {{
    const hoverGroups = plot.querySelectorAll('.hoverlayer .hovertext');
    const hoverTextGroups = [];
    for (const hoverGroup of hoverGroups) {{
      const lines = [];
      for (const textElement of hoverGroup.querySelectorAll('text')) {{
        for (const lineText of textElementLines(textElement)) {{
          if (lineText) {{
            lines.push(lineText);
          }}
        }}
      }}
      if (lines.length) {{
        hoverTextGroups.push(lines.map(escapeHtml).join('<br>'));
      }}
    }}
    return hoverTextGroups.join('<br>');
  }}

  function fallbackPointText(point) {{
    const plotlyHoverText = point.hovertext ?? point.text;
    if (plotlyHoverText !== undefined && plotlyHoverText !== null) {{
      return escapeHtml(plotlyHoverText);
    }}

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

  function lastNumberText(text) {{
    const plainText = String(text)
      .replace(/<br\\s*\\/?>/gi, '\\n')
      .replace(/<[^>]*>/g, ' ')
      .replace(/&(?:#\\d+|#x[\\da-f]+|[a-z]+);/gi, ' ');
    const numberMatches = plainText.match(
      /[-+]?(?:(?:\\d{{1,3}}(?:,\\d{{3}})+|\\d+)(?:\\.\\d*)?|\\.\\d+)(?:[eE][-+]?\\d+)?%?/g
    );
    return numberMatches && numberMatches.length
      ? numberMatches[numberMatches.length - 1]
      : text;
  }}

  function pinnedAnnotationText(point) {{
    const hoverText = currentPlotlyHoverText() || lastHoverText;
    const annotationText = hoverText || fallbackPointText(point);
    return annotationTextMode === 'last-number'
      ? lastNumberText(annotationText)
      : annotationText;
  }}

  function annotationFont() {{
    const fullLayout = plot._fullLayout || {{}};
    const layoutFont = fullLayout.font || {{}};
    const legendFont = fullLayout.legend && fullLayout.legend.font || {{}};
    return {{
      color: layoutFont.color || '#2a3f5f',
      family: legendFont.family || layoutFont.family,
      size: legendFont.size || layoutFont.size || 12
    }};
  }}

  function annotationStyle(annotation) {{
    const fullLayout = plot._fullLayout || {{}};
    const layoutFont = fullLayout.font || {{}};
    return {{
      ...annotation,
      name: annotationName,
      showarrow: true,
      arrowhead: 2,
      ax: annotation.ax ?? 30,
      ay: annotation.ay ?? -35,
      bgcolor: fullLayout.paper_bgcolor || 'rgba(255,255,255,0.92)',
      bordercolor: layoutFont.color || '#2a3f5f',
      borderwidth: 1,
      borderpad: 4,
      font: annotationFont(),
      captureevents: true
    }};
  }}

  function windowAnnotationState() {{
    if (!String(window.name || '').startsWith(windowStatePrefix)) {{
      return {{}};
    }}
    try {{
      return JSON.parse(String(window.name).slice(windowStatePrefix.length));
    }} catch (_error) {{
      return {{}};
    }}
  }}

  function saveWindowAnnotationState(savedAnnotations) {{
    const savedState = windowAnnotationState();
    savedState[annotationStateKey] = savedAnnotations;
    window.name = windowStatePrefix + JSON.stringify(savedState);
  }}

  function clearPinnedAnnotationsOnce() {{
    if (!clearAnnotationsToken) {{
      return;
    }}
    const savedState = windowAnnotationState();
    if (savedState.__clearAnnotationsToken === clearAnnotationsToken) {{
      return;
    }}
    for (const savedKey of Object.keys(savedState)) {{
      if (savedKey.startsWith(annotationStatePrefix)) {{
        delete savedState[savedKey];
      }}
    }}
    savedState.__clearAnnotationsToken = clearAnnotationsToken;
    window.name = windowStatePrefix + JSON.stringify(savedState);
    try {{
      const storedKeys = [];
      if (
        typeof window.sessionStorage.length === 'number' &&
        typeof window.sessionStorage.key === 'function'
      ) {{
        for (
          let keyIndex = 0;
          keyIndex < window.sessionStorage.length;
          keyIndex += 1
        ) {{
          const storedKey = window.sessionStorage.key(keyIndex);
          if (storedKey && storedKey.startsWith(annotationStatePrefix)) {{
            storedKeys.push(storedKey);
          }}
        }}
      }} else {{
        storedKeys.push(annotationStateKey);
      }}
      for (const storedKey of storedKeys) {{
        window.sessionStorage.removeItem(storedKey);
      }}
    }} catch (_error) {{
      // Some file:// browser policies disable sessionStorage.
    }}
  }}

  function loadPinnedAnnotations() {{
    let savedAnnotations = null;
    try {{
      savedAnnotations = JSON.parse(
        window.sessionStorage.getItem(annotationStateKey) || '[]'
      );
    }} catch (_error) {{
      savedAnnotations = null;
    }}
    if (!Array.isArray(savedAnnotations) || !savedAnnotations.length) {{
      savedAnnotations = windowAnnotationState()[annotationStateKey] || [];
    }}
    return Array.isArray(savedAnnotations)
      ? savedAnnotations.map(annotationStyle)
      : [];
  }}

  function savePinnedAnnotations(annotations) {{
    const pinnedAnnotations = annotations
      .filter(annotation => annotation && annotation.name === annotationName)
      .map(annotation => ({{
        x: annotation.x,
        y: annotation.y,
        xref: annotation.xref,
        yref: annotation.yref,
        text: annotation.text,
        ax: annotation.ax,
        ay: annotation.ay
      }}));
    try {{
      window.sessionStorage.setItem(
        annotationStateKey,
        JSON.stringify(pinnedAnnotations)
      );
    }} catch (_error) {{
      // Some file:// browser policies disable sessionStorage.
    }}
    saveWindowAnnotationState(pinnedAnnotations);
  }}

  clearPinnedAnnotationsOnce();
  const restoredAnnotations = loadPinnedAnnotations();
  if (restoredAnnotations.length) {{
    const existingAnnotations = (plot.layout.annotations || [])
      .filter(annotation => annotation && annotation.name !== annotationName);
    Plotly.relayout(plot, {{
      annotations: existingAnnotations.concat(restoredAnnotations)
    }});
  }}

  plot.on('plotly_hover', function() {{
    window.requestAnimationFrame(function() {{
      const hoverText = currentPlotlyHoverText();
      if (hoverText) {{
        lastHoverText = hoverText;
      }}
    }});
  }});

  plot.on('plotly_click', function(eventData) {{
    const point = eventData && eventData.points && eventData.points[0];
    if (!point || point.x === undefined || point.y === undefined) {{
      return;
    }}

    const annotations = (plot.layout.annotations || []).slice();
    annotations.push(annotationStyle({{
      x: point.x,
      y: point.y,
      xref: point.xaxis && point.xaxis._id ? point.xaxis._id : 'x',
      yref: point.yaxis && point.yaxis._id ? point.yaxis._id : 'y',
      text: pinnedAnnotationText(point)
    }}));
    savePinnedAnnotations(annotations);
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
    savePinnedAnnotations(annotations);
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
