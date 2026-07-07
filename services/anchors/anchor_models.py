"""Provider-neutral models for deterministic anchor detection."""

from dataclasses import dataclass, field
from enum import StrEnum

from models.document import Rectangle


class AnchorDetectionError(RuntimeError):
    """Base error for invalid anchor-detection input."""


class AnchorSearchMode(StrEnum):
    """Supported anchor search families."""

    EXACT = "exact"
    REGEX = "regex"


class AnchorResultStatus(StrEnum):
    """Deterministic result states for anchor detection."""

    MATCHED = "matched"
    NOT_FOUND = "not_found"
    EMPTY_DOCUMENT = "empty_document"
    INVALID_RULE = "invalid_rule"


@dataclass(frozen=True, slots=True)
class AnchorSearchOptions:
    """Normalization and matching options for one anchor search."""

    case_sensitive: bool = True
    normalize_whitespace: bool = False
    strip_accents: bool = False


@dataclass(frozen=True, slots=True)
class AnchorSearchRule:
    """Rule used by AnchorDetector to locate textual anchors."""

    rule_id: str
    expressions: tuple[str, ...]
    mode: AnchorSearchMode = AnchorSearchMode.EXACT
    options: AnchorSearchOptions = field(default_factory=AnchorSearchOptions)

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise AnchorDetectionError("Anchor rule id is required")
        if not self.expressions:
            raise AnchorDetectionError("At least one anchor expression is required")
        if any(not expression.strip() for expression in self.expressions):
            raise AnchorDetectionError("Anchor expressions cannot be empty")


@dataclass(frozen=True, slots=True)
class AnchorMatch:
    """One anchor occurrence found in a canonical document."""

    rule_id: str
    expression: str
    page_index: int
    text: str
    bounds: Rectangle
    score: float
    occurrence_index: int
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnchorResult:
    """Result of applying one anchor search rule to one document."""

    rule_id: str
    status: AnchorResultStatus
    matches: tuple[AnchorMatch, ...] = ()
    notes: tuple[str, ...] = ()
    pages_analyzed: int = 0
    words_analyzed: int = 0
    elapsed_ms: float = 0.0

    @property
    def found(self) -> bool:
        return self.status == AnchorResultStatus.MATCHED
