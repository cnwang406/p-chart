import plotly.graph_objects as go
import plotly.io as pio

CUSTOM_TEMPLATE_NAME = 'customized'


def register_custom_template() -> None:
    customTemplate = go.layout.Template()
    
    customTemplate.layout.update(
        font=dict(
            family='Cascadia Next TC',
            size=14,
            color='#E6E6E6',
        ),
        paper_bgcolor='#1e1e2e',
        plot_bgcolor='#1e1e2e',
        title_font_color='#FFFFFF',
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
            font=dict(color="#111010"),
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


register_custom_template()
