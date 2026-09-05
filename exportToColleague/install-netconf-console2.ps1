[CmdletBinding()]
param(
    [switch] $UseWheel
)

$ErrorActionPreference = "Stop"
$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $packageRoot ".venv\Scripts\python.exe"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python 3.10+ with the py launcher is required."
}

& py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.10 or newer is required."
}

if (-not (Test-Path -LiteralPath $python)) {
    & py -3 -m venv (Join-Path $packageRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the .venv virtual environment."
    }
}

$installTarget = $packageRoot
if ($UseWheel) {
    $wheel = Get-ChildItem -LiteralPath $packageRoot -Filter "netconf_console2-*.whl" -File |
        Sort-Object -Property Name |
        Select-Object -Last 1
    if ($null -eq $wheel) {
        throw "No netconf-console2 wheel was found in $packageRoot."
    }
    $installTarget = $wheel.FullName
}

& $python -m pip install --upgrade $installTarget
if ($LASTEXITCODE -ne 0) {
    throw "pip could not install netconf-console2 or its dependencies."
}

Write-Host "Installed netconf-console2 from $(if ($UseWheel) { 'wheel' } else { 'source' }) in $packageRoot\.venv"
Write-Host "Run .\run-netconf-console2.ps1 --interactive ..."
