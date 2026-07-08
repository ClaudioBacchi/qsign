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


@dataclass(frozen=True, slots=True)
class SignatureMetadata:
    reason: str
    location: str
    contact_info: str


DEFAULT_SIGNATURE_REASON = "SorveglianzaSanitaria"
DEFAULT_SIGNATURE_LOCATION = ""
DEFAULT_SIGNATURE_CONTACT_INFO = ""


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

    def get_signature_reason(self) -> str:
        return self.get_signature_metadata().reason

    def set_signature_reason(self, reason: str) -> None:
        self.set_signature_metadata(reason=reason)

    def get_signature_metadata(self) -> SignatureMetadata:
        payload = self._read_preferences()
        thumbprint = self._normalize_thumbprint(self._read_active_thumbprint())
        metadata = payload.get("signature_metadata")
        if thumbprint and isinstance(metadata, dict):
            certificate_metadata = metadata.get(thumbprint)
            if isinstance(certificate_metadata, dict):
                return SignatureMetadata(
                    reason=str(
                        certificate_metadata.get("reason") or DEFAULT_SIGNATURE_REASON
                    ).strip()
                    or DEFAULT_SIGNATURE_REASON,
                    location=str(
                        certificate_metadata.get("location")
                        or DEFAULT_SIGNATURE_LOCATION
                    ).strip(),
                    contact_info=str(
                        certificate_metadata.get("contact_info")
                        or DEFAULT_SIGNATURE_CONTACT_INFO
                    ).strip(),
                )
        return SignatureMetadata(
            reason=self._legacy_signature_reason(payload),
            location=str(payload.get("signature_location") or "").strip(),
            contact_info=str(payload.get("signature_contact_info") or "").strip(),
        )

    def set_signature_metadata(
        self,
        reason: str,
        location: str = "",
        contact_info: str = "",
    ) -> None:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise CertificateServiceError("Motivo firma obbligatorio")
        thumbprint = self._normalize_thumbprint(self._read_active_thumbprint())
        if not thumbprint:
            raise CertificateServiceError("Selezionare un certificato attivo")
        payload = self._read_preferences()
        all_metadata = payload.get("signature_metadata")
        if not isinstance(all_metadata, dict):
            all_metadata = {}
        all_metadata[thumbprint] = {
            "reason": normalized_reason,
            "location": location.strip(),
            "contact_info": contact_info.strip(),
        }
        payload["signature_metadata"] = all_metadata
        self._write_preferences(payload)

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

    def export_active_certificate_pfx(
        self, destination: str | Path, password: str
    ) -> CertificateInfo:
        if not password:
            raise CertificateServiceError("Password esportazione certificato obbligatoria")
        certificate = self.get_active_certificate()
        if certificate is None:
            raise CertificateServiceError("Nessun certificato attivo selezionato")
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$thumbprint = {self._ps_string(certificate.thumbprint)}\n"
            f"$destination = {self._ps_string(str(destination_path))}\n"
            f"$password = {self._ps_string(password)}\n"
            r"""
$store = [System.Security.Cryptography.X509Certificates.X509Store]::new("My", "CurrentUser")
$store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
try {
    $selected = $store.Certificates |
        Where-Object { ($_.Thumbprint -replace '\s', '') -eq $thumbprint } |
        Select-Object -First 1
    if ($null -eq $selected) {
        throw "Certificato non trovato nello Store Windows"
    }
    if (-not $selected.HasPrivateKey) {
        throw "Il certificato selezionato non contiene una chiave privata"
    }
    $bytes = $selected.Export(
        [System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx,
        $password
    )
    [System.IO.File]::WriteAllBytes($destination, $bytes)
}
finally {
    $store.Close()
}
"""
        )
        self._command_runner(script)
        return certificate

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
        payload = self._read_preferences()
        return str(payload.get("active_certificate_thumbprint", ""))

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

    def _write_active_thumbprint(self, thumbprint: str) -> None:
        payload = self._read_preferences()
        payload["active_certificate_thumbprint"] = self._normalize_thumbprint(thumbprint)
        self._write_preferences(payload)

    def _clear_active_thumbprint(self) -> None:
        if not self._preferences_path.is_file():
            return
        thumbprint = self._normalize_thumbprint(self._read_active_thumbprint())
        payload = self._read_preferences()
        payload.pop("active_certificate_thumbprint", None)
        reasons = payload.get("signature_reasons")
        if thumbprint and isinstance(reasons, dict):
            reasons.pop(thumbprint, None)
            if reasons:
                payload["signature_reasons"] = reasons
            else:
                payload.pop("signature_reasons", None)
        metadata = payload.get("signature_metadata")
        if thumbprint and isinstance(metadata, dict):
            metadata.pop(thumbprint, None)
            if metadata:
                payload["signature_metadata"] = metadata
            else:
                payload.pop("signature_metadata", None)
        self._write_preferences(payload)

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
    def _legacy_signature_reason(payload: dict[str, Any]) -> str:
        thumbprint = CertificateService._normalize_thumbprint(
            str(payload.get("active_certificate_thumbprint") or "")
        )
        reasons = payload.get("signature_reasons")
        if thumbprint and isinstance(reasons, dict):
            reason = str(reasons.get(thumbprint) or "").strip()
            if reason:
                return reason
        reason = str(payload.get("signature_reason") or "").strip()
        return reason or DEFAULT_SIGNATURE_REASON

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
