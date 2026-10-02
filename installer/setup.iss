#ifndef AppVersion
  #error AppVersion must be supplied by release_build.py
#endif
#define AppName "SC Capacitor Overlay"
#define AppExe "SC_Capacitor_Overlay.exe"

[Setup]
AppId={{A737BD14-5A13-44C5-BBFA-6A37FA93C870}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SC Capacitor Overlay
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=SC-Capacitor-Setup
SetupIconFile=..\sc_capacitor.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; AppUserModelID: "SCCapacitor.GlassCockpit.Desktop"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; AppUserModelID: "SCCapacitor.GlassCockpit.Desktop"; Tasks: desktopicon
Name: "{autoprograms}\SC Capacitor Settings Folder"; Filename: "{localappdata}\{#AppName}"; IconFilename: "{app}\{#AppExe}"

[Dirs]
Name: "{localappdata}\{#AppName}"; Flags: uninsneveruninstall

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch SC Capacitor Overlay"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\{#AppExe}.previous"

[Code]
var
  ImportPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  ImportPage := CreateInputDirPage(wpSelectDir,
    'Keep your existing settings', 'Optional: import from an older portable copy.',
    'Already used the app? Close the old copy, then choose the folder containing its config.json and missile_ai folder. Leave blank for a new installation. Existing saved settings will not be overwritten.',
    False, '');
  ImportPage.Add('Previous app folder (optional):');
  ImportPage.Values[0] := ExpandConstant('{param:LEGACYDIR|}');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  SourceDir: String;
begin
  Result := True;
  if CurPageID = ImportPage.ID then
  begin
    SourceDir := Trim(ImportPage.Values[0]);
    if (SourceDir <> '') and not DirExists(SourceDir) then
    begin
      MsgBox('Choose an existing app folder, or leave this field blank.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  SourceDir, MarkerPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    SourceDir := Trim(ImportPage.Values[0]);
    if SourceDir <> '' then
    begin
      MarkerPath := ExpandConstant('{localappdata}\{#AppName}\migration-source.txt');
      if not SaveStringToFile(MarkerPath, Utf8Encode(SourceDir), False) then
        MsgBox('Setup could not schedule the settings import. Your old files are unchanged.', mbError, MB_OK);
    end;
  end;
end;
