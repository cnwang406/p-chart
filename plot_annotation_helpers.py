def add_preview_filter_annotation(figure, annotationText: str) -> None:
    if not annotationText:
        return

    figure.add_annotation(
        x=0,
        y=1,
        xref='paper',
        yref='paper',
        text=annotationText,
        showarrow=False,
        xanchor='left',
        yanchor='top',
        xshift=8,
        yshift=-8,
        align='left',
        bgcolor='rgba(255,255,255,0.78)',
        bordercolor='rgba(0,0,0,0.18)',
        borderwidth=1,
        font=dict(size=11, color='rgba(40,40,40,0.88)'),
    )
