#define MyAppName "AURELIA"
#define MyAppVersion "5.0.1"
#define MyAppPublisher "FEWURA"
#define MyAppExeName "Aurelia.exe"

[Setup]
AppId={{8CE2A2D7-E18A-4B10-A913-2AC7CE2188C1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\FEWURA\AURELIA
DefaultGroupName=FEWURA\AURELIA
OutputDir=output
OutputBaseFilename=AURELIA_Setup_5.0.1
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
UninstallDisplayName=AURELIA
CreateUninstallRegKey=yes
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousGroup=yes
MinVersion=10.0.17763

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Files]
Source: "..\dist\Aurelia\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\data"
Name: "{app}\data\uploads"
Name: "{app}\data\archive"
Name: "{app}\data\exports"
Name: "{app}\data\generated"
Name: "{app}\data\gmail"
Name: "{app}\data\ocr"

[Icons]
Name: "{autoprograms}\FEWURA\AURELIA"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\AURELIA"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer AURELIA"; Flags: nowait postinstall skipifsilent
