"""JSON template loading helpers."""

import hashlib
import json
from pathlib import Path
from typing import Any

from models.template import (
    AnchorRule,
    DocumentRule,
    PlacementRule,
    RecognitionRule,
    RuleScope,
    Template,
    TemplateSettings,
    TemplateState,
)
from services.templates.template_repository import TemplateRepositoryError


def load_template_file(path: str | Path) -> Template:
    template_path = Path(path)
    try:
        raw = template_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TemplateRepositoryError("Template root must be a JSON object")
        return template_from_dict(payload, checksum=_checksum(raw))
    except TemplateRepositoryError:
        raise
    except Exception as error:
        raise TemplateRepositoryError(f"Unable to load template: {template_path}") from error


def template_from_dict(payload: dict[str, Any], checksum: str = "") -> Template:
    settings_payload = _object(payload.get("settings", {}), "settings")
    return Template(
        template_id=_required_text(payload, "template_id"),
        code=_required_text(payload, "code"),
        name=_required_text(payload, "name"),
        description=str(payload.get("description", "")),
        document_type=_required_text(payload, "document_type"),
        version=_required_text(payload, "version"),
        state=TemplateState(str(payload.get("state", TemplateState.DRAFT.value))),
        priority=int(payload.get("priority", 0)),
        schema_version=str(payload.get("schema_version", "1.0")),
        document_rules=tuple(
            DocumentRule(
                rule_id=_required_text(item, "rule_id"),
                description=str(item.get("description", "")),
            )
            for item in _objects(payload.get("document_rules", []), "document_rules")
        ),
        recognition_rules=tuple(
            RecognitionRule(
                rule_id=_required_text(item, "rule_id"),
                rule_type=_required_text(item, "rule_type"),
                expression=_required_text(item, "expression"),
                scope=_scope(item.get("scope", RuleScope.DOCUMENT.value)),
                required=bool(item.get("required", False)),
                exclusion=bool(item.get("exclusion", False)),
                weight=float(item.get("weight", 1.0)),
                minimum_occurrences=_optional_int(item.get("minimum_occurrences")),
                maximum_occurrences=_optional_int(item.get("maximum_occurrences")),
            )
            for item in _objects(payload.get("recognition_rules", []), "recognition_rules")
        ),
        anchor_rules=tuple(
            AnchorRule(
                anchor_id=_required_text(item, "anchor_id"),
                name=_required_text(item, "name"),
                search_type=_required_text(item, "search_type"),
                expression=_required_text(item, "expression"),
                scope=_scope(item.get("scope", RuleScope.DOCUMENT.value)),
                occurrence_policy=str(item.get("occurrence_policy", "unique")),
                required=bool(item.get("required", True)),
            )
            for item in _objects(payload.get("anchor_rules", []), "anchor_rules")
        ),
        placement_rules=tuple(
            PlacementRule(
                placement_id=_required_text(item, "placement_id"),
                role=_required_text(item, "role"),
                anchor_id=_required_text(item, "anchor_id"),
                side=_required_text(item, "side"),
                alignment=_required_text(item, "alignment"),
                x_offset=float(item.get("x_offset", 0.0)),
                y_offset=float(item.get("y_offset", 0.0)),
                width=float(item.get("width", 0.0)),
                height=float(item.get("height", 0.0)),
                required=bool(item.get("required", True)),
            )
            for item in _objects(payload.get("placement_rules", []), "placement_rules")
        ),
        settings=TemplateSettings(
            recognition_threshold=float(settings_payload.get("recognition_threshold", 80.0)),
            ambiguity_margin=float(settings_payload.get("ambiguity_margin", 5.0)),
            normalization_profile=str(settings_payload.get("normalization_profile", "default")),
        ),
        checksum=checksum or str(payload.get("checksum", "")),
    )


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TemplateRepositoryError(f"Required template field is missing: {key}")
    return value


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TemplateRepositoryError(f"Template field must be an object: {name}")
    return value


def _objects(value: object, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TemplateRepositoryError(f"Template field must be a list: {name}")
    if not all(isinstance(item, dict) for item in value):
        raise TemplateRepositoryError(f"Template list contains invalid entries: {name}")
    return value


def _scope(value: object) -> RuleScope:
    return RuleScope(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)
