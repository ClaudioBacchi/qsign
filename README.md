# QSign

QSign è una piattaforma professionale ed estensibile per la gestione della
firma documentale. La versione 0.2.1 comprende la Foundation Architecture, il
motore di rendering PDF e l'infrastruttura iniziale di sviluppo e rilascio.

Firma digitale, dispositivi di firma, certificati, trasporto documenti e
workflow cliente restano fuori dall'ambito corrente.

## Requisiti

- Windows
- Python 3.14
- Flet Desktop 0.85.3
- PyMuPDF 1.28.0

## Installazione dell'ambiente di sviluppo

Dalla cartella principale del progetto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Se l'esecuzione degli script PowerShell è disabilitata, il virtual environment
può essere attivato da Prompt dei comandi:

```bat
.venv\Scripts\activate.bat
```

## Avvio

Avvio normale:

```bat
go.bat
```

Avvio con Python Development Mode, output non bufferizzato e console persistente:

```bat
go_debug.bat
```

Entrambi gli script verificano la presenza di `.venv` e attivano
esplicitamente l'ambiente virtuale.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Rilascio Windows

Lo script crea il bundle PyInstaller, il pacchetto portable e l'installer Inno
Setup versionato leggendo la versione da `config/app.yaml`:

```powershell
.\build_release.ps1
```

Output principale:

```text
release/QSign-<version>/portable/QSign/
release/QSign-<version>/QSign-portable-<version>.zip
release/QSign-<version>/installer/QSignSetup-<version>.exe
```

Opzioni utili:

```powershell
.\build_release.ps1 -Release "01.001.001"
.\build_release.ps1 -SkipTests
.\build_release.ps1 -SkipInstaller
.\build_release.ps1 -InnoCompiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

L'installer non include `config/preferences.json`: le preferenze e le credenziali
restano locali alla macchina dell'utente.

## Struttura del progetto

```text
app/          composition root e controller applicativi
config/       configurazione
docs/         architettura, decisioni e roadmap
models/       modelli di dominio
resources/    icone, immagini e documenti campione
services/     servizi, contratti e provider
tests/        test automatici
ui/           presentazione Flet
build/        artefatti intermedi, ignorati da Git
dist/         output di packaging, ignorato da Git
release/      release predisposte, ignorate da Git
logs/         log runtime, ignorati da Git
```

## Roadmap

1. v0.1 — Foundation Architecture
2. v0.2 — Document Rendering Engine
3. v0.2.1 — Development Infrastructure
4. Trasporto documenti
5. SDK Wacom
6. Firma PAdES e certificati
7. Workflow completo
8. Distribuzione e aggiornamento automatico

Per le decisioni tecniche consultare
[`docs/architecture.md`](docs/architecture.md) e
[`docs/decisions.md`](docs/decisions.md).
