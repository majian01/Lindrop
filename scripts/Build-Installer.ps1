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
& (Join-Path $PSScriptRoot 'Test-ReleasePackage.ps1') -PackagePath $PackagePath
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
& $ISCC ("/DPackageDir=$PackagePath") ("/DReleaseDir=$OutputDirectory") (Join-Path $PSScriptRoot 'Lindrop.iss')
if ($LASTEXITCODE) { throw "Installer compilation failed: $LASTEXITCODE" }
$installer = Join-Path $OutputDirectory 'Lindrop-0.2.5-preview-Setup-win10-win11-x64.exe'
& (Join-Path $PSScriptRoot 'Set-ReleaseIntegrity.ps1') -InstallerPath $installer
Write-Host $installer
