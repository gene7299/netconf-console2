[CmdletBinding()]
param(
    [string] $WorkDirectory = "",
    [string] $OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path (Split-Path -Parent $projectRoot) "nectconf-client_backup_reference\gui-qt-build"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot "exportToColleagueEXE"
}
New-Item -ItemType Directory -Path $WorkDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$legacyAlias = Join-Path $OutputDirectory "netconf-console2-gui-qt.exe"
if (Test-Path -LiteralPath $legacyAlias -PathType Leaf) {
    Remove-Item -LiteralPath $legacyAlias -Force
}
Push-Location $projectRoot
try {
    & uv run --python 3.13 --group test --extra gui -- python -B -m unittest discover -s tests/unit -p "test_qt_app.py" -v
    if ($LASTEXITCODE -ne 0) { throw "PySide6 GUI unit tests failed." }
    & uv run --python 3.13 --group build --extra gui -- python -m PyInstaller --noconfirm --clean --distpath $OutputDirectory `
        --workpath (Join-Path $WorkDirectory "pyinstaller") `
        (Join-Path $PSScriptRoot "netconf_console2_gui_qt.spec")
    if ($LASTEXITCODE -ne 0) { throw "PySide6 GUI executable build failed." }
    $guiExe = Join-Path $OutputDirectory "netconf-console2-gui.exe"
    $report = Join-Path $WorkDirectory "gui-qt-self-test.json"
    $check = Start-Process -FilePath $guiExe -ArgumentList @("--self-test", ('"{0}"' -f $report)) -WindowStyle Hidden -Wait -PassThru
    if ($check.ExitCode -ne 0) { throw "Frozen PySide6 GUI self-test failed. See $report" }
    $json = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    if (-not $json.passed) { throw "Frozen PySide6 GUI self-test report failed. See $report" }
    $checksumLines = @(Get-ChildItem -LiteralPath $OutputDirectory -Filter '*.exe' -File |
        Sort-Object Name |
        ForEach-Object {
            '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $_.Name
        })
    [IO.File]::WriteAllLines((Join-Path $OutputDirectory 'SHA256SUMS.txt'), $checksumLines,
        [Text.UTF8Encoding]::new($false))
    Get-FileHash -LiteralPath $guiExe -Algorithm SHA256
}
finally {
    Pop-Location
}
