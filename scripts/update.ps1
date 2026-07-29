param(
    [string]$Config,
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
if (-not $Config) {
    $Config = Join-Path $project "viewer.config.json"
}
$Config = [System.IO.Path]::GetFullPath($Config)

if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
    throw "Config file does not exist: $Config"
}

Write-Host "[1/4] Checking source files..." -ForegroundColor Cyan
$configObject = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$configDirectory = Split-Path -Parent $Config
foreach ($name in @("map", "otb", "dat", "spr", "creatures")) {
    $configuredPath = $configObject.paths.$name
    if (-not $configuredPath) {
        throw "Missing paths.$name in $Config"
    }
    $absolutePath = [System.IO.Path]::GetFullPath((Join-Path $configDirectory $configuredPath))
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
        throw "Missing source file ($name): $absolutePath"
    }
    Write-Host "  $name -> $absolutePath"
}

if ($InstallDependencies) {
    Write-Host "[2/4] Installing Python dependencies..." -ForegroundColor Cyan
    python -m pip install -r (Join-Path $project "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed with exit code $LASTEXITCODE."
    }
} else {
    Write-Host "[2/4] Using installed Python dependencies (add -InstallDependencies on first run)." -ForegroundColor DarkGray
}

Write-Host "[3/4] Regenerating map chunks, overviews and sprite atlases..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "build.ps1") -Config $Config

Write-Host "[4/4] Verifying generated viewer..." -ForegroundColor Cyan
Push-Location $project
try {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Viewer tests failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

$manifestPath = Join-Path $project "docs\assets\manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$assetFiles = Get-ChildItem -LiteralPath (Join-Path $project "docs\assets") -Recurse -File
$assetBytes = ($assetFiles | Measure-Object -Property Length -Sum).Sum
$assetMiB = [Math]::Round($assetBytes / 1MB, 2)

Write-Host ""
Write-Host "Update ready." -ForegroundColor Green
Write-Host "  tiles:          $($manifest.stats.tiles)"
Write-Host "  item types:     $($manifest.stats.usedItemTypes)"
Write-Host "  creature types: $($manifest.stats.usedCreatureTypes)"
Write-Host "  used sprites:   $($manifest.stats.usedSprites)"
Write-Host "  atlas pages:    $($manifest.stats.atlasPages)"
Write-Host "  output size:    $assetMiB MiB"

if ($manifest.stats.missingItems -or $manifest.stats.missingCreatures) {
    Write-Host ""
    Write-Warning "The build completed, but manifest.json contains missing item/creature warnings."
}

if (Test-Path -LiteralPath (Join-Path $project ".git")) {
    Write-Host ""
    Write-Host "Changed files:" -ForegroundColor Cyan
    git -C $project status --short -- docs
    Write-Host ""
    Write-Host "Publish with:"
    Write-Host "  git -C `"$project`" add docs"
    Write-Host "  git -C `"$project`" commit -m `"Update map`""
    Write-Host "  git -C `"$project`" push"
} else {
    Write-Host ""
    Write-Host "The generated docs directory is ready. Initialize the GitHub repository as described in README.md."
}
