[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._-]*$")]
    [string]$Release = "0.2.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VirtualEnvironment = Join-Path $ProjectRoot ".venv"
$PythonExecutable = Join-Path $VirtualEnvironment "Scripts\python.exe"
$BuildDirectory = Join-Path $ProjectRoot "build"
$DistributionDirectory = Join-Path $ProjectRoot "dist"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$ReleaseDirectory = Join-Path $ReleaseRoot $Release

Set-Location -LiteralPath $ProjectRoot

Write-Host "QSign release preparation: $Release"

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Virtual environment not found. Create .venv before preparing a release."
}

$PythonVersion = & $PythonExecutable -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to execute Python from the virtual environment."
}

$PythonMinorVersion = & $PythonExecutable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $PythonMinorVersion -ne "3.14") {
    throw "QSign release preparation requires Python 3.14. Found: $PythonVersion"
}

Write-Host "Python environment verified: $PythonVersion"

foreach ($Directory in @($BuildDirectory, $DistributionDirectory)) {
    if (Test-Path -LiteralPath $Directory) {
        Remove-Item -LiteralPath $Directory -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Directory | Out-Null
}

New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
New-Item -ItemType Directory -Path $ReleaseDirectory -Force | Out-Null

# FUTURE: invoke PyInstaller after its configuration and dependency are approved.
Write-Host "[placeholder] PyInstaller packaging"

# FUTURE: sign the generated executable with the Queen code-signing certificate.
Write-Host "[placeholder] Executable signing"

# FUTURE: copy the approved end-user and release documentation.
Write-Host "[placeholder] Documentation copy"

Write-Host "Release structure prepared in: $ReleaseDirectory"
