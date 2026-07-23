"""General application preferences with encrypted Supabase credentials."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from services.logging.logging_service import LoggingService


class GeneralPreferencesServiceError(RuntimeError):
    """Raised when general preferences cannot be saved or tested."""


@dataclass(frozen=True, slots=True)
class SupabaseSettings:
    project_url: str = ""
    password: str = ""
    table_name: str = "SaluteLavoro"
    auto_sync_templates_on_startup: bool = False
    auto_save_signed_documents: bool = False
    list_erp_documents: bool = False
    auto_refresh_erp_documents: bool = False
    erp_refresh_interval_seconds: int = 0
    show_signature_text: bool = False
    signature_capture_mode: str = "mouse"
    local_erp_port: int = 9091


@dataclass(frozen=True, slots=True)
class SupabaseConnectionResult:
    success: bool
    message: str


@dataclass(frozen=True, slots=True)
class SupabaseTableResult:
    success: bool
    exists: bool
    message: str


@dataclass(frozen=True, slots=True)
class ErpUser:
    user_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class ErpDocument:
    name: str
    checkout_date: str = ""
    description: str = ""
    document_id: str = ""
    auth_code: str = ""
    checkout_by: str = ""


@dataclass(frozen=True, slots=True)
class ErpUserSettings:
    users_url: str = ""
    documents_url: str = ""
    document_service_url: str = ""
    company_id: str = "SALAV"
    basic_username: str = ""
    basic_password: str = ""
    selected_user_id: str = ""
    selected_user_name: str = ""
    persistent_user: bool = False


@dataclass(frozen=True, slots=True)
class ErpUsersResult:
    success: bool
    message: str
    users: tuple[ErpUser, ...] = ()


@dataclass(frozen=True, slots=True)
class ErpDocumentsResult:
    success: bool
    message: str
    documents: tuple[ErpDocument, ...] = ()


@dataclass(frozen=True, slots=True)
class ErpDocumentStorageInfo:
    document_id: str
    name: str = ""
    logical_path: str = ""


@dataclass(frozen=True, slots=True)
class ErpDocumentStorageInfoResult:
    success: bool
    message: str
    info: ErpDocumentStorageInfo | None = None


class GeneralPreferencesService:
    """Read and write encrypted general preferences."""

    _PREFERENCES_KEY = "general"
    _SUPABASE_URL_KEY = "supabase_url"
    _SUPABASE_PASSWORD_KEY = "supabase_password"
    _SUPABASE_TABLE_KEY = "supabase_table"
    _SUPABASE_AUTO_SYNC_KEY = "supabase_auto_sync_templates_on_startup"
    _AUTO_SAVE_SIGNED_DOCUMENTS_KEY = "auto_save_signed_documents"
    _ERP_LIST_DOCUMENTS_KEY = "erp_list_documents"
    _ERP_AUTO_REFRESH_DOCUMENTS_KEY = "erp_auto_refresh_documents"
    _ERP_REFRESH_INTERVAL_SECONDS_KEY = "erp_refresh_interval_seconds"
    _SHOW_SIGNATURE_TEXT_KEY = "show_signature_text"
    _SIGNATURE_CAPTURE_MODE_KEY = "signature_capture_mode"
    _LOCAL_ERP_PORT_KEY = "local_erp_port"
    _ERP_USERS_URL_KEY = "erp_users_url"
    _ERP_DOCUMENTS_URL_KEY = "erp_documents_url"
    _ERP_DOCUMENT_SERVICE_URL_KEY = "erp_document_service_url"
    _ERP_COMPANY_ID_KEY = "erp_company_id"
    _ERP_BASIC_USERNAME_KEY = "erp_basic_username"
    _ERP_BASIC_PASSWORD_KEY = "erp_basic_password"
    _ERP_SELECTED_USER_ID_KEY = "erp_selected_user_id"
    _ERP_SELECTED_USER_NAME_KEY = "erp_selected_user_name"
    _ERP_PERSISTENT_USER_KEY = "erp_persistent_user"
    _ADMIN_PASSWORD_KEY = "admin_password"

    def __init__(
        self,
        preferences_path: str | Path = "config/preferences.json",
        protect: Callable[[str], str] | None = None,
        unprotect: Callable[[str], str] | None = None,
        opener: Callable[[urllib.request.Request, float], object] | None = None,
        logger: LoggingService | None = None,
    ) -> None:
        self._preferences_path = Path(preferences_path)
        self._protect = protect or self._protect_text
        self._unprotect = unprotect or self._unprotect_text
        self._opener = opener or urllib.request.urlopen
        self._logger = logger

    def get_supabase_settings(self) -> SupabaseSettings:
        general = self._read_general_preferences()
        list_erp_documents = bool(
            general.get(self._ERP_LIST_DOCUMENTS_KEY)
            or (
                self._ERP_LIST_DOCUMENTS_KEY not in general
                and general.get(self._ERP_AUTO_REFRESH_DOCUMENTS_KEY)
            )
        )
        return SupabaseSettings(
            project_url=self._read_encrypted_value(
                general, self._SUPABASE_URL_KEY
            ),
            password=self._read_encrypted_value(
                general, self._SUPABASE_PASSWORD_KEY
            ),
            table_name=str(general.get(self._SUPABASE_TABLE_KEY) or "SaluteLavoro"),
            auto_sync_templates_on_startup=bool(
                general.get(self._SUPABASE_AUTO_SYNC_KEY)
            ),
            auto_save_signed_documents=bool(
                general.get(self._AUTO_SAVE_SIGNED_DOCUMENTS_KEY)
            ),
            list_erp_documents=list_erp_documents,
            auto_refresh_erp_documents=(
                list_erp_documents
                and bool(general.get(self._ERP_AUTO_REFRESH_DOCUMENTS_KEY))
            ),
            erp_refresh_interval_seconds=(
                _normalized_erp_refresh_interval_seconds(
                    general.get(self._ERP_REFRESH_INTERVAL_SECONDS_KEY)
                )
                if list_erp_documents
                else 0
            ),
            show_signature_text=bool(general.get(self._SHOW_SIGNATURE_TEXT_KEY)),
            signature_capture_mode=_normalized_signature_capture_mode(
                general.get(self._SIGNATURE_CAPTURE_MODE_KEY)
            ),
            local_erp_port=_normalized_local_erp_port(
                general.get(self._LOCAL_ERP_PORT_KEY)
            ),
        )

    def save_supabase_settings(self, settings: SupabaseSettings) -> None:
        previous_settings = self.get_supabase_settings()
        list_erp_documents = bool(settings.list_erp_documents)
        auto_refresh_erp_documents = (
            list_erp_documents and settings.auto_refresh_erp_documents
        )
        erp_refresh_interval_seconds = (
            _normalized_erp_refresh_interval_seconds(
                settings.erp_refresh_interval_seconds
            )
            if list_erp_documents
            else 0
        )
        payload = self._read_preferences()
        general = payload.get(self._PREFERENCES_KEY)
        if not isinstance(general, dict):
            general = {}
        general[self._SUPABASE_URL_KEY] = self._encrypted_payload(
            settings.project_url.strip()
        )
        general[self._SUPABASE_PASSWORD_KEY] = self._encrypted_payload(
            settings.password.strip()
        )
        general[self._SUPABASE_TABLE_KEY] = settings.table_name.strip() or "SaluteLavoro"
        general[self._SUPABASE_AUTO_SYNC_KEY] = settings.auto_sync_templates_on_startup
        general[self._AUTO_SAVE_SIGNED_DOCUMENTS_KEY] = (
            settings.auto_save_signed_documents
        )
        general[self._ERP_LIST_DOCUMENTS_KEY] = list_erp_documents
        general[self._ERP_AUTO_REFRESH_DOCUMENTS_KEY] = auto_refresh_erp_documents
        general[self._ERP_REFRESH_INTERVAL_SECONDS_KEY] = erp_refresh_interval_seconds
        general[self._SHOW_SIGNATURE_TEXT_KEY] = settings.show_signature_text
        general[self._SIGNATURE_CAPTURE_MODE_KEY] = (
            _normalized_signature_capture_mode(settings.signature_capture_mode)
        )
        general[self._LOCAL_ERP_PORT_KEY] = _normalized_local_erp_port(
            settings.local_erp_port
        )
        payload[self._PREFERENCES_KEY] = general
        self._write_preferences(payload)
        self._log_general_settings_saved(
            previous_settings,
            SupabaseSettings(
                project_url=settings.project_url,
                password=settings.password,
                table_name=settings.table_name,
                auto_sync_templates_on_startup=settings.auto_sync_templates_on_startup,
                auto_save_signed_documents=settings.auto_save_signed_documents,
                list_erp_documents=list_erp_documents,
                auto_refresh_erp_documents=auto_refresh_erp_documents,
                erp_refresh_interval_seconds=erp_refresh_interval_seconds,
                show_signature_text=settings.show_signature_text,
                signature_capture_mode=settings.signature_capture_mode,
                local_erp_port=settings.local_erp_port,
            ),
        )

    def get_erp_user_settings(self) -> ErpUserSettings:
        general = self._read_general_preferences()
        return ErpUserSettings(
            users_url=self._read_encrypted_value(general, self._ERP_USERS_URL_KEY),
            documents_url=self._read_encrypted_value(
                general, self._ERP_DOCUMENTS_URL_KEY
            ),
            document_service_url=self._read_encrypted_value(
                general, self._ERP_DOCUMENT_SERVICE_URL_KEY
            ),
            company_id=(
                self._read_encrypted_value(general, self._ERP_COMPANY_ID_KEY)
                or "SALAV"
            ),
            basic_username=self._read_encrypted_value(
                general, self._ERP_BASIC_USERNAME_KEY
            ),
            basic_password=self._read_encrypted_value(
                general, self._ERP_BASIC_PASSWORD_KEY
            ),
            selected_user_id=str(general.get(self._ERP_SELECTED_USER_ID_KEY) or ""),
            selected_user_name=str(general.get(self._ERP_SELECTED_USER_NAME_KEY) or ""),
            persistent_user=bool(general.get(self._ERP_PERSISTENT_USER_KEY)),
        )

    def save_erp_user_settings(self, settings: ErpUserSettings) -> None:
        users_url = settings.users_url.strip()
        if users_url and not users_url.startswith("https://"):
            raise GeneralPreferencesServiceError("URL utenti ERP non valido")
        documents_url = settings.documents_url.strip()
        if documents_url and not documents_url.startswith("https://"):
            raise GeneralPreferencesServiceError(
                "URL query documenti ERP non valido"
            )
        document_service_url = settings.document_service_url.strip()
        if document_service_url and not document_service_url.startswith("https://"):
            raise GeneralPreferencesServiceError(
                "URL servizio documentale SOAP non valido"
            )
        previous_settings = self.get_erp_user_settings()
        payload = self._read_preferences()
        general = payload.get(self._PREFERENCES_KEY)
        if not isinstance(general, dict):
            general = {}
        general[self._ERP_USERS_URL_KEY] = self._encrypted_payload(
            settings.users_url.strip()
        )
        general[self._ERP_DOCUMENTS_URL_KEY] = self._encrypted_payload(
            settings.documents_url.strip()
        )
        general[self._ERP_DOCUMENT_SERVICE_URL_KEY] = self._encrypted_payload(
            settings.document_service_url.strip()
        )
        general[self._ERP_COMPANY_ID_KEY] = self._encrypted_payload(
            settings.company_id.strip() or "SALAV"
        )
        general[self._ERP_BASIC_USERNAME_KEY] = self._encrypted_payload(
            settings.basic_username.strip()
        )
        general[self._ERP_BASIC_PASSWORD_KEY] = self._encrypted_payload(
            settings.basic_password.strip()
        )
        general[self._ERP_SELECTED_USER_ID_KEY] = settings.selected_user_id.strip()
        general[self._ERP_SELECTED_USER_NAME_KEY] = settings.selected_user_name.strip()
        general[self._ERP_PERSISTENT_USER_KEY] = bool(settings.persistent_user)
        payload[self._PREFERENCES_KEY] = general
        self._write_preferences(payload)
        self._log_erp_user_settings_saved(previous_settings, settings)

    def has_admin_password(self) -> bool:
        general = self._read_general_preferences()
        return bool(self._read_encrypted_value(general, self._ADMIN_PASSWORD_KEY))

    def set_admin_password(self, password: str) -> None:
        if not password.strip():
            raise GeneralPreferencesServiceError("Password amministratore obbligatoria")
        payload = self._read_preferences()
        general = payload.get(self._PREFERENCES_KEY)
        if not isinstance(general, dict):
            general = {}
        general[self._ADMIN_PASSWORD_KEY] = self._encrypted_payload(password)
        payload[self._PREFERENCES_KEY] = general
        self._write_preferences(payload)
        if self._logger is not None:
            self._logger.info("Administrator password configured")

    def verify_admin_password(self, password: str) -> bool:
        general = self._read_general_preferences()
        stored_password = self._read_encrypted_value(general, self._ADMIN_PASSWORD_KEY)
        return bool(stored_password) and password == stored_password

    def log_erp_user_session_selection(
        self,
        settings: ErpUserSettings | None = None,
        source: str = "manual",
    ) -> None:
        if self._logger is None:
            return
        effective_settings = settings or self.get_erp_user_settings()
        if not effective_settings.selected_user_id and not effective_settings.selected_user_name:
            return
        self._logger.info(
            "ERP user selected for signing session",
            selected_user_id=effective_settings.selected_user_id.strip(),
            selected_user_name=effective_settings.selected_user_name.strip(),
            source=source,
        )

    def fetch_erp_users(
        self, settings: ErpUserSettings | None = None
    ) -> ErpUsersResult:
        effective_settings = settings or self.get_erp_user_settings()
        users_url = effective_settings.users_url.strip()
        username = effective_settings.basic_username.strip()
        password = effective_settings.basic_password.strip()
        if not users_url:
            return ErpUsersResult(False, "URL utenti ERP obbligatorio")
        if not users_url.startswith("https://"):
            return ErpUsersResult(False, "URL utenti ERP non valido")
        if not username:
            return ErpUsersResult(False, "Utente Basic Auth obbligatorio")
        if not password:
            return ErpUsersResult(False, "Password Basic Auth obbligatoria")

        request = urllib.request.Request(
            users_url,
            headers={
                "Accept": "application/json",
                "Authorization": self._basic_auth_header(username, password),
            },
            method="GET",
        )
        try:
            response = self._opener(request, timeout=8)
            status = int(getattr(response, "status", 200))
            body = _read_response_body(response)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                return ErpUsersResult(
                    False, "Connessione ERP fallita: credenziali non autorizzate"
                )
            return ErpUsersResult(False, f"Risposta ERP non valida: {error.code}")
        except Exception as error:
            return ErpUsersResult(False, f"Connessione ERP fallita: {error}")

        if not 200 <= status < 300:
            return ErpUsersResult(False, f"Risposta ERP non valida: {status}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ErpUsersResult(False, "Risposta ERP non in formato JSON")
        users = _parse_erp_users(payload)
        if not users:
            return ErpUsersResult(False, "Nessun utente restituito dall'ERP")
        return ErpUsersResult(True, f"Caricati {len(users)} utenti", tuple(users))

    def fetch_erp_documents(
        self, settings: ErpUserSettings | None = None
    ) -> ErpDocumentsResult:
        effective_settings = settings or self.get_erp_user_settings()
        documents_url = effective_settings.documents_url.strip()
        username = effective_settings.basic_username.strip()
        password = effective_settings.basic_password.strip()
        selected_user_id = effective_settings.selected_user_id.strip()
        if not documents_url or not selected_user_id:
            return ErpDocumentsResult(True, "Configurazione documenti ERP incompleta")
        if not documents_url.startswith("https://"):
            return ErpDocumentsResult(False, "URL query documenti ERP non valido")
        if not username:
            return ErpDocumentsResult(False, "Utente Basic Auth obbligatorio")
        if not password:
            return ErpDocumentsResult(False, "Password Basic Auth obbligatoria")

        request = urllib.request.Request(
            _url_with_query_parameter(
                documents_url,
                "pVFCHECKOUTBY",
                selected_user_id,
            ),
            headers={
                "Accept": "application/json",
                "Authorization": self._basic_auth_header(username, password),
            },
            method="GET",
        )
        try:
            response = self._opener(request, timeout=8)
            status = int(getattr(response, "status", 200))
            body = _read_response_body(response)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                return ErpDocumentsResult(
                    False, "Connessione ERP fallita: credenziali non autorizzate"
                )
            return ErpDocumentsResult(False, f"Risposta ERP non valida: {error.code}")
        except Exception:
            return ErpDocumentsResult(False, "Connessione ERP documenti fallita")

        if not 200 <= status < 300:
            return ErpDocumentsResult(False, f"Risposta ERP non valida: {status}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ErpDocumentsResult(False, "Risposta ERP non in formato JSON")
        rows = _erp_document_rows(payload)
        if rows is None:
            return ErpDocumentsResult(False, "Risposta ERP documenti non valida")
        documents = _parse_erp_documents(payload, selected_user_id)
        return ErpDocumentsResult(
            True,
            (
                "Nessun documento da firmare"
                if not documents
                else f"Caricati {len(documents)} documenti"
            ),
            tuple(documents),
        )

    def fetch_erp_document_storage_info(
        self,
        document_id: str,
        settings: ErpUserSettings | None = None,
    ) -> ErpDocumentStorageInfoResult:
        effective_settings = settings or self.get_erp_user_settings()
        documents_url = effective_settings.documents_url.strip()
        username = effective_settings.basic_username.strip()
        password = effective_settings.basic_password.strip()
        normalized_document_id = document_id.strip()
        if not documents_url or not normalized_document_id:
            return ErpDocumentStorageInfoResult(
                False,
                "Configurazione metadati documento ERP incompleta",
            )
        if not documents_url.startswith("https://"):
            return ErpDocumentStorageInfoResult(
                False,
                "URL query documenti ERP non valido",
            )
        if not username:
            return ErpDocumentStorageInfoResult(
                False,
                "Utente Basic Auth obbligatorio",
            )
        if not password:
            return ErpDocumentStorageInfoResult(
                False,
                "Password Basic Auth obbligatoria",
            )

        request = urllib.request.Request(
            _url_with_query_parameter(
                documents_url,
                "pVFCODICEID",
                normalized_document_id,
            ),
            headers={
                "Accept": "application/json",
                "Authorization": self._basic_auth_header(username, password),
            },
            method="GET",
        )
        try:
            response = self._opener(request, timeout=8)
            status = int(getattr(response, "status", 200))
            body = _read_response_body(response)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                return ErpDocumentStorageInfoResult(
                    False,
                    "Connessione ERP fallita: credenziali non autorizzate",
                )
            return ErpDocumentStorageInfoResult(
                False,
                f"Risposta ERP non valida: {error.code}",
            )
        except Exception:
            return ErpDocumentStorageInfoResult(
                False,
                "Connessione ERP metadati documento fallita",
            )

        if not 200 <= status < 300:
            return ErpDocumentStorageInfoResult(
                False,
                f"Risposta ERP non valida: {status}",
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ErpDocumentStorageInfoResult(
                False,
                "Risposta ERP non in formato JSON",
            )
        rows = _erp_document_rows(payload)
        if rows is None:
            return ErpDocumentStorageInfoResult(
                False,
                "Risposta ERP documenti non valida",
            )
        info = _erp_document_storage_info_from_rows(rows, normalized_document_id)
        if info is None:
            return ErpDocumentStorageInfoResult(
                True,
                "Metadati documento ERP non trovati",
            )
        return ErpDocumentStorageInfoResult(
            True,
            "Metadati documento ERP caricati",
            info,
        )

    def test_supabase_connection(
        self, settings: SupabaseSettings | None = None
    ) -> SupabaseConnectionResult:
        effective_settings = settings or self.get_supabase_settings()
        project_url = effective_settings.project_url.strip().rstrip("/")
        password = effective_settings.password.strip()
        if not project_url:
            return SupabaseConnectionResult(False, "URL Supabase obbligatorio")
        if not project_url.startswith(("https://", "http://")):
            return SupabaseConnectionResult(
                False, "URL Supabase non valido"
            )
        if not password:
            return SupabaseConnectionResult(False, "Password/API key obbligatoria")

        request = urllib.request.Request(
            f"{project_url}/auth/v1/settings",
            headers=self._supabase_headers(password),
            method="GET",
        )
        try:
            response = self._opener(request, timeout=8)
            status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                return SupabaseConnectionResult(
                    False, "Connessione fallita: credenziali non autorizzate"
                )
            status = error.code
        except Exception as error:
            return SupabaseConnectionResult(False, f"Connessione fallita: {error}")
        if 200 <= status < 300:
            return SupabaseConnectionResult(True, "Connessione Supabase riuscita")
        return SupabaseConnectionResult(False, f"Risposta Supabase non valida: {status}")

    def test_supabase_template_table(
        self, settings: SupabaseSettings | None = None
    ) -> SupabaseTableResult:
        validation_error = self._validate_supabase_settings(settings)
        if validation_error is not None:
            return SupabaseTableResult(False, False, validation_error)
        effective_settings = settings or self.get_supabase_settings()
        table_name = effective_settings.table_name.strip() or "SaluteLavoro"
        request = urllib.request.Request(
            (
                f"{effective_settings.project_url.strip().rstrip('/')}/rest/v1/"
                f"{urllib.parse.quote(table_name, safe='')}"
                "?select=template_id&limit=1"
            ),
            headers={
                **self._supabase_headers(effective_settings.password.strip()),
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            response = self._opener(request, timeout=8)
            status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as error:
            body = _read_http_error_body(error)
            if self._is_missing_supabase_table(error.code, body):
                return SupabaseTableResult(
                    True,
                    False,
                    f"Tabella template Supabase '{table_name}' non trovata",
                )
            if error.code in {401, 403}:
                return SupabaseTableResult(
                    False,
                    True,
                    (
                        f"Tabella '{table_name}' non verificabile: "
                        "accesso negato da credenziali o Row Level Security"
                    ),
                )
            return SupabaseTableResult(
                False,
                False,
                f"Verifica tabella Supabase fallita: {error.code}",
            )
        except Exception as error:
            return SupabaseTableResult(
                False,
                False,
                f"Verifica tabella Supabase fallita: {error}",
            )
        if 200 <= status < 300:
            return SupabaseTableResult(
                True,
                True,
                f"Tabella template Supabase '{table_name}' disponibile",
            )
        return SupabaseTableResult(
            False,
            False,
            f"Risposta Supabase non valida: {status}",
        )

    def ensure_supabase_template_table(
        self,
        settings: SupabaseSettings | None = None,
        source_table: str = "SaluteLavoro",
    ) -> SupabaseTableResult:
        validation_error = self._validate_supabase_settings(settings)
        if validation_error is not None:
            return SupabaseTableResult(False, False, validation_error)
        effective_settings = settings or self.get_supabase_settings()
        table_name = effective_settings.table_name.strip() or "SaluteLavoro"
        payload = json.dumps(
            {
                "target_table": table_name,
                "source_table": source_table,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            (
                f"{effective_settings.project_url.strip().rstrip('/')}"
                "/rest/v1/rpc/qsign_ensure_template_table"
            ),
            data=payload,
            headers={
                **self._supabase_headers(effective_settings.password.strip()),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self._opener(request, timeout=12)
            status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as error:
            body = _read_http_error_body(error)
            if error.code == 404 or "qsign_ensure_template_table" in body:
                return SupabaseTableResult(
                    False,
                    False,
                    (
                        "Creazione tabella non configurata: installa la funzione "
                        "Supabase RPC qsign_ensure_template_table"
                    ),
                )
            if error.code in {401, 403}:
                return SupabaseTableResult(
                    False,
                    False,
                    (
                        "Creazione tabella non autorizzata: verifica policy RPC "
                        "o credenziali Supabase"
                    ),
                )
            return SupabaseTableResult(
                False,
                False,
                f"Creazione tabella Supabase fallita: {error.code}",
            )
        except Exception as error:
            return SupabaseTableResult(
                False,
                False,
                f"Creazione tabella Supabase fallita: {error}",
            )
        if 200 <= status < 300:
            return SupabaseTableResult(
                True,
                True,
                f"Tabella template Supabase '{table_name}' pronta",
            )
        return SupabaseTableResult(
            False,
            False,
            f"Risposta Supabase non valida: {status}",
        )

    def _validate_supabase_settings(
        self, settings: SupabaseSettings | None = None
    ) -> str | None:
        effective_settings = settings or self.get_supabase_settings()
        project_url = effective_settings.project_url.strip()
        password = effective_settings.password.strip()
        if not project_url:
            return "URL Supabase obbligatorio"
        if not project_url.startswith(("https://", "http://")):
            return "URL Supabase non valido"
        if not password:
            return "Password/API key obbligatoria"
        return None

    def _log_general_settings_saved(
        self,
        previous_settings: SupabaseSettings,
        settings: SupabaseSettings,
    ) -> None:
        if self._logger is None:
            return
        self._logger.info(
            "General preferences saved",
            table_name=settings.table_name.strip() or "SaluteLavoro",
            project_url_configured=bool(settings.project_url.strip()),
            project_url_changed=(
                previous_settings.project_url.strip()
                != settings.project_url.strip()
            ),
            api_key_configured=bool(settings.password.strip()),
            api_key_changed=(
                bool(previous_settings.password.strip())
                != bool(settings.password.strip())
            ),
            auto_sync_templates_on_startup=settings.auto_sync_templates_on_startup,
            auto_save_signed_documents=settings.auto_save_signed_documents,
            list_erp_documents=settings.list_erp_documents,
            auto_refresh_erp_documents=settings.auto_refresh_erp_documents,
            erp_refresh_interval_seconds=settings.erp_refresh_interval_seconds,
            show_signature_text=settings.show_signature_text,
            signature_capture_mode=_normalized_signature_capture_mode(
                settings.signature_capture_mode
            ),
            local_erp_port=_normalized_local_erp_port(settings.local_erp_port),
            changed_fields=sorted(
                _changed_general_settings_fields(previous_settings, settings)
            ),
        )

    def _log_erp_user_settings_saved(
        self,
        previous_settings: ErpUserSettings,
        settings: ErpUserSettings,
    ) -> None:
        if self._logger is None:
            return
        self._logger.info(
            "ERP user preferences saved",
            users_url_configured=bool(settings.users_url.strip()),
            users_url_changed=(
                previous_settings.users_url.strip() != settings.users_url.strip()
            ),
            documents_url_configured=bool(settings.documents_url.strip()),
            documents_url_changed=(
                previous_settings.documents_url.strip()
                != settings.documents_url.strip()
            ),
            document_service_url_configured=bool(
                settings.document_service_url.strip()
            ),
            document_service_url_changed=(
                previous_settings.document_service_url.strip()
                != settings.document_service_url.strip()
            ),
            company_id_configured=bool(settings.company_id.strip()),
            company_id_changed=(
                previous_settings.company_id.strip() != settings.company_id.strip()
            ),
            basic_username_configured=bool(settings.basic_username.strip()),
            basic_username_changed=(
                previous_settings.basic_username.strip()
                != settings.basic_username.strip()
            ),
            basic_password_configured=bool(settings.basic_password.strip()),
            basic_password_changed=(
                bool(previous_settings.basic_password.strip())
                != bool(settings.basic_password.strip())
            ),
            selected_user_id=settings.selected_user_id.strip(),
            selected_user_changed=(
                previous_settings.selected_user_id.strip()
                != settings.selected_user_id.strip()
                or previous_settings.selected_user_name.strip()
                != settings.selected_user_name.strip()
            ),
            persistent_user=settings.persistent_user,
            persistent_user_changed=(
                previous_settings.persistent_user != settings.persistent_user
            ),
        )

    @staticmethod
    def _supabase_headers(api_key: str) -> dict[str, str]:
        headers = {"apikey": api_key}
        if not api_key.startswith(("sb_publishable_", "sb_secret_")):
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _is_missing_supabase_table(status_code: int, body: str) -> bool:
        lower_body = body.lower()
        return status_code == 404 or (
            "could not find" in lower_body
            and "schema cache" in lower_body
        )

    @staticmethod
    def _basic_auth_header(username: str, password: str) -> str:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
            "ascii"
        )
        return f"Basic {token}"

    def _read_general_preferences(self) -> dict[str, Any]:
        general = self._read_preferences().get(self._PREFERENCES_KEY)
        return general if isinstance(general, dict) else {}

    def _read_encrypted_value(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, dict):
            return ""
        if value.get("protected_with") == "plain-internal-test":
            plain_value = value.get("value")
            return plain_value if isinstance(plain_value, str) else ""
        if value.get("protected_with") != "windows-dpapi-current-user":
            return ""
        encrypted = value.get("value")
        if not isinstance(encrypted, str) or not encrypted:
            return ""
        try:
            return self._unprotect(encrypted)
        except GeneralPreferencesServiceError as error:
            self._log_preferences_warning(
                "Encrypted preference could not be read",
                key=key,
                error_type=type(error).__name__,
            )
            return ""

    def _encrypted_payload(self, value: str) -> dict[str, str]:
        return {
            "protected_with": "windows-dpapi-current-user",
            "value": self._protect(value),
        }

    def _read_preferences(self) -> dict[str, Any]:
        payload = self._read_json_object(self._preferences_path)
        if payload is not None:
            return payload
        if not self._preferences_path.exists():
            return {}
        backup_path = self._backup_preferences_path()
        backup_payload = self._read_json_object(backup_path)
        if backup_payload is not None:
            self._log_preferences_warning(
                "Preferences file is not readable; backup loaded",
                source="backup",
            )
            return backup_payload
        self._log_preferences_warning(
            "Preferences file and backup are not readable",
            source="defaults",
        )
        return {}

    def _write_preferences(self, payload: dict[str, Any]) -> None:
        self._preferences_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._preferences_path.parent,
                prefix=f".{self._preferences_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            if self._read_json_object(self._preferences_path) is not None:
                self._copy_valid_preferences_to_backup()
            self._replace_preferences_file(temp_path, self._preferences_path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _backup_preferences_path(self) -> Path:
        return self._preferences_path.with_name(f"{self._preferences_path.name}.bak")

    def _read_json_object(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _copy_valid_preferences_to_backup(self) -> None:
        backup_path = self._backup_preferences_path()
        content = self._preferences_path.read_text(encoding="utf-8")
        with backup_path.open("w", encoding="utf-8") as backup_file:
            backup_file.write(content)
            backup_file.flush()
            os.fsync(backup_file.fileno())

    def _replace_preferences_file(self, source: Path, destination: Path) -> None:
        source.replace(destination)

    def _log_preferences_warning(self, message: str, **context: object) -> None:
        if self._logger is None:
            return
        warning = getattr(self._logger, "warning", None)
        if callable(warning):
            warning(message, **context)

    @classmethod
    def _protect_text(cls, value: str) -> str:
        return base64.b64encode(cls._crypt_protect(value.encode("utf-8"))).decode(
            "ascii"
        )

    @classmethod
    def _unprotect_text(cls, value: str) -> str:
        try:
            encrypted = base64.b64decode(value.encode("ascii"))
        except ValueError as error:
            raise GeneralPreferencesServiceError("Credenziale cifrata non valida") from error
        return cls._crypt_unprotect(encrypted).decode("utf-8")

    @staticmethod
    def _crypt_protect(content: bytes) -> bytes:
        if sys.platform != "win32":
            raise GeneralPreferencesServiceError(
                "Crittografia credenziali disponibile solo su Windows"
            )
        return _dpapi_crypt(content, protect=True)

    @staticmethod
    def _crypt_unprotect(content: bytes) -> bytes:
        if sys.platform != "win32":
            raise GeneralPreferencesServiceError(
                "Crittografia credenziali disponibile solo su Windows"
            )
        return _dpapi_crypt(content, protect=False)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _dpapi_crypt(content: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_buffer = ctypes.create_string_buffer(content)
    input_blob = _DataBlob(
        len(content),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = _DataBlob()
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        )
    if not ok:
        raise GeneralPreferencesServiceError("Impossibile cifrare le credenziali")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


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


def _read_http_error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read()
    finally:
        error.close()
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    return ""


def _changed_general_settings_fields(
    previous_settings: SupabaseSettings,
    settings: SupabaseSettings,
) -> set[str]:
    changed_fields: set[str] = set()
    if previous_settings.project_url.strip() != settings.project_url.strip():
        changed_fields.add("project_url")
    if bool(previous_settings.password.strip()) != bool(settings.password.strip()):
        changed_fields.add("api_key_presence")
    if previous_settings.table_name.strip() != settings.table_name.strip():
        changed_fields.add("table_name")
    if (
        previous_settings.auto_sync_templates_on_startup
        != settings.auto_sync_templates_on_startup
    ):
        changed_fields.add("auto_sync_templates_on_startup")
    if (
        previous_settings.auto_save_signed_documents
        != settings.auto_save_signed_documents
    ):
        changed_fields.add("auto_save_signed_documents")
    if previous_settings.list_erp_documents != settings.list_erp_documents:
        changed_fields.add("list_erp_documents")
    if (
        previous_settings.auto_refresh_erp_documents
        != settings.auto_refresh_erp_documents
    ):
        changed_fields.add("auto_refresh_erp_documents")
    if (
        _effective_erp_refresh_interval_seconds(previous_settings)
        != _effective_erp_refresh_interval_seconds(settings)
    ):
        changed_fields.add("erp_refresh_interval_seconds")
    if previous_settings.show_signature_text != settings.show_signature_text:
        changed_fields.add("show_signature_text")
    if (
        _normalized_signature_capture_mode(previous_settings.signature_capture_mode)
        != _normalized_signature_capture_mode(settings.signature_capture_mode)
    ):
        changed_fields.add("signature_capture_mode")
    if (
        _normalized_local_erp_port(previous_settings.local_erp_port)
        != _normalized_local_erp_port(settings.local_erp_port)
    ):
        changed_fields.add("local_erp_port")
    return changed_fields


def _normalized_signature_capture_mode(value: object) -> str:
    mode = str(value or "mouse").strip().casefold()
    if mode == "wacom":
        return "wacom"
    return "mouse"


def _normalized_erp_refresh_interval_seconds(value: object) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return 60
    return max(30, seconds)


def _effective_erp_refresh_interval_seconds(settings: SupabaseSettings) -> int:
    if not settings.list_erp_documents:
        return 0
    return _normalized_erp_refresh_interval_seconds(
        settings.erp_refresh_interval_seconds
    )


def _normalized_local_erp_port(value: object) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return 9091
    if 1024 <= port <= 65535:
        return port
    return 9091


def _url_with_query_parameter(url: str, name: str, value: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append((name, value))
    return urllib.parse.urlunsplit(
        parsed._replace(query=urllib.parse.urlencode(query))
    )


def _erp_document_rows(payload: object) -> list[object] | None:
    if not isinstance(payload, dict):
        return None
    if "data" not in payload:
        return None
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    return rows


def _parse_erp_documents(payload: object, selected_user_id: str) -> list[ErpDocument]:
    rows = _erp_document_rows(payload)
    if rows is None:
        return []
    documents: list[ErpDocument] = []
    normalized_user_id = selected_user_id.strip()
    for row in rows:
        document = _erp_document_from_row(row, normalized_user_id)
        if document is not None:
            documents.append(document)
    return documents


def _erp_document_storage_info_from_rows(
    rows: list[object],
    document_id: str,
) -> ErpDocumentStorageInfo | None:
    first_row: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if first_row is None:
            first_row = row
        row_document_id = _case_insensitive_row_text(row, "vfcodiceid")
        if row_document_id == document_id:
            return _erp_document_storage_info_from_row(row, document_id)
    if first_row is not None and len(rows) == 1:
        return _erp_document_storage_info_from_row(first_row, document_id)
    return None


def _erp_document_storage_info_from_row(
    row: dict[str, Any],
    document_id: str,
) -> ErpDocumentStorageInfo:
    return ErpDocumentStorageInfo(
        document_id=document_id,
        name=_case_insensitive_row_text(row, "vfname"),
        logical_path=_case_insensitive_row_text(row, "vfpath"),
    )


def _case_insensitive_row_text(row: dict[str, Any], name: str) -> str:
    normalized_name = name.casefold()
    for key, value in row.items():
        if str(key).casefold() == normalized_name:
            return str(value or "").strip()
    return ""


def _erp_document_from_row(
    row: object,
    selected_user_id: str,
) -> ErpDocument | None:
    if not isinstance(row, dict):
        return None
    checkout_by = str(row.get("vfcheckoutby") or "").strip()
    if checkout_by != selected_user_id:
        return None
    name = str(row.get("vfname") or "").strip()
    if not name:
        return None
    return ErpDocument(
        name=name,
        checkout_date=str(row.get("vfcheckoutdate") or "").strip(),
        description=str(row.get("vfdescri") or "").strip(),
        document_id=str(row.get("vfcodiceid") or "").strip(),
        auth_code=str(row.get("vfauthcode") or "").strip(),
        checkout_by=checkout_by,
    )


def _parse_erp_users(payload: object) -> list[ErpUser]:
    rows = _erp_user_rows(payload)
    users: list[ErpUser] = []
    seen: set[str] = set()
    for row in rows:
        user = _erp_user_from_row(row)
        if user is None or user.user_id in seen:
            continue
        seen.add(user.user_id)
        users.append(user)
    return users


def _erp_user_rows(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("users", "utenti", "data", "items", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _erp_user_from_row(row: object) -> ErpUser | None:
    if isinstance(row, str):
        value = row.strip()
        return ErpUser(value, value) if value else None
    if not isinstance(row, dict):
        return None
    user_id = _first_text(
        row,
        (
            "id",
            "user_id",
            "utente_id",
            "codice",
            "code",
            "username",
            "email",
            "name",
        ),
    )
    display_name = _first_text(
        row,
        (
            "display_name",
            "full_name",
            "name",
            "nome",
            "descrizione",
            "description",
            "username",
            "email",
            "id",
        ),
    )
    if not user_id and display_name:
        user_id = display_name
    if not display_name and user_id:
        display_name = user_id
    if not user_id or not display_name:
        return None
    return ErpUser(user_id, display_name)


def _first_text(payload: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
