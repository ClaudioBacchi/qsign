"""Tests for the first Windows certificate preference milestone."""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.certificate_service import CertificateService, CertificateServiceError


class CertificateServiceTests(unittest.TestCase):
    def test_list_certificates_reads_windows_store_payload(self) -> None:
        service = CertificateService(command_runner=lambda _: _certificates_payload())

        certificates = service.list_certificates()

        self.assertEqual(len(certificates), 2)
        self.assertEqual(certificates[0].name, "Mario Rossi")
        self.assertEqual(certificates[0].type, "Store Windows - chiave privata")
        self.assertEqual(certificates[0].valid_until, "2028-01-31")
        self.assertEqual(certificates[0].thumbprint, "AABB")

    def test_certificate_name_falls_back_to_subject_common_name(self) -> None:
        service = CertificateService(
            command_runner=lambda _: json.dumps(
                {
                    "name": "CN=Claudio Bacchi, O=Queen",
                    "subject": "CN=Claudio Bacchi, O=Queen",
                    "type": "Store Windows - chiave privata",
                    "valid_until": "2029-07-08",
                    "thumbprint": "AA BB",
                }
            )
        )

        certificates = service.list_certificates()

        self.assertEqual(certificates[0].name, "Claudio Bacchi")

    def test_active_certificate_is_resolved_from_saved_thumbprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            preferences.write_text(
                json.dumps({"active_certificate_thumbprint": "aa bb"}),
                encoding="utf-8",
            )
            service = CertificateService(
                preferences_path=preferences,
                command_runner=lambda _: _certificates_payload(),
            )

            certificate = service.get_active_certificate()

            self.assertIsNotNone(certificate)
            self.assertEqual(certificate.name, "Mario Rossi")

    def test_missing_active_certificate_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            preferences.write_text(
                json.dumps({"active_certificate_thumbprint": "missing"}),
                encoding="utf-8",
            )
            service = CertificateService(
                preferences_path=preferences,
                command_runner=lambda _: _certificates_payload(),
            )

            self.assertIsNone(service.get_active_certificate())

    def test_set_active_certificate_saves_only_thumbprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            service = CertificateService(
                preferences_path=preferences,
                command_runner=lambda _: _certificates_payload(),
            )

            service.set_active_certificate("cc dd")

            payload = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(payload, {"active_certificate_thumbprint": "CCDD"})

    def test_set_active_certificate_rejects_unknown_thumbprint(self) -> None:
        service = CertificateService(command_runner=lambda _: _certificates_payload())

        with self.assertRaises(CertificateServiceError):
            service.set_active_certificate("unknown")

    def test_delete_certificate_removes_active_thumbprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            preferences.write_text(
                json.dumps({"active_certificate_thumbprint": "AA BB"}),
                encoding="utf-8",
            )
            scripts: list[str] = []

            def run(script: str) -> str:
                scripts.append(script)
                return ""

            service = CertificateService(
                preferences_path=preferences,
                command_runner=run,
            )

            service.delete_certificate("aa bb")

            self.assertIn("$thumbprint = 'AABB'", scripts[0])
            payload = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertNotIn("active_certificate_thumbprint", payload)

    def test_generate_self_signed_updates_active_thumbprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "preferences.json"
            scripts: list[str] = []

            def run(script: str) -> str:
                scripts.append(script)
                return json.dumps(
                    {
                        "name": "Mario Rossi",
                        "type": "Store Windows - chiave privata",
                        "valid_until": "2029-01-31",
                        "thumbprint": "11 22",
                    }
                )

            service = CertificateService(
                preferences_path=preferences,
                command_runner=run,
            )

            certificate = service.generate_self_signed(
                "Mario",
                "Rossi",
                "Queen",
                "secret",
                "2031-05-20",
            )

            self.assertEqual(certificate.thumbprint, "1122")
            self.assertIn("CertificateRequest", scripts[0])
            self.assertIn("$validUntil = '2031-05-20'", scripts[0])
            payload = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(payload["active_certificate_thumbprint"], "1122")

    def test_generate_self_signed_rejects_invalid_expiration(self) -> None:
        service = CertificateService(command_runner=lambda _: "{}")

        with self.assertRaises(CertificateServiceError):
            service.generate_self_signed(
                "Mario",
                "Rossi",
                "Queen",
                "secret",
                "20/05/2031",
            )

    def test_generate_self_signed_rejects_expired_certificate_date(self) -> None:
        service = CertificateService(command_runner=lambda _: "{}")

        with self.assertRaises(CertificateServiceError):
            service.generate_self_signed(
                "Mario",
                "Rossi",
                "Queen",
                "secret",
                "2020-01-01",
            )

    def test_import_pfx_imports_file_and_updates_active_thumbprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "certificate.pfx"
            source.write_bytes(b"pfx")
            preferences = Path(directory) / "preferences.json"
            scripts: list[str] = []

            def run(script: str) -> str:
                scripts.append(script)
                return json.dumps(
                    {
                        "name": "Imported",
                        "type": "Store Windows - chiave privata",
                        "valid_until": "2029-01-31",
                        "thumbprint": "33 44",
                    }
                )

            service = CertificateService(
                preferences_path=preferences,
                command_runner=run,
            )

            certificate = service.import_pfx(source, "secret")

            self.assertEqual(certificate.thumbprint, "3344")
            self.assertIn("X509Certificate2Collection", scripts[0])
            payload = json.loads(preferences.read_text(encoding="utf-8"))
            self.assertEqual(payload["active_certificate_thumbprint"], "3344")


def _certificates_payload() -> str:
    return json.dumps(
        [
            {
                "name": "Mario Rossi",
                "type": "Store Windows - chiave privata",
                "valid_until": "2028-01-31",
                "thumbprint": "AA BB",
            },
            {
                "name": "Queen",
                "type": "Store Windows",
                "valid_until": "2027-05-10",
                "thumbprint": "CC DD",
            },
        ]
    )


if __name__ == "__main__":
    unittest.main()
