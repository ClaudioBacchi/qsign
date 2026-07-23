"""Helpers for mouse/Wacom SVG signature geometry."""

from __future__ import annotations

import re
from dataclasses import dataclass


DEFAULT_SIGNATURE_VIEWBOX_WIDTH = 420.0
DEFAULT_SIGNATURE_VIEWBOX_HEIGHT = 180.0
DEFAULT_SIGNATURE_PADDING = 6.0


Point = tuple[float, float]
Stroke = tuple[Point, ...]


@dataclass(frozen=True, slots=True)
class SvgSignatureGeometry:
    strokes: tuple[Stroke, ...]
    viewbox_width: float = DEFAULT_SIGNATURE_VIEWBOX_WIDTH
    viewbox_height: float = DEFAULT_SIGNATURE_VIEWBOX_HEIGHT


def parse_svg_signature(content: bytes) -> SvgSignatureGeometry:
    svg = content.decode("utf-8", errors="ignore")
    viewbox_width, viewbox_height = _svg_viewbox_size(svg)
    strokes: list[Stroke] = []
    for match in re.finditer(r"<polyline\b[^>]*\bpoints=(['\"])(.*?)\1", svg):
        points: list[Point] = []
        for point in match.group(2).split():
            x_value, separator, y_value = point.partition(",")
            if not separator:
                continue
            try:
                points.append((float(x_value), float(y_value)))
            except ValueError:
                continue
        if len(points) > 1:
            strokes.append(tuple(points))
    return SvgSignatureGeometry(
        strokes=tuple(strokes),
        viewbox_width=viewbox_width,
        viewbox_height=viewbox_height,
    )


def fit_svg_signature_strokes(
    geometry: SvgSignatureGeometry,
    *,
    target_width: float,
    target_height: float,
    target_x: float = 0.0,
    target_y: float = 0.0,
    padding: float = DEFAULT_SIGNATURE_PADDING,
) -> tuple[tuple[Stroke, ...], float]:
    if not geometry.strokes or target_width <= 0 or target_height <= 0:
        return (), 1.0

    left, top, right, bottom = _stroke_bounds(geometry.strokes)
    left = max(0.0, left - padding)
    top = max(0.0, top - padding)
    right = min(geometry.viewbox_width, right + padding)
    bottom = min(geometry.viewbox_height, bottom + padding)
    source_width = max(1.0, right - left)
    source_height = max(1.0, bottom - top)
    scale = min(target_width / source_width, target_height / source_height)
    offset_x = target_x + (target_width - source_width * scale) / 2.0
    offset_y = target_y + (target_height - source_height * scale) / 2.0

    transformed: list[Stroke] = []
    for stroke in geometry.strokes:
        transformed.append(
            tuple(
                (
                    offset_x + (x - left) * scale,
                    offset_y + (y - top) * scale,
                )
                for x, y in stroke
            )
        )
    return tuple(transformed), scale


def _svg_viewbox_size(svg: str) -> tuple[float, float]:
    match = re.search(r"\bviewBox=(['\"])\s*([^'\"]+?)\s*\1", svg)
    if match:
        parts = match.group(2).replace(",", " ").split()
        if len(parts) == 4:
            try:
                width = float(parts[2])
                height = float(parts[3])
                if width > 0 and height > 0:
                    return width, height
            except ValueError:
                pass
    return DEFAULT_SIGNATURE_VIEWBOX_WIDTH, DEFAULT_SIGNATURE_VIEWBOX_HEIGHT


def _stroke_bounds(strokes: tuple[Stroke, ...]) -> tuple[float, float, float, float]:
    points = [point for stroke in strokes for point in stroke]
    left = min(point[0] for point in points)
    top = min(point[1] for point in points)
    right = max(point[0] for point in points)
    bottom = max(point[1] for point in points)
    return left, top, right, bottom

