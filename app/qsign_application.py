"""Composition root for the current desktop shell."""

from typing import TYPE_CHECKING

from app.pdf_viewer_controller import PDFViewerController
from services.anchors.anchor_detector import AnchorDetector
from services.logging.logging_service import LoggingService
from services.pdf.pdf_service import PDFService
from services.pdf.providers.pymupdf_renderer import (
    PyMuPDFDocumentBackend,
    PyMuPDFRenderer,
)
from services.pdf.providers.pymupdf_provider import PyMuPDFProvider
from services.pdf.providers.pymupdf_signature_writer import PyMuPDFSignatureWriter
from services.templates.template_repository import FilesystemTemplateRepository

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
        pdf_provider = PyMuPDFProvider(logger=self._logger)
        signature_writer = PyMuPDFSignatureWriter(logger=self._logger)
        anchor_detector = AnchorDetector(logger=self._logger)
        template_repository = FilesystemTemplateRepository("templates")
        pdf_service = PDFService(
            backend=PyMuPDFDocumentBackend(renderer),
            renderer=renderer,
            signature_writer=signature_writer,
            logger=self._logger,
        )
        view = MainView(page=page)
        controller = PDFViewerController(
            pdf_service=pdf_service,
            view=view,
            logger=self._logger,
            pdf_provider=pdf_provider,
            anchor_detector=anchor_detector,
            template_repository=template_repository,
        )
        view.bind_actions(
            on_open_document=controller.open_document,
            on_close=controller.close_document,
            on_previous=controller.previous_page,
            on_next=controller.next_page,
            on_zoom_in=controller.zoom_in,
            on_zoom_out=controller.zoom_out,
            on_save_signed_pdf=controller.save_signed_pdf,
            on_manual_signature_rect=controller.set_manual_signature_rectangle,
            on_signature_area_click=controller.open_signature_dialog,
        )
        view.build()
        page.on_close = lambda _: controller.shutdown()
