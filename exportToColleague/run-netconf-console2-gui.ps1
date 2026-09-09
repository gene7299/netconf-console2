[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]] $GuiArguments)

$ErrorActionPreference = "Stop"
$guiPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $guiPython)) {
    throw 'Run .\install-netconf-console2.ps1 first.'
}
& $guiPython -m netconf_console.gui.app @GuiArguments
