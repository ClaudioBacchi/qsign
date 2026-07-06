"""PDF use-case service independent from any concrete PDF library."""

from pathlib import Path

from models.pdf_document import PDFDocument, PageSize
from services.logging.logging_service import LoggingService
from services.pdf.pdf_document import PDFDocumentBackend
from services.pdf.pdf_renderer import RenderedPage
from services.pdf.pdf_signature import SignatureArea


class PDFService:
    """Manage one PDF lifecycle through an injected backend."""

    def __init__(
        self, backend: PDFDocumentBackend, logger: LoggingService
    ) -> None:
        self._backend = backend
        self._logger = logger
        self._document: PDFDocument | None = None

    @property
    def current_document(self) -> PDFDocument | None:
        return self._document

    def open_document(self, path: str | Path) -> PDFDocument:
        document_path = Path(path)
        if not document_path.is_file():
            raise FileNotFoundError(document_path)

        data = self._backend.inspect(document_path)
        document = PDFDocument(
            path=document_path,
            filename=document_path.name,
            page_count=data.page_count,
            page_sizes=data.page_sizes,
            metadata=dict(data.metadata),
            loaded=True,
            signature_present=data.signature_present,
        )
        self._document = document
        self._logger.info("PDF opened", path=str(document_path))
        return document

    def close_document(self) -> None:
        document = self._require_document()
        document.loaded = False
        self._document = None
        self._logger.info("PDF closed", path=str(document.path))

    def read_metadata(self) -> dict[str, str]:
        return dict(self._require_document().metadata)

    def count_pages(self) -> int:
        return self._require_document().page_count

    def page_sizes(self) -> tuple[PageSize, ...]:
        return self._require_document().page_sizes

    def save(self) -> None:
        document = self._require_document()
        self._backend.save(document.path, document.path)
        document.modified = False
        self._logger.info("PDF saved", path=str(document.path))

    def save_as(self, destination: str | Path) -> PDFDocument:
        document = self._require_document()
        destination_path = Path(destination)
        self._backend.save(document.path, destination_path)
        document.path = destination_path
        document.filename = destination_path.name
        document.modified = False
        self._logger.info("PDF saved with a new name", path=str(destination_path))
        return document

    def prepare_signature_area(self, area: SignatureArea) -> None:
        raise NotImplementedError("Signature areas are planned for a later milestone")

    def insert_signature_image(self, image: bytes, area: SignatureArea) -> None:
        raise NotImplementedError("Signature images are planned for a later milestone")

    def sign_pdf(self, certificate_id: str) -> None:
        raise NotImplementedError("PAdES signing is planned for Milestone 5")

    def render_page(self, page_index: int, scale: float = 1.0) -> RenderedPage:
        raise NotImplementedError("PDF rendering is planned for Milestone 2")

    def _require_document(self) -> PDFDocument:
        if self._document is None or not self._document.loaded:
            raise RuntimeError("No PDF document is open")
        return self._document

