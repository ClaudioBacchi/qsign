"""Small SOAP client for Zucchetti InfinityDmsInterface document transfer."""

from __future__ import annotations

import base64
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pymupdf

from services.logging.logging_service import LoggingService


SOAP_NAMESPACE = "http://schemas.xmlsoap.org/soap/envelope/"
SERVICE_NAMESPACE = "http://services.dmsinterface.infinity.zucchetti.it"
DEFAULT_MAX_PDF_BYTES = 100 * 1024 * 1024


class InfinityDmsClientError(RuntimeError):
    """Raised when Infinity DMS document transfer fails safely."""


@dataclass(frozen=True, slots=True)
class InfinityDmsCredentials:
    username: str
    password: str
    company_id: str


class InfinityDmsClient:
    """Call InfinityDmsInterface without runtime WSDL or extra SOAP dependencies."""

    def __init__(
        self,
        opener: Callable[[urllib.request.Request, float], object] | None = None,
        logger: LoggingService | None = None,
        max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
    ) -> None:
        self._opener = opener or urllib.request.urlopen
        self._logger = logger
        self._max_pdf_bytes = max_pdf_bytes

    def download_document(
        self,
        *,
        service_url: str,
        credentials: InfinityDmsCredentials,
        document_id: str,
        auth_code: str,
    ) -> bytes:
        context_id = self._connect(service_url, credentials)
        pdf_bytes: bytes | None = None
        get_error: BaseException | None = None
        try:
            pdf_bytes = self._get_document(
                service_url,
                context_id,
                document_id,
                auth_code,
            )
            return pdf_bytes
        except BaseException as error:
            get_error = error
            raise
        finally:
            try:
                self._disconnect(service_url, context_id)
            except InfinityDmsClientError as error:
                if pdf_bytes is not None and self._logger is not None:
                    self._logger.warning(
                        "Infinity DMS disconnect failed after document download",
                        error=str(error),
                    )
                elif get_error is None:
                    raise

    def upload_document(
        self,
        *,
        service_url: str,
        credentials: InfinityDmsCredentials,
        content: bytes,
        logical_dir: str,
        logical_name: str,
        flags: str = "0",
        again: str = "0",
    ) -> str:
        logical_dir = logical_dir.strip()
        logical_name = logical_name.strip()
        if not logical_dir:
            raise InfinityDmsClientError("Directory logica documentale non configurata")
        if not logical_name:
            raise InfinityDmsClientError("Nome logico documentale non configurato")
        encoded = _encode_pdf_base64(content, max_pdf_bytes=self._max_pdf_bytes)
        context_id = self._connect(service_url, credentials)
        copy_result: str | None = None
        copy_error: BaseException | None = None
        try:
            copy_result = self._copy_file(
                service_url,
                context_id,
                encoded,
                logical_dir,
                logical_name,
                flags,
                again,
            )
            return copy_result
        except BaseException as error:
            copy_error = error
            raise
        finally:
            try:
                self._disconnect(service_url, context_id)
            except InfinityDmsClientError as error:
                if copy_result is not None and self._logger is not None:
                    self._logger.warning(
                        "Infinity DMS disconnect failed after document upload",
                        error=str(error),
                    )
                elif copy_error is None:
                    raise

    def _connect(
        self,
        service_url: str,
        credentials: InfinityDmsCredentials,
    ) -> str:
        envelope = _soap_envelope(
            "connect",
            {
                "sUser": credentials.username,
                "sPwd": credentials.password,
                "sCompanyId": credentials.company_id,
            },
        )
        payload = self._post(service_url, envelope)
        context_id = _required_text(payload, "connectReturn")
        if not context_id:
            raise InfinityDmsClientError("Risposta documentale priva di contesto")
        return context_id

    def _get_document(
        self,
        service_url: str,
        context_id: str,
        document_id: str,
        auth_code: str,
    ) -> bytes:
        envelope = _soap_envelope(
            "getDocument",
            {
                "sContextId": context_id,
                "sVfCodiceId": document_id,
                "sVfAuthCode": auth_code,
            },
        )
        payload = self._post(service_url, envelope)
        encoded = _required_text(payload, "getDocumentReturn")
        return _decode_pdf_base64(encoded, max_pdf_bytes=self._max_pdf_bytes)

    def _copy_file(
        self,
        service_url: str,
        context_id: str,
        encoded_pdf: str,
        logical_dir: str,
        logical_name: str,
        flags: str,
        again: str,
    ) -> str:
        envelope = _soap_envelope(
            "copyFile",
            {
                "sContextId": context_id,
                "bFile": encoded_pdf,
                "sLogicalDir": logical_dir,
                "sLogicalName": logical_name,
                "sFlags": flags,
                "iAgain": again,
            },
        )
        payload = self._post(service_url, envelope)
        return _required_text(payload, "copyFileReturn")

    def _disconnect(self, service_url: str, context_id: str) -> None:
        envelope = _soap_envelope("disconnect", {"sContextId": context_id})
        payload = self._post(service_url, envelope)
        _required_text(payload, "disconnectReturn")

    def _post(self, service_url: str, envelope: ET.Element) -> ET.Element:
        body = ET.tostring(envelope, encoding="utf-8", xml_declaration=True)
        request = urllib.request.Request(
            service_url,
            data=body,
            headers={
                "Content-Type": "text/xml;charset=UTF-8",
                "SOAPAction": '""',
            },
            method="POST",
        )
        try:
            response = self._opener(request, timeout=8)
            status = int(getattr(response, "status", 200))
            response_body = _read_response_body(response)
        except urllib.error.HTTPError as error:
            response_body = _read_http_error_body(error)
            try:
                payload = ET.fromstring(response_body)
            except ET.ParseError:
                raise InfinityDmsClientError(
                    f"Servizio documentale non valido: {error.code}"
                ) from error
            _raise_soap_fault_if_present(payload)
            raise InfinityDmsClientError(
                f"Servizio documentale non valido: {error.code}"
            ) from error
        except Exception as error:
            raise InfinityDmsClientError("Connessione documentale fallita") from error

        if not 200 <= status < 300:
            raise InfinityDmsClientError(f"Servizio documentale non valido: {status}")
        try:
            payload = ET.fromstring(response_body)
        except ET.ParseError as error:
            raise InfinityDmsClientError("Risposta documentale XML non valida") from error
        _raise_soap_fault_if_present(payload)
        return payload


def _soap_envelope(operation: str, parameters: dict[str, str]) -> ET.Element:
    envelope = ET.Element(f"{{{SOAP_NAMESPACE}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP_NAMESPACE}}}Body")
    operation_node = ET.SubElement(body, f"{{{SERVICE_NAMESPACE}}}{operation}")
    for name, value in parameters.items():
        param = ET.SubElement(operation_node, name)
        param.text = value
    return envelope


def _required_text(payload: ET.Element, local_name: str) -> str:
    node = _find_first_by_local_name(payload, local_name)
    text = (node.text or "").strip() if node is not None else ""
    if not text:
        raise InfinityDmsClientError("Risposta documentale incompleta")
    return text


def _raise_soap_fault_if_present(payload: ET.Element) -> None:
    fault = _find_first_by_local_name(payload, "Fault")
    if fault is None:
        return
    fault_text = _find_first_by_local_name(fault, "faultstring")
    detail = (fault_text.text or "").strip() if fault_text is not None else ""
    if not detail:
        detail = "Errore SOAP documentale"
    raise InfinityDmsClientError(detail)


def _find_first_by_local_name(root: ET.Element, local_name: str) -> ET.Element | None:
    for node in root.iter():
        if _local_name(node.tag) == local_name:
            return node
    return None


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _decode_pdf_base64(
    value: str,
    *,
    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
) -> bytes:
    normalized = re.sub(r"\s+", "", value)
    if not normalized:
        raise InfinityDmsClientError("Documento documentale vuoto")
    if _estimated_base64_decoded_size(normalized) > max_pdf_bytes:
        raise InfinityDmsClientError("Documento documentale troppo grande")
    try:
        content = base64.b64decode(normalized.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as error:
        raise InfinityDmsClientError("Documento documentale Base64 non valido") from error
    if not content:
        raise InfinityDmsClientError("Documento documentale vuoto")
    if len(content) > max_pdf_bytes:
        raise InfinityDmsClientError("Documento documentale troppo grande")
    if not content.startswith(b"%PDF-"):
        raise InfinityDmsClientError("Documento documentale non PDF")
    _validate_pdf_structure(content)
    return content


def _encode_pdf_base64(
    content: bytes,
    *,
    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
) -> str:
    if not content:
        raise InfinityDmsClientError("Documento documentale vuoto")
    if len(content) > max_pdf_bytes:
        raise InfinityDmsClientError("Documento documentale troppo grande")
    if not content.startswith(b"%PDF-"):
        raise InfinityDmsClientError("Documento documentale non PDF")
    _validate_pdf_structure(content)
    return base64.b64encode(content).decode("ascii")


def _estimated_base64_decoded_size(value: str) -> int:
    padding = len(value) - len(value.rstrip("="))
    return (len(value) * 3) // 4 - padding


def _validate_pdf_structure(content: bytes) -> None:
    document: pymupdf.Document | None = None
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
        if not document.is_pdf:
            raise InfinityDmsClientError("Documento documentale non PDF")
        if document.needs_pass:
            raise InfinityDmsClientError("Documento documentale protetto da password")
        if document.page_count == 0:
            raise InfinityDmsClientError("Documento documentale privo di pagine")
    except InfinityDmsClientError:
        raise
    except Exception as error:
        raise InfinityDmsClientError("Documento documentale PDF non valido") from error
    finally:
        if document is not None:
            document.close()


def _read_response_body(response: object) -> bytes:
    read = getattr(response, "read", None)
    if not callable(read):
        return b""
    body = read()
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return b""


def _read_http_error_body(error: urllib.error.HTTPError) -> bytes:
    try:
        body: Any = error.read()
    finally:
        error.close()
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return b""
