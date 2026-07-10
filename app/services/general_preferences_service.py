"""General application preferences with encrypted Supabase credentials."""

from __future__ import annotations

import base64
import ctypes
import json
import sys
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
    show_signature_text: bool = False
    signature_capture_mode: str = "mouse"


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
class ErpUserSettings:
    users_url: str = ""
    basic_username: str = ""
    basic_password: str = ""
    selected_user_id: str = ""
    selected_user_name: str = ""


@dataclass(frozen=True, slots=True)
class ErpUsersResult:
    success: bool
    message: str
    users: tuple[ErpUser, ...] = ()


class GeneralPreferencesService:
    """Read and write encrypted general preferences."""

    _PREFERENCES_KEY = "general"
    _SUPABASE_URL_KEY = "supabase_url"
    _SUPABASE_PASSWORD_KEY = "supabase_password"
    _SUPABASE_TABLE_KEY = "supabase_table"
    _SUPABASE_AUTO_SYNC_KEY = "supabase_auto_sync_templates_on_startup"
    _AUTO_SAVE_SIGNED_DOCUMENTS_KEY = "auto_save_signed_documents"
    _SHOW_SIGNATURE_TEXT_KEY = "show_signature_text"
    _SIGNATURE_CAPTURE_MODE_KEY = "signature_capture_mode"
    _ERP_USERS_URL_KEY = "erp_users_url"
    _ERP_BASIC_USERNAME_KEY = "erp_basic_username"
    _ERP_BASIC_PASSWORD_KEY = "erp_basic_password"
    _ERP_SELECTED_USER_ID_KEY = "erp_selected_user_id"
    _ERP_SELECTED_USER_NAME_KEY = "erp_selected_user_name"
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
            show_signature_text=bool(general.get(self._SHOW_SIGNATURE_TEXT_KEY)),
            signature_capture_mode=_normalized_signature_capture_mode(
                general.get(self._SIGNATURE_CAPTURE_MODE_KEY)
            ),
        )

    def save_supabase_settings(self, settings: SupabaseSettings) -> None:
        previous_settings = self.get_supabase_settings()
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
        general[self._SHOW_SIGNATURE_TEXT_KEY] = settings.show_signature_text
        general[self._SIGNATURE_CAPTURE_MODE_KEY] = (
            _normalized_signature_capture_mode(settings.signature_capture_mode)
        )
        payload[self._PREFERENCES_KEY] = general
        self._write_preferences(payload)
        self._log_general_settings_saved(previous_settings, settings)

    def get_erp_user_settings(self) -> ErpUserSettings:
        general = self._read_general_preferences()
        return ErpUserSettings(
            users_url=self._read_encrypted_value(general, self._ERP_USERS_URL_KEY),
            basic_username=self._read_encrypted_value(
                general, self._ERP_BASIC_USERNAME_KEY
            ),
            basic_password=self._read_encrypted_value(
                general, self._ERP_BASIC_PASSWORD_KEY
            ),
            selected_user_id=str(general.get(self._ERP_SELECTED_USER_ID_KEY) or ""),
            selected_user_name=str(general.get(self._ERP_SELECTED_USER_NAME_KEY) or ""),
        )

    def save_erp_user_settings(self, settings: ErpUserSettings) -> None:
        previous_settings = self.get_erp_user_settings()
        payload = self._read_preferences()
        general = payload.get(self._PREFERENCES_KEY)
        if not isinstance(general, dict):
            general = {}
        general[self._ERP_USERS_URL_KEY] = self._encrypted_payload(
            settings.users_url.strip()
        )
        general[self._ERP_BASIC_USERNAME_KEY] = self._encrypted_payload(
            settings.basic_username.strip()
        )
        general[self._ERP_BASIC_PASSWORD_KEY] = self._encrypted_payload(
            settings.basic_password.strip()
        )
        general[self._ERP_SELECTED_USER_ID_KEY] = settings.selected_user_id.strip()
        general[self._ERP_SELECTED_USER_NAME_KEY] = settings.selected_user_name.strip()
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
        if not users_url.startswith(("https://", "http://")):
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
            show_signature_text=settings.show_signature_text,
            signature_capture_mode=_normalized_signature_capture_mode(
                settings.signature_capture_mode
            ),
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
        except GeneralPreferencesServiceError:
            return ""

    def _encrypted_payload(self, value: str) -> dict[str, str]:
        return {
            "protected_with": "windows-dpapi-current-user",
            "value": self._protect(value),
        }

    def _read_preferences(self) -> dict[str, Any]:
        if not self._preferences_path.is_file():
            return {}
        try:
            payload = json.loads(self._preferences_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_preferences(self, payload: dict[str, Any]) -> None:
        self._preferences_path.parent.mkdir(parents=True, exist_ok=True)
        self._preferences_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

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
    if previous_settings.show_signature_text != settings.show_signature_text:
        changed_fields.add("show_signature_text")
    if (
        _normalized_signature_capture_mode(previous_settings.signature_capture_mode)
        != _normalized_signature_capture_mode(settings.signature_capture_mode)
    ):
        changed_fields.add("signature_capture_mode")
    return changed_fields


def _normalized_signature_capture_mode(value: object) -> str:
    mode = str(value or "mouse").strip().casefold()
    if mode == "wacom":
        return "wacom"
    return "mouse"


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
