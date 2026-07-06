"""Abstract PDF page rendering contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from services.pdf.pdf_document import PDFDocumentData


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """Backend-neutral raster result."""

    content: bytes
    width: int
    height: int
    media_type: str


class PDFRenderingError(RuntimeError):
    """Raised when a document cannot be opened or rendered."""


class PDFRenderer(ABC):
    """Port for a future PDF rendering implementation."""

    @abstractmethod
    def open_document(self, document_path: Path) -> PDFDocumentData:
        """Open a document and return its backend-neutral information."""

    @abstractmethod
    def close_document(self) -> None:
        """Close the current document and release cached resources."""

    @abstractmethod
    def render_page(
        self, document_path: Path, page_index: int, scale: float = 1.0
    ) -> RenderedPage:
        """Render one zero-based page without exposing a library-specific type."""

    def render_rotated_page(
        self,
        document_path: Path,
        page_index: int,
        angle: int,
        scale: float = 1.0,
    ) -> RenderedPage:
        """Placeholder for a later rotation capability."""
        raise NotImplementedError("Page rotation is not part of Milestone 2")

    def render_thumbnail(
        self, document_path: Path, page_index: int
    ) -> RenderedPage:
        """Placeholder for a later thumbnail capability."""
        raise NotImplementedError("Thumbnail rendering is not part of Milestone 2")

    def render_annotations(
        self, document_path: Path, page_index: int, scale: float = 1.0
    ) -> RenderedPage:
        """Placeholder for a later annotation capability."""
        raise NotImplementedError("Annotation rendering is not part of Milestone 2")

