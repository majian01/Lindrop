param(
    [string] $Python = 'python',
    [string] $PythonRuntimePath,
    [string] $Dotnet = 'dotnet',
    [string] $Cargo = 'cargo',
    [string] $Output
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (!$Output) { $Output = Join-Path $root 'dist\Lindrop-win-x64' }
$Output = [IO.Path]::GetFullPath($Output)
$app = Join-Path $Output 'app'
New-Item -ItemType Directory -Path $app -Force | Out-Null
$oldRustFlags = $env:CARGO_ENCODED_RUSTFLAGS
$remap = @('--remap-path-prefix=' + $root + '=/_/src')
if ($env:CARGO_HOME) { $remap += '--remap-path-prefix=' + $env:CARGO_HOME + '=/_/cargo' }
if ($env:RUSTUP_HOME) { $remap += '--remap-path-prefix=' + $env:RUSTUP_HOME + '=/_/rustup' }
$flags = @()
if ($oldRustFlags) { $flags += $oldRustFlags }
$flags += $remap
$env:CARGO_ENCODED_RUSTFLAGS = $flags -join [char]31
try { & $Cargo build --locked --release --manifest-path (Join-Path $root 'rust-receiver\Cargo.toml') }
finally { $env:CARGO_ENCODED_RUSTFLAGS = $oldRustFlags }
if ($LASTEXITCODE) { throw 'Rust build failed.' }
$cargoTarget = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $root 'rust-receiver\target' }
Copy-Item -LiteralPath (Join-Path $cargoTarget 'release\lindrop-transfer.exe') -Destination $app
& $Dotnet publish (Join-Path $root 'desktop\Lindrop.Windows.csproj') -c Release -r win-x64 --self-contained true -o $app /p:DebugType=None /p:DebugSymbols=false /p:UseSharedCompilation=false
if ($LASTEXITCODE) { throw '.NET publish failed.' }
New-Item -ItemType Directory -Path (Join-Path $app 'backend') -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $root 'transport') -Filter '*.py' |
    Copy-Item -Destination (Join-Path $app 'backend') -Force
Copy-Item (Join-Path $root 'transport\LICENSE') (Join-Path $app 'backend') -Force
$runtime = Join-Path $app 'python'
if ($PythonRuntimePath) {
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    & robocopy.exe $PythonRuntimePath $runtime /E /COPY:DAT /DCOPY:DA /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__
    if ($LASTEXITCODE -ge 8) { throw 'Python runtime copy failed.' }
} else {
    $scratch = Join-Path $root 'work\build'
    New-Item -ItemType Directory -Path $scratch -Force | Out-Null
    $zip = Join-Path $scratch 'python-3.12.10-embed-amd64.zip'
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $zip
    # Official release checksum (integrity check, not a signature).
    $md5 = [Security.Cryptography.MD5]::Create()
    try { $archiveHash = [BitConverter]::ToString($md5.ComputeHash([IO.File]::ReadAllBytes($zip))).Replace('-', '') }
    finally { $md5.Dispose() }
    if ($archiveHash -ne 'FE8EF205F2E9C3BA44D0CF9954E1ABD3') { throw 'Python archive checksum mismatch.' }
    Expand-Archive -LiteralPath $zip -DestinationPath $runtime -Force
    & $Python -c 'import sys; assert sys.version_info[:2] == (3,12) and sys.maxsize > 2**32'
    if ($LASTEXITCODE) { throw 'Install Python 3.12 x64 for packaging.' }
    & $Python -m pip install --only-binary=:all: --target (Join-Path $runtime 'Lib\site-packages') -r (Join-Path $root 'requirements.txt')
    if ($LASTEXITCODE) { throw 'Python dependency installation failed.' }
    [IO.File]::WriteAllLines((Join-Path $runtime 'python312._pth'), @('python312.zip','.','Lib/site-packages'))
}
$validation = 'import cffi; cffi.FFI().cdef("int puts(const char *);"); import Crypto.Cipher.AES; import cryptography.hazmat.bindings._rust; import winrt.windows.devices.bluetooth.advertisement; import winrt.windows.devices.wifidirect'
$validation | & (Join-Path $runtime 'python.exe') -B -
if ($LASTEXITCODE) { throw 'Bundled Python dependency check failed.' }
foreach ($file in @('Install-Lindrop.cmd','Install-Lindrop.ps1','Uninstall-Lindrop.cmd','Uninstall-Lindrop.ps1','Set-ReleaseIntegrity.ps1','ReleaseFileIntegrity.cs','Test-ReleasePackage.ps1')) {
    Copy-Item (Join-Path $PSScriptRoot $file) $Output -Force
}
foreach ($file in @('README.md','LICENSE')) { Copy-Item (Join-Path $root $file) $Output -Force }
Copy-Item (Join-Path $root 'LICENSES') $Output -Recurse -Force
[IO.File]::WriteAllText((Join-Path $Output 'BUILD_INFO.md'), @"
# Lindrop 0.2.6 Preview

Independent Windows file transfer client compatible with Xiaomi Share.
Built $(Get-Date -Format 'yyyy-MM-dd'), Release x64; Windows 10 build 19044+ / Windows 11 target.
.NET/WPF, Rust transfer service, and embedded Python runtimes included.
For PC-to-phone sending, add the supported Xiaomi PC Manager runtime as described in README.md.
No MiDropTransfer.dll, LIBEAY32.dll or ssleay32.dll is bundled by this build.
This build record does not assert real-device or cross-adapter validation.
"@, [Text.UTF8Encoding]::new($false))
& (Join-Path $PSScriptRoot 'Set-ReleaseIntegrity.ps1') -ApplicationDirectory $app
& (Join-Path $PSScriptRoot 'Test-ReleasePackage.ps1') -PackagePath $Output
Write-Host "Release directory: $Output"
