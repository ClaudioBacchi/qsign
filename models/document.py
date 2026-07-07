"""Canonical document model used by QSign document-processing components."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Point:
    """A point in QSign canonical PDF coordinates."""

    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rectangle:
    """A rectangle in QSign canonical PDF coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if self.right < self.left:
            raise ValueError("Rectangle right edge cannot be left of left edge")
        if self.bottom < self.top:
            raise ValueError("Rectangle bottom edge cannot be above top edge")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class Metadata:
    """Provider-neutral document metadata."""

    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Word:
    """A single text token with canonical geometry."""

    text: str
    bounds: Rectangle
    block_index: int
    line_index: int
    word_index: int


@dataclass(frozen=True, slots=True)
class TextBlock:
    """A contiguous text block extracted from a page."""

    text: str
    bounds: Rectangle
    words: tuple[Word, ...] = ()
    block_index: int = 0


@dataclass(frozen=True, slots=True)
class ImageBlock:
    """A non-text page block."""

    bounds: Rectangle
    block_index: int


@dataclass(frozen=True, slots=True)
class Page:
    """One page in a canonical document."""

    index: int
    width: float
    height: float
    rotation: int = 0
    text_blocks: tuple[TextBlock, ...] = ()
    image_blocks: tuple[ImageBlock, ...] = ()

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("Page index cannot be negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Page dimensions must be positive")

    @property
    def bounds(self) -> Rectangle:
        return Rectangle(0.0, 0.0, self.width, self.height)

    @property
    def words(self) -> tuple[Word, ...]:
        return tuple(word for block in self.text_blocks for word in block.words)


@dataclass(frozen=True, slots=True)
class Document:
    """Provider-neutral representation of a PDF document."""

    source_path: Path
    page_count: int
    pages: tuple[Page, ...]
    metadata: Metadata = field(default_factory=Metadata)

    def __post_init__(self) -> None:
        if self.page_count < 0:
            raise ValueError("Page count cannot be negative")
        if len(self.pages) != self.page_count:
            raise ValueError("Page count must match the number of pages")

    @property
    def has_text_layer(self) -> bool:
        return any(page.words for page in self.pages)
