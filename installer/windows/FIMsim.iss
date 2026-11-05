; ─────────────────────────────────────────────────────────────────────────────
; FIMsim - Inno Setup script for the Windows installer.
;
; Build (CI does this; locally you need Inno Setup 6):
;   python installer\stage_payload.py dist\payload
;   copy micromamba.exe  dist\payload\bin\micromamba.exe
;   copy installer\windows\*.ps1 + installer\smoke_test.py  dist\payload\
;   ISCC /DAppVersion=1.2.0 installer\windows\FIMsim.iss
;
; The installer only copies FIMsim's own files plus the micromamba package
; installer (about 100 MB).  The Python environment is built on the user's PC at
; first start - see installer\windows\setup.ps1 for why.
; ─────────────────────────────────────────────────────────────────────────────

#define AppName      "FIMsim"
#define AppFullName  "FIMsim - Flood Inundation Model Simulation Tool"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppPublisher "University of Alabama - Civil Engineering"
#define AppURL       "https://github.com/sdmlua/FIMsim"
#ifndef Payload
  #define Payload    "..\..\dist\payload"
#endif
#define Powershell   "{sys}\WindowsPowerShell\v1.0\powershell.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppFullName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
; Per-user install: no administrator password needed.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=FIMsim-setup-windows
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\app\assets\icon.ico
UninstallDisplayName={#AppFullName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{#Powershell}"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\launch.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icon.ico"; Comment: "Flood Inundation Model Simulation Tool"
Name: "{autoprograms}\Repair {#AppName}"; Filename: "{#Powershell}"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icon.ico"; \
  Comment: "Rebuild FIMsim's Python environment (use if FIMsim reports missing packages)"
Name: "{autodesktop}\{#AppName}"; Filename: "{#Powershell}"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\launch.ps1"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{#Powershell}"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\launch.ps1"""; \
  WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent; \
  Description: "Set up and start FIMsim now (the first start downloads about 1.5 GB and takes about 5-20 minutes)"

[UninstallDelete]
; The Python environment, its logs and caches (never the person's projects).
Type: filesandordirs; Name: "{localappdata}\FIMsim"

[Code]
procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel2.Caption :=
    'This installs ' + ExpandConstant('{#AppFullName}') + ' ' +
    ExpandConstant('{#AppVersion}') + ' on your computer.' + #13#10 + #13#10 +
    'FIMsim prepares model inputs and runs flood models: LISFLOOD-FP, TRITON, ' +
    'ARC-Curve2Flood and OWP HAND-FIM.' + #13#10 + #13#10 +
    'No Python installation is needed. The first time FIMsim starts, it ' +
    'downloads and installs the scientific packages it needs (about 1.5 GB, ' +
    '5-20 minutes, internet required). After that it opens immediately.' + #13#10 + #13#10 +
    'Click Next to continue.';
end;
