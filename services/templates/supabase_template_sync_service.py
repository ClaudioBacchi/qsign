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
class TemplateSyncConflict:
    template_id: str
    local_updated_at: datetime
    remote_updated_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class TemplateSyncResult:
    uploaded: int = 0
    downloaded: int = 0
    skipped: int = 0
    deleted: int = 0
    remote_deleted: int = 0
    remote_remaining: int = 0
    conflicts: tuple[TemplateSyncConflict, ...] = ()


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
        self._suppressed_remote_template_ids: set[str] = set()

    def list_local_templates(self) -> tuple[LearnedTemplateFile, ...]:
        if not self._template_root.is_dir():
            return ()
        return tuple(
            self._learned_template_file(path)
            for path in sorted(self._template_root.glob("learned_*.json"))
            if path.is_file()
        )

    def upload_templates(self) -> TemplateSyncResult:
        return self._upload_local_templates(self.list_local_templates())

    def upload_template(self, path: str | Path) -> TemplateSyncResult:
        template_path = Path(path)
        if not self._is_learned_template_name(template_path.name):
            return TemplateSyncResult(skipped=1)
        if not template_path.is_file():
            raise SupabaseTemplateSyncServiceError(
                f"Template locale non trovato: {template_path.name}"
            )
        return self._upload_local_templates((self._learned_template_file(template_path),))

    def _upload_local_templates(
        self,
        local_templates: tuple[LearnedTemplateFile, ...],
    ) -> TemplateSyncResult:
        settings = self._validated_settings()
        self._log_info(
            "Template upload started",
            table=settings.table_name,
            template_root=str(self._template_root),
        )
        if not local_templates:
            self._log_info("Template upload skipped: no local templates")
            return TemplateSyncResult()
        remote_rows = {
            str(row.get("template_id") or ""): row
            for row in self._fetch_remote_rows(settings)
        }
        rows = []
        conflicts = []
        for item in local_templates:
            payload = self._read_json(item.path)
            conflict = self._upload_conflict(item, payload, remote_rows.get(item.template_id))
            if conflict is not None:
                conflicts.append(conflict)
                continue
            rows.append(
                {
                    "template_id": item.template_id,
                    "json": payload,
                    "updated_at": item.updated_at.isoformat(),
                }
            )
        if not rows:
            self._log_info(
                "Template upload skipped: conflicts",
                conflicts=len(conflicts),
            )
            return TemplateSyncResult(
                skipped=len(conflicts),
                conflicts=tuple(conflicts),
            )
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
        self._log_info(
            "Template upload completed",
            uploaded=len(rows),
            conflicts=len(conflicts),
        )
        return TemplateSyncResult(
            uploaded=len(rows),
            skipped=len(conflicts),
            conflicts=tuple(conflicts),
        )

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
        conflicts = []
        self._template_root.mkdir(parents=True, exist_ok=True)
        for row in remote_rows:
            template_id = str(row.get("template_id") or "")
            if template_id in self._suppressed_remote_template_ids:
                skipped += 1
                continue
            if not template_id.startswith("learned_") or not template_id.endswith(".json"):
                skipped += 1
                continue
            destination = self._template_root / Path(template_id).name
            remote_updated_at = self._parse_remote_updated_at(row)
            conflict = self._download_conflict(destination, row, remote_updated_at)
            if conflict is not None:
                skipped += 1
                conflicts.append(conflict)
                continue
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
        result = TemplateSyncResult(
            downloaded=downloaded,
            skipped=skipped,
            conflicts=tuple(conflicts),
        )
        self._log_info(
            "Template download completed",
            downloaded=result.downloaded,
            skipped=result.skipped,
            conflicts=len(result.conflicts),
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
            conflicts=download_result.conflicts + upload_result.conflicts,
        )
        self._log_info(
            "Template synchronization completed",
            uploaded=result.uploaded,
            downloaded=result.downloaded,
            skipped=result.skipped,
            conflicts=len(result.conflicts),
        )
        return result

    def delete_templates(self) -> TemplateSyncResult:
        settings = self._validated_settings()
        self._log_info(
            "Template delete started",
            table=settings.table_name,
            template_root=str(self._template_root),
        )
        remote_template_ids = [
            template_id
            for template_id in self._remote_template_ids(settings)
            if template_id.startswith("learned_") and template_id.endswith(".json")
        ]
        requested_remote_ids = set(remote_template_ids)
        for template_id in remote_template_ids:
            self._request(
                settings,
                method="DELETE",
                path=(
                    f"/rest/v1/{self._quoted_table(settings)}"
                    f"?template_id=eq.{urllib.parse.quote(template_id, safe='')}"
                ),
                headers={"Prefer": "return=minimal"},
            )
        remaining_remote_ids = requested_remote_ids.intersection(
            self._remote_template_ids(settings)
        )
        if remaining_remote_ids:
            self._delete_remote_templates_via_rpc(settings)
            remaining_remote_ids = requested_remote_ids.intersection(
                self._remote_template_ids(settings)
            )
        self._suppressed_remote_template_ids.update(remaining_remote_ids)
        remote_deleted = len(requested_remote_ids) - len(remaining_remote_ids)
        deleted, failed = self._delete_local_templates()
        if failed:
            raise SupabaseTemplateSyncServiceError(
                f"Template locali non eliminati: {len(failed)}"
            )
        result = TemplateSyncResult(
            deleted=deleted,
            remote_deleted=remote_deleted,
            remote_remaining=len(remaining_remote_ids),
        )
        self._log_info(
            "Template delete completed",
            deleted=result.deleted,
            remote_deleted=result.remote_deleted,
            remote_remaining=result.remote_remaining,
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

    def _upload_conflict(
        self,
        local_template: LearnedTemplateFile,
        local_payload: dict[str, Any],
        remote_row: dict[str, Any] | None,
    ) -> TemplateSyncConflict | None:
        if remote_row is None:
            return None
        remote_payload = remote_row.get("json")
        if remote_payload == local_payload:
            return None
        remote_updated_at = self._parse_remote_updated_at(remote_row)
        if remote_updated_at <= local_template.updated_at:
            return None
        return TemplateSyncConflict(
            template_id=local_template.template_id,
            local_updated_at=local_template.updated_at,
            remote_updated_at=remote_updated_at,
            reason="versione Supabase piu' recente",
        )

    def _download_conflict(
        self,
        destination: Path,
        remote_row: dict[str, Any],
        remote_updated_at: datetime,
    ) -> TemplateSyncConflict | None:
        if not destination.is_file():
            return None
        local_updated_at = datetime.fromtimestamp(destination.stat().st_mtime, UTC)
        if local_updated_at >= remote_updated_at:
            return None
        remote_payload = remote_row.get("json")
        try:
            local_payload = self._read_json(destination)
        except SupabaseTemplateSyncServiceError:
            local_payload = None
        if remote_payload == local_payload:
            return None
        return TemplateSyncConflict(
            template_id=destination.name,
            local_updated_at=local_updated_at,
            remote_updated_at=remote_updated_at,
            reason="versione locale diversa",
        )

    def _remote_template_ids(self, settings: SupabaseSettings) -> list[str]:
        return [
            str(row.get("template_id") or "")
            for row in self._fetch_remote_rows(settings)
        ]

    def _delete_remote_templates_via_rpc(self, settings: SupabaseSettings) -> None:
        try:
            result = self._request(
                settings,
                method="POST",
                path="/rest/v1/rpc/qsign_delete_learned_templates",
                body={"target_table": settings.table_name.strip() or "SaluteLavoro"},
            )
        except SupabaseTemplateSyncServiceError as error:
            self._log_info("Template delete RPC unavailable", error=str(error))
            return
        self._log_info(
            "Template delete RPC completed",
            deleted=self._deleted_count_from_rpc_result(result),
        )

    @staticmethod
    def _deleted_count_from_rpc_result(result: object) -> int:
        if isinstance(result, bool):
            return 0
        if isinstance(result, int):
            return result
        if isinstance(result, float):
            return int(result)
        if isinstance(result, dict):
            for key in ("deleted", "deleted_count", "qsign_delete_learned_templates"):
                value = result.get(key)
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    return int(value)
        if isinstance(result, list) and result:
            return SupabaseTemplateSyncService._deleted_count_from_rpc_result(result[0])
        return 0

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
    def _is_learned_template_name(name: str) -> bool:
        return name.startswith("learned_") and name.endswith(".json")

    @staticmethod
    def _learned_template_file(path: Path) -> LearnedTemplateFile:
        return LearnedTemplateFile(
            template_id=path.name,
            path=path,
            updated_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
        )

    def _delete_local_templates(self) -> tuple[int, list[Path]]:
        deleted = 0
        failed: list[Path] = []
        for item in self.list_local_templates():
            try:
                item.path.unlink()
                deleted += 1
            except OSError:
                failed.append(item.path)
        return deleted, failed

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
                "Crea una policy SELECT/INSERT/UPDATE/DELETE su questa tabella "
                "oppure usa una chiave con permessi adeguati."
            )
        return f"Errore Supabase {error.code}: {message or error.reason}"

    def _log_info(self, message: str, **context: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, **context)
