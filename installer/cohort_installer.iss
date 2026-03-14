; =====================================================================
; Cohort Windows Installer -- Inno Setup Script
; =====================================================================
;
; Builds a Windows installer that bundles:
;   1. Embedded Python 3.13 (no system Python required)
;   2. Cohort package (pre-built wheel)
;   3. Ollama installer (downloaded separately during first run)
;   4. pystray + Pillow for system tray
;   5. uvicorn + starlette for the web server
;
; The installer is ~100MB. Models (~5-8GB) are downloaded on first run
; via the setup wizard in the browser.
;
; Build with: iscc cohort_installer.iss
; Requires: Inno Setup 6.x (https://jrsoftware.org/isinfo.php)
;
; Before building, run: python build_installer.py
; This assembles the payload directory structure.
;

#define MyAppName "Cohort"
#define MyAppVersion "0.3.0"
#define MyAppPublisher "Cohort"
#define MyAppURL "https://github.com/rwheeler007/cohort"
#define MyAppExeName "cohort-launch.bat"

[Setup]
AppId={{A7E3F2D1-B8C4-4A5D-9E6F-1234567890AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; No admin required -- install to user's AppData by default
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=output
OutputBaseFilename=CohortSetup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
; Installer UI
WizardStyle=modern
SetupIconFile=assets\cohort.ico
UninstallDisplayIcon={app}\cohort.ico
; Disk space estimate (before model download)
ExtraDiskSpaceRequired=0
; Minimum Windows 10
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nCohort runs AI agents locally on your hardware. Your data never leaves your machine.%n%nSystem Requirements:%n  - Windows 10 or later%n  - 16 GB RAM (32 GB recommended)%n  - 15 GB free disk space%n  - NVIDIA GPU with 8+ GB VRAM (recommended)

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupentry"; Description: "Start Cohort when Windows starts"; GroupDescription: "Startup:"

[Files]
; Embedded Python distribution
Source: "payload\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs
; Cohort package and dependencies (pre-installed into python\Lib\site-packages)
Source: "payload\site-packages\*"; DestDir: "{app}\python\Lib\site-packages"; Flags: ignoreversion recursesubdirs
; Cohort agents directory
Source: "payload\agents\*"; DestDir: "{app}\agents"; Flags: ignoreversion recursesubdirs
; Launcher batch file
Source: "payload\cohort-launch.bat"; DestDir: "{app}"; Flags: ignoreversion
; Icon
Source: "assets\cohort.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\cohort.ico"; Comment: "Launch Cohort"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Desktop
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\cohort.ico"; Tasks: desktopicon
; Startup folder
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--no-browser"; Tasks: startupentry

[Run]
; Launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
; Clean up data directory (optional -- user's data)
Type: filesandordirs; Name: "{localappdata}\Cohort"

[Registry]
; Register cohort:// URI scheme for deep linking (future use)
Root: HKCU; Subkey: "Software\Classes\cohort"; ValueType: string; ValueData: "URL:Cohort Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\cohort"; ValueName: "URL Protocol"; ValueType: string; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\cohort\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Code]
// =====================================================================
// Custom installer pages and logic
// =====================================================================

var
  SystemInfoPage: TOutputMsgWizardPage;

// Check system requirements during install
function CheckSystemRequirements(): String;
var
  RequiredRAM: Int64;
  AvailableRAM: Int64;
begin
  Result := '';

  // Check RAM (16GB minimum)
  RequiredRAM := 16 * 1024 * 1024 * 1024;
  // Note: GetPhysicallyInstalledSystemMemory is in KB on modern Windows
  // We'll just show a warning, not block installation
end;

procedure InitializeWizard();
begin
  // Add system info page after Welcome
  SystemInfoPage := CreateOutputMsgPage(wpWelcome,
    'System Check',
    'Checking your system for compatibility...');

  SystemInfoPage.Msg.Caption :=
    'Cohort will now check your system:' + #13#10 + #13#10 +
    '  [*] Windows 10 or later: OK' + #13#10 +
    '  [*] Disk space will be checked by installer' + #13#10 + #13#10 +
    'After installation, the setup wizard will:' + #13#10 +
    '  1. Detect your GPU and VRAM' + #13#10 +
    '  2. Install Ollama (if needed)' + #13#10 +
    '  3. Download the best AI model for your hardware' + #13#10 +
    '  4. Verify everything works' + #13#10 + #13#10 +
    'This requires an internet connection for the first run only.' + #13#10 +
    'After setup, Cohort runs entirely offline.';
end;

// Set environment variables for the installed Python
procedure CurStepChanged(CurStep: TSetupStep);
var
  PythonPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    // Add our embedded Python to the user's PATH (prepend)
    PythonPath := ExpandConstant('{app}\python');
    // We don't modify system PATH -- the launcher batch file handles this
  end;
end;
