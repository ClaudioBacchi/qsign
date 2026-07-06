"""Domain representation of a PDF document."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PageSize:
    """Page dimensions expressed in PDF points."""

    width: float
    height: float

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Page dimensions must be positive")


@dataclass(slots=True)
class PDFDocument:
    """Library-independent state of a document handled by QSign."""

    path: Path
    filename: str
    page_count: int = 0
    page_sizes: tuple[PageSize, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    loaded: bool = False
    modified: bool = False
    signature_present: bool = False

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.filename:
            raise ValueError("A document filename is required")
        if self.page_count < 0:
            raise ValueError("Page count cannot be negative")
        if self.page_sizes and len(self.page_sizes) != self.page_count:
            raise ValueError("Page sizes must match the page count")

