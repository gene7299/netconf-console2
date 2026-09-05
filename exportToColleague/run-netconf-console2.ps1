[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

$ErrorActionPreference = "Stop"
$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$console = Join-Path $packageRoot ".venv\Scripts\netconf-console2.exe"
if (-not (Test-Path -LiteralPath $console)) {
    throw "The source package is not installed. Run .\install-netconf-console2.ps1 first."
}

& $console @Arguments
exit $LASTEXITCODE
