"""PyMuPDF writer for visible mouse signatures."""

from pathlib import Path
import re

import pymupdf

from services.logging.logging_service import LoggingService
from services.pdf.pdf_signature import SignatureArea, VisiblePDFSignatureWriter
from services.signature.signature_service import CapturedSignature


class PyMuPDFSignatureWriter(VisiblePDFSignatureWriter):
    """Write a visible signature into a PDF copy using PyMuPDF."""

    _SIGNATURE_VIEWBOX_WIDTH = 420.0
    _SIGNATURE_VIEWBOX_HEIGHT = 180.0

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger

    def save_with_visible_signature(
        self,
        source: Path,
        destination: Path,
        signature: CapturedSignature,
        area: SignatureArea,
    ) -> None:
        """Create a destination PDF with the captured SVG signature drawn in place."""
        if signature.media_type != "image/svg+xml":
            raise ValueError(
                f"Unsupported signature media type: {signature.media_type}"
            )
        if area.width <= 0 or area.height <= 0:
            raise ValueError("Signature area must have positive dimensions")

        strokes = _signature_strokes_from_svg(signature.content)
        if not strokes:
            raise ValueError("Captured signature does not contain drawable strokes")

        destination.parent.mkdir(parents=True, exist_ok=True)
        document = pymupdf.open(source)
        try:
            if not 0 <= area.page_index < document.page_count:
                raise IndexError("Signature page index is outside the document")

            page = document.load_page(area.page_index)
            try:
                shape = page.new_shape()
                scale_x = area.width / self._SIGNATURE_VIEWBOX_WIDTH
                scale_y = area.height / self._SIGNATURE_VIEWBOX_HEIGHT
                stroke_width = max(0.8, min(scale_x, scale_y) * 3.0)
                for stroke in strokes:
                    scaled_points = [
                        pymupdf.Point(
                            area.x + (point[0] * scale_x),
                            area.y + (point[1] * scale_y),
                        )
                        for point in stroke
                    ]
                    shape.draw_polyline(scaled_points)
                    shape.finish(
                        color=(0, 0, 0),
                        width=stroke_width,
                        lineCap=1,
                        lineJoin=1,
                    )
                shape.commit()
            finally:
                del page
            document.save(destination)
        finally:
            document.close()

        self._logger.info(
            "Visible signature written to PDF",
            source=str(source),
            destination=str(destination),
            page=area.page_index,
            x=round(area.x, 2),
            y=round(area.y, 2),
            width=round(area.width, 2),
            height=round(area.height, 2),
        )


def _signature_strokes_from_svg(content: bytes) -> list[list[tuple[float, float]]]:
    svg = content.decode("utf-8", errors="ignore")
    strokes: list[list[tuple[float, float]]] = []
    for match in re.finditer(r"<polyline\b[^>]*\bpoints=(['\"])(.*?)\1", svg):
        points: list[tuple[float, float]] = []
        for point in match.group(2).split():
            x_value, separator, y_value = point.partition(",")
            if not separator:
                continue
            try:
                points.append((float(x_value), float(y_value)))
            except ValueError:
                continue
        if len(points) > 1:
            strokes.append(points)
    return strokes
