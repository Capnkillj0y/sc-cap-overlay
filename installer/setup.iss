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
  ImportPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  // This was a TInputDirWizardPage (CreateInputDirPage) before. That page
  // type has its OWN built-in "must be a full path" validation that runs
  // independently of -- and before -- anything in this script, and it
  // rejects an empty field outright. That's incompatible with this field
  // being genuinely optional ("leave blank for a new installation," per the
  // text below): a silent install without /LEGACYDIR, or a real user
  // correctly following that instruction and leaving it blank, would hit
  // Inno's own validation error and abort before our NextButtonClick check
  // below (which already correctly allows blank) ever gets a say.
  // TInputQueryWizardPage has no such built-in requirement, so our own
  // validation is the only validation that runs. The trade-off is losing
  // the automatic "Browse..." folder-picker button that only the Dir page
  // type provides -- this field now needs the path typed or pasted in.
  ImportPage := CreateInputQueryPage(wpSelectDir,
    'Keep your existing settings', 'Optional: import from an older portable copy.',
    'Already used the app? Close the old copy, then enter the folder containing its config.json and missile_ai folder. Leave blank for a new installation. Existing saved settings will not be overwritten.');
  ImportPage.Add('Previous app folder (optional):', False);
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
