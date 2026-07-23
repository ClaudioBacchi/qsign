"""Local HTTP listener compatible with the Zucchetti desktop bridge."""

from __future__ import annotations

import re
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from app.services.general_preferences_service import (
    ErpUserSettings,
    GeneralPreferencesService,
)
from app.services.erp_document_context import ErpSignedDocumentUploadContext
from app.services.infinity_dms_client import InfinityDmsClient, InfinityDmsCredentials
from services.logging.logging_service import LoggingService


DEFAULT_LOCAL_ERP_HOST = "127.0.0.1"
DEFAULT_LOCAL_ERP_PORT = 9091
_TRANSPARENT_GIF_1X1 = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


class LocalErpListenerError(RuntimeError):
    """Raised when the local ERP listener cannot handle a request."""


class LocalErpView(Protocol):
    def run_ui_task(self, callback: Callable[[], None]) -> None: ...

    def run_background_task(self, callback: Callable[[], None]) -> None: ...

    def activate_window(self) -> None: ...

    def show_status(self, message: str) -> None: ...

    def show_error(self, message: str) -> None: ...

    def show_document_flow_downloaded(self, document_name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class LocalErpDocumentRequest:
    document_id: str
    auth_code: str
    document_name: str
    user: str = ""
    company: str = ""
    source_url: str = ""
    query_parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class LocalErpDocumentPayload:
    path: Path
    upload_context: ErpSignedDocumentUploadContext | None = None
    document_name: str = ""


class LocalErpListener:
    """Receive browser GET requests from the ERP and open PDFs in qSign."""

    def __init__(
        self,
        *,
        view: LocalErpView,
        open_document: Callable[[str, ErpSignedDocumentUploadContext | None], None],
        preferences_service: GeneralPreferencesService,
        dms_client: InfinityDmsClient,
        logger: LoggingService,
        host: str = DEFAULT_LOCAL_ERP_HOST,
        port: int = DEFAULT_LOCAL_ERP_PORT,
        temp_base_directory: str | Path | None = None,
        session_id: str | None = None,
    ) -> None:
        self._view = view
        self._open_document = open_document
        self._preferences_service = preferences_service
        self._dms_client = dms_client
        self._logger = logger
        self._host = host
        self._port = port
        self._temp_base_directory = (
            Path(temp_base_directory)
            if temp_base_directory is not None
            else Path(tempfile.gettempdir()) / "qsign" / "local_erp"
        )
        self._session_id = session_id or uuid.uuid4().hex
        self._temp_session_root: Path | None = None
        self._temp_files: set[Path] = set()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    @property
    def address(self) -> tuple[str, int]:
        server = self._server
        if server is None:
            return self._host, self._port
        host, port = server.server_address[:2]
        return str(host), int(port)

    def start(self) -> bool:
        if self._server is not None:
            return True
        handler = self._build_handler()
        try:
            server = ThreadingHTTPServer((self._host, self._port), handler)
        except OSError as error:
            self._logger.warning(
                "Local ERP listener unavailable",
                host=self._host,
                port=self._port,
                error=str(error),
            )
            self._view.run_ui_task(
                lambda: self._view.show_error(
                    f"porta locale ERP {self._host}:{self._port} non disponibile"
                )
            )
            return False
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="qsign-local-erp-listener",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("Local ERP listener started", host=self._host, port=self._port)
        return True

    def stop(self) -> None:
        self._stopped.set()
        server = self._server
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)
        self._server = None
        self._thread = None
        self._cleanup_temp_files()

    def handle_document_request(
        self,
        request: LocalErpDocumentRequest,
    ) -> LocalErpDocumentPayload:
        settings = self._preferences_service.get_erp_user_settings()
        storage_info = self._load_document_storage_info(request, settings)
        service_url = settings.document_service_url.strip()
        if not service_url:
            raise LocalErpListenerError("servizio documentale ERP non configurato")
        username = settings.basic_username.strip() or request.user.strip()
        password = settings.basic_password
        if not username or not password:
            raise LocalErpListenerError("credenziali documentali ERP non configurate")
        company = request.company.strip() or settings.company_id.strip() or "SALAV"
        content = self._dms_client.download_document(
            service_url=service_url,
            credentials=InfinityDmsCredentials(
                username=username,
                password=password,
                company_id=company,
            ),
            document_id=request.document_id,
            auth_code=request.auth_code,
        )
        path = self._save_temp_pdf(request.document_name, content)
        upload_context = _erp_upload_context_from_storage_info(
            request,
            storage_info,
        )
        self._logger.info(
            "Local ERP document downloaded",
            path=str(path),
            document_id=request.document_id,
            user=request.user,
            company=company,
            erp_return_ready=upload_context is not None,
        )
        logical_name = (
            upload_context.logical_name
            if upload_context is not None and upload_context.logical_name.strip()
            else request.document_name
        )
        return LocalErpDocumentPayload(
            path=path,
            upload_context=upload_context,
            document_name=logical_name,
        )

    def _load_document_storage_info(
        self,
        request: LocalErpDocumentRequest,
        settings: ErpUserSettings,
    ) -> object | None:
        fetch_storage_info = getattr(
            self._preferences_service,
            "fetch_erp_document_storage_info",
            None,
        )
        if not callable(fetch_storage_info):
            return None
        try:
            result = fetch_storage_info(request.document_id, settings=settings)
        except Exception:
            self._logger.exception(
                "Local ERP document metadata lookup failed",
                document_id=request.document_id,
            )
            return None
        info = getattr(result, "info", None)
        logical_path = str(getattr(info, "logical_path", "") or "").strip()
        logical_name = str(getattr(info, "name", "") or "").strip()
        self._logger.info(
            "Local ERP document metadata loaded",
            document_id=request.document_id,
            success=bool(getattr(result, "success", False)),
            vfpath_configured=bool(logical_path),
            vfpath=_sanitize_local_erp_query_value("vfpath", logical_path),
            logical_name_configured=bool(logical_name),
            logical_name=_sanitize_local_erp_query_value("vfname", logical_name),
        )
        return info

    def queue_document_request(self, request: LocalErpDocumentRequest) -> None:
        def download_and_open() -> None:
            payload: LocalErpDocumentPayload | None = None
            try:
                payload = self.handle_document_request(request)
            except Exception as error:
                self._logger.exception(
                    "Local ERP document request failed",
                    document_id=request.document_id,
                )
                if not self._stopped.is_set():
                    self._view.run_ui_task(
                        lambda: self._view.show_error(
                            f"documento ERP locale non aperto: {error}"
                        )
                    )
                return
            if self._stopped.is_set():
                self._discard_temp_pdf(payload.path)
                return
            self._view.run_ui_task(lambda: self._open_and_activate(payload))

        self._view.run_background_task(download_and_open)

    def _open_and_activate(self, payload: LocalErpDocumentPayload) -> None:
        self._view.show_document_flow_downloaded(
            payload.document_name or payload.path.name
        )
        self._open_document(str(payload.path), payload.upload_context)
        activate_window = getattr(self._view, "activate_window", None)
        if callable(activate_window):
            activate_window()

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        listener = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_OPTIONS(self) -> None:
                self._send_text(HTTPStatus.NO_CONTENT, "")

            def do_GET(self) -> None:
                try:
                    request = _parse_local_erp_get(self.path)
                except LocalErpListenerError as error:
                    listener._logger.warning(
                        "Local ERP invalid GET received",
                        client=self.client_address[0],
                        path=self.path,
                        error=str(error),
                    )
                    self._send_text(HTTPStatus.BAD_REQUEST, str(error))
                    return
                if request is None:
                    if _is_local_erp_ping(self.path):
                        listener._logger.info(
                            "Local ERP ping received",
                            client=self.client_address[0],
                            path=_redact_sensitive_query_values(self.path),
                        )
                        self._send_bytes(
                            HTTPStatus.OK,
                            _TRANSPARENT_GIF_1X1,
                            content_type="image/gif",
                        )
                        return
                    else:
                        listener._logger.info(
                            "Local ERP non-document GET received",
                            client=self.client_address[0],
                            path=_redact_sensitive_query_values(self.path),
                        )
                    self._send_text(HTTPStatus.OK, "qSign attivo")
                    return
                listener._logger.info(
                    "Local ERP document GET received",
                    client=self.client_address[0],
                    document_id=request.document_id,
                    document_name=request.document_name,
                    user=request.user,
                    company=request.company,
                    source_url_configured=bool(request.source_url),
                    parameter_names=_local_erp_parameter_names(request),
                    logical_dir_candidates=_local_erp_query_candidates(
                        request,
                        _LOGICAL_DIR_FIELD_HINTS,
                    ),
                    logical_name_candidates=_local_erp_query_candidates(
                        request,
                        _LOGICAL_NAME_FIELD_HINTS,
                    ),
                )
                listener.queue_document_request(request)
                self._send_text(HTTPStatus.OK, "Documento ricevuto da qSign")

            def log_message(self, format: str, *args: object) -> None:
                listener._logger.debug(
                    "Local ERP listener request",
                    client=self.client_address[0],
                    request=_redact_sensitive_query_values(format % args),
                )

            def _send_text(self, status: HTTPStatus, text: str) -> None:
                body = text.encode("utf-8")
                self._send_bytes(status, body, content_type="text/plain; charset=utf-8")

            def _send_bytes(
                self,
                status: HTTPStatus,
                body: bytes,
                *,
                content_type: str,
            ) -> None:
                self.send_response(int(status))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

        return RequestHandler

    def _save_temp_pdf(self, document_name: str, content: bytes) -> Path:
        temp_root = self._temp_root()
        temp_root.mkdir(parents=True, exist_ok=True)
        path = temp_root / f"{uuid.uuid4().hex}_{_safe_document_filename(document_name)}"
        path.write_bytes(content)
        self._temp_files.add(path)
        return path

    def _temp_root(self) -> Path:
        if self._temp_session_root is None:
            self._temp_session_root = self._temp_base_directory / self._session_id
        return self._temp_session_root

    def _cleanup_temp_files(self) -> None:
        session_root = self._temp_session_root
        for path in tuple(self._temp_files):
            self._discard_temp_pdf(path)
        if session_root is not None:
            try:
                session_root.rmdir()
            except OSError:
                pass

    def _discard_temp_pdf(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            if self._is_own_temp_path(path):
                path.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            self._temp_files.discard(path)

    def _is_own_temp_path(self, path: Path) -> bool:
        session_root = self._temp_session_root
        if session_root is None or session_root.is_symlink():
            return False
        return path.absolute().parent == session_root.absolute()


def _parse_local_erp_get(path: str) -> LocalErpDocumentRequest | None:
    parsed = urlsplit(path)
    route = parsed.path.strip("/").lower()
    raw_query = {
        key: values[-1].strip()
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if values
    }
    query = {
        key.upper(): value
        for key, value in raw_query.items()
    }
    if route.startswith("ping") or "PING" in query:
        return None
    document_id = query.get("VFCODICEID", "")
    auth_code = query.get("VFAUTHCODE", "")
    if not document_id and not auth_code:
        return None
    if not document_id or not auth_code:
        raise LocalErpListenerError("parametri documento ERP incompleti")
    return LocalErpDocumentRequest(
        document_id=document_id,
        auth_code=auth_code,
        document_name=query.get("DOCUMENTNAME", "documento.pdf"),
        user=query.get("USER", ""),
        company=query.get("COMPANY", ""),
        source_url=query.get("URL", ""),
        query_parameters=tuple(raw_query.items()),
    )


def _is_local_erp_ping(path: str) -> bool:
    parsed = urlsplit(path)
    route = parsed.path.strip("/").lower()
    query = {
        key.upper()
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if values
    }
    return route.startswith("ping") or "PING" in query


def _redact_sensitive_query_values(value: str) -> str:
    return re.sub(
        r"(?i)(VFAUTHCODE=)[^&\s]+",
        r"\1[redacted]",
        value,
    )


_SENSITIVE_FIELD_HINTS = ("auth", "password", "pwd", "token", "secret")
_IDENTIFIER_FIELD_NAMES = {"VFCODICEID", "DOCUMENTID", "DOCID"}
_LOGICAL_DIR_FIELD_HINTS = (
    "dir",
    "directory",
    "folder",
    "parent",
    "logical",
    "path",
)
_LOGICAL_NAME_FIELD_HINTS = ("name", "file")


def _local_erp_parameter_names(request: LocalErpDocumentRequest) -> tuple[str, ...]:
    return tuple(name for name, _ in request.query_parameters)


def _local_erp_query_candidates(
    request: LocalErpDocumentRequest,
    hints: tuple[str, ...],
) -> dict[str, str]:
    candidates: dict[str, str] = {}
    for name, value in request.query_parameters:
        if any(hint in name.lower() for hint in hints):
            candidates[name] = _sanitize_local_erp_query_value(name, value)
    return candidates


def _erp_upload_context_from_storage_info(
    request: LocalErpDocumentRequest,
    storage_info: object | None,
) -> ErpSignedDocumentUploadContext | None:
    logical_dir = str(getattr(storage_info, "logical_path", "") or "").strip()
    logical_name = str(getattr(storage_info, "name", "") or "").strip()
    if not logical_name:
        logical_name = request.document_name.strip()
    if not logical_dir or not logical_name:
        return None
    return ErpSignedDocumentUploadContext(
        document_id=request.document_id,
        logical_dir=logical_dir,
        logical_name=logical_name,
    )


def _sanitize_local_erp_query_value(name: str, value: str) -> str:
    lower_name = name.lower()
    upper_name = name.upper()
    if (
        upper_name in _IDENTIFIER_FIELD_NAMES
        or any(hint in lower_name for hint in _SENSITIVE_FIELD_HINTS)
    ):
        return "[redacted]"
    text = value.strip()
    if not text:
        return ""
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", "[email]", text)
    text = re.sub(r"\b\d{5,}\b", "[number]", text)
    text = re.sub(r"[A-Za-z]:\\(?:[^\\/]+\\)*", r"[path]\\", text)
    text = re.sub(
        r"\\\\[^\\/]+\\[^\\/]+(?:\\[^\\/]+)*",
        r"\\[host]\[share]\[path]",
        text,
    )
    text = re.sub(r"/(?:[^/\s]+/)+", "/[path]/", text)
    return _redact_text_segments(text)


def _redact_text_segments(value: str) -> str:
    parts = re.split(r"([\\/])", value)
    redacted: list[str] = []
    for part in parts:
        if part in {"/", "\\"} or not part:
            redacted.append(part)
            continue
        if part.startswith("[") and part.endswith("]"):
            redacted.append(part)
            continue
        if re.search(r"[A-Za-z]{3,}", part):
            extension = ""
            if "." in part:
                suffix = part.rsplit(".", 1)[-1].lower()
                if suffix in {"pdf", "doc", "docx", "xml", "txt"}:
                    extension = f".{suffix}"
            redacted.append(f"[text]{extension}")
        else:
            redacted.append(part)
    return "".join(redacted)


def _safe_document_filename(value: str) -> str:
    name = Path(value).name.strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = name.strip(" .") or "documento.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name
