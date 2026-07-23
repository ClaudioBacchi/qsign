"""Integration tests for visible PDF signature persistence."""

import tempfile
import unittest
from pathlib import Path

import pymupdf

from services.logging.logging_service import LoggingService
from services.pdf.pdf_signature import SignatureArea
from services.pdf.providers.pymupdf_signature_writer import PyMuPDFSignatureWriter
from services.signature.signature_service import CapturedSignature


class PyMuPDFSignatureWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sample = (
            Path(__file__).parents[1]
            / "resources"
            / "sample"
            / "qsign-sample.pdf"
        )

    def test_save_with_visible_signature_creates_signed_copy(self) -> None:
        writer = PyMuPDFSignatureWriter(
            logger=LoggingService.create("qsign.tests.signature_writer")
        )
        signature = CapturedSignature(
            content=(
                b"<svg xmlns='http://www.w3.org/2000/svg' "
                b"width='420' height='180' viewBox='0 0 420 180'>"
                b"<polyline points='20,90 120,50 240,120 360,70' "
                b"fill='none' stroke='black' stroke-width='3'/></svg>"
            ),
            media_type="image/svg+xml",
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "qsign-sample_signed.pdf"

            writer.save_with_visible_signature(
                source=self.sample,
                destination=destination,
                signature=signature,
                area=SignatureArea(
                    page_index=0,
                    x=100,
                    y=600,
                    width=180,
                    height=60,
                ),
            )

            self.assertTrue(destination.is_file())
            saved = pymupdf.open(destination)
            try:
                original = pymupdf.open(self.sample)
                try:
                    self.assertEqual(saved.page_count, original.page_count)
                finally:
                    original.close()
            finally:
                saved.close()

    def test_save_with_visible_signature_rejects_empty_svg(self) -> None:
        writer = PyMuPDFSignatureWriter(
            logger=LoggingService.create("qsign.tests.signature_writer.empty")
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                writer.save_with_visible_signature(
                    source=self.sample,
                    destination=Path(directory) / "signed.pdf",
                    signature=CapturedSignature(
                        content=b"<svg></svg>",
                        media_type="image/svg+xml",
                    ),
                    area=SignatureArea(
                        page_index=0,
                        x=100,
                        y=600,
                        width=180,
                        height=60,
                    ),
                )

    def test_save_with_visible_signature_rejects_existing_destination(self) -> None:
        writer = PyMuPDFSignatureWriter(
            logger=LoggingService.create("qsign.tests.signature_writer.exists")
        )
        signature = CapturedSignature(
            content=(
                b"<svg xmlns='http://www.w3.org/2000/svg' "
                b"width='420' height='180' viewBox='0 0 420 180'>"
                b"<polyline points='20,90 120,50' "
                b"fill='none' stroke='black' stroke-width='3'/></svg>"
            ),
            media_type="image/svg+xml",
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "signed.pdf"
            destination.write_bytes(b"%PDF-existing")

            with self.assertRaises(FileExistsError):
                writer.save_with_visible_signature(
                    source=self.sample,
                    destination=destination,
                    signature=signature,
                    area=SignatureArea(
                        page_index=0,
                        x=100,
                        y=600,
                        width=180,
                        height=60,
                    ),
                )

            self.assertEqual(destination.read_bytes(), b"%PDF-existing")

    def test_separate_svg_strokes_are_not_connected_in_saved_pdf(self) -> None:
        writer = PyMuPDFSignatureWriter(
            logger=LoggingService.create("qsign.tests.signature_writer.strokes")
        )
        signature = CapturedSignature(
            content=(
                b"<svg xmlns='http://www.w3.org/2000/svg' "
                b"width='100' height='100' viewBox='0 0 100 100'>"
                b"<polyline points='10,10 90,10'/>"
                b"<polyline points='10,90 90,90'/></svg>"
            ),
            media_type="image/svg+xml",
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "qsign-sample_signed.pdf"

            writer.save_with_visible_signature(
                source=self.sample,
                destination=destination,
                signature=signature,
                area=SignatureArea(
                    page_index=0,
                    x=100,
                    y=600,
                    width=100,
                    height=100,
                ),
            )

            saved = pymupdf.open(destination)
            try:
                drawings = saved.load_page(0).get_drawings()
            finally:
                saved.close()

        lines = [
            item
            for drawing in drawings
            for item in drawing["items"]
            if item[0] == "l"
            and 100 <= item[1].x <= 200
            and 600 <= item[1].y <= 700
            and 100 <= item[2].x <= 200
            and 600 <= item[2].y <= 700
        ]
        self.assertGreaterEqual(len(lines), 2)
        self.assertTrue(all(line[1].y == line[2].y for line in lines))

    def test_open_svg_polyline_is_not_closed_in_saved_pdf(self) -> None:
        writer = PyMuPDFSignatureWriter(
            logger=LoggingService.create("qsign.tests.signature_writer.open_polyline")
        )
        signature = CapturedSignature(
            content=(
                b"<svg xmlns='http://www.w3.org/2000/svg' "
                b"width='100' height='100' viewBox='0 0 100 100'>"
                b"<polyline points='10,10 90,10 90,90'/></svg>"
            ),
            media_type="image/svg+xml",
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "qsign-sample_signed.pdf"

            writer.save_with_visible_signature(
                source=self.sample,
                destination=destination,
                signature=signature,
                area=SignatureArea(
                    page_index=0,
                    x=100,
                    y=600,
                    width=100,
                    height=100,
                ),
            )

            saved = pymupdf.open(destination)
            try:
                drawings = saved.load_page(0).get_drawings()
            finally:
                saved.close()

        lines = [
            item
            for drawing in drawings
            for item in drawing["items"]
            if item[0] == "l"
            and 100 <= item[1].x <= 200
            and 600 <= item[1].y <= 700
            and 100 <= item[2].x <= 200
            and 600 <= item[2].y <= 700
        ]
        diagonal_lines = [
            line
            for line in lines
            if line[1].x != line[2].x and line[1].y != line[2].y
        ]
        self.assertEqual(diagonal_lines, [])


if __name__ == "__main__":
    unittest.main()
