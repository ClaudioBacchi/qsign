# QSign

QSign is the foundation of a reusable document-signing platform. Milestone 1
defines replaceable service boundaries and a minimal desktop shell; it does not
yet implement PDF rendering, digital signatures, device SDKs, certificates, or
document transport.

## Requirements

- Python 3.14
- Flet Desktop

No PDF, signature, transport, or device library has been selected.

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

