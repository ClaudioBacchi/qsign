"""Tests for Supabase learned template synchronization."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from app.services.general_preferences_service import SupabaseSettings
from services.templates.supabase_template_sync_service import (
    SupabaseTemplateSyncService,
    SupabaseTemplateSyncServiceError,
)


class SupabaseTemplateSyncServiceTests(unittest.TestCase):
    def test_upload_sends_learned_templates_to_configured_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            (template_root / "learned_privacy.json").write_text(
                json.dumps({"template_id": "learned_privacy"}),
                encoding="utf-8",
            )
            requests = []
            service = SupabaseTemplateSyncService(
                preferences_service=FakePreferencesService(),
                template_root=template_root,
                opener=_recording_opener(requests, b"[]"),
            )

            result = service.upload_templates()

            self.assertEqual(result.uploaded, 1)
            request = requests[0]
            self.assertEqual(
                request.full_url,
                "https://demo.supabase.co/rest/v1/SaluteLavoro?on_conflict=template_id",
            )
            self.assertEqual(request.headers["Apikey"], "sb_publishable_test")
            self.assertNotIn("Authorization", request.headers)
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload[0]["template_id"], "learned_privacy.json")
            self.assertEqual(payload[0]["json"], {"template_id": "learned_privacy"})
            self.assertIn("updated_at", payload[0])

    def test_upload_logs_template_activity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            (template_root / "learned_privacy.json").write_text(
                json.dumps({"template_id": "learned_privacy"}),
                encoding="utf-8",
            )
            logger = FakeLogger()
            service = SupabaseTemplateSyncService(
                preferences_service=FakePreferencesService(),
                template_root=template_root,
                opener=_recording_opener([], b"[]"),
                logger=logger,
            )

            service.upload_templates()

            messages = [entry[0] for entry in logger.entries]
            self.assertIn("Template upload started", messages)
            self.assertIn("Template upload completed", messages)

    def test_download_writes_remote_learned_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            rows = [
                {
                    "template_id": "learned_privacy.json",
                    "json": {"template_id": "learned_privacy"},
                    "updated_at": "2026-07-09T08:00:00+00:00",
                }
            ]
            service = SupabaseTemplateSyncService(
                preferences_service=FakePreferencesService(),
                template_root=template_root,
                opener=_recording_opener([], json.dumps(rows).encode("utf-8")),
            )

            result = service.download_templates()

            self.assertEqual(result.downloaded, 1)
            self.assertEqual(
                json.loads((template_root / "learned_privacy.json").read_text()),
                {"template_id": "learned_privacy"},
            )

    def test_row_level_security_error_is_explained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            (template_root / "learned_privacy.json").write_text(
                json.dumps({"template_id": "learned_privacy"}),
                encoding="utf-8",
            )
            service = SupabaseTemplateSyncService(
                preferences_service=FakePreferencesService(),
                template_root=template_root,
                opener=_failing_opener(
                    401,
                    (
                        '{"code":"42501","message":"new row violates row-level '
                        'security policy for table \\"SaluteLavoro\\""}'
                    ).encode("utf-8"),
                ),
            )

            with self.assertRaisesRegex(
                SupabaseTemplateSyncServiceError,
                "Row Level Security sulla tabella 'SaluteLavoro'",
            ):
                service.upload_templates()

    def test_row_level_security_error_mentions_configured_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            (template_root / "learned_privacy.json").write_text(
                json.dumps({"template_id": "learned_privacy"}),
                encoding="utf-8",
            )
            service = SupabaseTemplateSyncService(
                preferences_service=FakePreferencesService(table_name="Meddoc"),
                template_root=template_root,
                opener=_failing_opener(
                    401,
                    (
                        '{"code":"42501","message":"new row violates row-level '
                        'security policy for table \\"Meddoc\\""}'
                    ).encode("utf-8"),
                ),
            )

            with self.assertRaisesRegex(
                SupabaseTemplateSyncServiceError,
                "Row Level Security sulla tabella 'Meddoc'",
            ):
                service.upload_templates()


class FakePreferencesService:
    def __init__(self, table_name: str = "SaluteLavoro") -> None:
        self.table_name = table_name

    def get_supabase_settings(self) -> SupabaseSettings:
        return SupabaseSettings(
            project_url="https://demo.supabase.co",
            password="sb_publishable_test",
            table_name=self.table_name,
        )


class FakeLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, object]]] = []

    def info(self, message: str, **context: object) -> None:
        self.entries.append((message, context))


def _recording_opener(requests: list[object], response_body: bytes):
    def open_request(request, *, timeout):
        requests.append(request)
        return SimpleNamespace(status=200, read=lambda: response_body)

    return open_request


def _failing_opener(status: int, response_body: bytes):
    def open_request(request, *, timeout):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=status,
            msg="Unauthorized",
            hdrs=None,
            fp=SimpleNamespace(read=lambda: response_body, close=lambda: None),
        )

    return open_request


if __name__ == "__main__":
    unittest.main()
