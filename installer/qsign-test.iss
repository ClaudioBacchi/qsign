#define MyAppName "QSign"
#define MyAppPublisher "Queen Srl"
#define MyAppURL "https://queensrl.net"
#define MyAppExeName "QSign.exe"
#define MyAppVersion "01.001.001"
#define WacomInstallerMsi "redist\wacom\Wacom-STU-SDK-x86-2.18.0.msi"
#define WacomInstallerExe "redist\wacom\Wacom-STU-SDK-Setup.exe"
#define HasWacomMsi FileExists(WacomInstallerMsi)
#define HasWacomExe FileExists(WacomInstallerExe)
#define HasWacomInstaller HasWacomMsi || HasWacomExe

[Setup]
AppId={{93B41F63-4E97-43A3-9BC9-11D90B7D48B2}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\QSign
DefaultGroupName=QSign
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=QSignSetup-test
SetupIconFile=..\resources\icons\favicon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName=QSign

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Types]
Name: "full"; Description: "Installazione completa"
Name: "compact"; Description: "Solo QSign"
Name: "custom"; Description: "Installazione personalizzata"; Flags: iscustom

[Components]
Name: "main"; Description: "QSign"; Types: full compact custom; Flags: fixed
#if HasWacomInstaller
Name: "wacom"; Description: "Supporto Wacom STU-430 (SDK/runtime)"; Types: full custom
#endif

[Tasks]
Name: "desktopicon"; Description: "Crea un collegamento sul desktop"; GroupDescription: "Collegamenti:"; Flags: checkedonce

[Files]
Source: "..\release\portable-test\QSign\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: main
Source: "..\build\preferences-full-internal-test\config\preferences.json"; DestDir: "{app}\config"; Flags: ignoreversion; Components: main
Source: "..\build\preferences-full-internal-test\config\LEGGIMI-PREFERENZE-TEST.txt"; DestDir: "{app}\config"; Flags: ignoreversion; Components: main
#if HasWacomInstaller
  #if HasWacomMsi
Source: "{#WacomInstallerMsi}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Components: wacom; Check: not WacomRuntimeInstalled
  #else
Source: "{#WacomInstallerExe}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Components: wacom; Check: not WacomRuntimeInstalled
  #endif
#endif

[Icons]
Name: "{group}\QSign"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\QSign"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
#if HasWacomInstaller
  #if HasWacomMsi
Filename: "msiexec.exe"; Parameters: "/i ""{tmp}\Wacom-STU-SDK-x86-2.18.0.msi"""; Description: "Installa supporto Wacom STU-430"; StatusMsg: "Installazione Wacom STU SDK..."; Flags: waituntilterminated; Components: wacom; Check: not WacomRuntimeInstalled
  #else
Filename: "{tmp}\Wacom-STU-SDK-Setup.exe"; Description: "Installa supporto Wacom STU-430"; StatusMsg: "Installazione Wacom STU SDK..."; Flags: waituntilterminated; Components: wacom; Check: not WacomRuntimeInstalled
  #endif
#endif
Filename: "{app}\{#MyAppExeName}"; Description: "Avvia QSign"; Flags: nowait postinstall skipifsilent

[Code]
function WacomRuntimeInstalled: Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{commonpf32}\Wacom STU SDK\C\bin\x64\wgssSTU.dll')) or
    FileExists(ExpandConstant('{commonpf32}\Wacom STU SDK\COM\bin\x64\wgssSTU.dll'));
end;
