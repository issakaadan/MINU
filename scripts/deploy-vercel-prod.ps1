[CmdletBinding()]
param(
    [string]$Scope = "minugame",
    [string]$Project = "minu",
    [string]$StageDir = (Join-Path $env:TEMP "minu-vercel-deploy")
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$datasetPath = Join-Path $env:LOCALAPPDATA "WhoIsThePlayerFootball\data\players.seed.json"
$catalogDbPath = Join-Path $env:LOCALAPPDATA "WhoIsThePlayerFootball\data\who_is_the_player_football.db"
$logoPath = Join-Path $env:TEMP "minu-logo-transparent-cropped.webp"

if (-not (Test-Path $datasetPath)) {
    throw "Player seed file not found at $datasetPath"
}

if (-not (Test-Path $catalogDbPath)) {
    throw "Player catalog database not found at $catalogDbPath"
}

if (-not (Test-Path $logoPath)) {
    throw "Transparent logo not found at $logoPath"
}

if (Test-Path $StageDir) {
    Remove-Item -LiteralPath $StageDir -Recurse -Force
}

New-Item -ItemType Directory -Path $StageDir | Out-Null

$null = robocopy $repoRoot $StageDir /E /XD .git frontend\node_modules frontend\dist .venv __pycache__ /XF *.pyc *.pyo
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

$stageDatasetPath = Join-Path $StageDir "backend\data\players.seed.json"
Copy-Item $datasetPath $stageDatasetPath -Force
$stageCatalogDbPath = Join-Path $StageDir "backend\data\players.catalog.db"
Copy-Item $catalogDbPath $stageCatalogDbPath -Force

$stagePublicDir = Join-Path $StageDir "frontend\public"
New-Item -ItemType Directory -Force -Path $stagePublicDir | Out-Null
Copy-Item $logoPath (Join-Path $stagePublicDir "minu-logo.webp") -Force

Push-Location $StageDir
try {
    npx vercel deploy --prod --yes --project $Project --scope $Scope --logs
}
finally {
    Pop-Location
}
