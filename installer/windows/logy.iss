; Inno Setup script for LOGY.
; Built by .github/workflows/build-installers.yml on windows-latest, from
; the PyInstaller "dist\LOGY" onedir output produced one step earlier in
; that same job - this script does not run PyInstaller itself.
;
; To build locally on your own Windows machine instead of via CI:
;   1. pip install pyinstaller
;   2. pyinstaller --name LOGY --windowed --onedir --icon assets\logo.ico --add-data "assets;assets" main.py
;   3. Install Inno Setup (https://jrsoftware.org/isdl.php)
;   4. iscc installer\windows\logy.iss
;   The finished installer lands in installer\windows\output\LOGY-Setup-<version>.exe
;
; MyAppVersion is passed in by the workflow via /DMyAppVersion=...; it
; defaults to 0.0.0-dev for a local build where nothing passes it.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "LOGY"
#define MyAppPublisher "LOGY"
#define MyAppExeName "LOGY.exe"

[Setup]
AppId={{7C6C6C7A-6E9E-4A6B-9A2A-2D6E7B4C0F31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=LOGY-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; PyInstaller's own dist\LOGY\LOGY.exe already lives outside Program
; Files by default (a normal per-machine install target) - no admin
; elevation quirks specific to LOGY beyond the standard installer prompt.
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

; Pulls in the ENTIRE PyInstaller onedir output (LOGY.exe + all its
; bundled DLLs/data, including the assets/ folder added via --add-data)
; - built by the workflow step just before this script runs, two
; directories up from here (repo root\dist\LOGY).
[Files]
Source: "..\..\dist\LOGY\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
