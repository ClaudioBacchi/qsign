"""PyMuPDF provider for canonical document extraction."""

from pathlib import Path

import pymupdf

from models.document import (
    Document,
    ImageBlock,
    Metadata,
    Page,
    Rectangle,
    TextBlock,
    Word,
)
from services.logging.logging_service import LoggingService
from services.pdf.pdf_provider import PDFProviderError, PdfProvider


class PyMuPDFProvider(PdfProvider):
    """Convert PDF text and layout into QSign canonical models."""

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger

    def load_document(self, path: str | Path) -> Document:
        document_path = Path(path)
        if not document_path.is_file():
            raise FileNotFoundError(document_path)

        pdf: pymupdf.Document | None = None
        try:
            self._logger.info("Loading PDF for canonical parsing", path=str(document_path))
            pdf = pymupdf.open(document_path)
            if not pdf.is_pdf:
                raise PDFProviderError(f"Not a PDF document: {document_path}")
            if pdf.needs_pass:
                raise PDFProviderError("Password-protected PDFs are not supported")

            pages = tuple(self._load_page(pdf, page_index) for page_index in range(pdf.page_count))
            metadata = Metadata(
                {
                    str(key): str(value)
                    for key, value in pdf.metadata.items()
                    if value not in (None, "")
                }
            )
            result = Document(
                source_path=document_path,
                page_count=pdf.page_count,
                pages=pages,
                metadata=metadata,
            )
            self._logger.info(
                "PDF parsed into canonical document",
                path=str(document_path),
                pages=result.page_count,
                has_text_layer=result.has_text_layer,
            )
            return result
        except (FileNotFoundError, PDFProviderError):
            raise
        except Exception as error:
            self._logger.exception("PDF canonical parsing failed", path=str(document_path))
            raise PDFProviderError(f"Unable to parse PDF: {document_path}") from error
        finally:
            if pdf is not None:
                pdf.close()

    def _load_page(self, document: pymupdf.Document, page_index: int) -> Page:
        page = document.load_page(page_index)
        try:
            words = self._load_words(page)
            text_blocks, image_blocks = self._load_blocks(page, words)
            self._logger.debug(
                "PDF page parsed",
                page=page_index,
                text_blocks=len(text_blocks),
                image_blocks=len(image_blocks),
                words=len(words),
            )
            return Page(
                index=page_index,
                width=float(page.rect.width),
                height=float(page.rect.height),
                rotation=int(page.rotation),
                text_blocks=text_blocks,
                image_blocks=image_blocks,
            )
        finally:
            del page

    @staticmethod
    def _load_words(page: pymupdf.Page) -> tuple[Word, ...]:
        words: list[Word] = []
        for item in page.get_text("words"):
            x0, y0, x1, y1, text, block_index, line_index, word_index = item[:8]
            words.append(
                Word(
                    text=str(text),
                    bounds=Rectangle(float(x0), float(y0), float(x1), float(y1)),
                    block_index=int(block_index),
                    line_index=int(line_index),
                    word_index=int(word_index),
                )
            )
        return tuple(words)

    @staticmethod
    def _load_blocks(
        page: pymupdf.Page, words: tuple[Word, ...]
    ) -> tuple[tuple[TextBlock, ...], tuple[ImageBlock, ...]]:
        word_index: dict[int, list[Word]] = {}
        for word in words:
            word_index.setdefault(word.block_index, []).append(word)

        text_blocks: list[TextBlock] = []
        image_blocks: list[ImageBlock] = []
        for block in page.get_text("dict").get("blocks", []):
            block_number = int(block.get("number", 0))
            bounds = _rect_from_sequence(block["bbox"])
            block_type = int(block.get("type", 0))
            if block_type == 0:
                block_text = _text_from_block(block)
                text_blocks.append(
                    TextBlock(
                        text=block_text,
                        bounds=bounds,
                        words=tuple(word_index.get(block_number, [])),
                        block_index=block_number,
                    )
                )
            else:
                image_blocks.append(ImageBlock(bounds=bounds, block_index=block_number))
        return tuple(text_blocks), tuple(image_blocks)


def _rect_from_sequence(values: object) -> Rectangle:
    x0, y0, x1, y1 = values  # type: ignore[misc]
    return Rectangle(float(x0), float(y0), float(x1), float(y1))


def _text_from_block(block: dict[str, object]) -> str:
    lines = block.get("lines", [])
    collected_lines: list[str] = []
    if isinstance(lines, list):
        for line in lines:
            if not isinstance(line, dict):
                continue
            spans = line.get("spans", [])
            if not isinstance(spans, list):
                continue
            collected_lines.append(
                "".join(
                    str(span.get("text", ""))
                    for span in spans
                    if isinstance(span, dict)
                )
            )
    return "\n".join(line for line in collected_lines if line)
