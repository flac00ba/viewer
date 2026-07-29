param([int]$Port = 8080)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $project "docs")
Write-Host "Viewer: http://localhost:$Port/"
python -m http.server $Port
