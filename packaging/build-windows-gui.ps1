[CmdletBinding()]
param(
    [string] $WorkDirectory = "",
    [string] $OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$guiProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path (Split-Path -Parent $guiProjectRoot) "nectconf-client_backup_reference\gui-build"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $guiProjectRoot "exportToColleagueEXE"
}
New-Item -ItemType Directory -Path $WorkDirectory -Force | Out-Null
Push-Location $guiProjectRoot
try {
    & py -3 -B -m unittest discover -s tests/unit -v
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }
    & py -3 -B tests/integration/gui_reconnect_smoke.py
    if ($LASTEXITCODE -ne 0) { throw "GUI dropped-transport reconnect tests failed." }
    & py -3 -B -m PyInstaller --noconfirm --clean --distpath $OutputDirectory --workpath (Join-Path $WorkDirectory "pyinstaller") (Join-Path $PSScriptRoot "netconf_console2_gui.spec")
    if ($LASTEXITCODE -ne 0) { throw "GUI executable build failed." }
    $guiExe = Join-Path $OutputDirectory "netconf-console2-gui.exe"
    $guiReport = Join-Path $WorkDirectory "gui-self-test.json"
    $guiCheck = Start-Process -FilePath $guiExe -ArgumentList @("--self-test", ('"{0}"' -f $guiReport)) -WindowStyle Hidden -Wait -PassThru
    if ($guiCheck.ExitCode -ne 0) { throw "Frozen GUI self-test failed. See $guiReport" }
    & py -3 -B tests/integration/gui_transport_smoke.py --exe $guiExe
    if ($LASTEXITCODE -ne 0) { throw "Frozen GUI transport tests failed." }
    $guiHashes = @(Get-ChildItem -LiteralPath $OutputDirectory -Filter '*.exe' -File | Sort-Object Name | ForEach-Object {
        '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName).Hash, $_.Name
    })
    [System.IO.File]::WriteAllLines((Join-Path $OutputDirectory 'SHA256SUMS.txt'), $guiHashes, [System.Text.UTF8Encoding]::new($false))
    Get-FileHash -LiteralPath $guiExe
}
finally {
    Pop-Location
}
