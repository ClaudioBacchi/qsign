"""Integration tests for PDF fill persistence."""

import tempfile
import unittest
import base64
from pathlib import Path

import pymupdf

from models.document import Rectangle
from services.logging.logging_service import LoggingService
from services.pdf.pdf_fill import PDFSignatureFillElement, PDFTextFillElement
from services.pdf.providers.pymupdf_fill_writer import PyMuPDFFillWriter
from services.signature.signature_service import CapturedSignature


class PyMuPDFFillWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sample = (
            Path(__file__).parents[1]
            / "resources"
            / "sample"
            / "qsign-sample.pdf"
        )
        cls.signature_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )

    def test_save_filled_pdf_writes_free_text(self) -> None:
        writer = PyMuPDFFillWriter(
            logger=LoggingService.create("qsign.tests.fill_writer.text")
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "filled.pdf"

            writer.save_filled_pdf(
                self.sample,
                destination,
                (
                    PDFTextFillElement(
                        page_index=0,
                        rectangle=Rectangle(80, 80, 300, 120),
                        text="Testo libero QSign",
                        font_size=14,
                    ),
                ),
            )

            saved = pymupdf.open(destination)
            try:
                self.assertIn("Testo libero QSign", saved.load_page(0).get_text())
            finally:
                saved.close()

    def test_save_filled_pdf_writes_text_from_tiny_selected_area(self) -> None:
        writer = PyMuPDFFillWriter(
            logger=LoggingService.create("qsign.tests.fill_writer.tiny_text")
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "filled.pdf"

            writer.save_filled_pdf(
                self.sample,
                destination,
                (
                    PDFTextFillElement(
                        page_index=0,
                        rectangle=Rectangle(80, 80, 90, 86),
                        text="Testo area minima",
                        font_size=14,
                    ),
                ),
            )

            saved = pymupdf.open(destination)
            try:
                saved_text = saved.load_page(0).get_text()
            finally:
                saved.close()

        self.assertTrue(all(character in saved_text for character in "Testoareaminima"))

    def test_save_filled_pdf_wraps_text_inside_selected_width(self) -> None:
        writer = PyMuPDFFillWriter(
            logger=LoggingService.create("qsign.tests.fill_writer.wrap_text")
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "blank.pdf"
            destination = Path(directory) / "filled.pdf"
            blank = pymupdf.open()
            try:
                blank.new_page(width=300, height=200)
                blank.save(source)
            finally:
                blank.close()

            writer.save_filled_pdf(
                source,
                destination,
                (
                    PDFTextFillElement(
                        page_index=0,
                        rectangle=Rectangle(50, 60, 135, 130),
                        text="Uno due tre quattro cinque sei",
                        font_size=14,
                    ),
                ),
            )

            saved = pymupdf.open(destination)
            try:
                words = saved.load_page(0).get_text("words")
            finally:
                saved.close()

        filled_words = [
            word
            for word in words
            if word[4] in {"Uno", "due", "tre", "quattro", "cinque", "sei"}
        ]
        self.assertGreaterEqual(len(filled_words), 6)
        self.assertTrue(all(word[2] <= 136 for word in filled_words))

    def test_save_filled_pdf_keeps_text_visible_over_opaque_graphic(self) -> None:
        writer = PyMuPDFFillWriter(
            logger=LoggingService.create("qsign.tests.fill_writer.visible_text")
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "blank.pdf"
            destination = Path(directory) / "filled.pdf"
            blank = pymupdf.open()
            try:
                blank.new_page(width=300, height=200)
                blank.save(source)
            finally:
                blank.close()
            image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1), False)
            image.clear_with(255)
            signature = CapturedSignature(
                content=image.tobytes("png"),
                media_type="image/png",
            )

            writer.save_filled_pdf(
                source,
                destination,
                (
                    PDFTextFillElement(
                        page_index=0,
                        rectangle=Rectangle(80, 80, 220, 110),
                        text="VISIBILE",
                        font_size=18,
                    ),
                    PDFSignatureFillElement(
                        page_index=0,
                        rectangle=Rectangle(70, 70, 240, 125),
                        signature=signature,
                    ),
                ),
            )

            saved = pymupdf.open(destination)
            try:
                page = saved.load_page(0)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                stride = pixmap.width * pixmap.n
                dark_pixels = 0
                for y in range(150, 230):
                    row = y * stride
                    for x in range(150, 440):
                        offset = row + x * pixmap.n
                        red, green, blue = pixmap.samples[offset : offset + 3]
                        if red < 80 and green < 80 and blue < 80:
                            dark_pixels += 1
                self.assertGreater(dark_pixels, 20)
            finally:
                saved.close()

    def test_save_filled_pdf_writes_saved_graphic_signature(self) -> None:
        writer = PyMuPDFFillWriter(
            logger=LoggingService.create("qsign.tests.fill_writer.signature")
        )
        signature = CapturedSignature(
            content=self.signature_png,
            media_type="image/png",
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "filled.pdf"

            writer.save_filled_pdf(
                self.sample,
                destination,
                (
                    PDFSignatureFillElement(
                        page_index=0,
                        rectangle=Rectangle(100, 600, 220, 660),
                        signature=signature,
                    ),
                ),
            )

            saved = pymupdf.open(destination)
            try:
                images = saved.load_page(0).get_images()
            finally:
                saved.close()

        self.assertGreaterEqual(len(images), 1)

    def test_save_filled_pdf_writes_acquired_signature_text_and_graphic_signature(
        self,
    ) -> None:
        writer = PyMuPDFFillWriter(
            logger=LoggingService.create("qsign.tests.fill_writer.mixed")
        )
        mouse_signature = CapturedSignature(
            content=b"<svg><polyline points='1,1 20,20 40,5'/></svg>",
            media_type="image/svg+xml",
        )
        graphic_signature = CapturedSignature(
            content=self.signature_png,
            media_type="image/png",
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "filled.pdf"

            writer.save_filled_pdf(
                self.sample,
                destination,
                (
                    PDFSignatureFillElement(
                        page_index=0,
                        rectangle=Rectangle(80, 420, 180, 470),
                        signature=mouse_signature,
                    ),
                    PDFTextFillElement(
                        page_index=0,
                        rectangle=Rectangle(80, 500, 210, 550),
                        text="Testo e firme insieme",
                        font_size=12,
                    ),
                    PDFSignatureFillElement(
                        page_index=0,
                        rectangle=Rectangle(80, 560, 180, 610),
                        signature=graphic_signature,
                    ),
                ),
            )

            saved = pymupdf.open(destination)
            try:
                page = saved.load_page(0)
                self.assertIn("Testo e firme insieme", page.get_text())
                self.assertGreaterEqual(len(page.get_images()), 1)
                drawings = page.get_drawings()
            finally:
                saved.close()

        self.assertGreaterEqual(len(drawings), 1)


if __name__ == "__main__":
    unittest.main()
