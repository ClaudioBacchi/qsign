"""Supabase synchronization for learned QSign templates."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.services.general_preferences_service import (
    GeneralPreferencesService,
    SupabaseSettings,
)
from services.logging.logging_service import LoggingService


class SupabaseTemplateSyncServiceError(RuntimeError):
    """Raised when learned template synchronization fails."""


@dataclass(frozen=True, slots=True)
class LearnedTemplateFile:
    template_id: str
    path: Path
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TemplateSyncResult:
    uploaded: int = 0
    downloaded: int = 0
    skipped: int = 0


class SupabaseTemplateSyncService:
    """Synchronize local learned templates with a Supabase table."""

    def __init__(
        self,
        preferences_service: GeneralPreferencesService,
        template_root: str | Path = "templates",
        opener: Callable[..., object] | None = None,
        logger: LoggingService | None = None,
    ) -> None:
        self._preferences_service = preferences_service
        self._template_root = Path(template_root)
        self._opener = opener or urllib.request.urlopen
        self._logger = logger

    def list_local_templates(self) -> tuple[LearnedTemplateFile, ...]:
        if not self._template_root.is_dir():
            return ()
        return tuple(
            LearnedTemplateFile(
                template_id=path.name,
                path=path,
                updated_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            )
            for path in sorted(self._template_root.glob("learned_*.json"))
            if path.is_file()
        )

    def upload_templates(self) -> TemplateSyncResult:
        settings = self._validated_settings()
        self._log_info(
            "Template upload started",
            table=settings.table_name,
            template_root=str(self._template_root),
        )
        rows = [
            {
                "template_id": item.template_id,
                "json": self._read_json(item.path),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in self.list_local_templates()
        ]
        if not rows:
            self._log_info("Template upload skipped: no local templates")
            return TemplateSyncResult()
        self._request(
            settings,
            method="POST",
            path=(
                f"/rest/v1/{self._quoted_table(settings)}"
                "?on_conflict=template_id"
            ),
            body=rows,
            headers={
                "Prefer": "resolution=merge-duplicates",
            },
        )
        self._log_info("Template upload completed", uploaded=len(rows))
        return TemplateSyncResult(uploaded=len(rows))

    def download_templates(self) -> TemplateSyncResult:
        settings = self._validated_settings()
        self._log_info(
            "Template download started",
            table=settings.table_name,
            template_root=str(self._template_root),
        )
        remote_rows = self._fetch_remote_rows(settings)
        downloaded = 0
        skipped = 0
        self._template_root.mkdir(parents=True, exist_ok=True)
        for row in remote_rows:
            template_id = str(row.get("template_id") or "")
            if not template_id.startswith("learned_") or not template_id.endswith(".json"):
                skipped += 1
                continue
            destination = self._template_root / Path(template_id).name
            remote_updated_at = self._parse_remote_updated_at(row)
            if (
                destination.is_file()
                and datetime.fromtimestamp(destination.stat().st_mtime, UTC)
                >= remote_updated_at
            ):
                skipped += 1
                continue
            payload = row.get("json")
            if not isinstance(payload, dict):
                skipped += 1
                continue
            destination.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            downloaded += 1
        result = TemplateSyncResult(downloaded=downloaded, skipped=skipped)
        self._log_info(
            "Template download completed",
            downloaded=result.downloaded,
            skipped=result.skipped,
        )
        return result

    def sync_templates(self) -> TemplateSyncResult:
        self._log_info("Template synchronization started")
        download_result = self.download_templates()
        upload_result = self.upload_templates()
        result = TemplateSyncResult(
            uploaded=upload_result.uploaded,
            downloaded=download_result.downloaded,
            skipped=download_result.skipped + upload_result.skipped,
        )
        self._log_info(
            "Template synchronization completed",
            uploaded=result.uploaded,
            downloaded=result.downloaded,
            skipped=result.skipped,
        )
        return result

    def _fetch_remote_rows(self, settings: SupabaseSettings) -> list[dict[str, Any]]:
        payload = self._request(
            settings,
            method="GET",
            path=(
                f"/rest/v1/{self._quoted_table(settings)}"
                "?select=template_id,json,updated_at"
            ),
        )
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def _request(
        self,
        settings: SupabaseSettings,
        method: str,
        path: str,
        body: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> object:
        request_headers = {
            **GeneralPreferencesService._supabase_headers(settings.password.strip()),
            "Accept": "application/json",
        }
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            f"{settings.project_url.strip().rstrip('/')}{path}",
            data=(
                json.dumps(body, ensure_ascii=False).encode("utf-8")
                if body is not None
                else None
            ),
            headers=request_headers,
            method=method,
        )
        try:
            response = self._opener(request, timeout=12)
            content = response.read() if hasattr(response, "read") else b""
        except urllib.error.HTTPError as error:
            message = error.read().decode("utf-8", errors="replace")
            error.close()
            raise SupabaseTemplateSyncServiceError(
                self._format_http_error(error, message, settings.table_name)
            ) from error
        except Exception as error:
            raise SupabaseTemplateSyncServiceError(
                f"Sincronizzazione Supabase fallita: {error}"
            ) from error
        if not content:
            return None
        try:
            return json.loads(content.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def _validated_settings(self) -> SupabaseSettings:
        settings = self._preferences_service.get_supabase_settings()
        if not settings.project_url.strip():
            raise SupabaseTemplateSyncServiceError("URL Supabase non configurato")
        if not settings.password.strip():
            raise SupabaseTemplateSyncServiceError("Password/API key non configurata")
        if not settings.table_name.strip():
            raise SupabaseTemplateSyncServiceError("Tabella Supabase non configurata")
        return settings

    @staticmethod
    def _quoted_table(settings: SupabaseSettings) -> str:
        return urllib.parse.quote(settings.table_name.strip(), safe="")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SupabaseTemplateSyncServiceError(
                f"Template JSON non valido: {path.name}"
            ) from error
        if not isinstance(payload, dict):
            raise SupabaseTemplateSyncServiceError(
                f"Template JSON non valido: {path.name}"
            )
        return payload

    @staticmethod
    def _parse_remote_updated_at(row: dict[str, Any]) -> datetime:
        raw_value = str(row.get("updated_at") or "")
        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)

    @staticmethod
    def _format_http_error(
        error: urllib.error.HTTPError,
        message: str,
        table_name: str,
    ) -> str:
        lower_message = message.lower()
        is_rls_error = (
            "row-level security" in lower_message
            or '"code":"42501"' in lower_message
        )
        if error.code in (401, 403) and is_rls_error:
            return (
                f"Errore Supabase {error.code}: accesso negato dalla "
                f"Row Level Security sulla tabella '{table_name}'. "
                "Crea una policy SELECT/INSERT/UPDATE su questa tabella "
                "oppure usa una chiave con permessi adeguati."
            )
        return f"Errore Supabase {error.code}: {message or error.reason}"

    def _log_info(self, message: str, **context: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, **context)
