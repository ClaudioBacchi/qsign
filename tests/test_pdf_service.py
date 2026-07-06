"""Unit tests for library-independent PDF lifecycle orchestration."""

import tempfile
import unittest
from pathlib import Path

from models.pdf_document import PageSize
from services.logging.logging_service import LoggingService
from services.pdf.pdf_document import PDFDocumentBackend, PDFDocumentData
from services.pdf.pdf_service import PDFService


class FakePDFBackend(PDFDocumentBackend):
    def __init__(self) -> None:
        self.saved: list[tuple[Path, Path]] = []

    def inspect(self, path: Path) -> PDFDocumentData:
        return PDFDocumentData(
            page_count=2,
            page_sizes=(PageSize(595.0, 842.0), PageSize(595.0, 842.0)),
            metadata={"title": "QSign sample"},
        )

    def save(self, source: Path, destination: Path) -> None:
        self.saved.append((source, destination))


class PDFServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakePDFBackend()
        self.service = PDFService(
            backend=self.backend,
            logger=LoggingService.create("qsign.tests"),
        )
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary_directory.name) / "sample.pdf"
        self.source.write_bytes(b"%PDF-placeholder")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_open_document(self) -> None:
        document = self.service.open_document(self.source)

        self.assertTrue(document.loaded)
        self.assertEqual(document.filename, "sample.pdf")
        self.assertEqual(document.path, self.source)

    def test_read_metadata(self) -> None:
        self.service.open_document(self.source)

        metadata = self.service.read_metadata()

        self.assertEqual(metadata, {"title": "QSign sample"})
        metadata["title"] = "Changed"
        self.assertEqual(
            self.service.current_document.metadata["title"], "QSign sample"
        )

    def test_count_pages(self) -> None:
        self.service.open_document(self.source)

        self.assertEqual(self.service.count_pages(), 2)

    def test_save_document(self) -> None:
        document = self.service.open_document(self.source)
        document.modified = True

        self.service.save()

        self.assertEqual(self.backend.saved, [(self.source, self.source)])
        self.assertFalse(document.modified)


if __name__ == "__main__":
    unittest.main()

