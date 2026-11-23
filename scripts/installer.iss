; Script de instalador Windows (Inno Setup) para DiscoClaro
[Setup]
AppName=DiscoClaro
AppVersion=1.0.0
AppPublisher=DiscoClaro
DefaultDirName={autopf}\DiscoClaro
DefaultGroupName=DiscoClaro
OutputDir=..\dist
OutputBaseFilename=DiscoClaro_Setup
SetupIconFile=..\src\discoclaro\assets\discoclaro.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
Source: "..\dist\DiscoClaro.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DiscoClaro"; Filename: "{app}\DiscoClaro.exe"
Name: "{autodesktop}\DiscoClaro"; Filename: "{app}\DiscoClaro.exe"

[Run]
Filename: "{app}\DiscoClaro.exe"; Description: "Iniciar DiscoClaro ahora"; Flags: nowait postinstall skipifsilent
