@echo off
setlocal
set "LINDROP_VERIFY_ONLY="
if /i "%~1"=="--verify-only" set "LINDROP_VERIFY_ONLY=1"

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$url = 'https://github.com/majian01/Lindrop/releases/download/v0.2.5-preview/Lindrop-0.2.5-preview-Setup-win10-win11-x64.exe';" ^
    "$expected = '462697c138de548b43cd9d398baeae53be6a876cfdfb48a57e18cc3ee624b8ca';" ^
    "$setup = Join-Path ([IO.Path]::GetTempPath()) ('Lindrop-' + [Guid]::NewGuid().ToString('N') + '.exe');" ^
    "try {" ^
    "    Write-Host 'Downloading Lindrop 0.2.5 Preview from GitHub...';" ^
    "    $curl = Join-Path $env:SystemRoot 'System32\curl.exe';" ^
    "    & $curl --fail --location --progress-bar --show-error --retry 2 --connect-timeout 15 --max-time 600 --output $setup $url;" ^
    "    if ($LASTEXITCODE -ne 0) { throw ('Download failed (curl exit ' + $LASTEXITCODE + ').') };" ^
    "    $sha256 = [Security.Cryptography.SHA256]::Create();" ^
    "    $file = [IO.File]::OpenRead($setup);" ^
    "    try { $actual = [BitConverter]::ToString($sha256.ComputeHash($file)).Replace('-', '') }" ^
    "    finally { $file.Dispose(); $sha256.Dispose() };" ^
    "    if ($actual -ne $expected) { throw 'Installer SHA-256 mismatch.' };" ^
    "    Write-Host 'Installer SHA-256 verified.';" ^
    "    if ($env:LINDROP_VERIFY_ONLY -ne '1') {" ^
    "        $installer = Start-Process -FilePath $setup -NoNewWindow -PassThru;" ^
    "        $installer.WaitForExit();" ^
    "        exit $installer.ExitCode;" ^
    "    };" ^
    "} catch { Write-Host $_.Exception.Message; exit 1 }" ^
    "finally { if (Test-Path -LiteralPath $setup) { Remove-Item -LiteralPath $setup -ErrorAction SilentlyContinue } }"

set "LINDROP_RESULT=%ERRORLEVEL%"
if not "%LINDROP_RESULT%"=="0" if not defined LINDROP_VERIFY_ONLY (
    echo Setup ended with exit code %LINDROP_RESULT%.
    pause
)
exit /b %LINDROP_RESULT%
