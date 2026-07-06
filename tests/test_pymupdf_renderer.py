"""Integration-level unit tests for the concrete rendering provider."""

import unittest
from pathlib import Path
from unittest.mock import patch

from services.logging.logging_service import LoggingService
from services.pdf.pdf_renderer import PDFRenderingError
from services.pdf.providers.pymupdf_renderer import PyMuPDFRenderer


class PyMuPDFRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sample = (
            Path(__file__).parents[1]
            / "resources"
            / "sample"
            / "qsign-sample.pdf"
        )

    def setUp(self) -> None:
        self.renderer = PyMuPDFRenderer(
            logger=LoggingService.create("qsign.tests.renderer"),
            maximum_cached_pages=4,
        )

    def tearDown(self) -> None:
        self.renderer.close_document()

    def test_open_document_reads_page_information(self) -> None:
        data = self.renderer.open_document(self.sample)

        self.assertEqual(data.page_count, 2)
        self.assertEqual(len(data.page_sizes), 2)
        self.assertEqual(data.metadata["title"], "QSign Sample")

    def test_render_page_returns_png(self) -> None:
        self.renderer.open_document(self.sample)

        rendered = self.renderer.render_page(self.sample, 0)

        self.assertTrue(rendered.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(rendered.media_type, "image/png")
        self.assertGreater(rendered.width, 0)
        self.assertGreater(rendered.height, 0)

    def test_same_page_and_zoom_are_rendered_once(self) -> None:
        self.renderer.open_document(self.sample)

        with patch.object(
            self.renderer,
            "_render_uncached",
            wraps=self.renderer._render_uncached,
        ) as uncached_render:
            first = self.renderer.render_page(self.sample, 0, 1.25)
            second = self.renderer.render_page(self.sample, 0, 1.25)

        self.assertIs(first, second)
        uncached_render.assert_called_once()

    def test_cache_evicts_the_least_recently_used_render(self) -> None:
        renderer = PyMuPDFRenderer(
            logger=LoggingService.create("qsign.tests.renderer.lru"),
            maximum_cached_pages=2,
        )
        self.addCleanup(renderer.close_document)
        renderer.open_document(self.sample)

        with patch.object(
            renderer,
            "_render_uncached",
            wraps=renderer._render_uncached,
        ) as uncached_render:
            renderer.render_page(self.sample, 0, 1.0)
            renderer.render_page(self.sample, 0, 1.25)
            renderer.render_page(self.sample, 1, 1.0)
            renderer.render_page(self.sample, 0, 1.0)

        self.assertEqual(uncached_render.call_count, 4)

    def test_zoom_creates_a_distinct_larger_render(self) -> None:
        self.renderer.open_document(self.sample)

        regular = self.renderer.render_page(self.sample, 0, 1.0)
        enlarged = self.renderer.render_page(self.sample, 0, 1.5)

        self.assertGreater(enlarged.width, regular.width)
        self.assertGreater(enlarged.height, regular.height)
        self.assertIsNot(regular, enlarged)

    def test_change_page_returns_different_content(self) -> None:
        self.renderer.open_document(self.sample)

        first_page = self.renderer.render_page(self.sample, 0)
        second_page = self.renderer.render_page(self.sample, 1)

        self.assertNotEqual(first_page.content, second_page.content)

    def test_close_releases_the_open_document(self) -> None:
        self.renderer.open_document(self.sample)
        self.renderer.close_document()

        with self.assertRaises(PDFRenderingError):
            self.renderer.render_page(self.sample, 0)


if __name__ == "__main__":
    unittest.main()
