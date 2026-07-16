"""PyMuPDF writer for visible mouse signatures."""

from pathlib import Path
from collections.abc import Sequence
import os
import re
import tempfile
import uuid

import pymupdf

from services.logging.logging_service import LoggingService
from services.pdf.pdf_signature import (
    DigitalPDFSignatureWriter,
    SignatureArea,
    VisiblePDFSignatureWriter,
)
from services.signature.signature_service import CapturedSignature


class PyMuPDFSignatureWriter(VisiblePDFSignatureWriter):
    """Write a visible signature into a PDF copy using PyMuPDF."""

    _SIGNATURE_VIEWBOX_WIDTH = 420.0
    _SIGNATURE_VIEWBOX_HEIGHT = 180.0

    def __init__(
        self,
        logger: LoggingService,
        digital_signature_writer: DigitalPDFSignatureWriter | None = None,
    ) -> None:
        self._logger = logger
        self._digital_signature_writer = digital_signature_writer

    def save_with_visible_signature(
        self,
        source: Path,
        destination: Path,
        signature: CapturedSignature,
        area: SignatureArea,
    ) -> None:
        """Create a destination PDF with the captured SVG signature drawn in place."""
        self.save_with_visible_signatures(source, destination, ((signature, area),))

    def save_with_visible_signatures(
        self,
        source: Path,
        destination: Path,
        signatures: Sequence[tuple[CapturedSignature, SignatureArea]],
    ) -> None:
        """Create a destination PDF with all captured SVG signatures drawn in place."""
        if not signatures:
            raise ValueError("At least one visible signature is required")
        for signature, area in signatures:
            self._validate_signature(signature, area)

        if destination.exists():
            raise FileExistsError(f"Signed PDF destination already exists: {destination}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        visible_destination = _temporary_pdf_path(destination)
        final_temporary_destination = visible_destination
        temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        if self._digital_signature_writer is not None:
            temporary_directory = tempfile.TemporaryDirectory(prefix="qsign-visible-")
            visible_destination = Path(temporary_directory.name) / destination.name
            final_temporary_destination = _temporary_pdf_path(destination)

        document = pymupdf.open(source)
        try:
            for signature, area in signatures:
                self._draw_visible_signature(document, signature, area)
            document.save(visible_destination)
        finally:
            document.close()

        try:
            if self._digital_signature_writer is not None:
                _, area = signatures[0]
                self._digital_signature_writer.sign_pdf(
                    source=visible_destination,
                    destination=final_temporary_destination,
                    area=area,
                )
            _publish_without_overwrite(final_temporary_destination, destination)
        finally:
            if temporary_directory is not None:
                temporary_directory.cleanup()
            if final_temporary_destination.exists():
                final_temporary_destination.unlink(missing_ok=True)

        self._logger.info(
            "Visible signatures written to PDF",
            source=str(source),
            destination=str(destination),
            signatures=len(signatures),
        )

    def _validate_signature(
        self, signature: CapturedSignature, area: SignatureArea
    ) -> None:
        if signature.media_type != "image/svg+xml":
            raise ValueError(
                f"Unsupported signature media type: {signature.media_type}"
            )
        if area.width <= 0 or area.height <= 0:
            raise ValueError("Signature area must have positive dimensions")

        strokes = _signature_strokes_from_svg(signature.content)
        if not strokes:
            raise ValueError("Captured signature does not contain drawable strokes")

    def _draw_visible_signature(
        self,
        document: pymupdf.Document,
        signature: CapturedSignature,
        area: SignatureArea,
    ) -> None:
        if not 0 <= area.page_index < document.page_count:
            raise IndexError("Signature page index is outside the document")

        strokes = _signature_strokes_from_svg(signature.content)
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


def _temporary_pdf_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination.parent / f".{destination.stem}.{uuid.uuid4().hex}.tmp.pdf"


def _publish_without_overwrite(source: Path, destination: Path) -> None:
    reserved = False
    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        reserved = True
        os.replace(source, destination)
    except FileExistsError:
        raise FileExistsError(
            f"Signed PDF destination already exists: {destination}"
        ) from None
    except Exception:
        if reserved:
            destination.unlink(missing_ok=True)
        raise
