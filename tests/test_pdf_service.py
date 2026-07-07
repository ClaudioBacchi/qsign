"""Unit tests for library-independent PDF lifecycle orchestration."""

import tempfile
import unittest
from pathlib import Path

from models.pdf_document import PageSize
from services.logging.logging_service import LoggingService
from services.pdf.pdf_document import PDFDocumentBackend, PDFDocumentData
from services.pdf.pdf_renderer import PDFRenderer, RenderedPage
from services.pdf.pdf_service import PDFService
from services.pdf.pdf_signature import SignatureArea, VisiblePDFSignatureWriter
from services.signature.signature_service import CapturedSignature


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


class FakePDFRenderer(PDFRenderer):
    def __init__(self) -> None:
        self.render_calls: list[tuple[Path, int, float]] = []
        self.closed = False

    def open_document(self, document_path: Path) -> PDFDocumentData:
        return PDFDocumentData(page_count=1, page_sizes=(PageSize(100, 200),))

    def close_document(self) -> None:
        self.closed = True

    def render_page(
        self, document_path: Path, page_index: int, scale: float = 1.0
    ) -> RenderedPage:
        self.render_calls.append((document_path, page_index, scale))
        return RenderedPage(b"png", 100, 200, "image/png")


class FakeSignatureWriter(VisiblePDFSignatureWriter):
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, CapturedSignature, SignatureArea]] = []

    def save_with_visible_signature(
        self,
        source: Path,
        destination: Path,
        signature: CapturedSignature,
        area: SignatureArea,
    ) -> None:
        self.calls.append((source, destination, signature, area))


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

    def test_render_page_uses_injected_renderer(self) -> None:
        renderer = FakePDFRenderer()
        service = PDFService(
            backend=self.backend,
            renderer=renderer,
            logger=LoggingService.create("qsign.tests"),
        )
        service.open_document(self.source)

        rendered = service.render_page(0, 1.5)

        self.assertEqual(rendered.content, b"png")
        self.assertEqual(renderer.render_calls, [(self.source, 0, 1.5)])

    def test_close_document_releases_renderer(self) -> None:
        renderer = FakePDFRenderer()
        service = PDFService(
            backend=self.backend,
            renderer=renderer,
            logger=LoggingService.create("qsign.tests"),
        )
        service.open_document(self.source)

        service.close_document()

        self.assertTrue(renderer.closed)
        self.assertIsNone(service.current_document)

    def test_save_signed_preview_uses_injected_signature_writer(self) -> None:
        writer = FakeSignatureWriter()
        service = PDFService(
            backend=self.backend,
            signature_writer=writer,
            logger=LoggingService.create("qsign.tests"),
        )
        service.open_document(self.source)
        signature = CapturedSignature(
            content=b"<svg><polyline points='1,1 2,2'/></svg>",
            media_type="image/svg+xml",
        )
        area = SignatureArea(page_index=1, x=10, y=20, width=90, height=40)
        destination = Path(self.temporary_directory.name) / "signed.pdf"

        saved_path = service.save_signed_preview(signature, area, destination)

        self.assertEqual(saved_path, destination)
        self.assertEqual(writer.calls, [(self.source, destination, signature, area)])

    def test_save_signed_preview_requires_signature_writer(self) -> None:
        self.service.open_document(self.source)

        with self.assertRaises(RuntimeError):
            self.service.save_signed_preview(
                CapturedSignature(content=b"svg", media_type="image/svg+xml"),
                SignatureArea(page_index=0, x=0, y=0, width=10, height=10),
            )


if __name__ == "__main__":
    unittest.main()
