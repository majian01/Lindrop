param(
    [string] $ISCC = 'ISCC.exe',
    [string] $PackagePath,
    [string] $OutputDirectory
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (!$PackagePath) { $PackagePath = Join-Path $root 'dist\Lindrop-win-x64' }
if (!$OutputDirectory) { $OutputDirectory = Join-Path $root 'dist' }
$PackagePath = (Resolve-Path -LiteralPath $PackagePath).Path
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
foreach ($name in @('app\Lindrop.exe','app\python\python.exe','app\lindrop-transfer.exe','app\backend\independent_server.py','app\backend\bks_identity.py','LICENSES')) {
    if (!(Test-Path -LiteralPath (Join-Path $PackagePath $name))) { throw "Incomplete package: $name" }
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
& $ISCC ("/DPackageDir=$PackagePath") ("/DReleaseDir=$OutputDirectory") (Join-Path $PSScriptRoot 'Lindrop.iss')
if ($LASTEXITCODE) { throw "Installer compilation failed: $LASTEXITCODE" }
$installer = Join-Path $OutputDirectory 'Lindrop-0.2.4-preview-Setup-win10-win11-x64.exe'
& (Join-Path $PSScriptRoot 'Set-ReleaseIntegrity.ps1') -InstallerPath $installer
Write-Host $installer
