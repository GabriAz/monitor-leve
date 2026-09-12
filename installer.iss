; monitor-leve — instalador (Inno Setup 6)
; Instala em %LOCALAPPDATA%\Programs\monitor-leve (sem exigir admin/UAC),
; cria atalhos no Menu Iniciar + area de trabalho, autostart no boot (HKCU Run)
; e registra desinstalador em "Adicionar ou remover programas".

#define MyAppName "monitor leve"
#define MyAppVersion "1.1.0"
#define MyAppExeName "monitor-leve.exe"

[Setup]
AppId={{5E1B0C8A-2A7E-4B32-9F14-3D0D4E6A1B2C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Gabriel Guimaraes
DefaultDirName={autopf}\monitor-leve
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=monitor-leve-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na &area de trabalho"; GroupDescription: "Atalhos adicionais:"
Name: "autostart"; Description: "Iniciar junto com o &Windows (recomendado)"; GroupDescription: "Inicializacao:"; Flags: unchecked

[Files]
Source: "dist\monitor-leve.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "PresentMon.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar monitor leve"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Autostart no boot (por usuario, sem precisar de admin)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "monitor-leve"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Iniciar monitor leve agora"; Flags: nowait postinstall skipifsilent