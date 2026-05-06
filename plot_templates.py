import plotly.graph_objects as go
import plotly.io as pio

from plotly_local import PLOTLY_FONT_STACK

CUSTOM_TEMPLATE_NAME = 'customized'
FOR_PPT_TEMPLATE_NAME = 'for_ppt'


def register_custom_template() -> None:
    customTemplate = go.layout.Template()
    
    customTemplate.layout.update(
        font=dict(
            family=PLOTLY_FONT_STACK,
            size=12,
            color='#E6E6E6',
        ),
        paper_bgcolor='#1e1e2e',
        plot_bgcolor='#1e1e2e',
        title_font_color='#FFFFFF',
        title_x=0,
        xaxis=dict(
            showgrid=True,
            gridcolor='#44475a',
            zerolinecolor='#6272a4',
            linecolor='#AAAAAA',
            tickfont=dict(color='#DDDDDD'),
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor='#44475a',
            zerolinecolor='#6272a4',
            linecolor='#AAAAAA',
            tickfont=dict(color='#DDDDDD'),
        ),
        legend=dict(
            bgcolor='rgba(0,0,0,0)',
            font=dict(color="#111010",
                family='Cascadia Code Next TC',
                size=10,
            ),
        ),
    )
    customTemplate.layout.colorway = [
    '#FF6B6B',  # 紅（醒目）
    '#4D96FF',  # 藍（主力）
    '#6BCB77',  # 綠（穩定）
    '#FFD93D',  # 黃（highlight）
    '#C77DFF',  # 紫（分類）
    '#FF9F1C',  # 橘（補充）
    ]
    pio.templates[CUSTOM_TEMPLATE_NAME] = customTemplate


def register_for_ppt_template() -> None:
    forPptTemplate = go.layout.Template(pio.templates['plotly'])

    forPptTemplate.layout.update(
        font=dict(
            family=PLOTLY_FONT_STACK,
            size=12,
            color='#E6E6E6',
        ),
        legend=dict(
            font=dict(
                family='Cascadia Code Next TC',
                size=10,
            ),
        ),
    )
    forPptTemplate.data.scatter = [
        go.Scatter(marker=dict(size=7)),
    ]
    forPptTemplate.data.scattergl = [
        go.Scattergl(marker=dict(size=7)),
    ]
    forPptTemplate.data.box = [
        go.Box(marker=dict(size=7)),
    ]
    forPptTemplate.layout.colorway = [
    '#FF6B6B',  # 紅（醒目）
    '#4D96FF',  # 藍（主力）
    '#6BCB77',  # 綠（穩定）
    '#FFD93D',  # 黃（highlight）
    '#C77DFF',  # 紫（分類）
    '#FF9F1C',  # 橘（補充）
    ]
    pio.templates[FOR_PPT_TEMPLATE_NAME] = forPptTemplate


register_custom_template()
register_for_ppt_template()
