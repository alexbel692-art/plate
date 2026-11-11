; Установщик PlateReader (Inno Setup 6)
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6E0F3C1B-7B9A-4C55-9C2E-5A1D2B7E9A11}
AppName=PlateReader
AppVersion={#AppVersion}
AppVerName=PlateReader {#AppVersion}
AppPublisher=PlateReader
DefaultDirName={localappdata}\Programs\PlateReader
DefaultGroupName=PlateReader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=PlateReader-Setup-{#AppVersion}
SetupIconFile=resources\icon.ico
UninstallDisplayIcon={app}\PlateReader.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Значок на рабочем столе"; Flags: checkedonce

[Files]
Source: "dist\PlateReader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PlateReader"; Filename: "{app}\PlateReader.exe"
Name: "{autodesktop}\PlateReader"; Filename: "{app}\PlateReader.exe"; Tasks: desktopicon

[InstallDelete]
; ярлыки старой версии (скрипт с консольным окном)
Type: files; Name: "{userstartup}\Распознавание номеров.lnk"
Type: files; Name: "{userdesktop}\Распознавание номеров.lnk"
Type: files; Name: "{userdesktop}\Область распознавания.lnk"
Type: filesandordirs; Name: "{userprograms}\Распознавание номеров"

[Run]
Filename: "{app}\PlateReader.exe"; Description: "Запустить PlateReader"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM PlateReader.exe"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v PlateReader /f"; Flags: runhidden; RunOnceId: "DelRun"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { закрыть работающую программу и старую консольную версию }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM PlateReader.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('powershell.exe',
    '-NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name=''python.exe''\" | ' +
    'Where-Object { $_.ExecutablePath -like ''*\AppData\Local\PlateReader\*'' } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
  Result := '';
end;
