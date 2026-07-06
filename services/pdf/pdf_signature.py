"""Contracts for future visible and digital PDF signatures."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SignatureArea:
    """Library-independent signature rectangle in PDF points."""

    page_index: int
    x: float
    y: float
    width: float
    height: float


class PDFSignature(ABC):
    """Port implemented when PDF signing technology is selected."""

    @abstractmethod
    def prepare_signature_area(
        self, document_path: Path, area: SignatureArea
    ) -> None:
        """Prepare the visual signature area."""

    @abstractmethod
    def insert_signature_image(
        self, document_path: Path, image: bytes, area: SignatureArea
    ) -> None:
        """Insert a captured signature image."""

    @abstractmethod
    def sign_pdf(self, document_path: Path, certificate_id: str) -> None:
        """Apply a digital signature."""

