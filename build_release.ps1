[CmdletBinding()]
param(
    [Parameter()]
    [ValidatePattern("^[0-9A-Za-z][0-9A-Za-z._-]*$")]
    [string]$Release = "",

    [Parameter()]
    [switch]$MajorRelease,

    [Parameter()]
    [string]$InnoCompiler = "",

    [Parameter()]
    [switch]$SkipTests,

    [Parameter()]
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VirtualEnvironment = Join-Path $ProjectRoot ".venv"
$PythonExecutable = Join-Path $VirtualEnvironment "Scripts\python.exe"
$BuildDirectory = Join-Path $ProjectRoot "build"
$DistributionDirectory = Join-Path $ProjectRoot "dist"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$InnoScript = Join-Path $ProjectRoot "installer\qsign.iss"
$AppConfigPath = Join-Path $ProjectRoot "config\app.yaml"

function Get-QSignAppVersion {
    if (-not (Test-Path -LiteralPath $AppConfigPath -PathType Leaf)) {
        throw "Application version file not found: $AppConfigPath"
    }
    foreach ($Line in Get-Content -LiteralPath $AppConfigPath) {
        if ($Line -match '^\s*version\s*:\s*["'']?([^"'']+)["'']?\s*$') {
            return ConvertTo-QSignReleaseVersion -Version $Matches[1].Trim()
        }
    }
    throw "Unable to read application version from config\app.yaml."
}

function ConvertTo-QSignReleaseVersion {
    param([Parameter(Mandatory = $true)][string]$Version)

    if ($Version -match '^(\d{1,2})\.(\d{1,3})$') {
        return ('{0:00}.{1:000}' -f [int]$Matches[1], [int]$Matches[2])
    }
    if ($Version -match '^(\d{1,2})\.(\d{1,3})\.(\d{1,3})$') {
        return ('{0:00}.{1:000}' -f [int]$Matches[1], [int]$Matches[3])
    }
    throw "Unsupported release version '$Version'. Expected format 00.000."
}

function Get-Next-QSignReleaseVersion {
    param(
        [Parameter(Mandatory = $true)][string]$Version,
        [bool]$Major = $false
    )

    $Normalized = ConvertTo-QSignReleaseVersion -Version $Version
    if ($Normalized -notmatch '^(\d{2})\.(\d{3})$') {
        throw "Unsupported release version '$Normalized'. Expected format 00.000."
    }
    $MajorNumber = [int]$Matches[1]
    $BuildNumber = [int]$Matches[2]
    if ($Major) {
        return ('{0:00}.000' -f ($MajorNumber + 1))
    }
    return ('{0:00}.{1:000}' -f $MajorNumber, ($BuildNumber + 1))
}

function Set-QSignAppVersion {
    param([Parameter(Mandatory = $true)][string]$Version)

    $Content = Get-Content -LiteralPath $AppConfigPath
    $Updated = $false
    $NextContent = foreach ($Line in $Content) {
        if ($Line -match '^(\s*)version\s*:\s*["'']?([^"'']+)["'']?\s*$') {
            $Updated = $true
            "$($Matches[1])version: `"$Version`""
        }
        else {
            $Line
        }
    }
    if (-not $Updated) {
        throw "Unable to update application version in config\app.yaml."
    }
    [System.IO.File]::WriteAllLines(
        $AppConfigPath,
        [string[]]$NextContent,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Get-InnoCompilerPath {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        if (Test-Path -LiteralPath $RequestedPath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $RequestedPath).Path
        }
        throw "Inno Setup compiler not found: $RequestedPath"
    }

    $Command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    $Candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 5\ISCC.exe"
    )
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            return $Candidate
        }
    }
    throw "Inno Setup compiler ISCC.exe not found. Install Inno Setup 6 or pass -InnoCompiler."
}

function New-FletRuntimeArchive {
    param([string]$DestinationPath)

    $RuntimeDirectory = Split-Path -Parent $DestinationPath
    New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null

    $RuntimeInfo = & $PythonExecutable -c @"
import flet_desktop
print(flet_desktop.get_artifact_filename())
print(flet_desktop.ensure_client_cached())
"@
    if ($LASTEXITCODE -ne 0 -or $RuntimeInfo.Count -lt 2) {
        throw "Unable to prepare Flet desktop runtime."
    }
    $ArtifactName = [string]$RuntimeInfo[0]
    $CacheDirectory = [string]$RuntimeInfo[1]
    if ($ArtifactName -ne "flet-windows.zip") {
        throw "Unexpected Flet runtime artifact for Windows release: $ArtifactName"
    }
    if (-not (Test-Path -LiteralPath $CacheDirectory -PathType Container)) {
        throw "Flet desktop runtime cache not found: $CacheDirectory"
    }

    if (Test-Path -LiteralPath $DestinationPath) {
        Remove-Item -LiteralPath $DestinationPath -Force
    }
    Compress-Archive -Path (Join-Path $CacheDirectory "*") -DestinationPath $DestinationPath -CompressionLevel Optimal -ErrorAction Stop
    Test-ZipArchive -Path $DestinationPath
}

function Compress-WithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$DestinationPath,

        [int]$Attempts = 5
    )

    for ($Index = 1; $Index -le $Attempts; $Index++) {
        try {
            if (Test-Path -LiteralPath $DestinationPath) {
                Remove-Item -LiteralPath $DestinationPath -Force
            }
            Compress-Archive -Path $Path -DestinationPath $DestinationPath -CompressionLevel Optimal -ErrorAction Stop
            Test-ZipArchive -Path $DestinationPath
            return
        }
        catch {
            if (Test-Path -LiteralPath $DestinationPath) {
                Remove-Item -LiteralPath $DestinationPath -Force -ErrorAction SilentlyContinue
            }
            if ($Index -eq $Attempts) {
                throw
            }
            Start-Sleep -Seconds 2
        }
    }
}

function Test-ZipArchive {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        $Archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
        try {
            if ($Archive.Entries.Count -eq 0) {
                throw "Zip archive is empty: $Path"
            }
            foreach ($Entry in $Archive.Entries) {
                if ($Entry.FullName.EndsWith("/")) {
                    continue
                }
                $Stream = $Entry.Open()
                try {
                    $Buffer = New-Object byte[] 1
                    [void]$Stream.Read($Buffer, 0, 1)
                }
                finally {
                    $Stream.Dispose()
                }
            }
        }
        finally {
            $Archive.Dispose()
        }
    }
    catch {
        throw "Zip archive validation failed: $Path. $($_.Exception.Message)"
    }
}

if ($Release) {
    $Release = ConvertTo-QSignReleaseVersion -Version $Release
    Set-QSignAppVersion -Version $Release
}
else {
    $CurrentRelease = Get-QSignAppVersion
    $Release = Get-Next-QSignReleaseVersion -Version $CurrentRelease -Major $MajorRelease.IsPresent
    Set-QSignAppVersion -Version $Release
}

$ReleaseDirectory = Join-Path $ReleaseRoot "QSign-$Release"
$PortableDirectory = Join-Path $ReleaseDirectory "portable"
$PortableAppDirectory = Join-Path $PortableDirectory "QSign"
$InstallerDirectory = Join-Path $ReleaseDirectory "installer"
$PortableZip = Join-Path $ReleaseDirectory "QSign-portable-$Release.zip"
$FletRuntimeArchive = Join-Path $BuildDirectory "flet_runtime\flet-windows.zip"

Set-Location -LiteralPath $ProjectRoot

Write-Host "QSign release: $Release"

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Virtual environment not found. Create .venv before preparing a release."
}

$PythonVersion = & $PythonExecutable -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to execute Python from the virtual environment."
}

$PythonMinorVersion = & $PythonExecutable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $PythonMinorVersion -ne "3.14") {
    throw "QSign release requires Python 3.14. Found: $PythonVersion"
}
Write-Host "Python environment verified: $PythonVersion"

if (-not $SkipTests) {
    Write-Host "Running test suite..."
    & $PythonExecutable -m unittest
    if ($LASTEXITCODE -ne 0) {
        throw "Test suite failed."
    }
}

foreach ($Directory in @($BuildDirectory, $DistributionDirectory, $ReleaseDirectory)) {
    if (Test-Path -LiteralPath $Directory) {
        Remove-Item -LiteralPath $Directory -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $BuildDirectory, $DistributionDirectory, $PortableDirectory, $InstallerDirectory -Force | Out-Null

Write-Host "Preparing Flet desktop runtime..."
New-FletRuntimeArchive -DestinationPath $FletRuntimeArchive

Write-Host "Building PyInstaller bundle..."
& $PythonExecutable -m PyInstaller --noconfirm --clean --distpath $DistributionDirectory --workpath (Join-Path $BuildDirectory "pyinstaller") QSign.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}
if (-not (Test-Path -LiteralPath (Join-Path $DistributionDirectory "QSign\QSign.exe") -PathType Leaf)) {
    throw "PyInstaller output not found: dist\QSign\QSign.exe"
}

Write-Host "Staging portable release..."
Copy-Item -LiteralPath (Join-Path $DistributionDirectory "QSign") -Destination $PortableDirectory -Recurse
if (Test-Path -LiteralPath $PortableZip) {
    Remove-Item -LiteralPath $PortableZip -Force
}
Compress-WithRetry -Path $PortableAppDirectory -DestinationPath $PortableZip

if (-not $SkipInstaller) {
    $Compiler = Get-InnoCompilerPath -RequestedPath $InnoCompiler
    Write-Host "Building Inno Setup installer..."
    & $Compiler "/DMyAppVersion=$Release" "/DPortableSource=$PortableAppDirectory" "/DOutputDir=$InstallerDirectory" $InnoScript
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup build failed."
    }
    $InstallerPath = Join-Path $InstallerDirectory "QSignSetup-$Release.exe"
    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw "Installer output not found: $InstallerPath"
    }
    Write-Host "Installer: $InstallerPath"
}

Write-Host "Portable zip: $PortableZip"
Write-Host "Release completed in: $ReleaseDirectory"
