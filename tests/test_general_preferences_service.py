"""Tests for encrypted general preferences."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from app.services.general_preferences_service import (
    ErpDocument,
    ErpDocumentStorageInfo,
    ErpDocumentStorageInfoResult,
    ErpDocumentsResult,
    ErpUser,
    ErpUserSettings,
    ErpUsersResult,
    GeneralPreferencesServiceError,
    GeneralPreferencesService,
    SupabaseConnectionResult,
    SupabaseSettings,
    SupabaseTableResult,
)


class FakeLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, object]]] = []
        self.warnings: list[tuple[str, dict[str, object]]] = []

    def info(self, message: str, **context: object) -> None:
        self.entries.append((message, context))

    def warning(self, message: str, **context: object) -> None:
        self.warnings.append((message, context))


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
                    list_erp_documents=True,
                    auto_refresh_erp_documents=True,
                    erp_refresh_interval_seconds=45,
                    show_signature_text=True,
                    signature_capture_mode="wacom",
                    local_erp_port=55123,
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
                    list_erp_documents=True,
                    auto_refresh_erp_documents=True,
                    erp_refresh_interval_seconds=45,
                    show_signature_text=True,
                    signature_capture_mode="wacom",
                    local_erp_port=55123,
                ),
            )

    def test_local_erp_port_defaults_and_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            self.assertEqual(service.get_supabase_settings().local_erp_port, 9091)

            service.save_supabase_settings(SupabaseSettings(local_erp_port=80))

            self.assertEqual(service.get_supabase_settings().local_erp_port, 9091)

    def test_disabled_erp_document_list_forces_refresh_options_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            service.save_supabase_settings(
                SupabaseSettings(
                    list_erp_documents=False,
                    auto_refresh_erp_documents=True,
                    erp_refresh_interval_seconds=45,
                )
            )

            settings = service.get_supabase_settings()
            self.assertFalse(settings.list_erp_documents)
            self.assertFalse(settings.auto_refresh_erp_documents)
            self.assertEqual(settings.erp_refresh_interval_seconds, 0)

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

    def test_preferences_write_is_atomic_and_creates_backup_from_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            preferences.write_text(
                json.dumps({"active_certificate_thumbprint": "AA BB"}),
                encoding="utf-8",
            )
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            service.save_supabase_settings(
                SupabaseSettings(project_url="https://demo.supabase.co")
            )

            payload = json.loads(preferences.read_text(encoding="utf-8"))
            backup = json.loads(preferences.with_name("preferences.json.bak").read_text(encoding="utf-8"))
            self.assertIn("general", payload)
            self.assertEqual(backup, {"active_certificate_thumbprint": "AA BB"})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_first_preferences_write_does_not_create_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            service.save_supabase_settings(
                SupabaseSettings(project_url="https://demo.supabase.co")
            )

            self.assertTrue(preferences.is_file())
            self.assertFalse(preferences.with_name("preferences.json.bak").exists())

    def test_preferences_write_error_before_replace_keeps_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            previous = {"active_certificate_thumbprint": "AA BB"}
            preferences.write_text(json.dumps(previous), encoding="utf-8")
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            def fail_backup() -> None:
                raise OSError("backup failed")

            service._copy_valid_preferences_to_backup = fail_backup

            with self.assertRaises(OSError):
                service.save_supabase_settings(
                    SupabaseSettings(project_url="https://demo.supabase.co")
                )

            self.assertEqual(json.loads(preferences.read_text(encoding="utf-8")), previous)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_preferences_replace_error_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            previous = {"active_certificate_thumbprint": "AA BB"}
            preferences.write_text(json.dumps(previous), encoding="utf-8")
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            def fail_replace(source: Path, destination: Path) -> None:
                raise OSError("replace failed")

            service._replace_preferences_file = fail_replace

            with self.assertRaises(OSError):
                service.save_supabase_settings(
                    SupabaseSettings(project_url="https://demo.supabase.co")
                )

            self.assertEqual(json.loads(preferences.read_text(encoding="utf-8")), previous)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_corrupt_preferences_fall_back_to_valid_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = FakeLogger()
            preferences = Path(directory) / "preferences.json"
            preferences.write_text("{broken", encoding="utf-8")
            preferences.with_name("preferences.json.bak").write_text(
                json.dumps(
                    {
                        "general": {
                            "supabase_table": "Meddoc",
                            "supabase_url": {
                                "protected_with": "plain-internal-test",
                                "value": "https://demo.supabase.co",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            service = GeneralPreferencesService(
                preferences_path=preferences,
                logger=logger,
            )

            settings = service.get_supabase_settings()

            self.assertEqual(settings.project_url, "https://demo.supabase.co")
            self.assertEqual(settings.table_name, "Meddoc")
            self.assertTrue(logger.warnings)
            self.assertNotIn("https://demo.supabase.co", str(logger.warnings))

    def test_corrupt_preferences_and_backup_return_defaults_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = FakeLogger()
            preferences = Path(directory) / "preferences.json"
            preferences.write_text("{broken", encoding="utf-8")
            preferences.with_name("preferences.json.bak").write_text("{also-broken", encoding="utf-8")
            service = GeneralPreferencesService(
                preferences_path=preferences,
                logger=logger,
            )

            settings = service.get_supabase_settings()

            self.assertEqual(settings, SupabaseSettings())
            self.assertTrue(logger.warnings)

    def test_dpapi_error_is_diagnostic_without_exposing_encrypted_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = FakeLogger()
            preferences = Path(directory) / "preferences.json"
            preferences.write_text(
                json.dumps(
                    {
                        "general": {
                            "supabase_url": {
                                "protected_with": "windows-dpapi-current-user",
                                "value": "encrypted-secret-value",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            service = GeneralPreferencesService(
                preferences_path=preferences,
                unprotect=lambda _: (_ for _ in ()).throw(
                    GeneralPreferencesServiceError("dpapi failed")
                ),
                logger=logger,
            )

            self.assertEqual(service.get_supabase_settings().project_url, "")
            self.assertTrue(logger.warnings)
            self.assertNotIn("encrypted-secret-value", str(logger.warnings))

    def test_erp_and_admin_preferences_survive_atomic_save_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            service = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            service.set_admin_password("admin-secret")
            service.save_erp_user_settings(
                ErpUserSettings(
                    users_url="https://erp.example.test/users",
                    documents_url="https://erp.example.test/documents",
                    document_service_url="https://erp.example.test/soap",
                    company_id="SALAV",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                    persistent_user=True,
                )
            )

            reloaded = GeneralPreferencesService(
                preferences_path=preferences,
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            self.assertTrue(reloaded.verify_admin_password("admin-secret"))
            self.assertEqual(
                reloaded.get_erp_user_settings(),
                ErpUserSettings(
                    users_url="https://erp.example.test/users",
                    documents_url="https://erp.example.test/documents",
                    document_service_url="https://erp.example.test/soap",
                    company_id="SALAV",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                    persistent_user=True,
                ),
            )

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
                    list_erp_documents=True,
                    auto_refresh_erp_documents=True,
                    erp_refresh_interval_seconds=10,
                    show_signature_text=True,
                    signature_capture_mode="wacom",
                    local_erp_port=55123,
                )
            )

            self.assertEqual(logger.entries[0][0], "General preferences saved")
            context = logger.entries[0][1]
            self.assertEqual(context["table_name"], "Meddoc")
            self.assertTrue(context["auto_save_signed_documents"])
            self.assertTrue(context["auto_sync_templates_on_startup"])
            self.assertTrue(context["list_erp_documents"])
            self.assertTrue(context["auto_refresh_erp_documents"])
            self.assertEqual(context["erp_refresh_interval_seconds"], 30)
            self.assertTrue(context["show_signature_text"])
            self.assertEqual(context["signature_capture_mode"], "wacom")
            self.assertEqual(context["local_erp_port"], 55123)
            self.assertIn("auto_save_signed_documents", context["changed_fields"])
            self.assertIn("list_erp_documents", context["changed_fields"])
            self.assertIn("auto_refresh_erp_documents", context["changed_fields"])
            self.assertIn("erp_refresh_interval_seconds", context["changed_fields"])
            self.assertIn("signature_capture_mode", context["changed_fields"])
            self.assertIn("local_erp_port", context["changed_fields"])
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
                    documents_url="https://erp.example.test/documents",
                    document_service_url="https://erp.example.test/soap",
                    company_id="SALAV",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                    persistent_user=True,
                )
            )

            self.assertEqual(logger.entries[0][0], "ERP user preferences saved")
            context = logger.entries[0][1]
            self.assertTrue(context["users_url_configured"])
            self.assertTrue(context["document_service_url_configured"])
            self.assertTrue(context["company_id_configured"])
            self.assertTrue(context["basic_password_configured"])
            self.assertEqual(context["selected_user_id"], "42")
            self.assertTrue(context["persistent_user"])
            self.assertTrue(context["persistent_user_changed"])
            self.assertNotIn("https://erp.example.test/users", str(context))
            self.assertNotIn("https://erp.example.test/soap", str(context))
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
                    "https://erp.example.test/documents": "encrypted-documents-url",
                    "https://erp.example.test/soap": "encrypted-soap-url",
                    "SALAV": "encrypted-company-id",
                    "api-user": "encrypted-user",
                    "api-secret": "encrypted-password",
                }[value],
                unprotect=lambda value: {
                    "encrypted-url": "https://erp.example.test/users",
                    "encrypted-documents-url": "https://erp.example.test/documents",
                    "encrypted-soap-url": "https://erp.example.test/soap",
                    "encrypted-company-id": "SALAV",
                    "encrypted-user": "api-user",
                    "encrypted-password": "api-secret",
                }[value],
            )

            service.save_erp_user_settings(
                ErpUserSettings(
                    users_url="https://erp.example.test/users",
                    documents_url="https://erp.example.test/documents",
                    document_service_url="https://erp.example.test/soap",
                    company_id="SALAV",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                    persistent_user=True,
                )
            )

            content = preferences.read_text(encoding="utf-8")
            payload = json.loads(content)
            self.assertEqual(payload["active_certificate_thumbprint"], "AA BB")
            self.assertNotIn("https://erp.example.test/users", content)
            self.assertNotIn("https://erp.example.test/documents", content)
            self.assertNotIn("https://erp.example.test/soap", content)
            self.assertNotIn("api-secret", content)
            self.assertEqual(
                service.get_erp_user_settings(),
                ErpUserSettings(
                    users_url="https://erp.example.test/users",
                    documents_url="https://erp.example.test/documents",
                    document_service_url="https://erp.example.test/soap",
                    company_id="SALAV",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="42",
                    selected_user_name="Mario Rossi",
                    persistent_user=True,
                ),
            )

    def test_erp_document_url_must_be_https_when_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = GeneralPreferencesService(
                preferences_path=Path(directory) / "preferences.json",
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            with self.assertRaises(GeneralPreferencesServiceError):
                service.save_erp_user_settings(
                    ErpUserSettings(
                        documents_url="http://erp.example.test/documents",
                    )
                )
            with self.assertRaisesRegex(
                GeneralPreferencesServiceError,
                "URL servizio documentale SOAP non valido",
            ):
                service.save_erp_user_settings(
                    ErpUserSettings(
                        document_service_url="http://erp.example.test/soap",
                    )
                )

    def test_erp_users_url_must_be_https_when_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = GeneralPreferencesService(
                preferences_path=Path(directory) / "preferences.json",
                protect=lambda value: f"encrypted:{value}",
                unprotect=lambda value: value.removeprefix("encrypted:"),
            )

            with self.assertRaisesRegex(
                GeneralPreferencesServiceError,
                "URL utenti ERP non valido",
            ):
                service.save_erp_user_settings(
                    ErpUserSettings(users_url="http://erp.example.test/users")
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
                    documents_url="",
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

    def test_erp_users_http_url_is_rejected_before_basic_auth(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return SimpleNamespace(status=200, read=lambda: b'{"users":[]}')

        service = GeneralPreferencesService(opener=opener)

        result = service.fetch_erp_users(
            ErpUserSettings(
                users_url="http://erp.example.test/users",
                basic_username="api-user",
                basic_password="api-secret",
            )
        )

        self.assertEqual(result, ErpUsersResult(False, "URL utenti ERP non valido"))
        self.assertEqual(requests, [])

    def test_erp_documents_are_loaded_with_selected_user_and_basic_auth(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            body = (
                b'{"data":['
                b'{"vfname":"NOME_DOCUMENTO.pdf","vfdescri":"Visita",'
                b'"vfcheckoutdate":"2026-07-16 09:29:57","vfcheckoutby":"20",'
                b'"vfcodiceid":"DOC-1","vfauthcode":"secret",'
                b'"vfpath":"//Dipendenti/Idoneita/",'
                b'"vfphysicname":"hidden.pdf"},'
                b'{"vfname":"ALTRO.pdf","vfcheckoutby":"21"}'
                b"]}"
            )
            return SimpleNamespace(status=200, read=lambda: body)

        service = GeneralPreferencesService(opener=opener)

        result = service.fetch_erp_documents(
            ErpUserSettings(
                documents_url="https://erp.example.test/documents",
                basic_username="api-user",
                basic_password="api-secret",
                selected_user_id="20",
            )
        )

        self.assertEqual(
            result,
            ErpDocumentsResult(
                True,
                "Caricati 1 documenti",
                (
                    ErpDocument(
                        "NOME_DOCUMENTO.pdf",
                        "2026-07-16 09:29:57",
                        "Visita",
                        "DOC-1",
                        "secret",
                        "20",
                        "//Dipendenti/Idoneita/",
                    ),
                ),
            ),
        )
        self.assertEqual(
            requests[0][0].full_url,
            "https://erp.example.test/documents?pVFCHECKOUTBY=20",
        )
        self.assertEqual(requests[0][0].headers["Accept"], "application/json")
        self.assertEqual(
            requests[0][0].headers["Authorization"],
            "Basic YXBpLXVzZXI6YXBpLXNlY3JldA==",
        )
        self.assertEqual(requests[0][1], 8)

    def test_erp_document_storage_info_is_loaded_by_document_id(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            body = (
                b'{"data":['
                b'{"vfcodiceid":"DOC-1","vfname":"NOME_DOCUMENTO.pdf",'
                b'"vfpath":"//Dipendenti/Idoneita/","vfunknown":"kept-raw"}'
                b"]}"
            )
            return SimpleNamespace(status=200, read=lambda: body)

        service = GeneralPreferencesService(opener=opener)

        result = service.fetch_erp_document_storage_info(
            "DOC-1",
            ErpUserSettings(
                documents_url="https://erp.example.test/documents",
                basic_username="api-user",
                basic_password="api-secret",
            ),
        )

        self.assertEqual(
            result,
            ErpDocumentStorageInfoResult(
                True,
                "Metadati documento ERP caricati",
                ErpDocumentStorageInfo(
                    document_id="DOC-1",
                    name="NOME_DOCUMENTO.pdf",
                    logical_path="//Dipendenti/Idoneita/",
                ),
            ),
        )
        self.assertEqual(
            requests[0][0].full_url,
            "https://erp.example.test/documents?pVFCODICEID=DOC-1",
        )
        self.assertEqual(requests[0][0].headers["Accept"], "application/json")
        self.assertEqual(
            requests[0][0].headers["Authorization"],
            "Basic YXBpLXVzZXI6YXBpLXNlY3JldA==",
        )
        self.assertEqual(requests[0][1], 8)

    def test_erp_document_storage_info_uses_single_filtered_row_without_id(self) -> None:
        service = GeneralPreferencesService(
            opener=lambda request, *, timeout: SimpleNamespace(
                status=200,
                read=lambda: b'{"data":[{"vfname":"NOME.pdf","vfpath":"//A/B/"}]}',
            )
        )

        result = service.fetch_erp_document_storage_info(
            "DOC-1",
            ErpUserSettings(
                documents_url="https://erp.example.test/documents",
                basic_username="api-user",
                basic_password="api-secret",
            ),
        )

        self.assertEqual(
            result.info,
            ErpDocumentStorageInfo("DOC-1", "NOME.pdf", "//A/B/"),
        )

    def test_erp_document_storage_info_accepts_slogicaldir_alias(self) -> None:
        service = GeneralPreferencesService(
            opener=lambda request, *, timeout: SimpleNamespace(
                status=200,
                read=lambda: b'{"data":[{"vfcodiceid":"DOC-1","vfname":"NOME.pdf",'
                b'"sLogicalDir":"//Dipendenti/Idoneita/"}]}',
            )
        )

        result = service.fetch_erp_document_storage_info(
            "DOC-1",
            ErpUserSettings(
                documents_url="https://erp.example.test/documents",
                basic_username="api-user",
                basic_password="api-secret",
            ),
        )

        self.assertEqual(
            result.info,
            ErpDocumentStorageInfo("DOC-1", "NOME.pdf", "//Dipendenti/Idoneita/"),
        )

    def test_erp_documents_are_not_loaded_without_url_or_user(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append(request)
            return SimpleNamespace(status=200, read=lambda: b'{"data":[]}')

        service = GeneralPreferencesService(opener=opener)

        without_url = service.fetch_erp_documents(
            ErpUserSettings(selected_user_id="20")
        )
        without_user = service.fetch_erp_documents(
            ErpUserSettings(documents_url="https://erp.example.test/documents")
        )

        self.assertTrue(without_url.success)
        self.assertTrue(without_user.success)
        self.assertEqual(requests, [])

    def test_erp_documents_require_https(self) -> None:
        service = GeneralPreferencesService()
        invalid_url = service.fetch_erp_documents(
            ErpUserSettings(
                documents_url="http://erp.example.test/documents",
                basic_username="api-user",
                basic_password="api-secret",
                selected_user_id="20",
            )
        )

        self.assertEqual(
            invalid_url,
            ErpDocumentsResult(False, "URL query documenti ERP non valido"),
        )

    def test_erp_documents_empty_data_is_valid_empty_list(self) -> None:
        service = GeneralPreferencesService(
            opener=lambda request, *, timeout: SimpleNamespace(
                status=200,
                read=lambda: b'{"data":[]}',
            )
        )

        result = service.fetch_erp_documents(
            ErpUserSettings(
                documents_url="https://erp.example.test/documents",
                basic_username="api-user",
                basic_password="api-secret",
                selected_user_id="20",
            )
        )

        self.assertEqual(
            result,
            ErpDocumentsResult(True, "Nessun documento da firmare", ()),
        )

    def test_erp_documents_reject_invalid_json_schema(self) -> None:
        for body in (b"{}", b'{"data":{}}', b"[]"):
            calls = []

            def opener(request, *, timeout, response_body=body):
                calls.append(request)
                return SimpleNamespace(status=200, read=lambda: response_body)

            service = GeneralPreferencesService(opener=opener)

            result = service.fetch_erp_documents(
                ErpUserSettings(
                    documents_url="https://erp.example.test/documents",
                    basic_username="api-user",
                    basic_password="api-secret",
                    selected_user_id="20",
                )
            )

            self.assertEqual(
                result,
                ErpDocumentsResult(False, "Risposta ERP documenti non valida"),
            )
            self.assertEqual(len(calls), 1)

    def test_erp_documents_reject_syntactically_invalid_json(self) -> None:
        service = GeneralPreferencesService(
            opener=lambda request, *, timeout: SimpleNamespace(
                status=200,
                read=lambda: b"{not-json",
            )
        )

        result = service.fetch_erp_documents(
            ErpUserSettings(
                documents_url="https://erp.example.test/documents",
                basic_username="api-user",
                basic_password="api-secret",
                selected_user_id="20",
            )
        )

        self.assertEqual(
            result,
            ErpDocumentsResult(False, "Risposta ERP non in formato JSON"),
        )

    def test_erp_documents_network_error_is_controlled(self) -> None:
        def opener(request, *, timeout):
            raise OSError("boom secret-token")

        service = GeneralPreferencesService(opener=opener)

        result = service.fetch_erp_documents(
            ErpUserSettings(
                documents_url="https://erp.example.test/documents",
                basic_username="api-user",
                basic_password="api-secret",
                selected_user_id="20",
            )
        )

        self.assertEqual(
            result,
            ErpDocumentsResult(False, "Connessione ERP documenti fallita"),
        )


if __name__ == "__main__":
    unittest.main()
