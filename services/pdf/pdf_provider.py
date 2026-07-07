"""Provider boundary for canonical PDF document extraction."""

from abc import ABC, abstractmethod
from pathlib import Path

from models.document import Document


class PDFProviderError(RuntimeError):
    """Raised when a PDF provider cannot extract a canonical document."""


class PdfProvider(ABC):
    """Extract provider-neutral document content from a PDF file."""

    @abstractmethod
    def load_document(self, path: str | Path) -> Document:
        """Open and convert a PDF into QSign's canonical document model."""
