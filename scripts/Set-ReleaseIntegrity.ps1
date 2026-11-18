param([string] $ApplicationDirectory, [string] $InstallerPath, [string[]] $ArchivePath)
$ErrorActionPreference = 'Stop'
if (!('LindropReleaseFileIntegrity' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'ReleaseFileIntegrity.cs')
}
$targets = @()
if ($ApplicationDirectory) {
    $app = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
    if (!(Test-Path -LiteralPath (Join-Path $app 'Lindrop.dll')) -or !(Test-Path -LiteralPath (Join-Path $app 'backend\receiver.py'))) {
        throw 'The application directory is not a complete Lindrop bundle.'
    }
    $targets += Get-ChildItem -LiteralPath $app -Recurse -File -Filter '*.exe' | Select-Object -ExpandProperty FullName
}
if ($InstallerPath) {
    $installer = Get-Item -LiteralPath $InstallerPath
    if ($installer.PSIsContainer -or $installer.Name -notmatch '^Lindrop-.*-Setup-.*\.exe$') { throw 'Expected a Lindrop installer executable.' }
    $targets += $installer.FullName
}
foreach ($path in $ArchivePath) {
    $archive = Get-Item -LiteralPath $path
    if ($archive.PSIsContainer -or $archive.Name -notmatch '^Lindrop-.*\.zip$') { throw 'Expected a Lindrop release archive.' }
    $targets += $archive.FullName
}
if (!$targets.Count) { throw 'No release executables were selected.' }
foreach ($file in ($targets | Select-Object -Unique)) {
    # Build workspaces can inherit Low integrity. Normalize only our release
    # executables, so Explorer launches them with the user's normal privileges.
    # This changes file metadata, not binary contents or system directory ACLs.
    if ([LindropReleaseFileIntegrity]::ReadLevel($file) -lt 8192) {
        $result = & icacls.exe $file /setintegritylevel M /q 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Could not set normal executable integrity: $file`n$result" }
        if ([LindropReleaseFileIntegrity]::ReadLevel($file) -lt 8192) { throw "Executable still has low integrity: $file" }
    }
}
Write-Host "Verified normal integrity for release files: $($targets.Count)"
