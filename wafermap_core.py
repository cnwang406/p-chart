from __future__ import annotations

import io
import math
import os
import tempfile

os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.path import Path as MplPath


defaultDiameterMm = 150.0
flatOptions = {
    "47.5 mm": 47.5,
    "57.5 mm": 57.5,
    "notch-180": "notch-180",
    "notch-135": "notch-135",
}
notchWidthMm = 6.0
notchDepthMm = 2.0


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    normalizedColumns = {col: col.strip().lower() for col in df.columns}
    renamedDf = df.rename(columns=normalizedColumns)
    required = {"sitex", "sitey", "thickness"}
    missing = required - set(renamedDf.columns)
    if missing:
        missingText = ", ".join(sorted(missing))
        raise ValueError(
            f"Excel 缺少必要欄位: {missingText}。需要包含 siteX, siteY, thickness。"
        )

    return renamedDf[["sitex", "sitey", "thickness"]].rename(
        columns={"sitex": "siteX", "sitey": "siteY"}
    )


def validate_parameters(
    stepXUm: float,
    stepYUm: float,
    offsetXUm: float,
    offsetYUm: float,
    diameterMm: float,
) -> None:
    if stepXUm <= 0 or stepYUm <= 0:
        raise ValueError("stepX 與 stepY 必須大於 0。")
    if offsetXUm <= -stepXUm or offsetXUm >= stepXUm:
        raise ValueError("offsetX 建議範圍為 (-stepX, stepX)。")
    if offsetYUm <= -stepYUm or offsetYUm >= stepYUm:
        raise ValueError("offsetY 建議範圍為 (-stepY, stepY)。")
    if diameterMm <= 0:
        raise ValueError("wafer diameter 必須大於 0。")


def build_wafer_outline(diameterMm: float, flatOption: str) -> np.ndarray:
    radius = diameterMm / 2.0

    if flatOption in {"notch-180", "notch-135"}:
        halfWidth = notchWidthMm / 2.0
        yJoin = -math.sqrt(max(radius**2 - halfWidth**2, 0.0))
        thetaLeft = math.atan2(yJoin, -halfWidth)
        thetaRight = math.atan2(yJoin, halfWidth)
        arcAngles = np.linspace(thetaRight, thetaLeft + 2.0 * math.pi, 720)
        arc = np.column_stack((radius * np.cos(arcAngles), radius * np.sin(arcAngles)))
        notch = np.array(
            [
                [-halfWidth, yJoin],
                [0.0, -radius + notchDepthMm],
                [halfWidth, yJoin],
            ]
        )
        outline = np.vstack((arc, notch, arc[0]))
        if flatOption == "notch-135":
            rotationDeg = 45.0
            rotationRad = math.radians(rotationDeg)
            rotationMatrix = np.array(
                [
                    [math.cos(rotationRad), -math.sin(rotationRad)],
                    [math.sin(rotationRad), math.cos(rotationRad)],
                ]
            )
            outline = outline @ rotationMatrix.T
        return outline

    flatLength = flatOptions[flatOption]
    halfFlat = float(flatLength) / 2.0
    yFlat = -math.sqrt(max(radius**2 - halfFlat**2, 0.0))
    thetaLeft = math.atan2(yFlat, -halfFlat)
    thetaRight = math.atan2(yFlat, halfFlat)
    arcAngles = np.linspace(thetaRight, thetaLeft + 2.0 * math.pi, 720)
    arc = np.column_stack((radius * np.cos(arcAngles), radius * np.sin(arcAngles)))
    flat = np.array([[-halfFlat, yFlat], [halfFlat, yFlat]])
    return np.vstack((arc, flat, arc[0]))


def polygon_area(points: np.ndarray) -> float:
    if len(points) < 3:
        return 0.0
    xValues = points[:, 0]
    yValues = points[:, 1]
    return 0.5 * np.sum(xValues * np.roll(yValues, -1) - yValues * np.roll(xValues, -1))


def min_distance_to_segments(
    points: np.ndarray,
    segmentStarts: np.ndarray,
    segmentEnds: np.ndarray,
    chunkSize: int = 3000,
) -> np.ndarray:
    if len(points) == 0:
        return np.array([])

    segmentVectors = segmentEnds - segmentStarts
    segmentLengthSquared = np.sum(segmentVectors * segmentVectors, axis=1)
    safeLengthSquared = np.where(segmentLengthSquared > 1e-14, segmentLengthSquared, 1.0)
    minDistances = np.empty(len(points), dtype=float)

    for startIndex in range(0, len(points), chunkSize):
        endIndex = min(startIndex + chunkSize, len(points))
        pointChunk = points[startIndex:endIndex]
        pointToStart = pointChunk[:, None, :] - segmentStarts[None, :, :]
        projected = np.sum(pointToStart * segmentVectors[None, :, :], axis=2) / safeLengthSquared[None, :]
        projected = np.clip(projected, 0.0, 1.0)
        closestPoints = segmentStarts[None, :, :] + projected[:, :, None] * segmentVectors[None, :, :]
        distanceSquared = np.sum((pointChunk[:, None, :] - closestPoints) ** 2, axis=2)
        minDistances[startIndex:endIndex] = np.sqrt(np.min(distanceSquared, axis=1))

    return minDistances


def nearest_value_lookup(
    sourcePoints: np.ndarray,
    sourceValues: np.ndarray,
    queryPoints: np.ndarray,
    chunkSize: int = 3000,
) -> np.ndarray:
    if len(queryPoints) == 0:
        return np.array([])

    nearestValues = np.empty(len(queryPoints), dtype=float)
    for startIndex in range(0, len(queryPoints), chunkSize):
        endIndex = min(startIndex + chunkSize, len(queryPoints))
        queryChunk = queryPoints[startIndex:endIndex]
        distanceSquared = np.sum((queryChunk[:, None, :] - sourcePoints[None, :, :]) ** 2, axis=2)
        nearestIndexes = np.argmin(distanceSquared, axis=1)
        nearestValues[startIndex:endIndex] = sourceValues[nearestIndexes]
    return nearestValues


def build_effective_outline(
    waferOutline: np.ndarray,
    edgeExcludeMm: float,
    gridSize: int = 520,
) -> np.ndarray:
    if edgeExcludeMm <= 0:
        return waferOutline.copy()

    xMin, yMin = waferOutline.min(axis=0)
    xMax, yMax = waferOutline.max(axis=0)
    xValues = np.linspace(xMin, xMax, gridSize)
    yValues = np.linspace(yMin, yMax, gridSize)
    gridX, gridY = np.meshgrid(xValues, yValues)

    outlinePath = MplPath(waferOutline)
    maskInside = outlinePath.contains_points(
        np.column_stack((gridX.ravel(), gridY.ravel()))
    ).reshape(gridX.shape)
    if not np.any(maskInside):
        return np.empty((0, 2))

    segmentStarts = waferOutline[:-1]
    segmentEnds = waferOutline[1:]
    insidePoints = np.column_stack((gridX[maskInside], gridY[maskInside]))
    insideDistances = min_distance_to_segments(insidePoints, segmentStarts, segmentEnds)
    if len(insideDistances) == 0 or float(np.max(insideDistances)) <= edgeExcludeMm:
        return np.empty((0, 2))
    effectiveMask = np.zeros_like(maskInside, dtype=bool)
    effectiveMask[maskInside] = insideDistances >= edgeExcludeMm
    if not np.any(effectiveMask):
        return np.empty((0, 2))

    contourFigure = Figure(figsize=(4, 4), dpi=100)
    contourAxis = contourFigure.add_subplot(111)
    contourSet = contourAxis.contour(xValues, yValues, effectiveMask.astype(float), levels=[0.5])
    contourSegments = contourSet.allsegs[0] if contourSet.allsegs else []

    if not contourSegments:
        return np.empty((0, 2))

    bestSegment = max(contourSegments, key=lambda segment: abs(polygon_area(segment)))
    if len(bestSegment) < 3:
        return np.empty((0, 2))
    if not np.allclose(bestSegment[0], bestSegment[-1]):
        bestSegment = np.vstack((bestSegment, bestSegment[0]))
    return bestSegment


def build_frame_origins(
    axisMin: float,
    axisMax: float,
    pitchMm: float,
    offsetMm: float,
) -> np.ndarray:
    if pitchMm <= 0:
        return np.array([])

    firstIndex = math.floor((axisMin - offsetMm) / pitchMm) - 1
    lastIndex = math.ceil((axisMax - offsetMm) / pitchMm) + 1
    frameIndexes = np.arange(firstIndex, lastIndex + 1)
    return offsetMm + frameIndexes * pitchMm


def build_frame_y_origins_from_top(
    yMin: float,
    yMax: float,
    pitchMm: float,
    topMm: float,
    offsetYMm: float,
    bottomMm: float,
    topReferenceY: float,
    bottomReferenceY: float,
) -> np.ndarray:
    if pitchMm <= 0:
        return np.array([])

    topEdgeStart = topReferenceY - topMm - offsetYMm
    maxRows = int(math.ceil((yMax - yMin + topMm + abs(offsetYMm)) / pitchMm)) + 8
    yOrigins: list[float] = []

    for rowIndex in range(maxRows):
        yTop = topEdgeStart - rowIndex * pitchMm
        yOrigin = yTop - pitchMm
        if yOrigin > yMax:
            continue
        if yOrigin < bottomReferenceY + bottomMm:
            break
        if yTop < yMin:
            break
        yOrigins.append(yOrigin)

    return np.array(yOrigins)


def top_y_at_x(outline: np.ndarray, xRef: float) -> float:
    intersections: list[float] = []

    for index in range(len(outline) - 1):
        pointA = outline[index]
        pointB = outline[index + 1]
        xA, yA = float(pointA[0]), float(pointA[1])
        xB, yB = float(pointB[0]), float(pointB[1])

        if abs(xA - xB) <= 1e-12:
            if abs(xRef - xA) <= 1e-9:
                intersections.append(max(yA, yB))
            continue

        t = (xRef - xA) / (xB - xA)
        if 0.0 <= t <= 1.0:
            yIntersect = yA + t * (yB - yA)
            intersections.append(float(yIntersect))

    if intersections:
        return max(intersections)

    nearestIndex = int(np.argmin(np.abs(outline[:, 0] - xRef)))
    fallbackY = float(outline[nearestIndex, 1])
    return max(fallbackY, float(np.max(outline[:, 1])))


def build_frame_edge_samples(
    xOrigin: float,
    yOrigin: float,
    stepXMm: float,
    stepYMm: float,
    samplesPerEdge: int = 7,
) -> np.ndarray:
    tValues = np.linspace(0.0, 1.0, samplesPerEdge)
    xRight = xOrigin + stepXMm
    yTop = yOrigin + stepYMm
    bottom = np.column_stack((xOrigin + tValues * stepXMm, np.full_like(tValues, yOrigin)))
    right = np.column_stack((np.full_like(tValues, xRight), yOrigin + tValues * stepYMm))
    top = np.column_stack((xRight - tValues * stepXMm, np.full_like(tValues, yTop)))
    left = np.column_stack((np.full_like(tValues, xOrigin), yTop - tValues * stepYMm))
    return np.vstack((bottom, right, top, left))


def is_complete_frame_inside(
    outlinePath: MplPath,
    xOrigin: float,
    yOrigin: float,
    stepXMm: float,
    stepYMm: float,
) -> bool:
    edgeSamples = build_frame_edge_samples(xOrigin, yOrigin, stepXMm, stepYMm)
    inside = outlinePath.contains_points(edgeSamples, radius=1e-9)
    return bool(np.all(inside))


def canonical_edge_key(
    pointA: tuple[float, float],
    pointB: tuple[float, float],
    decimals: int = 6,
) -> tuple[tuple[float, float], tuple[float, float]]:
    roundedA = (round(float(pointA[0]), decimals), round(float(pointA[1]), decimals))
    roundedB = (round(float(pointB[0]), decimals), round(float(pointB[1]), decimals))
    return (roundedA, roundedB) if roundedA <= roundedB else (roundedB, roundedA)


def radial_edge_distance(waferOutline: np.ndarray, radialDirection: np.ndarray) -> float | None:
    if len(waferOutline) < 2:
        return None

    rayPerp = np.array([-radialDirection[1], radialDirection[0]], dtype=float)
    bestDistance: float | None = None
    bestPerpendicularDistance = float("inf")

    for point in waferOutline:
        projection = float(np.dot(point, radialDirection))
        if projection < 0:
            continue
        perpendicularDistance = abs(float(np.dot(point, rayPerp)))
        if perpendicularDistance < bestPerpendicularDistance - 1e-9:
            bestPerpendicularDistance = perpendicularDistance
            bestDistance = projection
        elif abs(perpendicularDistance - bestPerpendicularDistance) <= 1e-9:
            if bestDistance is None or projection > bestDistance:
                bestDistance = projection

    return bestDistance


def build_laser_mark_geometry(
    waferOutline: np.ndarray,
    edgeToMarkTopMm: float,
    charHeightMm: float,
    markerLengthMm: float,
    positionDeg: float,
) -> dict[str, object]:
    debugInfo: dict[str, object] = {
        "positionDeg": float(positionDeg),
        "edgeToMarkTopMm": float(edgeToMarkTopMm),
        "charHeightMm": float(charHeightMm),
        "markerLengthMm": float(markerLengthMm),
        "polygon": None,
    }

    if len(waferOutline) < 3 or charHeightMm <= 0 or markerLengthMm <= 0:
        return debugInfo

    theta = math.radians(float(positionDeg))
    radialDirection = np.array([math.sin(theta), math.cos(theta)], dtype=float)
    tangentDirection = np.array([math.cos(theta), -math.sin(theta)], dtype=float)
    debugInfo["thetaRad"] = float(theta)
    debugInfo["radialDirection"] = radialDirection
    debugInfo["tangentDirection"] = tangentDirection

    edgeDistance = radial_edge_distance(waferOutline, radialDirection)
    debugInfo["edgeDistance"] = edgeDistance
    if edgeDistance is None or edgeDistance <= 0:
        return debugInfo

    topCenter = radialDirection * max(edgeDistance - edgeToMarkTopMm, 0.0)
    center = topCenter - radialDirection * (charHeightMm / 2.0)

    halfLengthVector = tangentDirection * (markerLengthMm / 2.0)
    halfHeightVector = radialDirection * (charHeightMm / 2.0)

    corners = np.vstack(
        (
            center - halfLengthVector + halfHeightVector,
            center + halfLengthVector + halfHeightVector,
            center + halfLengthVector - halfHeightVector,
            center - halfLengthVector - halfHeightVector,
        )
    )
    debugInfo["topCenter"] = topCenter
    debugInfo["center"] = center
    debugInfo["polygon"] = corners
    return debugInfo


def build_laser_mark_polygon(
    waferOutline: np.ndarray,
    edgeToMarkTopMm: float,
    charHeightMm: float,
    markerLengthMm: float,
    positionDeg: float,
) -> np.ndarray | None:
    return build_laser_mark_geometry(
        waferOutline=waferOutline,
        edgeToMarkTopMm=edgeToMarkTopMm,
        charHeightMm=charHeightMm,
        markerLengthMm=markerLengthMm,
        positionDeg=positionDeg,
    ).get("polygon")


def draw_laser_mark(
    ax: Axes,
    waferOutline: np.ndarray,
    showLaserMark: bool,
    edgeToMarkTopMm: float,
    charHeightMm: float,
    markerLengthMm: float,
    positionDeg: float,
    lineColor: str = "#00aa44",
) -> np.ndarray | None:
    debugInfo = build_laser_mark_geometry(
        waferOutline=waferOutline,
        edgeToMarkTopMm=edgeToMarkTopMm,
        charHeightMm=charHeightMm,
        markerLengthMm=markerLengthMm,
        positionDeg=positionDeg,
    )
    # print(
    #     f"[laser-mark] show={showLaserMark} positionDeg={positionDeg} edgeToMarkTopMm={edgeToMarkTopMm} charHeightMm={charHeightMm} markerLengthMm={markerLengthMm}",
    #     flush=True,
    # )
    # print(f"[laser-mark] edgeDistance={debugInfo.get('edgeDistance')}", flush=True)
    # if debugInfo.get("radialDirection") is not None:
    #     radialDirection = debugInfo["radialDirection"]
    #     print(
    #         f"[laser-mark] radialDirection=({radialDirection[0]:.6f},{radialDirection[1]:.6f})",
    #         flush=True,
    #     )
    # if debugInfo.get("topCenter") is not None:
    #     topCenter = debugInfo["topCenter"]
    #     print(f"[laser-mark] topCenter=({topCenter[0]:.3f},{topCenter[1]:.3f})", flush=True)
    # if debugInfo.get("center") is not None:
    #     center = debugInfo["center"]
    #     print(f"[laser-mark] center=({center[0]:.3f},{center[1]:.3f})", flush=True)

    laserMarkPolygon = debugInfo.get("polygon")
    # if laserMarkPolygon is None:
    #     print("[laser-mark] polygon=None", flush=True)
    # else:
    #     debugCorners = "; ".join(
    #         f"corner{index + 1}=({point[0]:.3f},{point[1]:.3f})"
    #         for index, point in enumerate(laserMarkPolygon)
    #     )
    #     print(f"[laser-mark] {debugCorners}", flush=True)

    if not showLaserMark:
        # print("[laser-mark] skipped: disabled", flush=True)
        return None
    if laserMarkPolygon is None:
        return None

    patch = MplPolygon(
        laserMarkPolygon,
        closed=True,
        fill=False,
        edgecolor=lineColor,
        linewidth=0.6,
        linestyle="-",
        alpha=0.55,
        zorder=4.2,
    )
    ax.add_patch(patch)
    return laserMarkPolygon


def draw_frames(
    ax: Axes,
    outline: np.ndarray,
    stepXUm: float,
    stepYUm: float,
    frameOffsetXUm: float,
    frameOffsetYUm: float,
    topMm: float,
    bottomMm: float,
    topReferenceY: float,
    bottomReferenceY: float,
    lineColor: str,
    lineWidth: float = 0.9,
) -> None:
    completeFrames = build_complete_frame_rectangles(
        outline=outline,
        stepXUm=stepXUm,
        stepYUm=stepYUm,
        frameOffsetXUm=frameOffsetXUm,
        frameOffsetYUm=frameOffsetYUm,
        topMm=topMm,
        bottomMm=bottomMm,
        topReferenceY=topReferenceY,
        bottomReferenceY=bottomReferenceY,
    )
    frameEdges: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    for xOrigin, yOrigin, xRight, yTop in completeFrames:
        corners = [
            (xOrigin, yOrigin),
            (xRight, yOrigin),
            (xRight, yTop),
            (xOrigin, yTop),
        ]
        for index in range(4):
            pointA = corners[index]
            pointB = corners[(index + 1) % 4]
            frameEdges.add(canonical_edge_key(pointA, pointB))

    for pointA, pointB in sorted(frameEdges):
        ax.plot(
            [pointA[0], pointB[0]],
            [pointA[1], pointB[1]],
            color=lineColor,
            linewidth=lineWidth,
            linestyle=(0, (4, 4)),
            alpha=0.9,
            zorder=2,
        )


def draw_dies(
    ax: Axes,
    outline: np.ndarray,
    stepXUm: float,
    stepYUm: float,
    arrayX: int,
    arrayY: int,
    frameOffsetXUm: float,
    frameOffsetYUm: float,
    topMm: float,
    topReferenceY: float,
    lineColor: str,
    lineWidth: float = 0.6,
    showDieLabels: bool = False,
) -> None:
    completeDies = build_complete_die_rectangles(
        outline=outline,
        stepXUm=stepXUm,
        stepYUm=stepYUm,
        arrayX=arrayX,
        arrayY=arrayY,
        frameOffsetXUm=frameOffsetXUm,
        frameOffsetYUm=frameOffsetYUm,
        topMm=topMm,
        topReferenceY=topReferenceY,
    )
    dieEdges: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    for xOrigin, yOrigin, xRight, yTop in completeDies:
        corners = [
            (xOrigin, yOrigin),
            (xRight, yOrigin),
            (xRight, yTop),
            (xOrigin, yTop),
        ]
        for index in range(4):
            pointA = corners[index]
            pointB = corners[(index + 1) % 4]
            dieEdges.add(canonical_edge_key(pointA, pointB))

    for pointA, pointB in sorted(dieEdges):
        ax.plot(
            [pointA[0], pointB[0]],
            [pointA[1], pointB[1]],
            color=lineColor,
            linewidth=lineWidth,
            linestyle="-",
            alpha=1.0,
            zorder=1.6,
        )

    if completeDies and showDieLabels:
        minDieLeft = min(die[0] for die in completeDies)
        minDieBottom = min(die[1] for die in completeDies)
        safeArrayX = max(int(arrayX), 1)
        safeArrayY = max(int(arrayY), 1)
        stepXMm = stepXUm / 1000.0
        stepYMm = stepYUm / 1000.0
        dieWidthMm = stepXMm / safeArrayX
        dieHeightMm = stepYMm / safeArrayY

        for dieLeft, dieBottom, dieRight, dieTop in completeDies:
            xIndex = int(round((dieLeft - minDieLeft) / dieWidthMm))
            yIndex = int(round((dieBottom - minDieBottom) / dieHeightMm))
            labelX = xIndex + 1
            labelY = yIndex + 1
            centerX = (dieLeft + dieRight) / 2.0
            centerY = (dieBottom + dieTop) / 2.0
            ax.text(
                centerX,
                centerY,
                f"{labelX},{labelY}",
                ha="center",
                va="center",
                fontsize=6,
                color="#333333",
                zorder=6,
                bbox={"boxstyle": "round,pad=0.1", "fc": "white", "ec": "none", "alpha": 0.65},
            )


def build_complete_rectangles(
    outline: np.ndarray,
    tileWidthMm: float,
    tileHeightMm: float,
    offsetXMm: float,
    offsetYMm: float,
    topMm: float,
    bottomMm: float,
    topReferenceY: float | None = None,
    bottomReferenceY: float | None = None,
    alignCenterX: bool = True,
) -> list[tuple[float, float, float, float]]:
    if tileWidthMm <= 0 or tileHeightMm <= 0:
        return []

    xMin, yMin = outline.min(axis=0)
    xMax, yMax = outline.max(axis=0)
    if topReferenceY is None:
        centerX = (xMin + xMax) / 2.0
        topReferenceY = top_y_at_x(outline, centerX)
    if bottomReferenceY is None:
        bottomReferenceY = yMin
    outlinePath = MplPath(outline)

    xGridOffset = offsetXMm - (tileWidthMm / 2.0 if alignCenterX else 0.0)
    xOrigins = build_frame_origins(xMin, xMax, tileWidthMm, xGridOffset)
    yOrigins = build_frame_y_origins_from_top(
        yMin=yMin,
        yMax=yMax,
        pitchMm=tileHeightMm,
        topMm=topMm,
        offsetYMm=offsetYMm,
        bottomMm=bottomMm,
        topReferenceY=topReferenceY,
        bottomReferenceY=bottomReferenceY,
    )
    completeRects: list[tuple[float, float, float, float]] = []

    for xOrigin in xOrigins:
        xRight = xOrigin + tileWidthMm
        if xRight < xMin or xOrigin > xMax:
            continue

        for yOrigin in yOrigins:
            yTop = yOrigin + tileHeightMm
            if yTop < yMin or yOrigin > yMax:
                continue
            if not is_complete_frame_inside(outlinePath, xOrigin, yOrigin, tileWidthMm, tileHeightMm):
                continue
            completeRects.append((xOrigin, yOrigin, xRight, yTop))

    return completeRects


def build_complete_frame_rectangles(
    outline: np.ndarray,
    stepXUm: float,
    stepYUm: float,
    frameOffsetXUm: float,
    frameOffsetYUm: float,
    topMm: float,
    bottomMm: float,
    topReferenceY: float,
    bottomReferenceY: float,
) -> list[tuple[float, float, float, float]]:
    stepXMm = stepXUm / 1000.0
    stepYMm = stepYUm / 1000.0
    frameOffsetXMm = frameOffsetXUm / 1000.0
    frameOffsetYMm = frameOffsetYUm / 1000.0
    return build_complete_rectangles(
        outline=outline,
        tileWidthMm=stepXMm,
        tileHeightMm=stepYMm,
        offsetXMm=frameOffsetXMm,
        offsetYMm=frameOffsetYMm,
        topMm=topMm,
        bottomMm=bottomMm,
        topReferenceY=topReferenceY,
        bottomReferenceY=bottomReferenceY,
        alignCenterX=True,
    )


def build_complete_die_rectangles(
    outline: np.ndarray,
    stepXUm: float,
    stepYUm: float,
    arrayX: int,
    arrayY: int,
    frameOffsetXUm: float,
    frameOffsetYUm: float,
    topMm: float,
    topReferenceY: float,
) -> list[tuple[float, float, float, float]]:
    safeArrayX = max(int(arrayX), 1)
    safeArrayY = max(int(arrayY), 1)
    stepXMm = stepXUm / 1000.0
    stepYMm = stepYUm / 1000.0
    frameOffsetXMm = frameOffsetXUm / 1000.0
    frameOffsetYMm = frameOffsetYUm / 1000.0
    dieWidthMm = stepXMm / safeArrayX
    dieHeightMm = stepYMm / safeArrayY

    xMin, yMin = outline.min(axis=0)
    xMax, yMax = outline.max(axis=0)
    outlinePath = MplPath(outline)

    frameXOffset = frameOffsetXMm - (stepXMm / 2.0)
    frameXOrigins = build_frame_origins(xMin, xMax, stepXMm, frameXOffset)
    frameYOrigins = build_frame_y_origins_from_top(
        yMin=yMin,
        yMax=yMax,
        pitchMm=stepYMm,
        topMm=topMm,
        offsetYMm=frameOffsetYMm,
        bottomMm=0.0,
        topReferenceY=topReferenceY,
        bottomReferenceY=float(outline[:, 1].min()),
    )

    completeDies: list[tuple[float, float, float, float]] = []
    for frameLeft in frameXOrigins:
        frameRight = frameLeft + stepXMm
        if frameRight < xMin or frameLeft > xMax:
            continue

        for frameBottom in frameYOrigins:
            frameTop = frameBottom + stepYMm
            if frameTop < yMin or frameBottom > yMax:
                continue

            for yIndex in range(safeArrayY):
                dieBottom = frameBottom + yIndex * dieHeightMm
                dieTop = dieBottom + dieHeightMm
                for xIndex in range(safeArrayX):
                    dieLeft = frameLeft + xIndex * dieWidthMm
                    dieRight = dieLeft + dieWidthMm
                    if not is_complete_frame_inside(outlinePath, dieLeft, dieBottom, dieWidthMm, dieHeightMm):
                        continue
                    completeDies.append((dieLeft, dieBottom, dieRight, dieTop))

    return completeDies


def count_complete_frames(
    outline: np.ndarray,
    stepXUm: float,
    stepYUm: float,
    frameOffsetXUm: float,
    frameOffsetYUm: float,
    topMm: float,
    bottomMm: float,
    topReferenceY: float,
    bottomReferenceY: float,
) -> int:
    completeFrames = build_complete_frame_rectangles(
        outline=outline,
        stepXUm=stepXUm,
        stepYUm=stepYUm,
        frameOffsetXUm=frameOffsetXUm,
        frameOffsetYUm=frameOffsetYUm,
        topMm=topMm,
        bottomMm=bottomMm,
        topReferenceY=topReferenceY,
        bottomReferenceY=bottomReferenceY,
    )
    return len(completeFrames)


def render_figure(
    waferOutline: np.ndarray,
    effectiveOutline: np.ndarray,
    title: str,
    stepXUm: float,
    stepYUm: float,
    arrayX: int,
    arrayY: int,
    frameOffsetXUm: float,
    frameOffsetYUm: float,
    topMm: float,
    bottomMm: float,
    topReferenceY: float,
    bottomReferenceY: float,
    showInfoPanel: bool,
    showDieLabels: bool,
    infoPanelText: str,
    signatureText: str,
    frameLineColor: str,
    dieLineColor: str,
    effectiveEdgeColor: str,
    waferEdgeColor: str,
    frameLineWidth: float = 0.9,
    dieLineWidth: float = 0.6,
    effectiveEdgeLineWidth: float = 1.4,
    waferEdgeLineWidth: float = 2.0,
    showLaserMark: bool = False,
    edgeToMarkTopMm: float = 3.0,
    charHeightMm: float = 1.3,
    markerLengthMm: float = 11.0,
    laserMarkPositionDeg: float = 0.0,
    laserMarkColor: str = "#00aa44",
    infoPanelFontSize: int = 6,
) -> Figure:
    radius = np.max(np.linalg.norm(waferOutline, axis=1))
    figureWidth = 10.2 if showInfoPanel else 8.0
    fig = Figure(figsize=(figureWidth, 8), dpi=200)
    ax = fig.add_subplot(111)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f8fbff")
    if showInfoPanel:
        fig.subplots_adjust(right=0.64)

    draw_dies(
        ax,
        effectiveOutline,
        stepXUm,
        stepYUm,
        arrayX,
        arrayY,
        frameOffsetXUm,
        frameOffsetYUm,
        topMm,
        topReferenceY,
        dieLineColor,
        lineWidth=dieLineWidth,
        showDieLabels=showDieLabels,
    )
    draw_frames(
        ax,
        effectiveOutline,
        stepXUm,
        stepYUm,
        frameOffsetXUm,
        frameOffsetYUm,
        topMm,
        bottomMm,
        topReferenceY,
        bottomReferenceY,
        frameLineColor,
        lineWidth=frameLineWidth,
    )
    ax.plot(waferOutline[:, 0], waferOutline[:, 1], color=waferEdgeColor, linewidth=waferEdgeLineWidth, zorder=4)
    if len(effectiveOutline) > 2:
        ax.plot(
            effectiveOutline[:, 0],
            effectiveOutline[:, 1],
            color=effectiveEdgeColor,
            linewidth=effectiveEdgeLineWidth,
            zorder=4,
        )
    draw_laser_mark(
        ax=ax,
        waferOutline=waferOutline,
        showLaserMark=showLaserMark,
        edgeToMarkTopMm=edgeToMarkTopMm,
        charHeightMm=charHeightMm,
        markerLengthMm=markerLengthMm,
        positionDeg=laserMarkPositionDeg,
        lineColor=laserMarkColor,
    )

    margin = max(radius * 0.06, 5.0)
    ax.set_xlim(waferOutline[:, 0].min() - margin, waferOutline[:, 0].max() + margin)
    ax.set_ylim(waferOutline[:, 1].min() - margin, waferOutline[:, 1].max() + margin)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_title(title)
    ax.grid(False)

    if showInfoPanel and infoPanelText:
        fig.text(
            0.72,
            0.50,
            infoPanelText,
            ha="left",
            va="center",
            fontsize=infoPanelFontSize,
            color="#2f2f2f",
            linespacing=1.25,
            bbox={
                "boxstyle": "round,pad=0.35",
                "fc": "white",
                "ec": "#c9c9c9",
                "alpha": 0.95,
            },
            clip_on=False,
        )

    if signatureText:
        fig.text(
            0.99,
            0.008,
            signatureText,
            ha="right",
            va="bottom",
            fontsize=5.5,
            color="#bdbdbd",
        )
    return fig


def figure_to_jpg_bytes(fig: Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="jpg", dpi=300, bbox_inches="tight", facecolor="white")
    buffer.seek(0)
    return buffer.getvalue()
