"""Tests for encrypted general preferences."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from app.services.general_preferences_service import (
    ErpUser,
    ErpUserSettings,
    ErpUsersResult,
    GeneralPreferencesService,
    SupabaseConnectionResult,
    SupabaseSettings,
    SupabaseTableResult,
)


class FakeLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, object]]] = []

    def info(self, message: str, **context: object) -> None:
        self.entries.append((message, context))


class GeneralPreferencesServiceTests(unittest.TestCase):
    def test_supabase_settings_are_encrypted_without_losing_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            preferences.write_text(
                json.dumps({"active_certificate_thumbprint": "AA BB"}),
                encoding="utf-8",
            )
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: {
                    "https://demo.supabase.co": "encrypted-url",
                    "secret": "encrypted-password",
                }[value],
                unprotect=lambda value: {
                    "encrypted-url": "https://demo.supabase.co",
                    "encrypted-password": "secret",
                }[value],
            )

            service.save_supabase_settings(
                SupabaseSettings(
                    project_url="https://demo.supabase.co",
                    password="secret",
                    table_name="SaluteLavoro",
                    auto_sync_templates_on_startup=True,
                    auto_save_signed_documents=True,
                    show_signature_text=True,
                    signature_capture_mode="wacom",
                )
            )

            payload = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(payload["active_certificate_thumbprint"], "AA BB")
            self.assertNotIn("https://demo.supabase.co", preferences.read_text())
            self.assertNotIn("secret", preferences.read_text())
            general = payload["general"]
            self.assertEqual(
                general["supabase_url"]["protected_with"],
                "windows-dpapi-current-user",
            )
            self.assertEqual(
                service.get_supabase_settings(),
                SupabaseSettings(
                    project_url="https://demo.supabase.co",
                    password="secret",
                    table_name="SaluteLavoro",
                    auto_sync_templates_on_startup=True,
                    auto_save_signed_documents=True,
                    show_signature_text=True,
                    signature_capture_mode="wacom",
                ),
            )

    def test_supabase_settings_persist_disabled_signature_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            service.save_supabase_settings(
                SupabaseSettings(
                    project_url="https://demo.supabase.co",
                    password="secret",
                    table_name="Meddoc",
                    auto_sync_templates_on_startup=False,
                    auto_save_signed_documents=True,
                    show_signature_text=False,
                    signature_capture_mode="wacom",
                )
            )

            payload = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertFalse(payload["general"]["show_signature_text"])
            self.assertFalse(service.get_supabase_settings().show_signature_text)

    def test_general_preferences_save_is_logged_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = FakeLogger()
            service = GeneralPreferencesService(
                preferences_path=Path(directory) / "preferences.json",
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
                logger=logger,
            )

            service.save_supabase_settings(
                SupabaseSettings(
                    project_url="https://demo.supabase.co",
                    password="secret",
                    table_name="Meddoc",
                    auto_sync_templates_on_startup=True,
                    auto_save_signed_documents=True,
                    show_signature_text=True,
                    signature_capture_mode="wacom",
                )
            )

            self.assertEqual(logger.entries[0][0], "General preferences saved")
            context = logger.entries[0][1]
            self.assertEqual(context["table_name"], "Meddoc")
            self.assertTrue(context["auto_save_signed_documents"])
            self.assertTrue(context["auto_sync_templates_on_startup"])
            self.assertTrue(context["show_signature_text"])
            self.assertEqual(context["signature_capture_mode"], "wacom")
            self.assertIn("auto_save_signed_documents", context["changed_fields"])
            self.assertIn("signature_capture_mode", context["changed_fields"])
            self.assertNotIn("https://demo.supabase.co", str(context))
            self.assertNotIn("secret", str(context))

    def test_erp_user_preferences_save_is_logged_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = FakeLogger()
            service = GeneralPreferencesService(
                preferences_path=Path(directory) / "preferences.json",
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
                logger=logger,
            )

            service.save_erp_user_settings(
                ErpUserSettings(
                    users_url="https://erp.example.test/users",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                )
            )

            self.assertEqual(logger.entries[0][0], "ERP user preferences saved")
            context = logger.entries[0][1]
            self.assertTrue(context["users_url_configured"])
            self.assertTrue(context["basic_password_configured"])
            self.assertEqual(context["selected_user_id"], "42")
            self.assertNotIn("https://erp.example.test/users", str(context))
            self.assertNotIn("api-secret", str(context))

    def test_erp_user_session_selection_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = FakeLogger()
            service = GeneralPreferencesService(
                preferences_path=Path(directory) / "preferences.json",
                logger=logger,
            )

            service.log_erp_user_session_selection(
                ErpUserSettings(
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                ),
                source="startup_confirmation",
            )

            self.assertEqual(
                logger.entries,
                [
                    (
                        "ERP user selected for signing session",
                        {
                            "selected_user_id": "42",
                            "selected_user_name": "Mario Rossi",
                            "source": "startup_confirmation",
                        },
                    )
                ],
            )

    def test_supabase_connection_uses_configured_credentials(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return SimpleNamespace(status=200)

        service = GeneralPreferencesService(opener=opener)

        result = service.test_supabase_connection(
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="legacy-jwt",
            )
        )

        self.assertEqual(
            result,
            SupabaseConnectionResult(True, "Connessione Supabase riuscita"),
        )
        self.assertEqual(
            requests[0][0].full_url,
            "https://demo.supabase.co/auth/v1/settings",
        )
        self.assertEqual(requests[0][0].headers["Apikey"], "legacy-jwt")
        self.assertEqual(requests[0][0].headers["Authorization"], "Bearer legacy-jwt")
        self.assertEqual(requests[0][1], 8)

    def test_publishable_supabase_key_is_not_sent_as_bearer_token(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return SimpleNamespace(status=200)

        service = GeneralPreferencesService(opener=opener)

        result = service.test_supabase_connection(
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="sb_publishable_test",
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(requests[0][0].headers["Apikey"], "sb_publishable_test")
        self.assertNotIn("Authorization", requests[0][0].headers)

    def test_supabase_template_table_check_uses_configured_table(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return SimpleNamespace(status=200)

        service = GeneralPreferencesService(opener=opener)

        result = service.test_supabase_template_table(
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="sb_publishable_test",
                table_name="Meddoc",
            )
        )

        self.assertEqual(
            result,
            SupabaseTableResult(
                True,
                True,
                "Tabella template Supabase 'Meddoc' disponibile",
            ),
        )
        self.assertEqual(
            requests[0][0].full_url,
            "https://demo.supabase.co/rest/v1/Meddoc?select=template_id&limit=1",
        )
        self.assertEqual(requests[0][0].headers["Apikey"], "sb_publishable_test")

    def test_supabase_template_table_check_reports_missing_table(self) -> None:
        def opener(request, *, timeout):
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=404,
                msg="Not Found",
                hdrs=None,
                fp=SimpleNamespace(
                    read=lambda: (
                        b'{"message":"Could not find the table in the schema cache"}'
                    ),
                    close=lambda: None,
                ),
            )

        service = GeneralPreferencesService(opener=opener)

        result = service.test_supabase_template_table(
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="sb_publishable_test",
                table_name="Meddoc",
            )
        )

        self.assertEqual(
            result,
            SupabaseTableResult(
                True,
                False,
                "Tabella template Supabase 'Meddoc' non trovata",
            ),
        )

    def test_supabase_template_table_creation_calls_admin_rpc(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return SimpleNamespace(status=200)

        service = GeneralPreferencesService(opener=opener)

        result = service.ensure_supabase_template_table(
            SupabaseSettings(
                project_url="https://demo.supabase.co",
                password="sb_publishable_test",
                table_name="Meddoc",
            )
        )

        self.assertEqual(
            result,
            SupabaseTableResult(
                True,
                True,
                "Tabella template Supabase 'Meddoc' pronta",
            ),
        )
        self.assertEqual(
            requests[0][0].full_url,
            "https://demo.supabase.co/rest/v1/rpc/qsign_ensure_template_table",
        )
        self.assertEqual(
            json.loads(requests[0][0].data.decode("utf-8")),
            {"target_table": "Meddoc", "source_table": "SaluteLavoro"},
        )

    def test_erp_user_settings_are_encrypted_without_losing_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            preferences.write_text(
                json.dumps({"active_certificate_thumbprint": "AA BB"}),
                encoding="utf-8",
            )
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: {
                    "https://erp.example.test/users": "encrypted-url",
                    "api-user": "encrypted-user",
                    "api-secret": "encrypted-password",
                }[value],
                unprotect=lambda value: {
                    "encrypted-url": "https://erp.example.test/users",
                    "encrypted-user": "api-user",
                    "encrypted-password": "api-secret",
                }[value],
            )

            service.save_erp_user_settings(
                ErpUserSettings(
                    users_url="https://erp.example.test/users",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                )
            )

            content = preferences.read_text(encoding="utf-8")
            payload = json.loads(content)
            self.assertEqual(payload["active_certificate_thumbprint"], "AA BB")
            self.assertNotIn("https://erp.example.test/users", content)
            self.assertNotIn("api-secret", content)
            self.assertEqual(
                service.get_erp_user_settings(),
                ErpUserSettings(
                    users_url="https://erp.example.test/users",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                ),
            )

    def test_plain_internal_test_preferences_are_read_for_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            preferences.write_text(
                json.dumps(
                    {
                        "general": {
                            "erp_users_url": {
                                "protected_with": "plain-internal-test",
                                "value": "https://erp.example.test/users",
                            },
                            "erp_basic_username": {
                                "protected_with": "plain-internal-test",
                                "value": "api-user",
                            },
                            "erp_basic_password": {
                                "protected_with": "plain-internal-test",
                                "value": "api-secret",
                            },
                            "supabase_url": {
                                "protected_with": "plain-internal-test",
                                "value": "https://demo.supabase.co",
                            },
                            "supabase_password": {
                                "protected_with": "plain-internal-test",
                                "value": "sb_publishable_demo",
                            },
                            "admin_password": {
                                "protected_with": "plain-internal-test",
                                "value": "admin-secret",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            service = GeneralPreferencesService(preferences_path=preferences)

            self.assertEqual(
                service.get_erp_user_settings(),
                ErpUserSettings(
                    users_url="https://erp.example.test/users",
                    basic_username="api-user",
                    basic_password="api-secret",
                ),
            )
            self.assertEqual(service.get_supabase_settings().project_url, "https://demo.supabase.co")
            self.assertTrue(service.verify_admin_password("admin-secret"))

    def test_admin_password_is_encrypted_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: {"admin-secret": "encrypted-admin"}[value],
                unprotect=lambda value: {"encrypted-admin": "admin-secret"}[value],
            )

            self.assertFalse(service.has_admin_password())

            service.set_admin_password("admin-secret")

            content = preferences.read_text(encoding="utf-8")
            self.assertNotIn("admin-secret", content)
            self.assertTrue(service.has_admin_password())
            self.assertTrue(service.verify_admin_password("admin-secret"))
            self.assertFalse(service.verify_admin_password("wrong"))

    def test_erp_users_are_loaded_with_basic_auth(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            body = b'{"users":[{"id":"42","name":"Mario Rossi"}]}'
            return SimpleNamespace(status=200, read=lambda: body)

        service = GeneralPreferencesService(opener=opener)

        result = service.fetch_erp_users(
            ErpUserSettings(
                users_url="https://erp.example.test/users",
                basic_username="api-user",
                basic_password="api-secret",
            )
        )

        self.assertEqual(
            result,
            ErpUsersResult(
                True,
                "Caricati 1 utenti",
                (ErpUser("42", "Mario Rossi"),),
            ),
        )
        self.assertEqual(requests[0][0].full_url, "https://erp.example.test/users")
        self.assertEqual(requests[0][0].headers["Accept"], "application/json")
        self.assertEqual(
            requests[0][0].headers["Authorization"],
            "Basic YXBpLXVzZXI6YXBpLXNlY3JldA==",
        )
        self.assertEqual(requests[0][1], 8)


if __name__ == "__main__":
    unittest.main()
