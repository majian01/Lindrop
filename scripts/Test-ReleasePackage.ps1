param([Parameter(Mandatory = $true)] [string] $PackagePath)
$ErrorActionPreference = 'Stop'
$package = (Resolve-Path -LiteralPath $PackagePath).Path
$required = @(
    'app\Lindrop.exe', 'app\Lindrop.dll', 'app\Lindrop.deps.json',
    'app\Lindrop.runtimeconfig.json', 'app\coreclr.dll', 'app\hostfxr.dll',
    'app\hostpolicy.dll', 'app\System.Private.CoreLib.dll',
    'app\PresentationFramework.dll', 'app\lindrop-transfer.exe',
    'app\transport.py', 'app\backend\receiver.py',
    'app\backend\independent_server.py', 'app\backend\bks_identity.py', 'app\backend\official_server.py',
    'app\python\python.exe', 'app\python\python312.dll',
    'app\python\vcruntime140.dll', 'app\python\vcruntime140_1.dll',
    'Install-Lindrop.cmd', 'Install-Lindrop.ps1',
    'Uninstall-Lindrop.cmd', 'Uninstall-Lindrop.ps1',
    'Set-ReleaseIntegrity.ps1', 'ReleaseFileIntegrity.cs',
    'README.md', 'LICENSE', 'BUILD_INFO.md', 'LICENSES\Tandem-MIT.txt',
    'app\python\LICENSE.txt'
)
foreach ($relative in $required) {
    $file = Join-Path $package $relative
    if (!(Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file).Length -eq 0) {
        throw "Incomplete release package: $relative"
    }
}
foreach ($directory in @('app\data', 'app\Identity', 'app\XiaomiRuntime')) {
    if (Test-Path -LiteralPath (Join-Path $package $directory)) {
        throw "Private data or vendor material in release package: $directory"
    }
}
$forbidden = @('.bks', '.jks', '.pfx', '.p12', '.pem', '.key', '.apk', '.log')
$vendor = @('MiDropTransfer.dll', 'LIBEAY32.dll', 'ssleay32.dll')
foreach ($file in Get-ChildItem -LiteralPath $package -Recurse -File -Force) {
    if ($file.Extension -in $forbidden -or $file.Name -in $vendor) {
        throw "Unexpected release file: $($file.FullName)"
    }
}
Write-Host "Release package layout verified: $($required.Count) required files."
