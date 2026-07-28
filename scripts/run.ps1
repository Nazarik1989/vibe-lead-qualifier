[CmdletBinding()]
param(
    [string]$ListenAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    $PythonExe = $VenvPython
}
else {
    $PythonExe = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $ProjectRoot
try {
    & $PythonExe -m uvicorn vibe_lead_qualifier.main:app --host $ListenAddress --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "Uvicorn завершился с кодом $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
