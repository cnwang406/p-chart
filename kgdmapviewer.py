"""
KGDmap Viewer - Visualization for KGD (Known Good Die) test data in KGDmap format
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import pandas as pd
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
import plotly.graph_objects as go
import plotly.express as px


def parse_kgdmap_data(dataFrame: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """
    Parse KGDmap CSV data.
    When read with skiprows=11 and header=0:
    - dataFrame already has proper column names
    - All rows are data (header extracted separately by app)
    
    Returns: (empty_dict_for_compat, data_dataframe)
    """
    # Since header is extracted separately in app.py, just return empty dict and data
    return {}, dataFrame


def get_kgdmap_items(dataDf: pd.DataFrame) -> list[str]:
    """
    Get list of available items from KGDmap columns after chip_column.
    """
    if dataDf.empty:
        return []

    items: list[str] = []
    found_chip_column = False
    exclude_cols = {"chip_row", "chip_column", "dier", "diec", "dier", "diec"}

    for col in dataDf.columns:
        col_str = str(col).strip().lower()
        if not found_chip_column:
            if col_str == "chip_column":
                found_chip_column = True
            continue
        if col_str in exclude_cols:
            continue
        items.append(str(col).strip())

    return items


def prepare_kgdmap_grid(dataDf: pd.DataFrame, itemName: str) -> tuple[np.ndarray, int, int]:
    """
    Prepare grid data for visualization.
    Coordinates: chip_row, chip_column (1-indexed, bottom-left = 1,1)
    
    Returns: (grid_array, max_row, max_col)
    """
    if dataDf.empty:
        return np.array([]), 0, 0
    
    # Convert chip_row and chip_column to numeric
    dataDf = dataDf.copy()
    dataDf['chip_row'] = pd.to_numeric(dataDf['chip_row'], errors='coerce')
    dataDf['chip_column'] = pd.to_numeric(dataDf['chip_column'], errors='coerce')
    
    # Find grid dimensions
    maxRow = int(dataDf['chip_row'].max())
    maxCol = int(dataDf['chip_column'].max())
    
    # Create grid initialized with NaN
    gridData = np.full((maxRow, maxCol), np.nan)
    
    # Fill grid with values
    if itemName == "(r,c)":
        # Special case: show row value (can use row or encode as r*100+c)
        for idx, row in dataDf.iterrows():
            r = int(row['chip_row']) - 1  # Convert to 0-indexed
            c = int(row['chip_column']) - 1
            if 0 <= r < maxRow and 0 <= c < maxCol:
                # Reverse row index for bottom-left origin
                reversed_r = maxRow - 1 - r
                # Store as row index (visible in colormap)
                gridData[reversed_r, c] = r + 1  # Use 1-indexed row value
    else:
        # Get values from the selected item column
        if itemName in dataDf.columns:
            valuesData = pd.to_numeric(dataDf[itemName], errors='coerce')
            for idx, row in dataDf.iterrows():
                r = int(row['chip_row']) - 1
                c = int(row['chip_column']) - 1
                if 0 <= r < maxRow and 0 <= c < maxCol:
                    # Reverse row index for bottom-left origin
                    reversed_r = maxRow - 1 - r
                    gridData[reversed_r, c] = valuesData.iloc[idx]
    
    return gridData, maxRow, maxCol


def create_kgdmap_figure(
    gridData: np.ndarray,
    maxRow: int,
    maxCol: int,
    itemName: str,
    headerInfo: dict | None = None,
    dieWidthUm: float = 1.0,
    dieHeightUm: float = 1.0,
) -> go.Figure:
    """
    Create plotly figure for KGDmap visualization with hover functionality.
    dieWidthUm and dieHeightUm control the cell aspect ratio.
    """
    if headerInfo is None:
        headerInfo = {}
    
    # Calculate aspect ratio from die dimensions
    aspectRatio = dieHeightUm / dieWidthUm if dieWidthUm > 0 else 1.0
    
    # Calculate figure height to maintain cell aspect ratio
    baseWidth = 900
    figHeight = max(400, int(baseWidth * maxRow / maxCol * aspectRatio))
    
    # Create the heatmap
    fig = go.Figure(data=go.Heatmap(
        z=gridData,
        x=list(range(1, maxCol + 1)),
        y=list(range(maxRow, 0, -1)),  # Reverse y-axis for bottom-left origin
        hoverongaps=False,
        hovertemplate="Column: %{x}<br>Row: %{y}<br>Value: %{z:.2f}<extra></extra>",
        colorscale='RdYlGn_r',
        showscale=True,
        colorbar=dict(title=itemName if itemName != "(r,c)" else "Chip Row")
    ))
    
    # Update layout
    fig.update_layout(
        title=f"KGDmap: {itemName}",
        font=dict(family="Cascadia"),
        xaxis_title="Chip Column (1-indexed)",
        yaxis_title="Chip Row (1-indexed)",
        width=baseWidth,
        height=figHeight,
        xaxis=dict(
            tickmode='linear',
            tick0=1,
            dtick=max(1, maxCol // 10)
        ),
        yaxis=dict(
            tickmode='linear',
            tick0=1,
            dtick=max(1, maxRow // 10)
        )
    )

    return fig


def render_kgdmap_viewer(
    kgdmapDf: pd.DataFrame,
    dieWidthUm: float = 1.0,
    dieHeightUm: float = 1.0,
    selectedItem: str | None = None,
    headerInfo: dict | None = None,
) -> tuple[go.Figure | None, dict[str, object], pd.DataFrame]:
    """
    Build KGDmap figure and detail data for desktop UI callers.
    dieWidthUm and dieHeightUm are used for proper cell aspect ratio.
    """
    if kgdmapDf is None or kgdmapDf.empty:
        return None, {"message": "KGDmap 數據為空"}, pd.DataFrame()
    
    _, dataDf = parse_kgdmap_data(kgdmapDf)
    if dataDf.empty:
        return None, {"message": "無法解析 KGDmap 數據"}, pd.DataFrame()

    if headerInfo is None:
        headerInfo = {}

    availableItems = get_kgdmap_items(dataDf)
    if not availableItems:
        return None, {"message": "未找到可用的 item 列"}, dataDf

    if not selectedItem or selectedItem not in availableItems:
        selectedItem = availableItems[0]
    
    gridData, maxRow, maxCol = prepare_kgdmap_grid(dataDf, selectedItem)
    if gridData.size == 0:
        return None, {"message": "無法生成網格數據"}, dataDf

    fig = create_kgdmap_figure(gridData, maxRow, maxCol, selectedItem, headerInfo, dieWidthUm, dieHeightUm)

    stats: dict[str, object] = {
        "item": selectedItem,
        "availableItems": availableItems,
        "maxRow": maxRow,
        "maxCol": maxCol,
        "totalChips": len(dataDf),
        **{key: headerInfo.get(key, "N/A") for key in ["Lot", "Wafer", "Product", "Date", "Time"]},
    }
    if selectedItem != "(r,c)" and selectedItem in dataDf.columns:
        valuesData = pd.to_numeric(dataDf[selectedItem], errors='coerce')
        meanValue = valuesData.mean()
        stdValue = valuesData.std()
        stats.update({
            "min": valuesData.min(),
            "max": valuesData.max(),
            "mean": meanValue,
            "std": stdValue,
            "uniformityPercent": (stdValue / meanValue * 100) if meanValue != 0 else 0,
        })

    if selectedItem == "(r,c)":
        displayDf = dataDf[['chip_row', 'chip_column']].copy()
        displayDf['chip_row'] = pd.to_numeric(displayDf['chip_row'], errors='coerce')
        displayDf['chip_column'] = pd.to_numeric(displayDf['chip_column'], errors='coerce')
        displayDf['(r,c)'] = displayDf.apply(
            lambda row: f"({int(row['chip_row'])},{int(row['chip_column'])})",
            axis=1,
        )
    else:
        displayDf = dataDf[['chip_row', 'chip_column', selectedItem]].copy()
        displayDf['chip_row'] = pd.to_numeric(displayDf['chip_row'], errors='coerce')
        displayDf['chip_column'] = pd.to_numeric(displayDf['chip_column'], errors='coerce')
        displayDf[selectedItem] = pd.to_numeric(displayDf[selectedItem], errors='coerce')

    return fig, stats, displayDf.sort_values(['chip_row', 'chip_column'])
