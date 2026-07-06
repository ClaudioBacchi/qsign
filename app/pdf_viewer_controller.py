"""Presentation controller for PDF viewing use cases."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from services.logging.logging_service import LoggingService
from services.pdf.pdf_service import PDFService


class PDFViewerView(Protocol):
    """UI operations required by the controller."""

    def display_document(
        self,
        filename: str,
        image_content: bytes,
        image_width: int,
        image_height: int,
        page_number: int,
        page_count: int,
        zoom: float,
    ) -> None: ...

    def clear_document(self) -> None: ...

    def show_error(self, message: str) -> None: ...


@dataclass(slots=True)
class PDFViewerState:
    """Navigation state independent from Flet controls."""

    page_index: int = 0
    page_count: int = 0
    zoom: float = 1.0


class PDFViewerController:
    """Translate viewer actions into PDF service calls."""

    _ZOOM_STEP = 0.25
    _MINIMUM_ZOOM = 0.25
    _MAXIMUM_ZOOM = 4.0

    def __init__(
        self,
        pdf_service: PDFService,
        view: PDFViewerView,
        logger: LoggingService,
    ) -> None:
        self._pdf_service = pdf_service
        self._view = view
        self._logger = logger
        self.state = PDFViewerState()

    def open_document(self, path: str) -> None:
        try:
            document = self._pdf_service.open_document(Path(path))
            self.state = PDFViewerState(page_count=document.page_count)
            self._render_current_page()
        except Exception as error:
            self._logger.exception("Unable to open PDF", path=path)
            self.state = PDFViewerState()
            self._view.clear_document()
            self._view.show_error(str(error))

    def close_document(self) -> None:
        try:
            if self._pdf_service.current_document is not None:
                self._pdf_service.close_document()
        finally:
            self.state = PDFViewerState()
            self._view.clear_document()

    def shutdown(self) -> None:
        """Release document resources without updating a closing window."""
        if self._pdf_service.current_document is not None:
            self._pdf_service.close_document()
        self.state = PDFViewerState()

    def previous_page(self) -> None:
        if self.state.page_index > 0:
            self.state.page_index -= 1
            self._render_current_page()

    def next_page(self) -> None:
        if self.state.page_index + 1 < self.state.page_count:
            self.state.page_index += 1
            self._render_current_page()

    def zoom_in(self) -> None:
        new_zoom = min(
            self._MAXIMUM_ZOOM, self.state.zoom + self._ZOOM_STEP
        )
        self._set_zoom(new_zoom)

    def zoom_out(self) -> None:
        new_zoom = max(
            self._MINIMUM_ZOOM, self.state.zoom - self._ZOOM_STEP
        )
        self._set_zoom(new_zoom)

    def _set_zoom(self, zoom: float) -> None:
        if self.state.page_count and zoom != self.state.zoom:
            self.state.zoom = zoom
            self._render_current_page()

    def _render_current_page(self) -> None:
        document = self._pdf_service.current_document
        if document is None or self.state.page_count == 0:
            return
        try:
            rendered = self._pdf_service.render_page(
                self.state.page_index, self.state.zoom
            )
            self._view.display_document(
                filename=document.filename,
                image_content=rendered.content,
                image_width=rendered.width,
                image_height=rendered.height,
                page_number=self.state.page_index + 1,
                page_count=self.state.page_count,
                zoom=self.state.zoom,
            )
        except Exception as error:
            self._logger.exception(
                "Unable to render PDF page",
                page=self.state.page_index,
                zoom=self.state.zoom,
            )
            self._view.show_error(str(error))
