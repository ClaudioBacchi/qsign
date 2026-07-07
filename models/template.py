"""Template model for deterministic QSign document processing."""

from dataclasses import dataclass, field
from enum import StrEnum


class TemplateState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"
    DISABLED = "disabled"


class RuleScope(StrEnum):
    DOCUMENT = "document"
    FIRST_PAGE = "first_page"
    LAST_PAGE = "last_page"
    PAGE_RANGE = "page_range"


@dataclass(frozen=True, slots=True)
class DocumentRule:
    """Coarse document constraints used by future processing stages."""

    rule_id: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class RecognitionRule:
    """Deterministic matcher definition; not an executable recognizer."""

    rule_id: str
    rule_type: str
    expression: str
    scope: RuleScope = RuleScope.DOCUMENT
    required: bool = False
    exclusion: bool = False
    weight: float = 1.0
    minimum_occurrences: int | None = None
    maximum_occurrences: int | None = None

    def __post_init__(self) -> None:
        if not self.rule_id:
            raise ValueError("Recognition rule id is required")
        if not self.rule_type:
            raise ValueError("Recognition rule type is required")
        if self.weight <= 0:
            raise ValueError("Recognition rule weight must be positive")


@dataclass(frozen=True, slots=True)
class AnchorRule:
    """Textual anchor definition for future anchor resolution."""

    anchor_id: str
    name: str
    search_type: str
    expression: str
    scope: RuleScope = RuleScope.DOCUMENT
    occurrence_policy: str = "unique"
    required: bool = True

    def __post_init__(self) -> None:
        if not self.anchor_id:
            raise ValueError("Anchor id is required")
        if not self.expression:
            raise ValueError("Anchor expression is required")


@dataclass(frozen=True, slots=True)
class PlacementRule:
    """Anchor-relative signature area definition."""

    placement_id: str
    role: str
    anchor_id: str
    side: str
    alignment: str
    x_offset: float
    y_offset: float
    width: float
    height: float
    required: bool = True

    def __post_init__(self) -> None:
        if not self.placement_id:
            raise ValueError("Placement id is required")
        if not self.anchor_id:
            raise ValueError("Placement anchor id is required")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Placement dimensions must be positive")


@dataclass(frozen=True, slots=True)
class TemplateSettings:
    """Template-level settings for future engines."""

    recognition_threshold: float = 80.0
    ambiguity_margin: float = 5.0
    normalization_profile: str = "default"

    def __post_init__(self) -> None:
        if not 0 <= self.recognition_threshold <= 100:
            raise ValueError("Recognition threshold must be between 0 and 100")
        if self.ambiguity_margin < 0:
            raise ValueError("Ambiguity margin cannot be negative")


@dataclass(frozen=True, slots=True)
class Template:
    """Immutable template definition loaded from a repository."""

    template_id: str
    code: str
    name: str
    document_type: str
    version: str
    state: TemplateState
    priority: int = 0
    schema_version: str = "1.0"
    description: str = ""
    document_rules: tuple[DocumentRule, ...] = ()
    recognition_rules: tuple[RecognitionRule, ...] = ()
    anchor_rules: tuple[AnchorRule, ...] = ()
    placement_rules: tuple[PlacementRule, ...] = ()
    settings: TemplateSettings = field(default_factory=TemplateSettings)
    checksum: str = ""

    def __post_init__(self) -> None:
        if not self.template_id:
            raise ValueError("Template id is required")
        if not self.code:
            raise ValueError("Template code is required")
        if not self.name:
            raise ValueError("Template name is required")
        if not self.document_type:
            raise ValueError("Template document type is required")
        if not self.version:
            raise ValueError("Template version is required")
