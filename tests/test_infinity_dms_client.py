"""Tests for the small InfinityDmsInterface SOAP client."""

import base64
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from app.services.infinity_dms_client import (
    InfinityDmsClient,
    InfinityDmsClientError,
    InfinityDmsCredentials,
    SERVICE_NAMESPACE,
)


VALID_PDF = b""


class InfinityDmsClientTests(unittest.TestCase):
    def test_connect_get_document_and_disconnect_requests_are_well_formed(self) -> None:
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_response(
                    "getDocumentResponse",
                    "getDocumentReturn",
                    _pdf_base64(_valid_pdf_bytes()),
                ),
                _soap_response("disconnectResponse", "disconnectReturn", "OK"),
            ]
        )
        client = InfinityDmsClient(opener=transport)

        content = client.download_document(
            service_url="https://erp.example.test/InfinityDmsInterface",
            credentials=InfinityDmsCredentials(
                username="api&user",
                password="secret<pwd>",
                company_id="SALAV",
            ),
            document_id="DOC-1",
            auth_code="AUTH-1",
        )

        self.assertEqual(content, _valid_pdf_bytes())
        self.assertEqual(len(transport.requests), 3)
        for request in transport.requests:
            self.assertEqual(request.headers["Content-type"], "text/xml;charset=UTF-8")
            self.assertEqual(request.headers["Soapaction"], '""')
            self.assertNotIn("Authorization", request.headers)

        connect = _body_operation(transport.requests[0].data)
        self.assertEqual(_local_name(connect.tag), "connect")
        self.assertEqual(_namespace(connect.tag), SERVICE_NAMESPACE)
        self.assertEqual(_child_text(connect, "sUser"), "api&user")
        self.assertEqual(_child_text(connect, "sPwd"), "secret<pwd>")
        self.assertEqual(_child_text(connect, "sCompanyId"), "SALAV")

        get_document = _body_operation(transport.requests[1].data)
        self.assertEqual(_local_name(get_document.tag), "getDocument")
        self.assertEqual(_child_text(get_document, "sContextId"), "CTX-1")
        self.assertEqual(_child_text(get_document, "sVfCodiceId"), "DOC-1")
        self.assertEqual(_child_text(get_document, "sVfAuthCode"), "AUTH-1")

        disconnect = _body_operation(transport.requests[2].data)
        self.assertEqual(_local_name(disconnect.tag), "disconnect")
        self.assertEqual(_child_text(disconnect, "sContextId"), "CTX-1")

    def test_base64_whitespace_is_accepted(self) -> None:
        encoded = _pdf_base64(_valid_pdf_bytes())
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_response(
                    "getDocumentResponse",
                    "getDocumentReturn",
                    f"\n {encoded[:4]} \n {encoded[4:]} ",
                ),
                _soap_response("disconnectResponse", "disconnectReturn", "OK"),
            ]
        )

        content = InfinityDmsClient(opener=transport).download_document(
            service_url="https://erp.example.test/service",
            credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
            document_id="DOC",
            auth_code="AUTH",
        )

        self.assertEqual(content, _valid_pdf_bytes())

    def test_upload_document_connects_copies_file_and_disconnects(self) -> None:
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_response("copyFileResponse", "copyFileReturn", "OK"),
                _soap_response("disconnectResponse", "disconnectReturn", "OK"),
            ]
        )
        client = InfinityDmsClient(opener=transport)

        result = client.upload_document(
            service_url="https://erp.example.test/InfinityDmsInterface",
            credentials=InfinityDmsCredentials("api-user", "secret", "SALAV"),
            content=_valid_pdf_bytes(),
            logical_dir="//Dipendenti/Idoneita/",
            logical_name="signed.pdf",
        )

        self.assertEqual(result, "OK")
        self.assertEqual(len(transport.requests), 3)
        copy_file = _body_operation(transport.requests[1].data)
        self.assertEqual(_local_name(copy_file.tag), "copyFile")
        self.assertEqual(_child_text(copy_file, "sContextId"), "CTX-1")
        self.assertEqual(_child_text(copy_file, "sLogicalDir"), "//Dipendenti/Idoneita/")
        self.assertEqual(_child_text(copy_file, "sLogicalName"), "signed.pdf")
        self.assertEqual(_child_text(copy_file, "sFlags"), "0")
        self.assertEqual(_child_text(copy_file, "iAgain"), "0")
        self.assertEqual(
            base64.b64decode(_child_text(copy_file, "bFile").encode("ascii")),
            _valid_pdf_bytes(),
        )

    def test_upload_rejects_missing_logical_directory(self) -> None:
        with self.assertRaisesRegex(InfinityDmsClientError, "Directory logica"):
            InfinityDmsClient(opener=FakeSoapTransport([])).upload_document(
                service_url="https://erp.example.test/service",
                credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
                content=_valid_pdf_bytes(),
                logical_dir="",
                logical_name="signed.pdf",
            )

    def test_disconnect_is_attempted_when_get_document_fails(self) -> None:
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_fault("get failed"),
                _soap_response("disconnectResponse", "disconnectReturn", "OK"),
            ]
        )

        with self.assertRaisesRegex(InfinityDmsClientError, "get failed"):
            InfinityDmsClient(opener=transport).download_document(
                service_url="https://erp.example.test/service",
                credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
                document_id="DOC",
                auth_code="AUTH",
            )

        self.assertEqual(
            [_local_name(_body_operation(request.data).tag) for request in transport.requests],
            ["connect", "getDocument", "disconnect"],
        )

    def test_disconnect_is_not_attempted_without_valid_context(self) -> None:
        transport = FakeSoapTransport(
            [_soap_response("connectResponse", "connectReturn", "")]
        )

        with self.assertRaises(InfinityDmsClientError):
            InfinityDmsClient(opener=transport).download_document(
                service_url="https://erp.example.test/service",
                credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
                document_id="DOC",
                auth_code="AUTH",
            )

        self.assertEqual(len(transport.requests), 1)

    def test_invalid_base64_and_non_pdf_are_rejected(self) -> None:
        for encoded in ("", "not-base64", base64.b64encode(b"hello").decode("ascii")):
            transport = FakeSoapTransport(
                [
                    _soap_response("connectResponse", "connectReturn", "CTX-1"),
                    _soap_response("getDocumentResponse", "getDocumentReturn", encoded),
                    _soap_response("disconnectResponse", "disconnectReturn", "OK"),
                ]
            )
            with self.assertRaises(InfinityDmsClientError):
                InfinityDmsClient(opener=transport).download_document(
                    service_url="https://erp.example.test/service",
                    credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
                    document_id="DOC",
                    auth_code="AUTH",
                )
            self.assertEqual(len(transport.requests), 3)

    def test_truncated_pdf_is_rejected(self) -> None:
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_response(
                    "getDocumentResponse",
                    "getDocumentReturn",
                    _pdf_base64(b"%PDF-truncated"),
                ),
                _soap_response("disconnectResponse", "disconnectReturn", "OK"),
            ]
        )

        with self.assertRaisesRegex(InfinityDmsClientError, "PDF non valido"):
            InfinityDmsClient(opener=transport).download_document(
                service_url="https://erp.example.test/service",
                credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
                document_id="DOC",
                auth_code="AUTH",
            )

    def test_encrypted_pdf_is_rejected(self) -> None:
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_response(
                    "getDocumentResponse",
                    "getDocumentReturn",
                    _pdf_base64(_encrypted_pdf_bytes()),
                ),
                _soap_response("disconnectResponse", "disconnectReturn", "OK"),
            ]
        )

        with self.assertRaisesRegex(InfinityDmsClientError, "password"):
            InfinityDmsClient(opener=transport).download_document(
                service_url="https://erp.example.test/service",
                credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
                document_id="DOC",
                auth_code="AUTH",
            )

    def test_pdf_without_pages_is_rejected(self) -> None:
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_response(
                    "getDocumentResponse",
                    "getDocumentReturn",
                    _pdf_base64(_empty_pdf_bytes()),
                ),
                _soap_response("disconnectResponse", "disconnectReturn", "OK"),
            ]
        )

        with self.assertRaisesRegex(InfinityDmsClientError, "privo di pagine"):
            InfinityDmsClient(opener=transport).download_document(
                service_url="https://erp.example.test/service",
                credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
                document_id="DOC",
                auth_code="AUTH",
            )

    def test_pdf_payload_over_limit_is_rejected_before_full_decode(self) -> None:
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_response(
                    "getDocumentResponse",
                    "getDocumentReturn",
                    base64.b64encode(b"0" * 32).decode("ascii"),
                ),
                _soap_response("disconnectResponse", "disconnectReturn", "OK"),
            ]
        )

        with self.assertRaisesRegex(InfinityDmsClientError, "troppo grande"):
            InfinityDmsClient(opener=transport, max_pdf_bytes=8).download_document(
                service_url="https://erp.example.test/service",
                credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
                document_id="DOC",
                auth_code="AUTH",
            )

    def test_disconnect_failure_after_success_does_not_destroy_success(self) -> None:
        logger = FakeLogger()
        transport = FakeSoapTransport(
            [
                _soap_response("connectResponse", "connectReturn", "CTX-1"),
                _soap_response(
                    "getDocumentResponse",
                    "getDocumentReturn",
                    _pdf_base64(_valid_pdf_bytes()),
                ),
                _soap_fault("disconnect failed"),
            ]
        )

        content = InfinityDmsClient(opener=transport, logger=logger).download_document(
            service_url="https://erp.example.test/service",
            credentials=InfinityDmsCredentials("user", "pwd", "SALAV"),
            document_id="DOC",
            auth_code="AUTH",
        )

        self.assertEqual(content, _valid_pdf_bytes())
        self.assertEqual(logger.warnings[0][0], "Infinity DMS disconnect failed after document download")


class FakeSoapTransport:
    def __init__(self, responses: list[bytes]) -> None:
        self._responses = responses
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append(request)
        body = self._responses.pop(0)
        return SimpleNamespace(status=200, read=lambda: body)


class FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, object]]] = []

    def warning(self, message: str, **context: object) -> None:
        self.warnings.append((message, context))


def _soap_response(operation: str, return_name: str, value: str) -> bytes:
    envelope = ET.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
    body = ET.SubElement(envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
    response = ET.SubElement(body, f"{{{SERVICE_NAMESPACE}}}{operation}")
    result = ET.SubElement(response, return_name)
    result.text = value
    return ET.tostring(envelope, encoding="utf-8")


def _soap_fault(message: str) -> bytes:
    envelope = ET.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
    body = ET.SubElement(envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
    fault = ET.SubElement(body, "{http://schemas.xmlsoap.org/soap/envelope/}Fault")
    faultstring = ET.SubElement(fault, "faultstring")
    faultstring.text = message
    return ET.tostring(envelope, encoding="utf-8")


def _pdf_base64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _valid_pdf_bytes() -> bytes:
    global VALID_PDF
    if not VALID_PDF:
        document = pymupdf.open()
        try:
            page = document.new_page()
            page.insert_text((72, 72), "qSign ERP PDF")
            VALID_PDF = document.tobytes()
        finally:
            document.close()
    return VALID_PDF


def _empty_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n"
        b"xref\n"
        b"0 3\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000060 00000 n \n"
        b"trailer << /Root 1 0 R /Size 3 >>\n"
        b"startxref\n"
        b"114\n"
        b"%%EOF\n"
    )


def _encrypted_pdf_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "encrypted.pdf"
        document = pymupdf.open()
        try:
            document.new_page()
            document.save(
                path,
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="owner",
                user_pw="user",
            )
        finally:
            document.close()
        return path.read_bytes()


def _body_operation(payload: bytes) -> ET.Element:
    root = ET.fromstring(payload)
    body = next(node for node in root.iter() if _local_name(node.tag) == "Body")
    return list(body)[0]


def _child_text(root: ET.Element, name: str) -> str:
    node = next(child for child in root if _local_name(child.tag) == name)
    return node.text or ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0]


if __name__ == "__main__":
    unittest.main()
