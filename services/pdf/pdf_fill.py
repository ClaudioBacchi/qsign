"""Provider-neutral contracts for filling a PDF copy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from models.document import Rectangle
from services.signature.signature_service import CapturedSignature


@dataclass(frozen=True, slots=True)
class PDFTextFillElement:
    """Free text placed on a PDF page in PDF points."""

    page_index: int
    rectangle: Rectangle
    text: str
    font_size: float = 12.0


@dataclass(frozen=True, slots=True)
class PDFSignatureFillElement:
    """Saved graphic signature placed on a PDF page in PDF points."""

    page_index: int
    rectangle: Rectangle
    signature: CapturedSignature


PDFFillElement = PDFTextFillElement | PDFSignatureFillElement
PDFFillElementKind = Literal["text", "signature"]


class PDFFillWriter(ABC):
    """Port for writing fill elements into a new PDF copy."""

    @abstractmethod
    def save_filled_pdf(
        self,
        source: Path,
        destination: Path,
        elements: tuple[PDFFillElement, ...],
    ) -> None:
        """Create a destination PDF with all fill elements fixed in place."""
