"""Deterministic provider-neutral anchor detection."""

import re
import time
from dataclasses import dataclass
from re import Pattern

from models.document import Document, Page, Rectangle, Word
from services.anchors.anchor_models import (
    AnchorMatch,
    AnchorResult,
    AnchorResultStatus,
    AnchorSearchMode,
    AnchorSearchRule,
)
from services.anchors.text_normalization import normalize_text
from services.logging.logging_service import LoggingService


@dataclass(frozen=True, slots=True)
class _PageTextMap:
    page: Page
    text: str
    words_by_character: tuple[Word | None, ...]
    word_count: int


@dataclass(frozen=True, slots=True)
class _PendingMatch:
    expression: str
    page_index: int
    text: str
    bounds: Rectangle
    score: float
    notes: tuple[str, ...]


class AnchorDetector:
    """Locate anchor occurrences inside QSign canonical documents."""

    def __init__(self, logger: LoggingService) -> None:
        self._logger = logger

    def find(self, document: Document, rule: AnchorSearchRule | None) -> AnchorResult:
        """Apply one anchor rule and return every occurrence in stable order."""

        start = time.perf_counter()
        if rule is None:
            return self._result(
                rule_id="",
                status=AnchorResultStatus.INVALID_RULE,
                notes=("Anchor rule cannot be None",),
                start=start,
            )

        self._logger.info(
            "Anchor search started",
            rule_id=rule.rule_id,
            mode=rule.mode.value,
            expressions=len(rule.expressions),
            pages=document.page_count,
        )

        page_maps = tuple(self._build_page_text_map(page, rule) for page in document.pages)
        words_analyzed = sum(page_map.word_count for page_map in page_maps)
        if document.page_count == 0 or words_analyzed == 0:
            self._logger.warning(
                "Anchor search skipped empty document",
                rule_id=rule.rule_id,
                pages=document.page_count,
                words=words_analyzed,
            )
            return self._result(
                rule_id=rule.rule_id,
                status=AnchorResultStatus.EMPTY_DOCUMENT,
                notes=("Document contains no searchable words",),
                pages_analyzed=document.page_count,
                words_analyzed=words_analyzed,
                start=start,
            )

        try:
            pending = self._find_pending_matches(page_maps, rule)
        except re.error as error:
            self._logger.error(
                "Anchor regex is invalid",
                rule_id=rule.rule_id,
                error=str(error),
            )
            return self._result(
                rule_id=rule.rule_id,
                status=AnchorResultStatus.INVALID_RULE,
                notes=(f"Invalid regular expression: {error}",),
                pages_analyzed=document.page_count,
                words_analyzed=words_analyzed,
                start=start,
            )

        ordered = sorted(
            pending,
            key=lambda match: (
                match.page_index,
                match.bounds.top,
                match.bounds.left,
                match.expression,
                match.text,
            ),
        )
        matches = tuple(
            AnchorMatch(
                rule_id=rule.rule_id,
                expression=match.expression,
                page_index=match.page_index,
                text=match.text,
                bounds=match.bounds,
                score=match.score,
                occurrence_index=index,
                notes=match.notes,
            )
            for index, match in enumerate(ordered, start=1)
        )

        status = (
            AnchorResultStatus.MATCHED
            if matches
            else AnchorResultStatus.NOT_FOUND
        )
        result = self._result(
            rule_id=rule.rule_id,
            status=status,
            matches=matches,
            pages_analyzed=document.page_count,
            words_analyzed=words_analyzed,
            start=start,
        )
        self._logger.info(
            "Anchor search completed",
            rule_id=rule.rule_id,
            status=result.status.value,
            matches=len(result.matches),
            pages=result.pages_analyzed,
            words=result.words_analyzed,
            elapsed_ms=round(result.elapsed_ms, 3),
        )
        return result

    def _find_pending_matches(
        self, page_maps: tuple[_PageTextMap, ...], rule: AnchorSearchRule
    ) -> tuple[_PendingMatch, ...]:
        if rule.mode == AnchorSearchMode.EXACT:
            return self._find_exact_matches(page_maps, rule)
        if rule.mode == AnchorSearchMode.REGEX:
            return self._find_regex_matches(page_maps, rule)
        return ()

    def _find_exact_matches(
        self, page_maps: tuple[_PageTextMap, ...], rule: AnchorSearchRule
    ) -> tuple[_PendingMatch, ...]:
        matches: list[_PendingMatch] = []
        normalized_expressions = tuple(
            (expression, normalize_text(expression, rule.options))
            for expression in rule.expressions
        )
        for page_map in page_maps:
            for expression, normalized_expression in normalized_expressions:
                start = 0
                while True:
                    index = page_map.text.find(normalized_expression, start)
                    if index < 0:
                        break
                    end = index + len(normalized_expression)
                    match = self._pending_match(
                        page_map=page_map,
                        start=index,
                        end=end,
                        expression=expression,
                        score=1.0,
                        notes=("exact",),
                    )
                    if match is not None:
                        matches.append(match)
                    start = index + 1
        return tuple(matches)

    def _find_regex_matches(
        self, page_maps: tuple[_PageTextMap, ...], rule: AnchorSearchRule
    ) -> tuple[_PendingMatch, ...]:
        flags = 0 if rule.options.case_sensitive else re.IGNORECASE
        patterns: tuple[tuple[str, Pattern[str]], ...] = tuple(
            (expression, re.compile(normalize_text(expression, rule.options), flags))
            for expression in rule.expressions
        )
        matches: list[_PendingMatch] = []
        for page_map in page_maps:
            for expression, pattern in patterns:
                for found in pattern.finditer(page_map.text):
                    match = self._pending_match(
                        page_map=page_map,
                        start=found.start(),
                        end=found.end(),
                        expression=expression,
                        score=0.95,
                        notes=("regex",),
                    )
                    if match is not None:
                        matches.append(match)
        return tuple(matches)

    def _build_page_text_map(
        self, page: Page, rule: AnchorSearchRule
    ) -> _PageTextMap:
        ordered_words = sorted(
            page.words,
            key=lambda word: (word.block_index, word.line_index, word.word_index),
        )
        chunks: list[str] = []
        words_by_character: list[Word | None] = []
        for index, word in enumerate(ordered_words):
            if index:
                chunks.append(" ")
                words_by_character.append(None)
            normalized = normalize_text(word.text, rule.options)
            chunks.append(normalized)
            words_by_character.extend([word] * len(normalized))
        return _PageTextMap(
            page=page,
            text="".join(chunks),
            words_by_character=tuple(words_by_character),
            word_count=len(ordered_words),
        )

    @staticmethod
    def _pending_match(
        page_map: _PageTextMap,
        start: int,
        end: int,
        expression: str,
        score: float,
        notes: tuple[str, ...],
    ) -> _PendingMatch | None:
        words = tuple(
            word
            for word in page_map.words_by_character[start:end]
            if word is not None
        )
        unique_words = tuple(dict.fromkeys(words))
        if not unique_words:
            return None
        bounds = _union_bounds(tuple(word.bounds for word in unique_words))
        text = " ".join(word.text for word in unique_words)
        return _PendingMatch(
            expression=expression,
            page_index=page_map.page.index,
            text=text,
            bounds=bounds,
            score=score,
            notes=notes,
        )

    @staticmethod
    def _result(
        rule_id: str,
        status: AnchorResultStatus,
        start: float,
        matches: tuple[AnchorMatch, ...] = (),
        notes: tuple[str, ...] = (),
        pages_analyzed: int = 0,
        words_analyzed: int = 0,
    ) -> AnchorResult:
        return AnchorResult(
            rule_id=rule_id,
            status=status,
            matches=matches,
            notes=notes,
            pages_analyzed=pages_analyzed,
            words_analyzed=words_analyzed,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )


def _union_bounds(rectangles: tuple[Rectangle, ...]) -> Rectangle:
    return Rectangle(
        left=min(rectangle.left for rectangle in rectangles),
        top=min(rectangle.top for rectangle in rectangles),
        right=max(rectangle.right for rectangle in rectangles),
        bottom=max(rectangle.bottom for rectangle in rectangles),
    )
