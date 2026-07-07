"""Tests for provider-neutral document models."""

import unittest
from pathlib import Path

from models.document import Document, Page, Rectangle


class DocumentModelTests(unittest.TestCase):
    def test_rectangle_rejects_invalid_edges(self) -> None:
        with self.assertRaises(ValueError):
            Rectangle(left=10, top=0, right=5, bottom=10)

    def test_document_requires_matching_page_count(self) -> None:
        with self.assertRaises(ValueError):
            Document(
                source_path=Path("sample.pdf"),
                page_count=2,
                pages=(Page(index=0, width=100, height=200),),
            )

    def test_page_exposes_canonical_bounds(self) -> None:
        page = Page(index=0, width=100, height=200)

        self.assertEqual(page.bounds, Rectangle(0.0, 0.0, 100, 200))


if __name__ == "__main__":
    unittest.main()
