"""General application preferences with encrypted Supabase credentials."""

from __future__ import annotations

import base64
import ctypes
import json
import sys
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class GeneralPreferencesServiceError(RuntimeError):
    """Raised when general preferences cannot be saved or tested."""


@dataclass(frozen=True, slots=True)
class SupabaseSettings:
    project_url: str = ""
    password: str = ""
    table_name: str = "SaluteLavoro"
    auto_sync_templates_on_startup: bool = False


@dataclass(frozen=True, slots=True)
class SupabaseConnectionResult:
    success: bool
    message: str


class GeneralPreferencesService:
    """Read and write encrypted general preferences."""

    _PREFERENCES_KEY = "general"
    _SUPABASE_URL_KEY = "supabase_url"
    _SUPABASE_PASSWORD_KEY = "supabase_password"
    _SUPABASE_TABLE_KEY = "supabase_table"
    _SUPABASE_AUTO_SYNC_KEY = "supabase_auto_sync_templates_on_startup"

    def __init__(
        self,
        preferences_path: str | Path = "config/preferences.json",
        protect: Callable[[str], str] | None = None,
        unprotect: Callable[[str], str] | None = None,
        opener: Callable[[urllib.request.Request, float], object] | None = None,
    ) -> None:
        self._preferences_path = Path(preferences_path)
        self._protect = protect or self._protect_text
        self._unprotect = unprotect or self._unprotect_text
        self._opener = opener or urllib.request.urlopen

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
        )

    def save_supabase_settings(self, settings: SupabaseSettings) -> None:
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
        payload[self._PREFERENCES_KEY] = general
        self._write_preferences(payload)

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

    @staticmethod
    def _supabase_headers(api_key: str) -> dict[str, str]:
        headers = {"apikey": api_key}
        if not api_key.startswith(("sb_publishable_", "sb_secret_")):
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _read_general_preferences(self) -> dict[str, Any]:
        general = self._read_preferences().get(self._PREFERENCES_KEY)
        return general if isinstance(general, dict) else {}

    def _read_encrypted_value(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, dict):
            return ""
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
