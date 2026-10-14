; Inno Setup script for the Windows installer (built by packaging/build_app.py).
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6A1E3C2B-8D4F-4E8A-9B1C-7F3D2E5A9C10}
AppName=vcinject
AppVersion={#AppVersion}
AppPublisher=vcinject
AppPublisherURL=https://github.com/petter1/vcinject
DefaultDirName={autopf}\vcinject
DefaultGroupName=vcinject
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=vcinject-{#AppVersion}-windows-setup
SetupIconFile=..\build\icon\vcinject.ico
UninstallDisplayIcon={app}\vcinject.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\vcinject\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\vcinject"; Filename: "{app}\vcinject.exe"
Name: "{autodesktop}\vcinject"; Filename: "{app}\vcinject.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\vcinject.exe"; Description: "{cm:LaunchProgram,vcinject}"; Flags: nowait postinstall skipifsilent
