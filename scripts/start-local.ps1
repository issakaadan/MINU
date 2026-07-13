param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000,
    [string]$FrontendHost = "0.0.0.0",
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend"
$mirrorDir = Join-Path $env:TEMP "authorized-network-assessment-frontend"
$logDir = Join-Path $env:TEMP "authorized-network-assessment-logs"

$frontendPattern = [regex]::Escape($mirrorDir)

Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "uvicorn app\.main:app" -and $_.CommandLine -match "--port $BackendPort" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "node.exe" -and $_.CommandLine -match $frontendPattern -and $_.CommandLine -match "vite" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

New-Item -Path $logDir -ItemType Directory -Force | Out-Null
New-Item -Path $mirrorDir -ItemType Directory -Force | Out-Null

if (!(Test-Path (Join-Path $backendDir ".env"))) {
    Copy-Item (Join-Path $backendDir ".env.example") (Join-Path $backendDir ".env")
}

if (!(Test-Path (Join-Path $frontendDir ".env"))) {
    Copy-Item (Join-Path $frontendDir ".env.example") (Join-Path $frontendDir ".env")
}

if (Test-Path $mirrorDir) {
    Get-ChildItem -Path $mirrorDir -Force | Remove-Item -Recurse -Force
}

Get-ChildItem -Path $frontendDir -Force |
    Where-Object { $_.Name -notin @("node_modules", "dist") } |
    Copy-Item -Destination $mirrorDir -Recurse -Force

Copy-Item (Join-Path $frontendDir ".env") (Join-Path $mirrorDir ".env") -Force

$backendOut = Join-Path $logDir "backend.out.log"
$backendErr = Join-Path $logDir "backend.err.log"
$frontendOut = Join-Path $logDir "frontend.out.log"
$frontendErr = Join-Path $logDir "frontend.err.log"

foreach ($file in @($backendOut, $backendErr, $frontendOut, $frontendErr)) {
    if (Test-Path $file) {
        Remove-Item $file -Force
    }
}

Push-Location $mirrorDir
try {
    npm.cmd install --no-audit --no-fund | Out-Null
}
finally {
    Pop-Location
}

Start-Process `
    -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $BackendHost, "--port", "$BackendPort" `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendOut `
    -RedirectStandardError $backendErr | Out-Null

Start-Process `
    -FilePath "npm.cmd" `
    -ArgumentList "run", "dev", "--", "--host", $FrontendHost, "--port", "$FrontendPort" `
    -WorkingDirectory $mirrorDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $frontendOut `
    -RedirectStandardError $frontendErr | Out-Null

Start-Process `
    -FilePath "powershell" `
    -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $projectRoot "scripts\start-public-card-share.ps1") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden | Out-Null

Write-Host "Backend:  http://$BackendHost`:$BackendPort"
Write-Host "Frontend: http://$FrontendHost`:$FrontendPort"
Write-Host "Logs:     $logDir"
