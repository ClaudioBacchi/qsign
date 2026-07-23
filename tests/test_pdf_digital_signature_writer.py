"""Tests for digitally signed PDF output."""

import asyncio
from datetime import datetime, timedelta, UTC
from pathlib import Path
import tempfile
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
import pymupdf
from pyhanko.pdf_utils.reader import PdfFileReader

from app.services.certificate_service import CertificateInfo, SignatureMetadata
from services.logging.logging_service import LoggingService
from services.pdf.pdf_signature import DigitalPDFSignatureWriter, SignatureArea
from services.pdf.providers.pyhanko_digital_signature_writer import (
    PyHankoDigitalSignatureWriter,
)
from services.pdf.providers.pymupdf_signature_writer import PyMuPDFSignatureWriter
from services.signature.signature_service import CapturedSignature


class PDFDigitalSignatureWriterTests(unittest.TestCase):
    def test_visible_writer_delegates_to_digital_signature_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            destination = Path(directory) / "signed.pdf"
            _create_pdf(source)
            digital_writer = FakeDigitalSignatureWriter()
            writer = PyMuPDFSignatureWriter(
                logger=LoggingService.create("qsign.tests.signature_writer"),
                digital_signature_writer=digital_writer,
            )
            area = SignatureArea(page_index=0, x=40, y=50, width=120, height=50)

            writer.save_with_visible_signature(
                source=source,
                destination=destination,
                signature=CapturedSignature(
                    content=b"<svg><polyline points='1,1 20,20'/></svg>",
                    media_type="image/svg+xml",
                ),
                area=area,
            )

            self.assertEqual(len(digital_writer.calls), 1)
            call = digital_writer.calls[0]
            self.assertNotEqual(call[0], destination)
            self.assertNotEqual(call[1], destination)
            self.assertEqual(call[1].parent, destination.parent)
            self.assertFalse(call[1].exists())
            self.assertEqual(call[2], area)
            self.assertTrue(destination.read_bytes().startswith(b"%PDF"))

    def test_pyhanko_writer_creates_adobe_detached_signature_structure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            destination = Path(directory) / "signed.pdf"
            _create_pdf(source)
            writer = PyHankoDigitalSignatureWriter(
                certificate_service=FakeExportingCertificateService(),
                logger=LoggingService.create("qsign.tests.digital_signature_writer"),
                metadata_provider=lambda: SignatureMetadata(
                    reason="Privacy",
                    location="Forli",
                    contact_info="privacy@example.test",
                ),
                visible_text_provider=lambda: True,
            )

            writer.sign_pdf(
                source=source,
                destination=destination,
                area=SignatureArea(page_index=0, x=40, y=50, width=120, height=50),
            )

            content = destination.read_bytes()
            self.assertIn(b"/ByteRange", content)
            self.assertIn(b"/Contents", content)
            self.assertIn(b"/FT /Sig", content.replace(b"/FT/Sig", b"/FT /Sig"))
            self.assertIn(b"/adbe.pkcs7.detached", content)
            signed_document = pymupdf.open(destination)
            try:
                visible_text = signed_document.load_page(0).get_text()
            finally:
                signed_document.close()
            self.assertIn("Firmato Digitalmente", visible_text)
            self.assertNotIn("Firmato digitalmente da: QSign Test", visible_text)
            self.assertIn("Motivo: Privacy", visible_text)
            self.assertIn("Luogo: Forli", visible_text)
            self.assertIn("Contatto: privacy@example.test", visible_text)
            self.assertNotIn("Digitally signed by", visible_text)
            with destination.open("rb") as output:
                signature = list(PdfFileReader(output).embedded_signatures)[0]
                self.assertEqual(str(signature.sig_object["/Reason"]), "Privacy")
                self.assertEqual(str(signature.sig_object["/Location"]), "Forli")
                self.assertEqual(
                    str(signature.sig_object["/ContactInfo"]),
                    "privacy@example.test",
                )

    def test_pyhanko_writer_omits_visible_text_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            destination = Path(directory) / "signed.pdf"
            _create_pdf(source)
            writer = PyHankoDigitalSignatureWriter(
                certificate_service=FakeExportingCertificateService(),
                logger=LoggingService.create("qsign.tests.digital_signature_writer"),
                metadata_provider=lambda: SignatureMetadata(
                    reason="Privacy",
                    location="Forli",
                    contact_info="privacy@example.test",
                ),
                visible_text_provider=lambda: False,
            )

            writer.sign_pdf(
                source=source,
                destination=destination,
                area=SignatureArea(page_index=0, x=40, y=50, width=120, height=50),
            )

            signed_document = pymupdf.open(destination)
            try:
                visible_text = signed_document.load_page(0).get_text()
            finally:
                signed_document.close()
            self.assertNotIn("Firmato Digitalmente", visible_text)
            self.assertNotIn("Motivo: Privacy", visible_text)
            with destination.open("rb") as output:
                signature = list(PdfFileReader(output).embedded_signatures)[0]
                self.assertEqual(str(signature.sig_object["/Reason"]), "Privacy")

    def test_pyhanko_writer_can_sign_while_event_loop_is_running(self) -> None:
        async def sign_from_event_loop() -> bytes:
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source.pdf"
                destination = Path(directory) / "signed.pdf"
                _create_pdf(source)
                writer = PyHankoDigitalSignatureWriter(
                    certificate_service=FakeExportingCertificateService(),
                    logger=LoggingService.create(
                        "qsign.tests.digital_signature_writer"
                    ),
                )

                writer.sign_pdf(
                    source=source,
                    destination=destination,
                    area=SignatureArea(page_index=0, x=40, y=50, width=120, height=50),
                )

                return destination.read_bytes()

        content = asyncio.run(sign_from_event_loop())

        self.assertIn(b"/ByteRange", content)
        self.assertIn(b"/adbe.pkcs7.detached", content)

    def test_pyhanko_writer_uses_new_field_when_pdf_is_already_signed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            first_signed = Path(directory) / "first-signed.pdf"
            second_signed = Path(directory) / "second-signed.pdf"
            _create_pdf(source)
            writer = PyHankoDigitalSignatureWriter(
                certificate_service=FakeExportingCertificateService(),
                logger=LoggingService.create("qsign.tests.digital_signature_writer"),
            )
            area = SignatureArea(page_index=0, x=40, y=50, width=120, height=50)

            writer.sign_pdf(source=source, destination=first_signed, area=area)
            writer.sign_pdf(source=first_signed, destination=second_signed, area=area)

            with second_signed.open("rb") as output:
                reader = PdfFileReader(output)
                signatures = list(reader.embedded_signatures)
                field_names = {signature.field_name for signature in signatures}

            self.assertEqual(len(signatures), 2)
            self.assertEqual(field_names, {"Signature1", "Signature2"})


class FakeDigitalSignatureWriter(DigitalPDFSignatureWriter):
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, SignatureArea, str]] = []

    def sign_pdf(
        self,
        source: Path,
        destination: Path,
        area: SignatureArea,
        field_name: str = "Signature1",
    ) -> None:
        self.calls.append((source, destination, area, field_name))
        destination.write_bytes(source.read_bytes())


class FakeExportingCertificateService:
    def export_active_certificate_pfx(
        self, destination: str | Path, password: str
    ) -> CertificateInfo:
        Path(destination).write_bytes(_pfx_bytes(password.encode("utf-8")))
        return CertificateInfo(
            name="QSign Test",
            type="Store Windows - chiave privata",
            valid_until="2030-01-01",
            thumbprint="AABB",
        )


def _create_pdf(path: Path) -> None:
    document = pymupdf.open()
    try:
        page = document.new_page(width=300, height=200)
        page.insert_text((40, 40), "QSign test")
        document.save(path)
    finally:
        document.close()


def _pfx_bytes(password: bytes) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "QSign Test"),
        ]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        name=b"QSign Test",
        key=key,
        cert=certificate,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )


if __name__ == "__main__":
    unittest.main()
