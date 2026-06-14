import base64
import os
import json
import re
import tempfile
from pathlib import Path

import pandas as pd

os.environ.setdefault('MPLCONFIGDIR', tempfile.gettempdir())

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.colors import Normalize
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from qt_helpers import require_child
from async_helpers import BackgroundTaskMixin
from loading_overlay import LoadingOverlay
from wafermap_core import (
    build_complete_die_rectangles,
    build_complete_frame_rectangles,
    build_effective_outline,
    build_wafer_outline,
    figure_to_jpg_bytes,
    render_figure,
    top_y_at_x,
    validate_parameters,
)


WAFERMAP_FONT_SIZE = 10
WAFERMAP_TEXT_ALPHA = 0.5


class TabWafermapWidget(QObject, BackgroundTaskMixin):
    def __init__(self, rootWidget: QWidget):
        super().__init__(rootWidget)
        self.rootWidget = rootWidget
        self.tabDataWidget = None
        self.currentFigure = None
        self.currentHtml = ''
        self.currentTitle = ''
        self._isApplyingConfig = False
        self._geometryCacheKey = None
        self._geometryCacheData = None
        self.isActiveTab = False
        self._pendingDataRefresh = False
        self._pendingRedraw = False

        self.tabWidget = require_child(rootWidget, QWidget, 'tabWafermap')
        self.xComboBox = require_child(rootWidget, QComboBox, 'xColComboBox')
        self.yComboBox = require_child(rootWidget, QComboBox, 'yColComboBox')
        self.zComboBox = require_child(rootWidget, QComboBox, 'zColComboBox')
        self.waferIDTitleComboBox = require_child(
            rootWidget,
            QComboBox,
            'waferIDTitleComboBox',
        )
        self.waferIDComboBox = require_child(rootWidget, QComboBox, 'waferIDComboBox')
        self.stepXLineEdit = require_child(rootWidget, QLineEdit, 'stepXLineEdit')
        self.stepYLineEdit = require_child(rootWidget, QLineEdit, 'stepYLineEdit')
        self.frameOffsetXLineEdit = require_child(rootWidget, QLineEdit, 'offsetXLineEdit')
        self.frameOffsetYLineEdit = require_child(rootWidget, QLineEdit, 'offsetYLineEdit')
        self.arrayXSpinBox = require_child(rootWidget, QSpinBox, 'arrayXspinBox')
        self.arrayYSpinBox = require_child(rootWidget, QSpinBox, 'arrayYspinBox')
        self.frameLineColorLabel = require_child(rootWidget, QLabel, 'frameLineColorLabel')
        self.frameLineWidthSpinBox = require_child(rootWidget, QDoubleSpinBox, 'frameLineWidthSpinBox')
        self.dieLineColorLabel = require_child(rootWidget, QLabel, 'dieLineColorLabel')
        self.dieLineWidthSpinBox = require_child(rootWidget, QDoubleSpinBox, 'dieLineWidthSpinBox')
        self.waferDiameterComboBox = require_child(rootWidget, QComboBox, 'waferDiameterComboBox')
        self.edgeExcludeSpinBox = require_child(rootWidget, QDoubleSpinBox, 'waferEdgeExcludeDoubleSpinBox')
        self.waferFlatComboBox = require_child(rootWidget, QComboBox, 'waferFlatComboBox')
        self.topLineEdit = require_child(rootWidget, QLineEdit, 'stepXLineEdit_2')
        self.bottomLineEdit = require_child(rootWidget, QLineEdit, 'stepYLineEdit_2')
        self.effectiveEdgeColorLabel = require_child(rootWidget, QLabel, 'effectEdgeColorLabel')
        self.effectiveEdgeWidthSpinBox = require_child(rootWidget, QDoubleSpinBox, 'effectEdgeWidthSpinBox')
        self.waferEdgeColorLabel = require_child(rootWidget, QLabel, 'waferEdgeColorLabel')
        self.waferEdgeWidthSpinBox = require_child(rootWidget, QDoubleSpinBox, 'waferEdgeWidthSpinBox')
        self.enableLaserMarkCheckBox = require_child(rootWidget, QCheckBox, 'enableLaserMarkCheckBox')
        self.laserEdgeToTopSpinBox = require_child(rootWidget, QDoubleSpinBox, 'waferEdgeExcludeDoubleSpinBox_2')
        self.laserHeightSpinBox = require_child(rootWidget, QDoubleSpinBox, 'laserMarkHeightDoubleSpinBox')
        self.laserLengthSpinBox = require_child(rootWidget, QDoubleSpinBox, 'laserMarkLengthDoubleSpinBox')
        self.laserPosSpinBox = require_child(rootWidget, QSpinBox, 'laserMarkPosSpinBox')
        self.siteOffsetXLineEdit = require_child(rootWidget, QLineEdit, 'siteOffsetXLineEdit')
        self.siteOffsetYLineEdit = require_child(rootWidget, QLineEdit, 'siteOffsetYLineEdit')
        self.plotAreaWidget = require_child(rootWidget, QWidget, 'waferMapPlotAreaWidget')
        self.statusLabel = require_child(rootWidget, QLabel, 'waferMapStatusLabelx')
        self.boxStatusLabel = require_child(rootWidget, QLabel, 'boxStatusLabel')
        self.mapTitleLineEdit = require_child(rootWidget, QLineEdit, 'mapTitleLineEdit')
        self.showDetailCheckBox = require_child(rootWidget, QCheckBox, 'showDetailCheckBox')
        self.showDieRCCheckBox = require_child(rootWidget, QCheckBox, 'showDieRCCheckBox')
        self.heatMapOrContourComboBox = require_child(rootWidget, QComboBox, 'heatMapOrContourComboBox')
        self.saveJsonButton = require_child(rootWidget, QPushButton, 'saveJsonButton')
        self.loadJsonButton = require_child(rootWidget, QPushButton, 'loadJsonButton')
        self.downloadPngButton = require_child(rootWidget, QPushButton, 'waferMapDownloadPngButton')
        self.downloadHtmlButton = require_child(rootWidget, QPushButton, 'waferMapDownloadHtmlButton')
        self.refreshButton = require_child(rootWidget, QPushButton, 'refreshButton')
        self.fontSizeSpinBox = require_child(rootWidget, QSpinBox, 'fontSizeSpinBox')
        self.colorBarHLineEdit = rootWidget.findChild(QLineEdit, 'colorBarHLineEdit')
        self.colorBarLLineEdit = rootWidget.findChild(QLineEdit, 'colorBarLLineEdit')

        self.canvas = FigureCanvas()
        self.redrawTimer = QTimer(self.rootWidget)
        self.redrawTimer.setSingleShot(True)
        self.redrawTimer.setInterval(120)
        self.redrawTimer.timeout.connect(self._draw_plot_when_ready)
        self.plotLayout = QVBoxLayout(self.plotAreaWidget)
        self.plotLayout.setContentsMargins(0, 0, 0, 0)
        self.plotLayout.addWidget(self.canvas)
        self.loadingOverlay = LoadingOverlay(self.plotAreaWidget)

        self._configure_widgets()

    def _configure_widgets(self) -> None:
        self.statusLabel.setFont(self.boxStatusLabel.font())
        self.downloadPngButton.clicked.connect(self._download_png)
        self.downloadHtmlButton.clicked.connect(self._download_html)
        self.saveJsonButton.clicked.connect(self._save_config)
        self.loadJsonButton.clicked.connect(self._load_config)
        self.refreshButton.clicked.connect(self._draw_plot_when_ready)
        self._configure_color_label(self.frameLineColorLabel, '#f4a3a3')
        self._configure_color_label(self.dieLineColorLabel, '#ececec')
        self._configure_color_label(self.effectiveEdgeColorLabel, '#f4a3a3')
        self._configure_color_label(self.waferEdgeColorLabel, '#000000')

        for comboBox in [self.xComboBox, self.yComboBox]:
            comboBox.currentTextChanged.connect(self._mark_plot_dirty)
        self.waferIDTitleComboBox.currentTextChanged.connect(
            self._on_wafer_id_column_changed
        )
        self.waferIDComboBox.currentTextChanged.connect(self._redraw_after_change)
        for comboBox in [
            self.zComboBox,
            self.waferDiameterComboBox,
            self.waferFlatComboBox,
            self.heatMapOrContourComboBox,
        ]:
            comboBox.currentTextChanged.connect(self._redraw_after_change)
        for spinBox in [
            self.arrayXSpinBox,
            self.arrayYSpinBox,
            self.edgeExcludeSpinBox,
            self.frameLineWidthSpinBox,
            self.dieLineWidthSpinBox,
            self.effectiveEdgeWidthSpinBox,
            self.waferEdgeWidthSpinBox,
            self.laserEdgeToTopSpinBox,
            self.laserHeightSpinBox,
            self.laserLengthSpinBox,
            self.laserPosSpinBox,
            self.fontSizeSpinBox,
        ]:
            spinBox.valueChanged.connect(self._redraw_after_change)
        for checkBox in [
            self.enableLaserMarkCheckBox,
            self.showDetailCheckBox,
            self.showDieRCCheckBox,
        ]:
            checkBox.stateChanged.connect(self._redraw_after_change)
        for lineEdit in [
            self.stepXLineEdit,
            self.stepYLineEdit,
            self.frameOffsetXLineEdit,
            self.frameOffsetYLineEdit,
            self.topLineEdit,
            self.bottomLineEdit,
            self.siteOffsetXLineEdit,
            self.siteOffsetYLineEdit,
            self.mapTitleLineEdit,
        ]:
            lineEdit.editingFinished.connect(self._redraw_after_change)
        for lineEdit in [self.colorBarHLineEdit, self.colorBarLLineEdit]:
            if lineEdit is not None:
                lineEdit.editingFinished.connect(self._redraw_after_change)

        if not self.mapTitleLineEdit.text().strip():
            self.mapTitleLineEdit.setText('wafer_frame_preview')

    def set_tab_data(self, tabDataWidget) -> None:
        self.tabDataWidget = tabDataWidget
        self.tabDataWidget.add_data_changed_callback(self._on_data_changed)
        self._on_data_changed()

    def set_active_tab(self, isActive: bool) -> None:
        wasActive = self.isActiveTab
        self.isActiveTab = isActive
        if not isActive or wasActive:
            return
        if self._pendingDataRefresh:
            self._on_data_changed()
            return
        if self._pendingRedraw:
            self._pendingRedraw = False
            self._redraw_after_change()

    def _on_data_changed(self) -> None:
        if not self.isActiveTab:
            self._pendingDataRefresh = True
            return
        self._pendingDataRefresh = False
        dataFrame = self._plot_data()
        columnNames = list(dataFrame.columns.astype(str)) if not dataFrame.empty else []
        for comboBox in [self.xComboBox, self.yComboBox, self.zComboBox]:
            currentText = comboBox.currentText().strip()
            comboBox.blockSignals(True)
            comboBox.clear()
            comboBox.addItem('')
            comboBox.addItems(columnNames)
            if currentText in columnNames:
                comboBox.setCurrentText(currentText)
            comboBox.blockSignals(False)
        self._populate_wafer_id_title_combo(columnNames)
        self._update_wafer_id_values()
        self._mark_plot_dirty()

    def _populate_wafer_id_title_combo(self, columnNames: list[str]) -> None:
        currentText = self.waferIDTitleComboBox.currentText().strip()
        selectedText = (
            currentText
            if currentText in columnNames
            else self._default_wafer_id_column(columnNames)
        )

        self.waferIDTitleComboBox.blockSignals(True)
        self.waferIDTitleComboBox.clear()
        self.waferIDTitleComboBox.addItems(columnNames)
        if selectedText:
            self.waferIDTitleComboBox.setCurrentText(selectedText)
        self.waferIDTitleComboBox.blockSignals(False)

    def _default_wafer_id_column(self, columnNames: list[str]) -> str:
        for columnName in columnNames:
            if self._is_wafer_id_column_name(columnName):
                return columnName
        return columnNames[0] if columnNames else ''

    def _is_wafer_id_column_name(self, columnName: str) -> bool:
        text = str(columnName).strip().lower()
        normalizedText = re.sub(r'[\s_-]+', '', text)
        return (
            text == '#'
            or normalizedText in {'wafer', 'waferid', 'id'}
            or 'wafer' in normalizedText
        )

    def _on_wafer_id_column_changed(self, *_args) -> None:
        self._update_wafer_id_values('')
        self._redraw_after_change()

    def _update_wafer_id_values(self, preferredText: str | None = None) -> None:
        dataFrame = self._plot_data()
        columnName = self.waferIDTitleComboBox.currentText().strip()
        selectedText = (
            preferredText.strip()
            if preferredText is not None
            else self.waferIDComboBox.currentText().strip()
        )
        valueTexts = []
        seenTexts = set()

        if not dataFrame.empty and columnName in dataFrame.columns:
            for value in dataFrame[columnName]:
                valueText = self._format_wafer_id_value(value)
                if not valueText or valueText in seenTexts:
                    continue
                seenTexts.add(valueText)
                valueTexts.append(valueText)

        self.waferIDComboBox.blockSignals(True)
        self.waferIDComboBox.clear()
        self.waferIDComboBox.addItem('')
        self.waferIDComboBox.addItems(valueTexts)
        if selectedText and selectedText in seenTexts:
            self.waferIDComboBox.setCurrentText(selectedText)
        else:
            self.waferIDComboBox.setCurrentIndex(0)
        self.waferIDComboBox.blockSignals(False)

    def _format_wafer_id_value(self, value) -> str:
        if pd.isna(value):
            return ''
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _plot_data(self) -> pd.DataFrame:
        if self.tabDataWidget is None:
            return pd.DataFrame()
        return self.tabDataWidget.get_plot_data()

    def _filtered_plot_data(self) -> pd.DataFrame:
        dataFrame = self._plot_data()
        columnName = self.waferIDTitleComboBox.currentText().strip()
        selectedWaferID = self.waferIDComboBox.currentText().strip()
        if (
            dataFrame.empty
            or not columnName
            or not selectedWaferID
            or columnName not in dataFrame.columns
        ):
            return dataFrame

        waferTextSeries = dataFrame[columnName].map(self._format_wafer_id_value)
        return dataFrame.loc[waferTextSeries == selectedWaferID]

    def _wafermap_title_text(self) -> str:
        baseTitle = self.mapTitleLineEdit.text().strip() or 'wafer_frame_preview'
        selectedWaferID = self.waferIDComboBox.currentText().strip()
        waferSuffix = f'(#{selectedWaferID})' if selectedWaferID else '(all wafers)'
        return f'{baseTitle} {waferSuffix}'

    def _draw_plot_when_ready(self, *_args) -> None:
        if not self.isActiveTab:
            self._pendingRedraw = True
            return
        try:
            self.draw_plot()
        except Exception as exc:
            self._set_status(f'Wafer map error: {exc}', error=True)

    def _mark_plot_dirty(self, *_args) -> None:
        self.redrawTimer.stop()
        self._set_status('Wafer map settings changed. Press refresh to redraw.')

    def _redraw_after_change(self, *_args) -> None:
        if self._isApplyingConfig:
            return
        if not self.isActiveTab:
            self._pendingRedraw = True
            return
        if self.currentFigure is None:
            self._mark_plot_dirty()
            return
        self._set_status('Wafer map settings changed. Redrawing...')
        self.redrawTimer.start()

    def draw_plot(self) -> None:
        snapshot = self._wafermap_snapshot()
        self._set_status('Wafer map rendering...')
        self.loadingOverlay.show('Loading...')
        self._activeRenderTaskId = self._start_background_task(
            lambda: self._build_wafermap_result(snapshot),
            self._on_wafermap_render_finished,
            self._on_wafermap_render_failed,
        )

    def _wafermap_snapshot(self) -> dict[str, object]:
        params = self._read_parameters()
        params['title'] = self._wafermap_title_text()
        return {
            'params': params,
            'dataFrame': self._filtered_plot_data().copy(),
            'xColumn': self.xComboBox.currentText().strip(),
            'yColumn': self.yComboBox.currentText().strip(),
            'zColumn': self.zComboBox.currentText().strip(),
            'isHeatMap': self._is_heatmap_mode(),
            'modeText': self.heatMapOrContourComboBox.currentText().strip(),
            'showDetail': self.showDetailCheckBox.isChecked(),
            'showDieLabels': self.showDieRCCheckBox.isChecked(),
            'frameLineColor': self._label_color(self.frameLineColorLabel, '#f4a3a3'),
            'dieLineColor': self._label_color(self.dieLineColorLabel, '#ececec'),
            'effectiveEdgeColor': self._label_color(self.effectiveEdgeColorLabel, '#f4a3a3'),
            'waferEdgeColor': self._label_color(self.waferEdgeColorLabel, '#000000'),
            'frameLineWidth': float(self.frameLineWidthSpinBox.value()),
            'dieLineWidth': float(self.dieLineWidthSpinBox.value()),
            'effectiveEdgeLineWidth': float(self.effectiveEdgeWidthSpinBox.value()),
            'waferEdgeLineWidth': float(self.waferEdgeWidthSpinBox.value()),
            'showLaserMark': self.enableLaserMarkCheckBox.isChecked(),
            'edgeToMarkTopMm': float(self.laserEdgeToTopSpinBox.value()),
            'charHeightMm': float(self.laserHeightSpinBox.value()),
            'markerLengthMm': float(self.laserLengthSpinBox.value()),
            'laserMarkPositionDeg': float(self.laserPosSpinBox.value()),
            'waferValueFontSize': self._wafer_value_font_size(),
            'colorBarLimits': self._colorbar_limits(),
            'skipRows': self.tabDataWidget.get_skip_rows() if self.tabDataWidget is not None else 0,
        }

    def _build_wafermap_result(self, snapshot: dict[str, object]) -> dict[str, object]:
        params = snapshot['params']
        validate_parameters(
            params['stepXUm'],
            params['stepYUm'],
            params['siteOffsetXUm'],
            params['siteOffsetYUm'],
            params['diameterMm'],
        )

        dataFrame = snapshot['dataFrame']
        xColumn = str(snapshot['xColumn'])
        yColumn = str(snapshot['yColumn'])
        zColumn = str(snapshot['zColumn'])
        isHeatMap = bool(snapshot['isHeatMap'])
        modeText = str(snapshot.get('modeText', ''))
        isDeprecatedContour = self._is_deprecated_contour_mode(modeText)
        valueLabel = zColumn if isHeatMap and zColumn else 'N/A'
        dieValueDf = (
            self._build_die_value_data_for_columns(dataFrame, xColumn, yColumn, zColumn)
            if isHeatMap
            else pd.DataFrame()
        )

        geometry = self._build_geometry(params)
        waferOutline = geometry['waferOutline']
        effectiveOutline = geometry['effectiveOutline']
        topReferenceY = geometry['topReferenceY']
        bottomReferenceY = geometry['bottomReferenceY']
        completeFrames = geometry['completeFrames']
        completeDies = geometry['completeDies']
        frameBottomGapMm = min((frame[1] for frame in completeFrames), default=float('nan')) - bottomReferenceY
        if not completeFrames:
            frameBottomGapMm = -1.0

        infoPanelText = self._build_info_panel_text(
            params=params,
            valueLabel=valueLabel,
            renderMode=modeText or 'Heat map',
            deprecatedContour=isDeprecatedContour,
            totalFrames=len(completeFrames),
            totalDies=len(completeDies),
            frameBottomGapMm=frameBottomGapMm,
            skipRows=int(snapshot['skipRows']),
        )

        figure = render_figure(
            waferOutline,
            effectiveOutline,
            params['title'],
            stepXUm=params['stepXUm'],
            stepYUm=params['stepYUm'],
            arrayX=params['arrayX'],
            arrayY=params['arrayY'],
            frameOffsetXUm=params['frameOffsetXUm'],
            frameOffsetYUm=params['frameOffsetYUm'],
            topMm=params['topMm'],
            bottomMm=params['bottomMm'],
            topReferenceY=topReferenceY,
            bottomReferenceY=bottomReferenceY,
            showInfoPanel=bool(snapshot['showDetail']),
            showDieLabels=bool(snapshot['showDieLabels']),
            infoPanelText=infoPanelText,
            signatureText='by cnwang',
            frameLineColor=str(snapshot['frameLineColor']),
            dieLineColor=str(snapshot['dieLineColor']),
            effectiveEdgeColor=str(snapshot['effectiveEdgeColor']),
            waferEdgeColor=str(snapshot['waferEdgeColor']),
            frameLineWidth=float(snapshot['frameLineWidth']),
            dieLineWidth=float(snapshot['dieLineWidth']),
            effectiveEdgeLineWidth=float(snapshot['effectiveEdgeLineWidth']),
            waferEdgeLineWidth=float(snapshot['waferEdgeLineWidth']),
            showLaserMark=bool(snapshot['showLaserMark']),
            edgeToMarkTopMm=float(snapshot['edgeToMarkTopMm']),
            charHeightMm=float(snapshot['charHeightMm']),
            markerLengthMm=float(snapshot['markerLengthMm']),
            laserMarkPositionDeg=float(snapshot['laserMarkPositionDeg']),
            infoPanelFontSize=5,
        )
        heatmapMissCount = 0
        if isHeatMap and not dieValueDf.empty:
            heatmapMissCount = self._overlay_die_rect_heatmap_with_limits(
                figure,
                dieValueDf,
                completeDies,
                params,
                valueLabel,
                snapshot['colorBarLimits'],
                int(snapshot['waferValueFontSize']),
            )
        self._apply_wafermap_font_style(figure, int(snapshot['waferValueFontSize']))

        statusParts = [f'Wafer map updated. frames={len(completeFrames)}, dies={len(completeDies)}']
        if isHeatMap:
            statusParts.append(f'heatmap cells={len(dieValueDf)}')
        if isDeprecatedContour:
            statusParts.append('Contour map is deprecated; use Contour tab for X/Y contour mapping. Rendering frame/die only')
        if heatmapMissCount:
            statusParts.append(f'unmatched cells={heatmapMissCount}')
        return {
            'figure': figure,
            'title': params['title'],
            'statusText': ', '.join(statusParts),
        }

    def _on_wafermap_render_finished(self, taskId: int, result: dict[str, object]) -> None:
        if taskId != getattr(self, '_activeRenderTaskId', None):
            return
        self.loadingOverlay.hide()
        figure = result['figure']
        self.currentFigure = figure
        self._replace_canvas(figure)
        self.canvas.draw_idle()
        self.currentHtml = ''
        self.currentTitle = str(result.get('title', ''))
        self._set_status(str(result.get('statusText', 'Wafer map updated.')))

    def _on_wafermap_render_failed(self, taskId: int, errorText: str) -> None:
        if taskId != getattr(self, '_activeRenderTaskId', None):
            return
        self.loadingOverlay.hide()
        self._set_status(f'Wafer map error: {errorText}', error=True)

    def _build_die_value_data_for_columns(
        self,
        dataFrame: pd.DataFrame,
        xColumn: str,
        yColumn: str,
        zColumn: str,
    ) -> pd.DataFrame:
        if (
            dataFrame.empty
            or not xColumn
            or not yColumn
            or not zColumn
            or xColumn not in dataFrame.columns
            or yColumn not in dataFrame.columns
            or zColumn not in dataFrame.columns
        ):
            return pd.DataFrame(columns=['dieX', 'dieY', 'value'])

        dieValueDf = dataFrame[[xColumn, yColumn, zColumn]].copy()
        # Heat map follows the original Plotly KGD convention:
        # X = chip/die column, Y = chip/die row, and map (1, 1) is bottom-left.
        # Data coordinates are shifted so min(col/row) becomes 1 on the map.
        dieValueDf.columns = ['dieX', 'dieY', 'value']
        dieValueDf = dieValueDf.apply(pd.to_numeric, errors='coerce')
        dieValueDf = dieValueDf.dropna(subset=['dieX', 'dieY', 'value']).reset_index(drop=True)
        if dieValueDf.empty:
            return pd.DataFrame(columns=['dieX', 'dieY', 'value'])

        dieValueDf['dieX'] = self._map_data_index_to_die_index(dieValueDf['dieX'])
        dieValueDf['dieY'] = self._map_data_index_to_die_index(dieValueDf['dieY'])
        return (
            dieValueDf.groupby(['dieX', 'dieY'], as_index=False)
            .agg(value=('value', 'mean'))
            .sort_values(['dieY', 'dieX'])
            .reset_index(drop=True)
        )

    def _map_data_index_to_die_index(self, series: pd.Series) -> pd.Series:
        indexSeries = series.astype(float).round().astype(int)
        if indexSeries.empty:
            return indexSeries
        return indexSeries - int(indexSeries.min()) + 1

    def _overlay_die_rect_heatmap_with_limits(
        self,
        figure,
        dieValueDf: pd.DataFrame,
        completeDies: list[tuple[float, float, float, float]],
        params: dict[str, object],
        valueLabel: str,
        colorBarLimits: tuple[float, float] | None,
        waferValueFontSize: int,
    ) -> int:
        if dieValueDf.empty or not completeDies:
            return len(dieValueDf)

        ax = figure.gca()
        dieMap = self._die_rect_by_label(completeDies, params)
        values = pd.to_numeric(dieValueDf['value'], errors='coerce').dropna()
        if values.empty:
            return len(dieValueDf)

        valueMin = float(values.min())
        valueMax = float(values.max())
        if colorBarLimits is not None:
            valueMin, valueMax = colorBarLimits
        if valueMin == valueMax:
            valueMin -= 1.0
            valueMax += 1.0
        cmap = plt.get_cmap('RdYlGn_r')
        norm = Normalize(vmin=valueMin, vmax=valueMax)
        missCount = 0

        for row in dieValueDf.itertuples(index=False):
            dieX = int(row.dieX)
            dieY = int(row.dieY)
            value = float(row.value)
            rect = dieMap.get((dieX, dieY))
            if rect is None:
                missCount += 1
                continue

            dieLeft, dieBottom, dieRight, dieTop = rect
            isValueInRange = valueMin <= value <= valueMax
            color = cmap(norm(value)) if isValueInRange else 'white'
            patch = plt.Rectangle(
                (dieLeft, dieBottom),
                dieRight - dieLeft,
                dieTop - dieBottom,
                facecolor=color,
                edgecolor='none',
                linewidth=0,
                alpha=1.0,
                zorder=1.2,
            )
            ax.add_patch(patch)

            centerX = (dieLeft + dieRight) / 2.0
            centerY = (dieBottom + dieTop) / 2.0
            ax.text(
                centerX,
                centerY,
                f'{value:.1f}',
                ha='center',
                va='center',
                fontsize=waferValueFontSize,
                color=(0, 0, 0, WAFERMAP_TEXT_ALPHA),
                zorder=5,
                bbox={'boxstyle': 'round,pad=0.1', 'fc': color, 'ec': 'none', 'alpha': WAFERMAP_TEXT_ALPHA},
            )

        scalarMappable = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
        scalarMappable.set_array([])
        colorbar = figure.colorbar(scalarMappable, ax=ax, fraction=0.046, pad=0.04)
        axisFontSize = waferValueFontSize + 2
        colorbar.set_label(valueLabel)
        colorbar.ax.yaxis.label.set_fontsize(axisFontSize)
        colorbar.ax.yaxis.label.set_alpha(WAFERMAP_TEXT_ALPHA)
        colorbar.ax.tick_params(labelsize=axisFontSize)
        for tickLabel in colorbar.ax.get_yticklabels():
            tickLabel.set_fontsize(axisFontSize)
            tickLabel.set_alpha(WAFERMAP_TEXT_ALPHA)
        return missCount

    def _die_rect_by_label(
        self,
        completeDies: list[tuple[float, float, float, float]],
        params: dict[str, object],
    ) -> dict[tuple[int, int], tuple[float, float, float, float]]:
        minDieLeft = min(die[0] for die in completeDies)
        minDieBottom = min(die[1] for die in completeDies)
        stepXMm = params['stepXUm'] / 1000.0
        stepYMm = params['stepYUm'] / 1000.0
        dieWidthMm = stepXMm / max(int(params['arrayX']), 1)
        dieHeightMm = stepYMm / max(int(params['arrayY']), 1)

        dieMap: dict[tuple[int, int], tuple[float, float, float, float]] = {}
        for dieLeft, dieBottom, dieRight, dieTop in completeDies:
            xIndex = int(round((dieLeft - minDieLeft) / dieWidthMm))
            yIndex = int(round((dieBottom - minDieBottom) / dieHeightMm))
            dieMap[(xIndex + 1, yIndex + 1)] = (dieLeft, dieBottom, dieRight, dieTop)
        return dieMap

    def _apply_wafermap_font_style(self, figure, waferValueFontSize: int) -> None:
        axisFontSize = self._wafer_axis_font_size()
        for ax in figure.axes:
            title = ax.title
            title.set_fontsize(axisFontSize)
            title.set_alpha(WAFERMAP_TEXT_ALPHA)
            ax.xaxis.label.set_fontsize(axisFontSize)
            ax.xaxis.label.set_alpha(WAFERMAP_TEXT_ALPHA)
            ax.yaxis.label.set_fontsize(axisFontSize)
            ax.yaxis.label.set_alpha(WAFERMAP_TEXT_ALPHA)
            for tickLabel in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
                tickLabel.set_fontsize(axisFontSize)
                tickLabel.set_alpha(WAFERMAP_TEXT_ALPHA)
            for text in ax.texts:
                text.set_fontsize(waferValueFontSize)
                text.set_alpha(WAFERMAP_TEXT_ALPHA)
        for text in figure.texts:
            isInfoPanelText = '\n' in text.get_text()
            text.set_fontsize(5 if isInfoPanelText else WAFERMAP_FONT_SIZE)
            text.set_alpha(0.8 if isInfoPanelText else WAFERMAP_TEXT_ALPHA)

    def _wafer_value_font_size(self) -> int:
        return max(1, int(self.fontSizeSpinBox.value()))

    def _wafer_axis_font_size(self) -> int:
        return self._wafer_value_font_size() + 2

    def _colorbar_limits(self) -> tuple[float, float] | None:
        if self.colorBarHLineEdit is None or self.colorBarLLineEdit is None:
            return None
        highText = self.colorBarHLineEdit.text().strip()
        lowText = self.colorBarLLineEdit.text().strip()
        if not highText and not lowText:
            return None
        try:
            highValue = float(highText) if highText else 0.0
            lowValue = float(lowText) if lowText else 0.0
        except ValueError:
            raise ValueError('colorbar H/L 必須是數字。')
        if highValue == 0.0 and lowValue == 0.0:
            return None
        if highValue <= lowValue:
            raise ValueError('colorBarHLineEdit 必須大於 colorBarLLineEdit。')
        return lowValue, highValue

    def _read_parameters(self) -> dict[str, object]:
        return {
            'title': self.mapTitleLineEdit.text().strip() or 'wafer_frame_preview',
            'stepXUm': self._float_line_edit(self.stepXLineEdit, 10000.0),
            'stepYUm': self._float_line_edit(self.stepYLineEdit, 10000.0),
            'frameOffsetXUm': self._float_line_edit(self.frameOffsetXLineEdit, 0.0),
            'frameOffsetYUm': self._float_line_edit(self.frameOffsetYLineEdit, 0.0),
            'arrayX': max(1, int(self.arrayXSpinBox.value())),
            'arrayY': max(1, int(self.arrayYSpinBox.value())),
            'diameterMm': self._combo_float(self.waferDiameterComboBox, 150.0),
            'edgeExcludeMm': float(self.edgeExcludeSpinBox.value()),
            'frameLineWidth': float(self.frameLineWidthSpinBox.value()),
            'dieLineWidth': float(self.dieLineWidthSpinBox.value()),
            'effectiveEdgeLineWidth': float(self.effectiveEdgeWidthSpinBox.value()),
            'waferEdgeLineWidth': float(self.waferEdgeWidthSpinBox.value()),
            'flatOption': self._flat_option(),
            'topMm': self._float_line_edit(self.topLineEdit, 10.0),
            'bottomMm': self._float_line_edit(self.bottomLineEdit, 3.0),
            'siteOffsetXUm': self._float_line_edit(self.siteOffsetXLineEdit, 0.0),
            'siteOffsetYUm': self._float_line_edit(self.siteOffsetYLineEdit, 0.0),
        }

    def _build_geometry(self, params: dict[str, object]) -> dict[str, object]:
        cacheKey = (
            round(float(params['diameterMm']), 6),
            str(params['flatOption']),
            round(float(params['edgeExcludeMm']), 6),
            round(float(params['stepXUm']), 6),
            round(float(params['stepYUm']), 6),
            round(float(params['frameOffsetXUm']), 6),
            round(float(params['frameOffsetYUm']), 6),
            round(float(params['topMm']), 6),
            round(float(params['bottomMm']), 6),
            int(params['arrayX']),
            int(params['arrayY']),
        )
        if cacheKey == self._geometryCacheKey and self._geometryCacheData is not None:
            return self._geometryCacheData

        waferOutline = build_wafer_outline(
            diameterMm=params['diameterMm'],
            flatOption=params['flatOption'],
        )
        effectiveOutline = build_effective_outline(
            waferOutline=waferOutline,
            edgeExcludeMm=params['edgeExcludeMm'],
        )
        if len(effectiveOutline) < 3:
            raise ValueError('edge exclude 太大，已無可用 wafer 區域。')

        centerReferenceX = (float(waferOutline[:, 0].min()) + float(waferOutline[:, 0].max())) / 2.0
        topReferenceY = top_y_at_x(waferOutline, centerReferenceX)
        bottomReferenceY = float(waferOutline[:, 1].min())
        completeFrames = build_complete_frame_rectangles(
            outline=effectiveOutline,
            stepXUm=params['stepXUm'],
            stepYUm=params['stepYUm'],
            frameOffsetXUm=params['frameOffsetXUm'],
            frameOffsetYUm=params['frameOffsetYUm'],
            topMm=params['topMm'],
            bottomMm=params['bottomMm'],
            topReferenceY=topReferenceY,
            bottomReferenceY=bottomReferenceY,
        )
        completeDies = build_complete_die_rectangles(
            outline=effectiveOutline,
            stepXUm=params['stepXUm'],
            stepYUm=params['stepYUm'],
            arrayX=params['arrayX'],
            arrayY=params['arrayY'],
            frameOffsetXUm=params['frameOffsetXUm'],
            frameOffsetYUm=params['frameOffsetYUm'],
            topMm=params['topMm'],
            topReferenceY=topReferenceY,
        )
        geometry = {
            'waferOutline': waferOutline,
            'effectiveOutline': effectiveOutline,
            'topReferenceY': topReferenceY,
            'bottomReferenceY': bottomReferenceY,
            'completeFrames': completeFrames,
            'completeDies': completeDies,
        }
        self._geometryCacheKey = cacheKey
        self._geometryCacheData = geometry
        return geometry

    def _build_info_panel_text(
        self,
        params: dict[str, object],
        valueLabel: str,
        renderMode: str,
        deprecatedContour: bool,
        totalFrames: int,
        totalDies: int,
        frameBottomGapMm: float,
        skipRows: int | None = None,
    ) -> str:
        if skipRows is None:
            skipRows = self.tabDataWidget.get_skip_rows() if self.tabDataWidget is not None else 0
        renderModeText = renderMode or 'frame/die'
        if deprecatedContour:
            renderModeText = 'Contour map deprecated; frame/die only'
        return '\n'.join([
            f"title: {params['title']}",
            "",
            "--- data source ---",
            f"skip rows: {skipRows}",
            f"mode: {renderModeText}",
            f"value: {valueLabel}",
            "",
            "--- frame parameters ---",
            f"frame W: {params['stepXUm']:.1f} um, H: {params['stepYUm']:.1f} um",
            f"array X: {params['arrayX']}, Y: {params['arrayY']}",
            f"frameOffset X: {params['frameOffsetXUm']:.1f} um, Y: {params['frameOffsetYUm']:.1f} um",
            f"top to edge : {params['topMm']:.2f} mm",
            f"bottom to edge : {params['bottomMm']:.2f} mm",
            f"frame bottom gap: {frameBottomGapMm:.2f} mm" if frameBottomGapMm >= 0 else "frame bottom gap: N/A",
            "",
            f"total frames: {totalFrames}",
            f"total dies: {totalDies}",
            "",
            "--- measurement site offset ---",
            f"site offset X: {params['siteOffsetXUm']:.1f} um, Y: {params['siteOffsetYUm']:.1f} um",
            "",
            "--- wafer geometry ---",
            f"diameter: {params['diameterMm']:.1f} mm",
            f"flat: {params['flatOption']}",
            f"edge exclude: {params['edgeExcludeMm']:.2f} mm",
        ])

    def _float_line_edit(self, lineEdit: QLineEdit, defaultValue: float) -> float:
        try:
            return float(lineEdit.text().strip())
        except ValueError:
            lineEdit.setText(str(defaultValue))
            return defaultValue

    def _combo_float(self, comboBox: QComboBox, defaultValue: float) -> float:
        try:
            return float(comboBox.currentText().strip())
        except ValueError:
            return defaultValue

    def _flat_option(self) -> str:
        text = self.waferFlatComboBox.currentText().strip().lower()
        if '47.5' in text:
            return '47.5 mm'
        if 'notch' in text and '135' in text:
            return 'notch-135'
        if 'notch' in text:
            return 'notch-180'
        return '57.5 mm'

    def _is_heatmap_mode(self) -> bool:
        return 'heat' in self.heatMapOrContourComboBox.currentText().strip().lower()

    def _is_deprecated_contour_mode(self, modeText: str) -> bool:
        return 'contour' in str(modeText).strip().lower()

    def _label_color(self, label: QLabel, fallbackColor: str) -> str:
        styleSheet = label.styleSheet()
        rgbMatch = re.search(r'rgb\((\d+),\s*(\d+),\s*(\d+)\)', styleSheet)
        if rgbMatch:
            red, green, blue = (int(rgbMatch.group(index)) for index in range(1, 4))
            return f'#{red:02x}{green:02x}{blue:02x}'
        hexMatch = re.search(r'#[0-9a-fA-F]{6}', styleSheet)
        if hexMatch:
            return hexMatch.group(0)
        return fallbackColor

    def _configure_color_label(self, label: QLabel, fallbackColor: str) -> None:
        self._set_label_color(label, self._label_color(label, fallbackColor))
        label.setCursor(Qt.PointingHandCursor)
        label.setToolTip('Select color')
        label.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        colorLabels = {
            self.frameLineColorLabel,
            self.dieLineColorLabel,
            self.effectiveEdgeColorLabel,
            self.waferEdgeColorLabel,
        }
        if watched in colorLabels and event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton:
                self._choose_label_color(watched)
                return True
        return False

    def _choose_label_color(self, label: QLabel) -> None:
        currentColor = QColor(self._label_color(label, '#000000'))
        selectedColor = QColorDialog.getColor(
            currentColor,
            self.rootWidget,
            'Select color',
            QColorDialog.DontUseNativeDialog,
        )
        if not selectedColor.isValid():
            return
        self._set_label_color(label, selectedColor.name())
        self._redraw_after_change()

    def _set_label_color(self, label: QLabel, colorText: str) -> None:
        label.setStyleSheet(
            f'QLabel {{ background-color: {colorText}; border: 1px solid #8a8a8a; min-width: 24px; }}'
        )

    def _config(self) -> dict[str, object]:
        params = self._read_parameters()
        params.update({
            'xColumn': self.xComboBox.currentText().strip(),
            'yColumn': self.yComboBox.currentText().strip(),
            'zColumn': self.zComboBox.currentText().strip(),
            'waferIDColumn': self.waferIDTitleComboBox.currentText().strip(),
            'waferIDValue': self.waferIDComboBox.currentText().strip(),
            'showDetail': self.showDetailCheckBox.isChecked(),
            'showDieRC': self.showDieRCCheckBox.isChecked(),
            'mode': self.heatMapOrContourComboBox.currentText().strip(),
            'showLaserMark': self.enableLaserMarkCheckBox.isChecked(),
            'laserMarkEdgeToTopMm': float(self.laserEdgeToTopSpinBox.value()),
            'laserMarkCharHeightMm': float(self.laserHeightSpinBox.value()),
            'laserMarkLengthMm': float(self.laserLengthSpinBox.value()),
            'laserMarkPositionDeg': int(self.laserPosSpinBox.value()),
            'fontSize': self._wafer_value_font_size(),
        })
        if self.colorBarHLineEdit is not None:
            params['colorBarHigh'] = self.colorBarHLineEdit.text().strip()
        if self.colorBarLLineEdit is not None:
            params['colorBarLow'] = self.colorBarLLineEdit.text().strip()
        return params

    def _apply_config(self, config: dict[str, object]) -> None:
        self._isApplyingConfig = True
        try:
            lineEditMap = {
                'title': self.mapTitleLineEdit,
                'stepXUm': self.stepXLineEdit,
                'stepYUm': self.stepYLineEdit,
                'frameOffsetXUm': self.frameOffsetXLineEdit,
                'frameOffsetYUm': self.frameOffsetYLineEdit,
                'topMm': self.topLineEdit,
                'bottomMm': self.bottomLineEdit,
                'siteOffsetXUm': self.siteOffsetXLineEdit,
                'siteOffsetYUm': self.siteOffsetYLineEdit,
            }
            for key, lineEdit in lineEditMap.items():
                if key in config:
                    lineEdit.setText(str(config[key]))

            if 'arrayX' in config:
                self.arrayXSpinBox.setValue(max(1, int(config['arrayX'])))
            if 'arrayY' in config:
                self.arrayYSpinBox.setValue(max(1, int(config['arrayY'])))
            if 'edgeExcludeMm' in config:
                self.edgeExcludeSpinBox.setValue(float(config['edgeExcludeMm']))
            if 'frameLineWidth' in config:
                self.frameLineWidthSpinBox.setValue(float(config['frameLineWidth']))
            if 'dieLineWidth' in config:
                self.dieLineWidthSpinBox.setValue(float(config['dieLineWidth']))
            if 'effectiveEdgeLineWidth' in config:
                self.effectiveEdgeWidthSpinBox.setValue(float(config['effectiveEdgeLineWidth']))
            if 'waferEdgeLineWidth' in config:
                self.waferEdgeWidthSpinBox.setValue(float(config['waferEdgeLineWidth']))
            if 'laserMarkEdgeToTopMm' in config:
                self.laserEdgeToTopSpinBox.setValue(float(config['laserMarkEdgeToTopMm']))
            if 'laserMarkCharHeightMm' in config:
                self.laserHeightSpinBox.setValue(float(config['laserMarkCharHeightMm']))
            if 'laserMarkLengthMm' in config:
                self.laserLengthSpinBox.setValue(float(config['laserMarkLengthMm']))
            if 'laserMarkPositionDeg' in config:
                self.laserPosSpinBox.setValue(int(config['laserMarkPositionDeg']))
            if 'fontSize' in config:
                self.fontSizeSpinBox.setValue(max(1, int(config['fontSize'])))
            if self.colorBarHLineEdit is not None and 'colorBarHigh' in config:
                self.colorBarHLineEdit.setText(str(config['colorBarHigh']))
            if self.colorBarLLineEdit is not None and 'colorBarLow' in config:
                self.colorBarLLineEdit.setText(str(config['colorBarLow']))

            self._set_combo_text(self.waferDiameterComboBox, str(config.get('diameterMm', '')))
            self._set_combo_text(self.waferFlatComboBox, str(config.get('flatOption', '')).replace('-', ' '))
            self._set_combo_text(self.xComboBox, str(config.get('xColumn', '')))
            self._set_combo_text(self.yComboBox, str(config.get('yColumn', '')))
            self._set_combo_text(self.zComboBox, str(config.get('zColumn', '')))
            self._set_combo_text(
                self.waferIDTitleComboBox,
                str(config.get('waferIDColumn', '')),
            )
            self._update_wafer_id_values(str(config.get('waferIDValue', '')))
            self._set_combo_text(self.heatMapOrContourComboBox, str(config.get('mode', '')))
            self.showDetailCheckBox.setChecked(bool(config.get('showDetail', False)))
            self.showDieRCCheckBox.setChecked(bool(config.get('showDieRC', False)))
            self.enableLaserMarkCheckBox.setChecked(bool(config.get('showLaserMark', False)))
        finally:
            self._isApplyingConfig = False
        self._draw_plot_when_ready()

    def _set_combo_text(self, comboBox: QComboBox, text: str) -> None:
        if not text:
            return
        for index in range(comboBox.count()):
            if comboBox.itemText(index).strip().lower() == text.strip().lower():
                comboBox.setCurrentIndex(index)
                return

    def _save_config(self) -> None:
        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Save wafermap config',
            'wafermap_config.json',
            'JSON Files (*.json);;All Files (*)',
        )
        if not selectedFile:
            return
        Path(selectedFile).write_text(
            json.dumps(self._config(), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        self._set_status(f'Wafermap config saved: {selectedFile}')

    def _load_config(self) -> None:
        if self._plot_data().empty:
            QMessageBox.warning(self.rootWidget, 'Wafermap', '請先載入資料')
            self._set_status('請先載入資料', error=True)
            return
        selectedFile, _ = QFileDialog.getOpenFileName(
            self.rootWidget,
            'Load wafermap config',
            '',
            'JSON Files (*.json);;All Files (*)',
        )
        if not selectedFile:
            return
        config = json.loads(Path(selectedFile).read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('Config JSON must be an object.')
        self._apply_config(config)
        self._set_status(f'Wafermap config loaded: {selectedFile}')

    def _download_png(self) -> None:
        if self.currentFigure is None:
            self._set_status('No wafer map to save yet.', error=True)
            return

        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Save wafermap PNG',
            'wafermap.png',
            'PNG Files (*.png);;All Files (*)',
        )
        if selectedFile and not selectedFile.lower().endswith('.png'):
            selectedFile = f'{selectedFile}.png'

        try:
            # Keep behavior aligned with other tabs: always copy current widget image
            # to clipboard, regardless of whether the user saves a file.
            widgetPixmap = self.canvas.grab()
            if widgetPixmap.isNull():
                raise ValueError('Failed to capture wafermap widget image.')

            QApplication.clipboard().setPixmap(widgetPixmap)

            if selectedFile:
                if not widgetPixmap.save(selectedFile, 'PNG'):
                    raise ValueError(f'Failed to save wafermap PNG to {selectedFile}')
                self._set_status(f'Wafermap PNG saved to {selectedFile}, and copied to clipboard.')
            else:
                self._set_status('Wafermap PNG copied to clipboard.')
        except Exception as exc:
            self._set_status(f'Failed to save wafermap PNG: {exc}', error=True)

    def _download_html(self) -> None:
        if self.currentFigure is None:
            self._set_status('No wafer map to save yet.', error=True)
            return
        selectedFile, _ = QFileDialog.getSaveFileName(
            self.rootWidget,
            'Save wafermap HTML',
            'wafermap.html',
            'HTML Files (*.html);;All Files (*)',
        )
        if not selectedFile:
            return
        if not self.currentHtml:
            self.currentHtml = self._figure_html(
                self.currentFigure,
                self.currentTitle or self.mapTitleLineEdit.text().strip() or 'wafer_frame_preview',
            )
        Path(selectedFile).write_text(self.currentHtml, encoding='utf-8')
        self._set_status(f'Wafermap HTML saved: {selectedFile}')

    def _figure_html(self, figure, title: str) -> str:
        imageBytes = figure_to_jpg_bytes(figure)
        imageBase64 = base64.b64encode(imageBytes).decode('ascii')
        escapedTitle = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{escapedTitle}</title>
  <style>
    body {{ margin: 0; background: #ffffff; }}
    img {{ display: block; max-width: 100%; height: auto; margin: 0 auto; }}
  </style>
</head>
<body>
  <img alt="{escapedTitle}" src="data:image/jpeg;base64,{imageBase64}">
</body>
</html>
'''

    def _replace_canvas(self, figure) -> None:
        oldCanvas = self.canvas
        self.plotLayout.removeWidget(oldCanvas)
        oldCanvas.setParent(None)
        oldCanvas.deleteLater()
        self.canvas = FigureCanvas(figure)
        self.plotLayout.addWidget(self.canvas)

    def _set_status(self, message: str, error: bool = False) -> None:
        self.statusLabel.setText(message)
        color = 'rgb(190, 20, 20)' if error else 'rgba(0, 0, 0, 204)'
        self.statusLabel.setStyleSheet(f'QLabel {{ color: {color}; }}')
