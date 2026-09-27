#define VersionHandle FileOpen(SourcePath + "\..\VERSION.txt")
#define MyAppVersion Trim(FileRead(VersionHandle))
#expr FileClose(VersionHandle)
#define MyAppName "Aurelia"
#define MyAppPublisher "FEWURA"
#define MyAppExeName "Aurelia.exe"

[Setup]
AppId={{8CE2A2D7-E18A-4B10-A913-2AC7CE2188C1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Aurelia
DefaultGroupName=Aurelia
OutputDir=..\release
OutputBaseFilename=Aurelia-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
UninstallDisplayName=Aurelia
UninstallDisplayIcon={app}\{#MyAppExeName}
CreateUninstallRegKey=yes
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousGroup=yes
MinVersion=10.0.17763
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Aurelia - facturation et pre-comptabilite locale
VersionInfoProductName={#MyAppName}

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Files]
; The installer consumes the already validated Phase 6D onedir artifact.
Source: "..\release\Aurelia-{#MyAppVersion}\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"; Flags: unchecked

[Icons]
Name: "{autoprograms}\Aurelia"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Aurelia"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer Aurelia"; Flags: nowait postinstall skipifsilent

; Aucune donnée utilisateur n'est installée dans {app}. La désinstallation retire
; uniquement le programme et les raccourcis. LocalAppData et Documents sont conservés.
