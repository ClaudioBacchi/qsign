"""pyHanko-backed PDF digital signature writer."""

import asyncio
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from types import TracebackType
from typing import Callable

import pymupdf
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.stamp import NoOpStampStyle, TextStampStyle

from app.services.certificate_service import CertificateService, SignatureMetadata
from services.logging.logging_service import LoggingService
from services.pdf.pdf_signature import DigitalPDFSignatureWriter, SignatureArea


class PyHankoDigitalSignatureWriter(DigitalPDFSignatureWriter):
    """Apply an Adobe-compatible detached PKCS#7 signature to a PDF."""

    def __init__(
        self,
        certificate_service: CertificateService,
        logger: LoggingService,
        reason: str = "SorveglianzaSanitaria",
        reason_provider: Callable[[], str] | None = None,
        metadata_provider: Callable[[], SignatureMetadata] | None = None,
        visible_text_provider: Callable[[], bool] | None = None,
    ) -> None:
        self._certificate_service = certificate_service
        self._logger = logger
        self._reason = reason
        self._reason_provider = reason_provider
        self._metadata_provider = metadata_provider
        self._visible_text_provider = visible_text_provider

    def sign_pdf(
        self,
        source: Path,
        destination: Path,
        area: SignatureArea,
        field_name: str = "Signature1",
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        selected_field_name = _next_available_signature_field_name(
            source,
            preferred_name=field_name,
        )
        password = secrets.token_urlsafe(24)
        with tempfile.TemporaryDirectory(prefix="qsign-cert-") as directory:
            pfx_path = Path(directory) / "certificate.pfx"
            certificate = self._certificate_service.export_active_certificate_pfx(
                pfx_path,
                password,
            )
            signer = signers.SimpleSigner.load_pkcs12(
                pfx_path,
                passphrase=password.encode("utf-8"),
            )
            metadata_values = self._signature_metadata()
            metadata = signers.PdfSignatureMetadata(
                field_name=selected_field_name,
                reason=metadata_values.reason,
                location=metadata_values.location or None,
                contact_info=metadata_values.contact_info or None,
                name=certificate.name,
                subfilter=fields.SigSeedSubFilter.ADOBE_PKCS7_DETACHED,
            )
            field_spec = fields.SigFieldSpec(
                sig_field_name=selected_field_name,
                on_page=area.page_index,
                box=self._signature_box(source, area),
            )
            pdf_signer = signers.PdfSigner(
                metadata,
                signer=signer,
                stamp_style=self._stamp_style(self._show_visible_text()),
                new_field_spec=field_spec,
            )
            _run_pyhanko_sign_pdf(
                pdf_signer,
                source,
                destination,
                appearance_text_params={
                    "reason": metadata_values.reason,
                    "location": metadata_values.location or "Non disponibile",
                    "contact": metadata_values.contact_info or "Non disponibile",
                },
            )

        self._logger.info(
            "Digital PDF signature written",
            source=str(source),
            destination=str(destination),
            certificate=certificate.name,
            thumbprint=certificate.thumbprint,
            field_name=selected_field_name,
        )

    @staticmethod
    def _signature_box(source: Path, area: SignatureArea) -> tuple[int, int, int, int]:
        document = pymupdf.open(source)
        try:
            page = document.load_page(area.page_index)
            height = float(page.rect.height)
        finally:
            document.close()
        left = int(round(area.x))
        bottom = int(round(height - area.y - area.height))
        right = int(round(area.x + area.width))
        top = int(round(height - area.y))
        return left, bottom, right, top

    def _signature_metadata(self) -> SignatureMetadata:
        if self._metadata_provider is not None:
            return self._metadata_provider()
        reason = self._reason_provider() if self._reason_provider else self._reason
        return SignatureMetadata(reason=reason, location="", contact_info="")

    def _show_visible_text(self) -> bool:
        return bool(
            self._visible_text_provider()
            if self._visible_text_provider is not None
            else False
        )

    @staticmethod
    def _stamp_style(show_visible_text: bool) -> NoOpStampStyle | TextStampStyle:
        if not show_visible_text:
            return NoOpStampStyle()
        return TextStampStyle(
            stamp_text=(
                "Firmato Digitalmente\n"
                "Data: %(ts)s\n"
                "Motivo: %(reason)s\n"
                "Luogo: %(location)s\n"
                "Contatto: %(contact)s"
            ),
            timestamp_format="%d/%m/%Y %H:%M:%S %Z",
        )


def _run_pyhanko_sign_pdf(
    pdf_signer: signers.PdfSigner,
    source: Path,
    destination: Path,
    appearance_text_params: dict[str, str] | None = None,
) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _sign_pdf_with_pyhanko(
            pdf_signer,
            source,
            destination,
            appearance_text_params=appearance_text_params,
        )
        return

    error: list[BaseException] = []
    traceback: list[TracebackType | None] = []

    def sign_in_thread() -> None:
        try:
            _sign_pdf_with_pyhanko(
                pdf_signer,
                source,
                destination,
                appearance_text_params=appearance_text_params,
            )
        except BaseException as exc:
            error.append(exc)
            traceback.append(sys.exc_info()[2])

    thread = threading.Thread(target=sign_in_thread, name="qsign-pyhanko-signer")
    thread.start()
    thread.join()
    if error:
        raise error[0].with_traceback(traceback[0])


def _sign_pdf_with_pyhanko(
    pdf_signer: signers.PdfSigner,
    source: Path,
    destination: Path,
    appearance_text_params: dict[str, str] | None = None,
) -> None:
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        writer = IncrementalPdfFileWriter(input_file)
        pdf_signer.sign_pdf(
            writer,
            output=output_file,
            appearance_text_params=appearance_text_params,
        )


def _next_available_signature_field_name(
    source: Path,
    *,
    preferred_name: str = "Signature1",
) -> str:
    existing_names = _existing_signature_field_names(source)
    if preferred_name not in existing_names:
        return preferred_name
    prefix = "Signature"
    index = 1
    if preferred_name.startswith(prefix):
        suffix = preferred_name[len(prefix) :]
        if suffix.isdigit():
            index = int(suffix)
    while True:
        index += 1
        candidate = f"{prefix}{index}"
        if candidate not in existing_names:
            return candidate


def _existing_signature_field_names(source: Path) -> set[str]:
    try:
        with source.open("rb") as handle:
            reader = PdfFileReader(handle)
            return {
                str(name)
                for name, _, _ in fields.enumerate_sig_fields(reader)
                if str(name).strip()
            }
    except Exception:
        return set()
