# QSign

QSign is a reusable document-signing platform. Version 0.2 implements PDF
viewing through a replaceable PyMuPDF renderer, including page navigation,
zoom, and an internal bounded cache.

Digital signatures, document editing, device SDKs, certificates, transport, and
client workflows remain intentionally outside this milestone.

## Requirements

- Python 3.14
- Flet Desktop
- PyMuPDF

No signature, transport, certificate, or device library has been selected.

## Run

```powershell
python -m app.main
```

## Test

```powershell
python -m unittest discover -s tests -v
```

Architectural decisions and extension rules are documented in
[`docs/architecture.md`](docs/architecture.md).
