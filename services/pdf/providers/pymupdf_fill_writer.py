"""PyMuPDF writer for fixed PDF fill elements."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from services.logging.logging_service import LoggingService
from services.pdf.pdf_fill import (
    PDFFillElement,
    PDFSignatureFillElement,
    PDFTextFillElement,
)
from services.pdf.providers.pymupdf_signature_writer import (
    _publish_without_overwrite,
    _temporary_pdf_path,
)
from services.signature.signature_service import CapturedSignature
from services.signature.svg_signature import fit_svg_signature_strokes, parse_svg_signature


class PyMuPDFFillWriter:
    """Write text and saved graphic signatures into a PDF copy using PyMuPDF."""

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger

    def save_filled_pdf(
        self,
        source: Path,
        destination: Path,
        elements: tuple[PDFFillElement, ...],
    ) -> None:
        if not elements:
            raise ValueError("At least one PDF fill element is required")
        if destination.exists():
            raise FileExistsError(f"Filled PDF destination already exists: {destination}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_destination = _temporary_pdf_path(destination)
        document = pymupdf.open(source)
        try:
            for element in elements:
                if isinstance(element, PDFSignatureFillElement):
                    self._draw_signature(document, element)
            for element in elements:
                if isinstance(element, PDFTextFillElement):
                    self._draw_text(document, element)
            document.save(temporary_destination)
        finally:
            document.close()

        try:
            _publish_without_overwrite(temporary_destination, destination)
        finally:
            if temporary_destination.exists():
                temporary_destination.unlink(missing_ok=True)

        self._logger.info(
            "PDF fill elements written",
            source=str(source),
            destination=str(destination),
            elements=len(elements),
        )

    def _draw_text(self, document: pymupdf.Document, element: PDFTextFillElement) -> None:
        if not 0 <= element.page_index < document.page_count:
            raise IndexError("Text fill page index is outside the document")
        text = element.text.strip()
        if not text:
            raise ValueError("Text fill element cannot be empty")
        if element.font_size <= 0:
            raise ValueError("Text font size must be positive")
        rectangle = element.rectangle
        if rectangle.width <= 0 or rectangle.height <= 0:
            raise ValueError("Text rectangle must have positive dimensions")

        page = document.load_page(element.page_index)
        try:
            baseline = rectangle.top + element.font_size
            line_height = element.font_size * 1.2
            for line in self._wrap_text_lines(text, rectangle.width, element.font_size):
                if line:
                    page.insert_text(
                        pymupdf.Point(rectangle.left, baseline),
                        line,
                        fontsize=element.font_size,
                        fontname="helv",
                        color=(0, 0, 0),
                    )
                baseline += line_height
        finally:
            del page

    @staticmethod
    def _wrap_text_lines(
        text: str,
        max_width: float,
        font_size: float,
    ) -> tuple[str, ...]:
        if max_width <= 0:
            return (text,)
        wrapped_lines: list[str] = []
        for paragraph in text.splitlines() or [text]:
            if not paragraph:
                wrapped_lines.append("")
                continue
            line = ""
            for word in paragraph.split(" "):
                candidate = word if not line else f"{line} {word}"
                if PyMuPDFFillWriter._text_width(candidate, font_size) <= max_width:
                    line = candidate
                    continue
                if line:
                    wrapped_lines.append(line)
                if PyMuPDFFillWriter._text_width(word, font_size) <= max_width:
                    line = word
                else:
                    chunks = PyMuPDFFillWriter._wrap_long_word(
                        word,
                        max_width,
                        font_size,
                    )
                    wrapped_lines.extend(chunks[:-1])
                    line = chunks[-1] if chunks else ""
            if line:
                wrapped_lines.append(line)
        return tuple(wrapped_lines)

    @staticmethod
    def _wrap_long_word(
        word: str,
        max_width: float,
        font_size: float,
    ) -> tuple[str, ...]:
        chunks: list[str] = []
        chunk = ""
        for character in word:
            candidate = f"{chunk}{character}"
            if chunk and PyMuPDFFillWriter._text_width(candidate, font_size) > max_width:
                chunks.append(chunk)
                chunk = character
            else:
                chunk = candidate
        if chunk:
            chunks.append(chunk)
        return tuple(chunks)

    @staticmethod
    def _text_width(text: str, font_size: float) -> float:
        return pymupdf.get_text_length(text, fontname="helv", fontsize=font_size)

    def _draw_signature(
        self,
        document: pymupdf.Document,
        element: PDFSignatureFillElement,
    ) -> None:
        if not 0 <= element.page_index < document.page_count:
            raise IndexError("Signature fill page index is outside the document")
        signature = element.signature
        self._validate_signature(signature)
        rectangle = element.rectangle
        if rectangle.width <= 0 or rectangle.height <= 0:
            raise ValueError("Signature rectangle must have positive dimensions")

        page = document.load_page(element.page_index)
        try:
            box = pymupdf.Rect(
                rectangle.left,
                rectangle.top,
                rectangle.right,
                rectangle.bottom,
            )
            if signature.media_type == "image/svg+xml":
                self._draw_legacy_svg_signature(page, signature, rectangle)
            else:
                page.insert_image(
                    box,
                    stream=signature.content,
                    keep_proportion=True,
                )
        finally:
            del page

    @staticmethod
    def _validate_signature(signature: CapturedSignature) -> None:
        if signature.media_type in {"image/png", "image/jpeg", "image/jpg"}:
            if not signature.content:
                raise ValueError("Graphic signature image is empty")
            return
        if signature.media_type != "image/svg+xml":
            raise ValueError(f"Unsupported signature media type: {signature.media_type}")
        if not parse_svg_signature(signature.content).strokes:
            raise ValueError("Captured signature does not contain drawable strokes")

    @staticmethod
    def _draw_legacy_svg_signature(
        page: pymupdf.Page,
        signature: CapturedSignature,
        rectangle: object,
    ) -> None:
        geometry = parse_svg_signature(signature.content)
        strokes, scale = fit_svg_signature_strokes(
            geometry,
            target_x=rectangle.left,
            target_y=rectangle.top,
            target_width=rectangle.width,
            target_height=rectangle.height,
        )
        stroke_width = max(0.8, scale * 3.0)
        for stroke in strokes:
            shape = page.new_shape()
            shape.draw_polyline([pymupdf.Point(x, y) for x, y in stroke])
            shape.finish(
                color=(0, 0, 0),
                width=stroke_width,
                lineCap=1,
                lineJoin=1,
                closePath=False,
            )
            shape.commit()
