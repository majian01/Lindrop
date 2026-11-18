param(
    [Parameter(Mandatory = $true)] [string] $InstallerPath,
    [Parameter(Mandatory = $true)] [string] $OutputDirectory,
    [string] $SevenZip = (Join-Path $env:ProgramFiles '7-Zip\7z.exe')
)

$ErrorActionPreference = 'Stop'
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$extractor = (Get-Command $SevenZip -CommandType Application).Source
$destination = [IO.Path]::GetFullPath($OutputDirectory)
$hashes = [ordered]@{
    'MiDropTransfer.dll' = 'fac9fde6d7b86a83555ea51fc7523dd3d03ec23c55611625ef5c1537ee8b95aa'
    'LIBEAY32.dll'       = '0dfd5ed5265a014213c71b12df30141cdebadf3410c58ba9cf85d1c7ff6bbef0'
    'ssleay32.dll'      = '73d263a26866fe2a6d163e9bc955e60227bbe7560d6465a21cddcab5e2fd3655'
}

function Get-Sha256([string] $Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace('-', '') }
    finally { $sha256.Dispose(); $stream.Dispose() }
}

if ((Get-Sha256 $installer) -ne 'd812cf30805d172474eb33b2f97f37129593f9d50f09d4224f73d530a2cb40e9') {
    throw 'Use the exact Xiaomi PC Manager 5.8.1.130 x64 installer linked in README.md.'
}
foreach ($name in $hashes.Keys) {
    $target = Join-Path $destination $name
    if ((Test-Path -LiteralPath $target) -and (Get-Sha256 $target) -ne $hashes[$name]) {
        throw "A different file already exists: $target"
    }
}

New-Item -ItemType Directory -Path $destination -Force | Out-Null
$stage = Join-Path $destination ('.extract-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
$archive = Join-Path $stage 'components.7z'
try {
    $reader = [IO.File]::OpenRead($installer)
    try {
        $reader.Position = 290304352 # Embedded 7z archive in the checksum-verified installer.
        $header = [byte[]]::new(32)
        if ($reader.Read($header, 0, 32) -ne 32 -or [BitConverter]::ToString($header, 0, 6) -ne '37-7A-BC-AF-27-1C') {
            throw 'Unexpected 7z header.'
        }
        $remaining = [BitConverter]::ToUInt64($header, 12) + [BitConverter]::ToUInt64($header, 20)
        if ($remaining -gt ($reader.Length - $reader.Position)) { throw 'Truncated installer.' }
        $writer = [IO.File]::Create($archive)
        try {
            $writer.Write($header, 0, $header.Length)
            $buffer = [byte[]]::new(4MB)
            while ($remaining -gt 0) {
                $count = $reader.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
                if ($count -eq 0) { throw 'Truncated installer.' }
                $writer.Write($buffer, 0, $count)
                $remaining -= $count
            }
        } finally { $writer.Dispose() }
    } finally { $reader.Dispose() }

    $names = [string[]]$hashes.Keys
    & $extractor e -y "-o$stage" $archive @names | Out-Host
    if ($LASTEXITCODE) { throw '7-Zip could not extract the three DLLs.' }
    foreach ($name in $names) {
        if ((Get-Sha256 (Join-Path $stage $name)) -ne $hashes[$name]) { throw "Checksum mismatch: $name" }
    }
    foreach ($name in $names) {
        $target = Join-Path $destination $name
        if (!(Test-Path -LiteralPath $target)) { [IO.File]::Copy((Join-Path $stage $name), $target) }
    }
    Write-Host "Verified Xiaomi runtime: $destination"
} finally {
    foreach ($name in @('components.7z') + [string[]]$hashes.Keys) {
        $file = Join-Path $stage $name
        if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file }
    }
    if (!(Get-ChildItem -LiteralPath $stage -Force)) { Remove-Item -LiteralPath $stage }
}
