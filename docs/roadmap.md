# QSign roadmap

1. Foundation Architecture — completed in v0.1
2. PDF Rendering — completed in v0.2
3. Development Infrastructure — completed in v0.2.1
4. Document Intelligence Engine — design completed; implementation not started
5. Document Transport (HTTP API / FTP)
6. Wacom STU-430 SDK
7. PAdES Signing with Windows Certificates
8. Complete Workflow
9. Distribution and Automatic Updates
   - Evaluate migration from PyInstaller/manual Flet runtime patching to
     `flet build windows` before adding further `flet.exe` shell/metadata
     workarounds.

Each milestone must select technology only for its own scope and preserve the
provider-neutral contracts established by the platform.
