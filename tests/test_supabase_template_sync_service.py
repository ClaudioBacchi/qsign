"""Tests for Supabase learned template synchronization."""

import json
import tempfile
import unittest
import urllib.error
import urllib.parse
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

    def test_delete_removes_remote_and_local_learned_templates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            learned = template_root / "learned_privacy.json"
            manual = template_root / "manual.json"
            learned.write_text(
                json.dumps({"template_id": "learned_privacy"}),
                encoding="utf-8",
            )
            manual.write_text("{}", encoding="utf-8")
            rows = [
                {
                    "template_id": "learned_privacy.json",
                    "json": {"template_id": "learned_privacy"},
                    "updated_at": "2026-07-09T08:00:00+00:00",
                },
                {
                    "template_id": "manual.json",
                    "json": {},
                    "updated_at": "2026-07-09T08:00:00+00:00",
                },
            ]
            requests = []
            service = SupabaseTemplateSyncService(
                preferences_service=FakePreferencesService(),
                template_root=template_root,
                opener=_stateful_template_delete_opener(requests, rows),
            )

            result = service.delete_templates()

            self.assertEqual(result.deleted, 1)
            self.assertEqual(result.remote_deleted, 1)
            self.assertEqual(result.remote_remaining, 0)
            self.assertFalse(learned.exists())
            self.assertTrue(manual.exists())
            self.assertEqual(requests[0].method, "GET")
            self.assertEqual(requests[1].method, "DELETE")
            self.assertEqual(requests[2].method, "GET")
            self.assertEqual(
                requests[1].full_url,
                "https://demo.supabase.co/rest/v1/SaluteLavoro"
                "?template_id=eq.learned_privacy.json",
            )

    def test_delete_suppresses_remote_templates_when_policy_keeps_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            learned = template_root / "learned_privacy.json"
            learned.write_text(
                json.dumps({"template_id": "learned_privacy"}),
                encoding="utf-8",
            )
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

            delete_result = service.delete_templates()
            download_result = service.download_templates()

            self.assertEqual(delete_result.deleted, 1)
            self.assertEqual(delete_result.remote_deleted, 0)
            self.assertEqual(delete_result.remote_remaining, 1)
            self.assertEqual(download_result.downloaded, 0)
            self.assertEqual(download_result.skipped, 1)
            self.assertFalse(learned.exists())

    def test_delete_uses_admin_rpc_when_delete_policy_keeps_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_root = Path(directory)
            learned = template_root / "learned_privacy.json"
            learned.write_text(
                json.dumps({"template_id": "learned_privacy"}),
                encoding="utf-8",
            )
            rows = [
                {
                    "template_id": "learned_privacy.json",
                    "json": {"template_id": "learned_privacy"},
                    "updated_at": "2026-07-09T08:00:00+00:00",
                }
            ]
            requests = []
            service = SupabaseTemplateSyncService(
                preferences_service=FakePreferencesService(),
                template_root=template_root,
                opener=_rpc_template_delete_opener(requests, rows),
            )

            result = service.delete_templates()

            self.assertEqual(result.deleted, 1)
            self.assertEqual(result.remote_deleted, 1)
            self.assertEqual(result.remote_remaining, 0)
            self.assertFalse(learned.exists())
            self.assertEqual(
                [request.method for request in requests],
                ["GET", "DELETE", "GET", "POST", "GET"],
            )
            self.assertEqual(
                requests[3].full_url,
                "https://demo.supabase.co/rest/v1/rpc/qsign_delete_learned_templates",
            )
            self.assertEqual(
                json.loads(requests[3].data.decode("utf-8")),
                {"target_table": "SaluteLavoro"},
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


def _stateful_template_delete_opener(
    requests: list[object],
    rows: list[dict[str, object]],
):
    remote_rows = [dict(row) for row in rows]

    def open_request(request, *, timeout):
        requests.append(request)
        method = request.method
        if method == "DELETE":
            parsed = urllib.parse.urlparse(request.full_url)
            filters = urllib.parse.parse_qs(parsed.query)
            raw_filter = filters.get("template_id", [""])[0]
            template_id = raw_filter.removeprefix("eq.")
            remote_rows[:] = [
                row for row in remote_rows if row.get("template_id") != template_id
            ]
            return SimpleNamespace(status=204, read=lambda: b"")
        return SimpleNamespace(
            status=200,
            read=lambda: json.dumps(remote_rows).encode("utf-8"),
        )

    return open_request


def _rpc_template_delete_opener(
    requests: list[object],
    rows: list[dict[str, object]],
):
    remote_rows = [dict(row) for row in rows]

    def open_request(request, *, timeout):
        requests.append(request)
        if request.method == "POST" and request.full_url.endswith(
            "/rest/v1/rpc/qsign_delete_learned_templates"
        ):
            deleted = len(
                [
                    row
                    for row in remote_rows
                    if str(row.get("template_id") or "").startswith("learned_")
                    and str(row.get("template_id") or "").endswith(".json")
                ]
            )
            remote_rows[:] = [
                row
                for row in remote_rows
                if not (
                    str(row.get("template_id") or "").startswith("learned_")
                    and str(row.get("template_id") or "").endswith(".json")
                )
            ]
            return SimpleNamespace(status=200, read=lambda: str(deleted).encode())
        return SimpleNamespace(
            status=200,
            read=lambda: json.dumps(remote_rows).encode("utf-8"),
        )

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
