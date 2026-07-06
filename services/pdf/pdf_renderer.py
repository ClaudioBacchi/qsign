"""Abstract PDF page rendering contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """Backend-neutral raster result."""

    content: bytes
    width: int
    height: int
    media_type: str


class PDFRenderer(ABC):
    """Port for a future PDF rendering implementation."""

    @abstractmethod
    def render_page(
        self, document_path: Path, page_index: int, scale: float = 1.0
    ) -> RenderedPage:
        """Render one zero-based page without exposing a library-specific type."""

