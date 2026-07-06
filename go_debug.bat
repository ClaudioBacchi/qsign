@echo off
setlocal
title QSign DEBUG

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ============================================
    echo Ambiente virtuale non trovato.
    echo Creare prima il venv seguendo il README.
    echo ============================================
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo ERRORE: attivazione dell'ambiente virtuale non riuscita.
    pause
    exit /b 1
)

set "PYTHONUNBUFFERED=1"

python -X dev -m app.main
set "EXIT_CODE=%ERRORLEVEL%"

pause
endlocal & exit /b %EXIT_CODE%
