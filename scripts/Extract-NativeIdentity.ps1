param(
    [Parameter(Mandatory=$true)] [string] $ApkPath,
    [string] $DestinationPath
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (!$DestinationPath) { $DestinationPath = Join-Path $root 'work\identity\pc.bks' }
$apk = (Resolve-Path -LiteralPath $ApkPath).Path
$destination = [IO.Path]::GetFullPath($DestinationPath)
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($apk)
try {
    $entry = $archive.GetEntry('res/aO.bks')
    if (!$entry -or $entry.Length -gt 2MB) { throw 'Expected native PC identity resource not found. Supported APK: Xiaomi Share 4.13.12.' }
    $source = $entry.Open()
    $memory = New-Object IO.MemoryStream
    try { $source.CopyTo($memory) } finally { $source.Dispose() }
    $data = $memory.ToArray()
    $memory.Dispose()
} finally { $archive.Dispose() }
$sha = [Security.Cryptography.SHA256]::Create()
try { $hash = [BitConverter]::ToString($sha.ComputeHash($data)).Replace('-', '').ToLowerInvariant() }
finally { $sha.Dispose() }
if ($hash -ne '7429ee33c15dfe959dce153cc6928a86d539aa85229990f71c7af59148003fbb') {
    throw 'Unsupported identity resource. Do not guess another certificate or overwrite the working identity.'
}
if (Test-Path -LiteralPath $destination) {
    if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) {
        throw 'Destination already contains a different file.'
    }
} else {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
    $output = [IO.File]::Open($destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try { $output.Write($data, 0, $data.Length) } finally { $output.Dispose() }
}
Write-Host "Native identity extracted: $destination"
Write-Host "SHA256: $hash"
Write-Host 'Keep this vendor identity separate from public source. No plaintext private key was exported.'
