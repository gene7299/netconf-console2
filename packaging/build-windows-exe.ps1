[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$spec = Join-Path $PSScriptRoot "netconf_console2.spec"
$dist = Join-Path $projectRoot "exportToColleagueEXE"
$work = Join-Path $projectRoot "build\pyinstaller"
$exe = Join-Path $dist "netconf-console2.exe"
$checksums = Join-Path $dist "SHA256SUMS.txt"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python with the Windows py launcher is required to build the executable."
}

Push-Location $projectRoot
try {
    & py -3 -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "The unit test suite failed; the executable was not built."
    }
}
finally {
    Pop-Location
}

& py -3 -m PyInstaller --noconfirm --clean --distpath $dist --workpath $work $spec
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller failed to create $exe."
}

& $exe --bundle-self-test
if ($LASTEXITCODE -ne 0) {
    throw "The frozen dependency and transport self-test failed."
}

& $exe --help | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The frozen CLI help smoke test failed."
}

& py -3 (Join-Path $projectRoot "tests\integration\frozen_transport_smoke.py") --exe $exe
if ($LASTEXITCODE -ne 0) {
    throw "A frozen Direct/Call Home SSH/TLS loopback handshake failed."
}

$hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
$checksumLines = @("$hash  netconf-console2.exe")
[System.IO.File]::WriteAllLines(
    $checksums,
    $checksumLines,
    [System.Text.UTF8Encoding]::new($false)
)

Get-FileHash -LiteralPath $exe -Algorithm SHA256
