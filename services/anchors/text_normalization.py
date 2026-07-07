"""Deterministic text normalization helpers for anchor detection."""

import unicodedata

from services.anchors.anchor_models import AnchorSearchOptions


def normalize_text(value: str, options: AnchorSearchOptions) -> str:
    """Normalize text according to explicit anchor-search options."""

    normalized = unicodedata.normalize("NFC", value)
    if options.strip_accents:
        normalized = _strip_accents(normalized)
    if options.normalize_whitespace:
        normalized = " ".join(normalized.split())
    if not options.case_sensitive:
        normalized = normalized.casefold()
    return normalized


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )
    return unicodedata.normalize("NFC", without_marks)
