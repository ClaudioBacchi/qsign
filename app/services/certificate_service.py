"""Simple Windows certificate store integration for QSign preferences."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Any


class CertificateServiceError(RuntimeError):
    """Raised when Windows certificate operations fail."""


@dataclass(frozen=True, slots=True)
class CertificateInfo:
    name: str
    type: str
    valid_until: str
    thumbprint: str


class CertificateService:
    """Read and update the active certificate from the Windows user store."""

    def __init__(
        self,
        preferences_path: str | Path = "config/preferences.json",
        command_runner: Callable[[str], str] | None = None,
    ) -> None:
        self._preferences_path = Path(preferences_path)
        self._command_runner = command_runner or self._run_powershell

    def list_certificates(self) -> tuple[CertificateInfo, ...]:
        payload = self._run_json(
            r"""
$store = [System.Security.Cryptography.X509Certificates.X509Store]::new("My", "CurrentUser")
$store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
try {
    $store.Certificates |
        Sort-Object NotAfter -Descending |
        ForEach-Object {
            [pscustomobject]@{
                name = if ($_.FriendlyName) { $_.FriendlyName } elseif ($_.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)) { $_.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } else { $_.Subject }
                type = if ($_.HasPrivateKey) { "Store Windows - chiave privata" } else { "Store Windows" }
                valid_until = $_.NotAfter.ToString("yyyy-MM-dd")
                thumbprint = $_.Thumbprint
                subject = $_.Subject
            }
        } | ConvertTo-Json -Depth 3
}
finally {
    $store.Close()
}
"""
        )
        if payload is None:
            return ()
        items = payload if isinstance(payload, list) else [payload]
        return tuple(self._certificate_from_payload(item) for item in items)

    def get_active_certificate(self) -> CertificateInfo | None:
        thumbprint = self._read_active_thumbprint()
        if not thumbprint:
            return None
        normalized = self._normalize_thumbprint(thumbprint)
        for certificate in self.list_certificates():
            if self._normalize_thumbprint(certificate.thumbprint) == normalized:
                return certificate
        return None

    def set_active_certificate(self, thumbprint: str) -> None:
        normalized = self._normalize_thumbprint(thumbprint)
        if not normalized:
            raise CertificateServiceError("Thumbprint certificato non valido")
        certificates = self.list_certificates()
        if not any(
            self._normalize_thumbprint(certificate.thumbprint) == normalized
            for certificate in certificates
        ):
            raise CertificateServiceError("Certificato non trovato nello Store Windows")
        self._write_active_thumbprint(normalized)

    def delete_certificate(self, thumbprint: str) -> None:
        normalized = self._normalize_thumbprint(thumbprint)
        if not normalized:
            raise CertificateServiceError("Thumbprint certificato non valido")
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$thumbprint = {self._ps_string(normalized)}\n"
            r"""
$store = [System.Security.Cryptography.X509Certificates.X509Store]::new("My", "CurrentUser")
$store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
try {
    $selected = $store.Certificates |
        Where-Object { ($_.Thumbprint -replace '\s', '') -eq $thumbprint } |
        Select-Object -First 1
    if ($null -eq $selected) {
        throw "Certificato non trovato nello Store Windows"
    }
    $store.Remove($selected)
}
finally {
    $store.Close()
}
"""
        )
        self._command_runner(script)
        if self._normalize_thumbprint(self._read_active_thumbprint()) == normalized:
            self._clear_active_thumbprint()

    def generate_self_signed(
        self,
        first_name: str,
        last_name: str,
        organization: str,
        pfx_password: str,
        valid_until: str | None = None,
    ) -> CertificateInfo:
        display_name = " ".join(part for part in (first_name, last_name) if part).strip()
        if not display_name:
            raise CertificateServiceError("Nome e cognome sono obbligatori")
        if not pfx_password:
            raise CertificateServiceError("Password PFX obbligatoria")
        valid_until_date = self._valid_until_date(valid_until)
        subject_parts = [f"CN={self._escape_subject(display_name)}"]
        if organization.strip():
            subject_parts.append(f"O={self._escape_subject(organization.strip())}")
        subject = ", ".join(subject_parts)
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$subject = {self._ps_string(subject)}\n"
            f"$friendlyName = {self._ps_string(display_name)}\n"
            f"$password = {self._ps_string(pfx_password)}\n"
            f"$validUntil = {self._ps_string(valid_until_date.isoformat())}\n"
            r"""
$rsa = [System.Security.Cryptography.RSA]::Create(2048)
$request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
    $subject,
    $rsa,
    [System.Security.Cryptography.HashAlgorithmName]::SHA256,
    [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
)
$request.CertificateExtensions.Add(
    [System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
        [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature,
        $true
    )
)
$notAfter = [datetimeoffset]::ParseExact($validUntil, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
$certificate = $request.CreateSelfSigned([datetimeoffset]::Now.AddDays(-1), $notAfter)
$certificate.FriendlyName = $friendlyName
$pfxBytes = $certificate.Export(
    [System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx,
    $password
)
$imported = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
    $pfxBytes,
    $password,
    [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet -bor
    [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::PersistKeySet -bor
    [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable
)
$imported.FriendlyName = $friendlyName
$store = [System.Security.Cryptography.X509Certificates.X509Store]::new("My", "CurrentUser")
$store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
try {
    $store.Add($imported)
}
finally {
    $store.Close()
}
[pscustomobject]@{
    name = if ($imported.FriendlyName) { $imported.FriendlyName } else { $friendlyName }
    type = "Store Windows - chiave privata"
    valid_until = $imported.NotAfter.ToString("yyyy-MM-dd")
    thumbprint = $imported.Thumbprint
    subject = $imported.Subject
} | ConvertTo-Json -Depth 3
"""
        )
        certificate = self._certificate_from_payload(self._run_json(script))
        self._write_active_thumbprint(certificate.thumbprint)
        return certificate

    def import_pfx(self, pfx_path: str | Path, password: str) -> CertificateInfo:
        source = Path(pfx_path)
        if not source.is_file():
            raise CertificateServiceError("File PFX non trovato")
        if source.suffix.lower() != ".pfx":
            raise CertificateServiceError("Selezionare un file .pfx")
        if not password:
            raise CertificateServiceError("Password PFX obbligatoria")
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$pfxPath = {self._ps_string(str(source))}\n"
            f"$password = {self._ps_string(password)}\n"
            r"""
$collection = [System.Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
$collection.Import(
    $pfxPath,
    $password,
    [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet -bor
    [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::PersistKeySet -bor
    [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable
)
$store = [System.Security.Cryptography.X509Certificates.X509Store]::new("My", "CurrentUser")
$store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
try {
    foreach ($certificate in $collection) {
        $store.Add($certificate)
    }
}
finally {
    $store.Close()
}
$selected = $collection | Where-Object { $_.HasPrivateKey } | Select-Object -First 1
if ($null -eq $selected) {
    $selected = $collection | Select-Object -First 1
}
if ($null -eq $selected) {
    throw "Il file PFX non contiene certificati"
}
[pscustomobject]@{
    name = if ($selected.FriendlyName) { $selected.FriendlyName } elseif ($selected.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)) { $selected.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } else { $selected.Subject }
    type = if ($selected.HasPrivateKey) { "Store Windows - chiave privata" } else { "Store Windows" }
    valid_until = $selected.NotAfter.ToString("yyyy-MM-dd")
    thumbprint = $selected.Thumbprint
    subject = $selected.Subject
} | ConvertTo-Json -Depth 3
"""
        )
        certificate = self._certificate_from_payload(self._run_json(script))
        self._write_active_thumbprint(certificate.thumbprint)
        return certificate

    def _read_active_thumbprint(self) -> str:
        if not self._preferences_path.is_file():
            return ""
        try:
            payload = json.loads(self._preferences_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(payload.get("active_certificate_thumbprint", ""))

    def _write_active_thumbprint(self, thumbprint: str) -> None:
        payload: dict[str, Any] = {}
        if self._preferences_path.is_file():
            try:
                existing = json.loads(self._preferences_path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    payload = existing
            except (OSError, json.JSONDecodeError):
                payload = {}
        payload["active_certificate_thumbprint"] = self._normalize_thumbprint(thumbprint)
        self._preferences_path.parent.mkdir(parents=True, exist_ok=True)
        self._preferences_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _clear_active_thumbprint(self) -> None:
        if not self._preferences_path.is_file():
            return
        try:
            existing = json.loads(self._preferences_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        payload = existing if isinstance(existing, dict) else {}
        payload.pop("active_certificate_thumbprint", None)
        self._preferences_path.parent.mkdir(parents=True, exist_ok=True)
        self._preferences_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _run_json(self, script: str) -> Any:
        output = self._command_runner(script).strip()
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise CertificateServiceError(
                "Risposta non valida dallo Store certificati di Windows"
            ) from error

    @staticmethod
    def _run_powershell(script: str) -> str:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise CertificateServiceError(
                message or "Operazione certificato non riuscita"
            )
        return completed.stdout

    @staticmethod
    def _certificate_from_payload(payload: Any) -> CertificateInfo:
        if not isinstance(payload, dict):
            raise CertificateServiceError("Dati certificato non validi")
        return CertificateInfo(
            name=CertificateService._display_name_from_payload(payload),
            type=str(payload.get("type") or "Store Windows"),
            valid_until=str(payload.get("valid_until") or ""),
            thumbprint=CertificateService._normalize_thumbprint(
                str(payload.get("thumbprint") or "")
            ),
        )

    @staticmethod
    def _normalize_thumbprint(thumbprint: str) -> str:
        return "".join(character for character in thumbprint.upper() if character.isalnum())

    @staticmethod
    def _valid_until_date(value: str | None) -> date:
        if not value or not value.strip():
            return date.today() + timedelta(days=365 * 3)
        try:
            valid_until = datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError as error:
            raise CertificateServiceError(
                "Scadenza certificato non valida. Usare AAAA-MM-GG"
            ) from error
        if valid_until <= date.today():
            raise CertificateServiceError(
                "La scadenza del certificato deve essere futura"
            )
        return valid_until

    @staticmethod
    def _display_name_from_payload(payload: dict[str, Any]) -> str:
        name = str(payload.get("name") or "").strip()
        subject = str(payload.get("subject") or "").strip()
        for candidate in (name, subject):
            common_name = CertificateService._common_name_from_subject(candidate)
            if common_name:
                return common_name
        return name or subject or "Certificato senza nome"

    @staticmethod
    def _common_name_from_subject(subject: str) -> str:
        for part in subject.split(","):
            key, separator, value = part.strip().partition("=")
            if separator and key.strip().upper() == "CN" and value.strip():
                return value.strip().replace("\\,", ",")
        return ""

    @staticmethod
    def _ps_string(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _escape_subject(value: str) -> str:
        return value.replace("\\", "\\\\").replace(",", "\\,")
