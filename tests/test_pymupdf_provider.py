"""Tests for PyMuPDF canonical document extraction."""

import unittest
from pathlib import Path

from services.logging.logging_service import LoggingService
from services.pdf.providers.pymupdf_provider import PyMuPDFProvider


class PyMuPDFProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sample = (
            Path(__file__).parents[1]
            / "resources"
            / "sample"
            / "qsign-sample.pdf"
        )

    def setUp(self) -> None:
        self.provider = PyMuPDFProvider(
            logger=LoggingService.create("qsign.tests.pdf_provider")
        )

    def test_load_document_populates_canonical_model(self) -> None:
        document = self.provider.load_document(self.sample)

        self.assertEqual(document.source_path, self.sample)
        self.assertEqual(document.page_count, 2)
        self.assertEqual(len(document.pages), 2)
        self.assertTrue(document.has_text_layer)
        self.assertEqual(document.metadata.values["title"], "QSign Sample")

    def test_load_document_extracts_words_and_coordinates(self) -> None:
        document = self.provider.load_document(self.sample)
        first_page = document.pages[0]

        words = first_page.words

        self.assertGreater(len(words), 0)
        self.assertIn("QSign", [word.text for word in words])
        self.assertGreater(first_page.width, 0)
        self.assertGreater(first_page.height, 0)
        self.assertGreaterEqual(first_page.rotation, 0)
        self.assertTrue(all(word.bounds.width >= 0 for word in words))
        self.assertTrue(all(word.bounds.height >= 0 for word in words))

    def test_missing_file_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.provider.load_document(self.sample.with_name("missing.pdf"))


if __name__ == "__main__":
    unittest.main()
