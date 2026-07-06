"""Tests for document navigation state without loading Flet."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.pdf_viewer_controller import PDFViewerController
from models.pdf_document import PDFDocument
from services.logging.logging_service import LoggingService
from services.pdf.pdf_renderer import RenderedPage
from services.pdf.pdf_service import PDFService


class FakeViewer:
    def __init__(self) -> None:
        self.pages: list[tuple[int, int, float, bytes]] = []
        self.cleared = False
        self.errors: list[str] = []

    def display_document(
        self,
        filename: str,
        image_content: bytes,
        image_width: int,
        image_height: int,
        page_number: int,
        page_count: int,
        zoom: float,
    ) -> None:
        self.pages.append((page_number, page_count, zoom, image_content))

    def clear_document(self) -> None:
        self.cleared = True

    def show_error(self, message: str) -> None:
        self.errors.append(message)


class PDFViewerControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = PDFDocument(
            path=Path("sample.pdf"),
            filename="sample.pdf",
            page_count=2,
            loaded=True,
        )
        self.service = MagicMock(spec=PDFService)
        self.service.open_document.return_value = self.document
        self.service.current_document = self.document
        self.service.render_page.side_effect = (
            lambda page, zoom: RenderedPage(
                content=f"{page}:{zoom}".encode(),
                width=100,
                height=200,
                media_type="image/png",
            )
        )
        self.view = FakeViewer()
        self.controller = PDFViewerController(
            pdf_service=self.service,
            view=self.view,
            logger=LoggingService.create("qsign.tests.controller"),
        )

    def test_change_page_renders_the_next_page(self) -> None:
        self.controller.open_document("sample.pdf")

        self.controller.next_page()

        self.assertEqual(self.controller.state.page_index, 1)
        self.assertEqual(self.view.pages[-1][0], 2)
        self.service.render_page.assert_called_with(1, 1.0)

    def test_previous_page_does_not_move_before_first_page(self) -> None:
        self.controller.open_document("sample.pdf")

        self.controller.previous_page()

        self.assertEqual(self.controller.state.page_index, 0)
        self.assertEqual(self.service.render_page.call_count, 1)

    def test_zoom_changes_render_scale(self) -> None:
        self.controller.open_document("sample.pdf")

        self.controller.zoom_in()

        self.assertEqual(self.controller.state.zoom, 1.25)
        self.service.render_page.assert_called_with(0, 1.25)


if __name__ == "__main__":
    unittest.main()
