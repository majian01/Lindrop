param([switch] $NoShortcut, [string] $InstallDirectory)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'app'
if (!$InstallDirectory) {
    $systemRoot = [IO.Path]::GetPathRoot([Environment]::GetFolderPath('Windows'))
    $drive = [IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq 'Fixed' -and $_.IsReady -and $_.RootDirectory.FullName -ne $systemRoot } | Sort-Object Name | Select-Object -First 1
    $InstallDirectory = if ($drive) { Join-Path $drive.RootDirectory.FullName 'Programs\Lindrop' } else { Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Lindrop' }
}
$target = [IO.Path]::GetFullPath($InstallDirectory)
if ($target.TrimEnd('\') -eq [IO.Path]::GetPathRoot($target).TrimEnd('\')) { throw 'Select an application folder, not a drive root.' }
$programs = [IO.Path]::GetDirectoryName($target)
$exe = Join-Path $target 'Lindrop.exe'
if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Uninstall-Lindrop.ps1'))) { throw 'Missing uninstaller.' }
$required = @('Lindrop.exe','Lindrop.dll','lindrop-transfer.exe','transport.py','backend\receiver.py','backend\windows_ip.py','python\python.exe',
    'backend\independent_server.py','backend\bks_identity.py')
foreach ($file in $required) {
    if (!(Test-Path -LiteralPath (Join-Path $source $file) -PathType Leaf)) {
        throw "Missing application file: $file"
    }
}
if (Test-Path -LiteralPath $target) {
    if (!(Test-Path -LiteralPath (Join-Path $target '.lindrop-install'))) {
        throw "The installation path is occupied by an unrelated folder: $target"
    }
    if (Get-Process Lindrop -ErrorAction SilentlyContinue) {
        throw 'Exit Lindrop from the tray before updating it.'
    }
}

$stage = Join-Path $programs ('Lindrop-staging-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage -Force | Out-Null
& robocopy.exe $source $stage /E /COPY:DAT /DCOPY:DA /R:1 /W:1 /NJH /NJS /NFL /NDL /NP /XD data
if ($LASTEXITCODE -ge 8) { throw "Copy failed: robocopy exit code $LASTEXITCODE" }
& (Join-Path $PSScriptRoot 'Set-ReleaseIntegrity.ps1') -ApplicationDirectory $stage
Set-Content -LiteralPath (Join-Path $stage '.lindrop-install') -Value 'Lindrop' -Encoding ascii
Copy-Item (Join-Path $PSScriptRoot 'Uninstall-Lindrop.ps1') $stage -Force
Copy-Item (Join-Path $PSScriptRoot 'Uninstall-Lindrop.cmd') $stage -Force
foreach ($notice in @('LICENSE','README.md','LICENSES')) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot $notice)) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $notice) -Destination $stage -Recurse -Force
    }
}
$backup = $null
if (Test-Path -LiteralPath $target) {
    $backup = Join-Path $programs ('Lindrop-backup-' + [guid]::NewGuid().ToString('N'))
    Move-Item -LiteralPath $target -Destination $backup
}
$dataMoved = $false
try {
    if ($backup -and (Test-Path -LiteralPath (Join-Path $backup 'data'))) {
        $oldData = [IO.Path]::GetFullPath((Join-Path $backup 'data'))
        $newData = [IO.Path]::GetFullPath((Join-Path $stage 'data'))
        if (!$oldData.StartsWith($backup + '\', [StringComparison]::OrdinalIgnoreCase) -or
            !$newData.StartsWith($stage + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe data migration path.' }
        Move-Item -LiteralPath $oldData -Destination $newData
        $dataMoved = $true
    }
    Move-Item -LiteralPath $stage -Destination $target
} catch {
    if ($backup -and !(Test-Path -LiteralPath $target)) {
        if ($dataMoved -and (Test-Path -LiteralPath (Join-Path $stage 'data'))) {
            Move-Item -LiteralPath (Join-Path $stage 'data') -Destination (Join-Path $backup 'data')
        }
        Move-Item -LiteralPath $backup -Destination $target
    }
    throw
}

if (!$NoShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) '邻渡 (Lindrop).lnk'))
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = $target
    $shortcut.Save()
}
Write-Host "Installed: $exe"
$uninstall = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall\Lindrop-Windows')
try {
    $uninstall.SetValue('DisplayName', '邻渡 (Lindrop)')
    $uninstall.SetValue('DisplayVersion', '0.2.5-preview')
    $uninstall.SetValue('InstallLocation', $target)
    $uninstall.SetValue('DisplayIcon', $exe)
    $uninstall.SetValue('UninstallString', ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $target 'Uninstall-Lindrop.ps1') + '"'))
    $uninstall.SetValue('NoModify', 1, [Microsoft.Win32.RegistryValueKind]::DWord)
    $uninstall.SetValue('NoRepair', 1, [Microsoft.Win32.RegistryValueKind]::DWord)
} finally { $uninstall.Dispose() }
