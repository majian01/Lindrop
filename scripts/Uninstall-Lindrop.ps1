param([switch] $Quiet, [string] $InstallDirectory)
$ErrorActionPreference = 'Stop'
if (!$InstallDirectory) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.lindrop-install')) { $InstallDirectory = $PSScriptRoot }
    else { $InstallDirectory = [Microsoft.Win32.Registry]::GetValue('HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\Lindrop-Windows', 'InstallLocation', $null) }
}
if (!$InstallDirectory) { throw 'No script-based Lindrop installation was found. Use Windows Settings to uninstall an EXE installation.' }
$target = [IO.Path]::GetFullPath($InstallDirectory)
if ($target.TrimEnd('\') -eq [IO.Path]::GetPathRoot($target).TrimEnd('\')) { throw 'Unsafe uninstall path.' }
if (Test-Path -LiteralPath $target) {
    $marker = Join-Path $target '.lindrop-install'
    if (!(Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw).Trim() -ne 'Lindrop') { throw "Unrecognized installation: $target" }
    $processes = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase) }
    foreach ($process in $processes) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
    # Preserve any fallback state stored alongside the application.
    $data = Join-Path $target 'data'
    if (Test-Path -LiteralPath $data) {
        $save = Join-Path ([IO.Path]::GetDirectoryName($target)) ('Lindrop-data-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
        New-Item -ItemType Directory -Path $save -Force | Out-Null
        Copy-Item -LiteralPath $data -Destination $save -Recurse
        Write-Host "Preserved fallback data: $save"
    }
    Remove-Item -LiteralPath $target -Recurse -Force
}
$run = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Run', $true)
if ($run) {
    foreach ($name in @('Lindrop-Windows','Lindrop-PC')) {
        $value = $run.GetValue($name)
        if ($value -is [string] -and $value -match '(?i)Lindrop|Lindrop-PC') { $run.DeleteValue($name, $false) }
    }
    $run.Dispose()
}
foreach ($name in @('Lindrop-Windows','Lindrop-PC')) {
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree("Software\Classes\*\shell\$name", $false)
}
[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree('Software\Microsoft\Windows\CurrentVersion\Uninstall\Lindrop-Windows', $false)
$link = Join-Path ([Environment]::GetFolderPath('Programs')) '邻渡 (Lindrop).lnk'
if (Test-Path -LiteralPath $link) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($link)
    if ($shortcut.TargetPath -eq (Join-Path $target 'Lindrop.exe')) { Remove-Item -LiteralPath $link -Force }
}
Write-Host 'Lindrop uninstalled. Received files, history and settings were preserved.'
if (!$Quiet) { Write-Host 'You may close this window.' }
