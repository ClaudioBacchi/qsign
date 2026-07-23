"""Shared ERP document context used to return signed files."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErpSignedDocumentUploadContext:
    document_id: str
    logical_dir: str
    logical_name: str

