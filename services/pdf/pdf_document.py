"""Replaceable boundary for document inspection and persistence."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from models.pdf_document import PageSize


@dataclass(frozen=True, slots=True)
class PDFDocumentData:
    """Backend-neutral information obtained while opening a PDF."""

    page_count: int
    page_sizes: tuple[PageSize, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    signature_present: bool = False


class PDFDocumentBackend(ABC):
    """Port to be implemented by the PDF library selected in a later milestone."""

    @abstractmethod
    def inspect(self, path: Path) -> PDFDocumentData:
        """Read structural document information."""

    @abstractmethod
    def save(self, source: Path, destination: Path) -> None:
        """Persist a document to the requested destination."""


class UnavailablePDFDocumentBackend(PDFDocumentBackend):
    """Explicit placeholder used until a PDF implementation is selected."""

    def inspect(self, path: Path) -> PDFDocumentData:
        raise NotImplementedError("PDF backend is planned for Milestone 2")

    def save(self, source: Path, destination: Path) -> None:
        raise NotImplementedError("PDF backend is planned for Milestone 2")

