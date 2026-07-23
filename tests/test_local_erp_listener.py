"""Tests for the local ERP browser bridge."""

import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf

from app.services.general_preferences_service import ErpUserSettings
from app.services.local_erp_listener import (
    LocalErpDocumentRequest,
    LocalErpListener,
    LocalErpListenerError,
    _parse_local_erp_get,
)
from services.logging.logging_service import LoggingService


class LocalErpListenerTests(unittest.TestCase):
    def test_parse_ping_returns_no_document_request(self) -> None:
        self.assertIsNone(_parse_local_erp_get("/ping20260717150127"))
        self.assertIsNone(_parse_local_erp_get("/?ping=1"))

    def test_parse_document_request_accepts_zucchetti_parameters(self) -> None:
        request = _parse_local_erp_get(
            "/?VFCODICEID=855860&VFAUTHCODE=abc"
            "&DOCUMENTNAME=BRIGHI_ALESSANDRO_20260715(1).pdf"
            "&USER=Romani&COMPANY=SALAV&URL=https%3A%2F%2Fapp.example.test%2F"
        )

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.document_id, "855860")
        self.assertEqual(request.auth_code, "abc")
        self.assertEqual(request.document_name, "BRIGHI_ALESSANDRO_20260715(1).pdf")
        self.assertEqual(request.user, "Romani")
        self.assertEqual(request.company, "SALAV")
        self.assertEqual(request.source_url, "https://app.example.test/")
        self.assertIn(("VFCODICEID", "855860"), request.query_parameters)
        self.assertIn(("VFAUTHCODE", "abc"), request.query_parameters)

    def test_parse_document_request_preserves_extra_query_parameters(self) -> None:
        request = _parse_local_erp_get(
            "/?VFCODICEID=855860&VFAUTHCODE=abc"
            "&DOCUMENTNAME=GHINASSI_ENNIO_MASSIMO.pdf"
            "&sLogicalDir=%2F%2FDipendenti%2FIdoneita%2F"
            "&sLogicalName=GHINASSI_ENNIO_MASSIMO_20260226.pdf"
            "&vfparent=12345"
        )

        self.assertIsNotNone(request)
        assert request is not None
        self.assertIn(("sLogicalDir", "//Dipendenti/Idoneita/"), request.query_parameters)
        self.assertIn(
            ("sLogicalName", "GHINASSI_ENNIO_MASSIMO_20260226.pdf"),
            request.query_parameters,
        )
        self.assertIn(("vfparent", "12345"), request.query_parameters)

    def test_parse_rejects_incomplete_document_request(self) -> None:
        with self.assertRaises(LocalErpListenerError):
            _parse_local_erp_get("/?VFCODICEID=855860")

    def test_handle_document_request_downloads_and_saves_temp_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf_bytes = _valid_pdf_bytes()
            view = FakeView()
            preferences = FakePreferencesService(
                ErpUserSettings(
                    document_service_url="https://erp.example.test/soap",
                    company_id="PREF",
                    basic_username="api-user",
                    basic_password="api-secret",
                )
            )
            client = FakeDmsClient(pdf_bytes)
            listener = LocalErpListener(
                view=view,
                open_document=lambda _, __=None: None,
                preferences_service=preferences,
                dms_client=client,
                logger=LoggingService.create("qsign.tests.local_erp_listener"),
                host="127.0.0.1",
                port=0,
                temp_base_directory=directory,
                session_id="session-a",
            )

            payload = listener.handle_document_request(
                LocalErpDocumentRequest(
                    document_id="DOC-1",
                    auth_code="AUTH-1",
                    document_name="../BRIGHI_ALESSANDRO.pdf",
                    user="Romani",
                    company="SALAV",
                )
            )
            path = payload.path

            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), pdf_bytes)
            self.assertNotIn("..", path.name)
            self.assertEqual(client.calls[0]["service_url"], "https://erp.example.test/soap")
            self.assertEqual(client.calls[0]["document_id"], "DOC-1")
            self.assertEqual(client.calls[0]["auth_code"], "AUTH-1")
            self.assertEqual(client.calls[0]["credentials"].username, "api-user")
            self.assertEqual(client.calls[0]["credentials"].password, "api-secret")
            self.assertEqual(client.calls[0]["credentials"].company_id, "SALAV")
            self.assertEqual(preferences.storage_info_calls[0][0], "DOC-1")
            self.assertIsNotNone(payload.upload_context)
            assert payload.upload_context is not None
            self.assertEqual(payload.upload_context.document_id, "DOC-1")
            self.assertEqual(payload.upload_context.logical_dir, "//Dipendenti/Idoneita/")
            self.assertEqual(
                payload.upload_context.logical_name,
                "GHINASSI_ENNIO_MASSIMO_20260226.pdf",
            )

            listener.stop()

            self.assertFalse(path.exists())

    def test_http_get_queues_document_open_and_allows_browser_cors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            opened_documents: list[tuple[str, object]] = []
            view = FakeView()
            logger = FakeLogger()
            listener = LocalErpListener(
                view=view,
                open_document=lambda path, context=None: opened_documents.append(
                    (path, context)
                ),
                preferences_service=FakePreferencesService(
                    ErpUserSettings(
                        document_service_url="https://erp.example.test/soap",
                        company_id="SALAV",
                        basic_username="api-user",
                        basic_password="api-secret",
                    )
                ),
                dms_client=FakeDmsClient(_valid_pdf_bytes()),
                logger=logger,
                host="127.0.0.1",
                port=0,
                temp_base_directory=directory,
                session_id="session-b",
            )
            self.assertTrue(listener.start())
            host, port = listener.address

            with urllib.request.urlopen(
                f"http://{host}:{port}/?ping&a=20260720094928",
                timeout=3,
            ) as response:
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
                self.assertEqual(response.headers["Content-Type"], "image/gif")
                self.assertTrue(body.startswith(b"GIF89a"))

            url = (
                f"http://{host}:{port}/?VFCODICEID=DOC-1&VFAUTHCODE=AUTH-1"
                "&DOCUMENTNAME=doc.pdf&USER=Romani&COMPANY=SALAV"
                "&sLogicalDir=%2F%2FDipendenti%2FIdoneita%2F"
                "&sLogicalName=GHINASSI_ENNIO_MASSIMO_20260226.pdf"
            )
            with urllib.request.urlopen(url, timeout=3) as response:
                self.assertEqual(response.status, 200)

            self.assertEqual(len(opened_documents), 1)
            opened_path, upload_context = opened_documents[0]
            self.assertTrue(Path(opened_path).is_file())
            self.assertIsNotNone(upload_context)
            self.assertEqual(upload_context.logical_dir, "//Dipendenti/Idoneita/")
            self.assertEqual(
                upload_context.logical_name,
                "GHINASSI_ENNIO_MASSIMO_20260226.pdf",
            )
            self.assertEqual(view.activate_count, 1)
            self.assertIn("Local ERP ping received", logger.messages("info"))
            self.assertIn("Local ERP document GET received", logger.messages("info"))
            self.assertNotIn("AUTH-1", str(logger.records))
            document_log = next(
                context
                for level, message, context in logger.records
                if level == "info" and message == "Local ERP document GET received"
            )
            self.assertIn("sLogicalDir", document_log["parameter_names"])
            self.assertEqual(
                document_log["logical_dir_candidates"]["sLogicalDir"],
                "//[path]/",
            )
            self.assertEqual(
                document_log["logical_name_candidates"]["sLogicalName"],
                "[text].pdf",
            )
            self.assertNotIn("GHINASSI", str(document_log))
            metadata_log = next(
                context
                for level, message, context in logger.records
                if level == "info" and message == "Local ERP document metadata loaded"
            )
            self.assertTrue(metadata_log["vfpath_configured"])
            self.assertEqual(metadata_log["vfpath"], "//[path]/")
            self.assertEqual(metadata_log["logical_name"], "[text].pdf")
            listener.stop()

    def test_http_get_reports_bad_request_for_incomplete_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            listener = LocalErpListener(
                view=FakeView(),
                open_document=lambda _, __=None: None,
                preferences_service=FakePreferencesService(ErpUserSettings()),
                dms_client=FakeDmsClient(_valid_pdf_bytes()),
                logger=LoggingService.create("qsign.tests.local_erp_listener.bad"),
                host="127.0.0.1",
                port=0,
                temp_base_directory=directory,
            )
            self.assertTrue(listener.start())
            host, port = listener.address

            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(
                    f"http://{host}:{port}/?VFCODICEID=DOC-1",
                    timeout=3,
                )

            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            listener.stop()


class FakeView:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.errors: list[str] = []
        self.activate_count = 0

    def run_ui_task(self, callback) -> None:
        callback()

    def run_background_task(self, callback) -> None:
        callback()

    def activate_window(self) -> None:
        self.activate_count += 1

    def show_status(self, message: str) -> None:
        self.statuses.append(message)

    def show_error(self, message: str) -> None:
        self.errors.append(message)


class FakePreferencesService:
    def __init__(self, settings: ErpUserSettings) -> None:
        self.settings = settings
        self.storage_info_calls: list[tuple[str, ErpUserSettings]] = []

    def get_erp_user_settings(self) -> ErpUserSettings:
        return self.settings

    def fetch_erp_document_storage_info(
        self,
        document_id: str,
        *,
        settings: ErpUserSettings,
    ):
        self.storage_info_calls.append((document_id, settings))
        return FakeStorageInfoResult(
            success=True,
            info=FakeStorageInfo(
                name="GHINASSI_ENNIO_MASSIMO_20260226.pdf",
                logical_path="//Dipendenti/Idoneita/",
            ),
        )


class FakeStorageInfo:
    def __init__(self, *, name: str, logical_path: str) -> None:
        self.name = name
        self.logical_path = logical_path


class FakeStorageInfoResult:
    def __init__(self, *, success: bool, info: FakeStorageInfo | None) -> None:
        self.success = success
        self.info = info


class FakeDmsClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def download_document(self, **kwargs) -> bytes:
        self.calls.append(kwargs)
        return self.content


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def debug(self, message: str, **context: object) -> None:
        self.records.append(("debug", message, context))

    def info(self, message: str, **context: object) -> None:
        self.records.append(("info", message, context))

    def warning(self, message: str, **context: object) -> None:
        self.records.append(("warning", message, context))

    def exception(self, message: str, **context: object) -> None:
        self.records.append(("exception", message, context))

    def messages(self, level: str) -> list[str]:
        return [message for record_level, message, _ in self.records if record_level == level]


def _valid_pdf_bytes() -> bytes:
    document = pymupdf.open()
    try:
        document.new_page()
        return document.tobytes()
    finally:
        document.close()


if __name__ == "__main__":
    unittest.main()
