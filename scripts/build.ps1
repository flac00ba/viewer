param(
    [string]$Config
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
if (-not $Config) {
    $Config = Join-Path $project "viewer.config.json"
}

python (Join-Path $project "tools\build_viewer.py") --config $Config
if ($LASTEXITCODE -ne 0) {
    throw "Map conversion failed with exit code $LASTEXITCODE."
}
