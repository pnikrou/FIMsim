; ─────────────────────────────────────────────────────────────────────────────
; installer_windows.iss  —  Inno Setup script for FIMsim Windows installer
;
; Produces: FIMsim-setup.exe  (~200-400 MB, self-contained, no Python needed)
;
; Requirements:
;   1. Run build_windows.bat first → creates dist\FIMsim\ folder
;   2. Download & install Inno Setup 6: https://jrsoftware.org/isdl.php
;   3. Open this .iss file in Inno Setup Compiler and click Build > Compile
; ─────────────────────────────────────────────────────────────────────────────

#define AppName      "FIMsim"
#define AppFullName  "FIMsim - Flood Inundation Model Simulation Tool"
#define AppVersion   "1.0.0"
#define AppPublisher "University of Alabama - Civil Engineering"
#define AppURL       "https://github.com/your-username/fimsim"
#define AppExeName   "FIMsim.exe"
#define SourceDir    "dist\FIMsim"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppFullName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
; Output installer file
OutputDir=dist
OutputBaseFilename=FIMsim-setup-windows
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Require Windows 10 or later
MinVersion=10.0
; 64-bit only (GDAL/Qt are 64-bit)
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
; Show license (optional — comment out if no license file)
; LicenseFile=LICENSE.txt
; Show a nice icon
; SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppFullName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Files]
; Copy the entire PyInstaller output folder
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start menu shortcut
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Comment: "Flood Inundation Model Simulation Tool"
; Desktop shortcut (only if user selected the task)
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Launch app after install (optional)
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up any cache files the app creates
Type: filesandordirs; Name: "{app}\cache"

[Code]
// Optional: check .NET or other prerequisites here
// (not needed — all Python/Qt/GDAL is bundled)

procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel2.Caption :=
    'This will install ' + ExpandConstant('{#AppFullName}') + ' version ' +
    ExpandConstant('{#AppVersion}') + ' on your computer.' + #13#10 + #13#10 +
    'FIMsim is a desktop tool for automated flood model pre-processing.' + #13#10 +
    'It downloads DEM, LULC, and streamflow data and generates ' + #13#10 +
    'ready-to-run input files for LISFLOOD-FP, HEC-RAS, and TRITON.' + #13#10 + #13#10 +
    'No Python installation is required — everything is bundled.' + #13#10 + #13#10 +
    'Click Next to continue.';
end;
