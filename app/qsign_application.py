"""Composition root for the current desktop shell."""

from typing import TYPE_CHECKING

from app.pdf_viewer_controller import PDFViewerController
from services.logging.logging_service import LoggingService
from services.pdf.pdf_service import PDFService
from services.pdf.providers.pymupdf_renderer import (
    PyMuPDFDocumentBackend,
    PyMuPDFRenderer,
)

if TYPE_CHECKING:
    import flet as ft


class QSignApplication:
    """Build the UI and inject application-level callbacks."""

    def __init__(self, logger: LoggingService | None = None) -> None:
        self._logger = logger or LoggingService.create("qsign")

    def main(self, page: "ft.Page") -> None:
        """Configure the QSign desktop window."""
        from ui.main_view import MainView

        self._logger.info("Starting QSign desktop shell")
        renderer = PyMuPDFRenderer(logger=self._logger)
        pdf_service = PDFService(
            backend=PyMuPDFDocumentBackend(renderer),
            renderer=renderer,
            logger=self._logger,
        )
        view = MainView(page=page)
        controller = PDFViewerController(
            pdf_service=pdf_service,
            view=view,
            logger=self._logger,
        )
        view.bind_actions(
            on_open_document=controller.open_document,
            on_close=controller.close_document,
            on_previous=controller.previous_page,
            on_next=controller.next_page,
            on_zoom_in=controller.zoom_in,
            on_zoom_out=controller.zoom_out,
        )
        view.build()
        page.on_close = lambda _: controller.shutdown()
