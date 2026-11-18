; Lindrop Community installer. Build with Inno Setup 7.1.1 or newer.
; New installer script: MIT. Inno Setup has its own license; see LICENSES.
#ifndef PackageDir
  #define PackageDir "..\..\package"
#endif
#ifndef ReleaseDir
  #define ReleaseDir "..\.."
#endif

[Setup]
AppId=Lindrop-Windows
AppName=邻渡 (Lindrop)
AppVersion=0.2.4
AppVerName=邻渡 (Lindrop) 0.2.4 Preview
AppPublisher=Lindrop contributors
DefaultDirName={code:GetDefaultInstallDirectory}
DefaultGroupName=邻渡 (Lindrop)
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19044
DisableDirPage=no
DisableProgramGroupPage=yes
DisableWelcomePage=no
WizardStyle=modern windows11
WizardSizePercent=110
DefaultDialogFontName=Microsoft YaHei UI
SetupIconFile=..\desktop\Assets\Lindrop.ico
UninstallDisplayIcon={app}\Lindrop.exe
OutputDir={#ReleaseDir}
OutputBaseFilename=Lindrop-0.2.4-preview-Setup-win10-win11-x64
Compression=lzma2/normal
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[LangOptions]
; Use a font with native CJK coverage for both Setup and Uninstall. This also
; keeps the Chinese project name readable when the English language is chosen.
DialogFontName=Microsoft YaHei UI
DialogFontSize=9
WelcomeFontName=Microsoft YaHei UI
WelcomeFontSize=14

[Messages]
chinesesimp.WelcomeLabel2=这将在您的电脑上安装 [name/ver]。%n%n无需另装 Python、.NET 或 Rust。您可以选择安装到 D 盘、E 盘或其他可写位置，不需要管理员权限。%n%n设置、日志和默认接收文件保存在安装目录的 data 文件夹内。更新前请从系统托盘退出 Lindrop。
chinesesimp.FinishedLabel=Lindrop 已安装完成。%n%n可从开始菜单打开。卸载不会删除收到的文件、传输历史和设置。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "install-marker.txt"; DestDir: "{app}"; DestName: ".lindrop-install"; Flags: ignoreversion
Source: "{#PackageDir}\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "data\*,__pycache__\*,*.pyc,*.pdb"
Source: "{#PackageDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PackageDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PackageDir}\BUILD_INFO.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PackageDir}\LICENSES\*"; DestDir: "{app}\LICENSES"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\邻渡 (Lindrop)"; Filename: "{app}\Lindrop.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\邻渡 (Lindrop)"; Filename: "{app}\Lindrop.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[InstallDelete]
Type: files; Name: "{app}\README.en.md"
Type: files; Name: "{app}\THIRD_PARTY_NOTICES.md"
Type: files; Name: "{app}\LOCAL-BUNDLE-NOTICE.md"

[Run]
Filename: "{app}\Lindrop.exe"; Description: "{cm:LaunchProgram,Lindrop}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
function GetDriveTypeW(RootPath: String): Cardinal;
  external 'GetDriveTypeW@kernel32.dll stdcall';

function GetDefaultInstallDirectory(Param: String): String;
var
  Drive: Integer;
  RootPath: String;
begin
  Result := ExpandConstant('{localappdata}\Programs\Lindrop');
  for Drive := Ord('D') to Ord('Z') do begin
    RootPath := Chr(Drive) + ':\';
    if (GetDriveTypeW(RootPath) = 3) and DirExists(RootPath) and
       (CompareText(RootPath, ExtractFileDrive(ExpandConstant('{sys}')) + '\') <> 0) then begin
      Result := RootPath + 'Programs\Lindrop';
      Exit;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Existing: String;
  Entry: TFindRec;
  Nonempty: Boolean;
begin
  Result := '';
  Existing := ExpandConstant('{app}');
  if (CompareText(Existing, ExtractFileDrive(Existing) + '\') = 0) then begin
    Result := 'Please select an application folder, not a drive root.';
    Exit;
  end;
  if DirExists(Existing) and not FileExists(Existing + '\.lindrop-install') then begin
    Nonempty := False;
    if FindFirst(Existing + '\*', Entry) then begin
      try
        repeat
          if (Entry.Name <> '.') and (Entry.Name <> '..') then Nonempty := True;
        until not FindNext(Entry);
      finally
        FindClose(Entry);
      end;
    end;
    if Nonempty then Result := 'The selected folder contains unrelated files. Please choose an empty folder for Lindrop.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  OldLocation: String;
begin
  if CurStep = ssPostInstall then begin
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Lindrop-Windows', 'InstallLocation', OldLocation) then
      if CompareText(OldLocation, ExpandConstant('{app}')) = 0 then
        RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\Lindrop-Windows');
  end;
end;

// Deliberately no recursive UninstallDelete entries. Only installed files are
// removed; user files, fallback data and LocalAppData\Lindrop-PC are preserved.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\*\shell\Lindrop-Windows');
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\*\shell\Lindrop-PC');
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Lindrop-Windows');
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Lindrop-PC');
    if DirExists(ExpandConstant('{app}\data')) then
      SaveStringToFile(ExpandConstant('{app}\.lindrop-install'), 'Lindrop', False);
  end;
end;
