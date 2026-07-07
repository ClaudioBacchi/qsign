"""Tests for provider-neutral anchor detection."""

import unittest
from pathlib import Path

from models.document import Document, Metadata, Page, Rectangle, TextBlock, Word
from services.anchors import (
    AnchorDetector,
    AnchorResultStatus,
    AnchorSearchMode,
    AnchorSearchOptions,
    AnchorSearchRule,
)
from services.logging.logging_service import LoggingService


class AnchorDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = AnchorDetector(
            logger=LoggingService.create("qsign.tests.anchor_detector")
        )
        self.document = _document(
            _page(
                0,
                [
                    ("Firma", 10, 20, 45, 30),
                    ("Cliente", 50, 20, 95, 30),
                    ("In", 10, 80, 20, 90),
                    ("fede", 25, 80, 55, 90),
                ],
            ),
            _page(
                1,
                [
                    ("firma", 10, 10, 45, 20),
                    ("del", 50, 10, 65, 20),
                    ("Cliente", 70, 10, 115, 20),
                    ("Città", 10, 50, 45, 60),
                ],
            ),
        )

    def test_exact_text_search(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule("signature", ("Firma Cliente",)),
        )

        self.assertEqual(result.status, AnchorResultStatus.MATCHED)
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(result.matches[0].text, "Firma Cliente")
        self.assertEqual(result.matches[0].page_index, 0)
        self.assertEqual(result.matches[0].bounds, Rectangle(10, 20, 95, 30))

    def test_case_insensitive_search(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule(
                "signature",
                ("FIRMA",),
                options=AnchorSearchOptions(case_sensitive=False),
            ),
        )

        self.assertEqual(result.status, AnchorResultStatus.MATCHED)
        self.assertEqual(len(result.matches), 2)
        self.assertEqual([match.page_index for match in result.matches], [0, 1])

    def test_normalized_whitespace_search(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule(
                "signature",
                ("Firma\n\t   Cliente",),
                options=AnchorSearchOptions(normalize_whitespace=True),
            ),
        )

        self.assertEqual(result.status, AnchorResultStatus.MATCHED)
        self.assertEqual(result.matches[0].text, "Firma Cliente")

    def test_optional_accent_normalization(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule(
                "city",
                ("citta",),
                options=AnchorSearchOptions(
                    case_sensitive=False,
                    strip_accents=True,
                ),
            ),
        )

        self.assertEqual(result.status, AnchorResultStatus.MATCHED)
        self.assertEqual(result.matches[0].text, "Città")

    def test_regex_search(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule(
                "signature",
                (r"Firma\s+Cliente",),
                mode=AnchorSearchMode.REGEX,
                options=AnchorSearchOptions(case_sensitive=False),
            ),
        )

        self.assertEqual(result.status, AnchorResultStatus.MATCHED)
        self.assertEqual(result.matches[0].score, 0.95)
        self.assertEqual(result.matches[0].notes, ("regex",))

    def test_multiple_search_expressions(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule(
                "signature",
                ("Firma Cliente", "Firma del Cliente"),
                options=AnchorSearchOptions(case_sensitive=False),
            ),
        )

        self.assertEqual(result.status, AnchorResultStatus.MATCHED)
        self.assertEqual(len(result.matches), 2)
        self.assertEqual(
            [match.text for match in result.matches],
            ["Firma Cliente", "firma del Cliente"],
        )

    def test_multiple_occurrences_do_not_stop_at_first_match(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule(
                "signature",
                ("firma",),
                options=AnchorSearchOptions(case_sensitive=False),
            ),
        )

        self.assertEqual(len(result.matches), 2)
        self.assertEqual([match.occurrence_index for match in result.matches], [1, 2])

    def test_not_found_is_deterministic(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule("missing", ("Responsabile",)),
        )

        self.assertEqual(result.status, AnchorResultStatus.NOT_FOUND)
        self.assertFalse(result.matches)
        self.assertFalse(result.found)

    def test_results_are_ordered_by_page_vertical_and_horizontal_position(self) -> None:
        document = _document(
            _page(
                0,
                [
                    ("Firma", 80, 50, 110, 60),
                    ("Firma", 10, 20, 40, 30),
                    ("Firma", 50, 20, 75, 30),
                ],
            )
        )

        result = self.detector.find(
            document,
            AnchorSearchRule("signature", ("Firma",)),
        )

        self.assertEqual(
            [(match.bounds.top, match.bounds.left) for match in result.matches],
            [(20, 10), (20, 50), (50, 80)],
        )

    def test_exact_score_is_deterministic(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule("signature", ("Firma Cliente",)),
        )

        self.assertEqual(result.matches[0].score, 1.0)

    def test_empty_document_returns_empty_document_status(self) -> None:
        result = self.detector.find(
            Document(
                source_path=Path("empty.pdf"),
                page_count=0,
                pages=(),
                metadata=Metadata(),
            ),
            AnchorSearchRule("signature", ("Firma",)),
        )

        self.assertEqual(result.status, AnchorResultStatus.EMPTY_DOCUMENT)
        self.assertIn("no searchable words", result.notes[0])

    def test_none_rule_returns_invalid_rule_status(self) -> None:
        result = self.detector.find(self.document, None)

        self.assertEqual(result.status, AnchorResultStatus.INVALID_RULE)

    def test_invalid_regex_returns_invalid_rule_status(self) -> None:
        result = self.detector.find(
            self.document,
            AnchorSearchRule(
                "broken",
                ("(",),
                mode=AnchorSearchMode.REGEX,
            ),
        )

        self.assertEqual(result.status, AnchorResultStatus.INVALID_RULE)
        self.assertIn("Invalid regular expression", result.notes[0])

    def test_rule_validation_rejects_empty_expressions(self) -> None:
        with self.assertRaises(Exception):
            AnchorSearchRule("broken", ("",))


def _document(*pages: Page) -> Document:
    return Document(
        source_path=Path("synthetic.pdf"),
        page_count=len(pages),
        pages=pages,
        metadata=Metadata(),
    )


def _page(page_index: int, word_specs: list[tuple[str, int, int, int, int]]) -> Page:
    words = tuple(
        Word(
            text=text,
            bounds=Rectangle(left, top, right, bottom),
            block_index=0,
            line_index=index,
            word_index=0,
        )
        for index, (text, left, top, right, bottom) in enumerate(word_specs)
    )
    text_block = TextBlock(
        text=" ".join(word.text for word in words),
        bounds=Rectangle(
            min(word.bounds.left for word in words),
            min(word.bounds.top for word in words),
            max(word.bounds.right for word in words),
            max(word.bounds.bottom for word in words),
        ),
        words=words,
        block_index=0,
    )
    return Page(
        index=page_index,
        width=200,
        height=300,
        text_blocks=(text_block,),
    )


if __name__ == "__main__":
    unittest.main()
