"""Tests for encrypted general preferences."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.general_preferences_service import (
    GeneralPreferencesService,
    SupabaseConnectionResult,
    SupabaseSettings,
)


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
                ),
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


if __name__ == "__main__":
    unittest.main()
