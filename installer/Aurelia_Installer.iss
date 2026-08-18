#define MyAppName "Aurelia"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "JeanMarc31110"
#define MyAppExeName "Aurelia.exe"

[Setup]
AppId={{C9H4G5E3-1D67-6G0C-1H3F-4C7E0D5F6G8H}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={commonpf64}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=Aurelia_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
UninstallDisplayName={#MyAppName}
CreateUninstallRegKey=yes
SetupLogging=yes
MinVersion=10.0.17763
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[InstallDelete]
Type: filesandordirs; Name: "{app}\*"
Type: files; Name: "{userdesktop}\Aurelia.lnk"
Type: files; Name: "{commondesktop}\Aurelia.lnk"
Type: files; Name: "{userprograms}\Aurelia.lnk"
Type: files; Name: "{commonprograms}\Aurelia.lnk"

[Files]
Source: "..\dist\Aurelia\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Aurelia"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Aurelia"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Aurelia"; Flags: nowait postinstall skipifsilent

[Code]
procedure StopLegacyInstances();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /T /IM Aurelia.exe >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
begin
  StopLegacyInstances();
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    StopLegacyInstances();
end;
