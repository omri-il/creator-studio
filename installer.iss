; Inno Setup script for Creator Studio
; Run via: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss

#define AppName      "Creator Studio"
#define AppVersion   "2.0.0"
#define AppPublisher "Omri Iram"
#define AppExeName   "CreatorStudio.exe"
#define AppURL       "https://github.com/omri-il/studio-flow"

[Setup]
; New app identity (windowed rebuild of Studio Flow).
AppId={{7C1E9A44-2B3D-4E5F-9A10-3F6B8C2D1E00}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=dist\installer
OutputBaseFilename=CreatorStudio-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "הפעל אוטומטית עם הפעלת Windows (מומלץ — נעילת מיק' וזיהוי מצלמה ברקע)"; GroupDescription: "Startup:"

[Files]
Source: "dist\CreatorStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/f /im {#AppExeName}"; Flags: runhidden; RunOnceId: "KillApp"

[Code]
// Before installing: stop any running instance, and retire the OLD tray app
// (Studio Flow) so it doesn't keep auto-starting alongside the new window app.
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then begin
    Exec('taskkill', '/f /im {#AppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec('taskkill', '/f /im StudioFlow.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    DeleteFile(ExpandConstant('{userstartup}\Studio Flow.lnk'));
  end;
end;
