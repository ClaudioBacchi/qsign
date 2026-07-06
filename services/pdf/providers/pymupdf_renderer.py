"""PyMuPDF implementation of the QSign rendering contract."""

from collections import OrderedDict
from pathlib import Path
from threading import RLock
from types import TracebackType

import pymupdf

from models.pdf_document import PageSize
from services.logging.logging_service import LoggingService
from services.pdf.pdf_document import PDFDocumentBackend, PDFDocumentData
from services.pdf.pdf_renderer import PDFRenderer, PDFRenderingError, RenderedPage

_CacheKey = tuple[int, float]


class PyMuPDFDocumentBackend(PDFDocumentBackend):
    """Adapt the renderer session to the Foundation document backend port."""

    def __init__(self, renderer: "PyMuPDFRenderer") -> None:
        self._renderer = renderer

    def inspect(self, path: Path) -> PDFDocumentData:
        return self._renderer.open_document(path)

    def save(self, source: Path, destination: Path) -> None:
        raise NotImplementedError("PDF persistence is not part of Milestone 2")


class PyMuPDFRenderer(PDFRenderer):
    """Render one PDF at a time and retain a bounded page/zoom cache."""

    def __init__(
        self, logger: LoggingService, maximum_cached_pages: int = 12
    ) -> None:
        if maximum_cached_pages < 1:
            raise ValueError("The page cache must contain at least one entry")
        self._logger = logger
        self._maximum_cached_pages = maximum_cached_pages
        self._document: pymupdf.Document | None = None
        self._document_path: Path | None = None
        self._cache: OrderedDict[_CacheKey, RenderedPage] = OrderedDict()
        self._lock = RLock()

    def open_document(self, document_path: Path) -> PDFDocumentData:
        path = Path(document_path)
        if not path.is_file():
            raise FileNotFoundError(path)

        with self._lock:
            self._close_unlocked()
            document: pymupdf.Document | None = None
            try:
                document = pymupdf.open(path)
                if not document.is_pdf:
                    raise PDFRenderingError(f"Not a PDF document: {path}")
                if document.needs_pass:
                    raise PDFRenderingError(
                        "Password-protected PDFs are not supported in Milestone 2"
                    )
                if document.page_count == 0:
                    raise PDFRenderingError("The PDF does not contain any pages")

                page_sizes = self._read_page_sizes(document)
                metadata = {
                    str(key): str(value)
                    for key, value in document.metadata.items()
                    if value not in (None, "")
                }
                data = PDFDocumentData(
                    page_count=document.page_count,
                    page_sizes=page_sizes,
                    metadata=metadata,
                )
                self._document = document
                self._document_path = path.resolve()
                self._logger.info(
                    "PDF renderer opened document",
                    path=str(path),
                    pages=document.page_count,
                )
                return data
            except (FileNotFoundError, PDFRenderingError):
                if document is not None:
                    document.close()
                raise
            except Exception as error:
                if document is not None:
                    document.close()
                raise PDFRenderingError(f"Unable to open PDF: {path}") from error

    def close_document(self) -> None:
        with self._lock:
            self._close_unlocked()

    def render_page(
        self, document_path: Path, page_index: int, scale: float = 1.0
    ) -> RenderedPage:
        if scale <= 0:
            raise ValueError("Render scale must be positive")

        normalized_scale = round(float(scale), 4)
        with self._lock:
            document = self._require_document(document_path)
            if not 0 <= page_index < document.page_count:
                raise IndexError(
                    f"Page index {page_index} is outside the document"
                )

            key = (page_index, normalized_scale)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                self._logger.debug(
                    "PDF page served from cache",
                    page=page_index,
                    scale=normalized_scale,
                )
                return cached

            rendered = self._render_uncached(
                document, page_index, normalized_scale
            )
            self._cache[key] = rendered
            self._cache.move_to_end(key)
            while len(self._cache) > self._maximum_cached_pages:
                self._cache.popitem(last=False)
            return rendered

    def __enter__(self) -> "PyMuPDFRenderer":
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close_document()

    @staticmethod
    def _read_page_sizes(
        document: pymupdf.Document,
    ) -> tuple[PageSize, ...]:
        sizes: list[PageSize] = []
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            try:
                sizes.append(PageSize(page.rect.width, page.rect.height))
            finally:
                del page
        return tuple(sizes)

    def _render_uncached(
        self,
        document: pymupdf.Document,
        page_index: int,
        scale: float,
    ) -> RenderedPage:
        page = document.load_page(page_index)
        pixmap: pymupdf.Pixmap | None = None
        try:
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                alpha=False,
                annots=False,
            )
            result = RenderedPage(
                content=pixmap.tobytes("png"),
                width=pixmap.width,
                height=pixmap.height,
                media_type="image/png",
            )
            self._logger.debug(
                "PDF page rendered", page=page_index, scale=scale
            )
            return result
        except Exception as error:
            raise PDFRenderingError(
                f"Unable to render page {page_index}"
            ) from error
        finally:
            if pixmap is not None:
                del pixmap
            del page

    def _require_document(self, path: Path) -> pymupdf.Document:
        requested_path = Path(path).resolve()
        if self._document is None or self._document_path != requested_path:
            raise PDFRenderingError("The requested PDF is not open")
        return self._document

    def _close_unlocked(self) -> None:
        self._cache.clear()
        if self._document is not None:
            path = self._document_path
            self._document.close()
            self._document = None
            self._document_path = None
            self._logger.info(
                "PDF renderer closed document",
                path=str(path) if path is not None else "",
            )
