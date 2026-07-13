param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000
)

$ErrorActionPreference = "Stop"

function Wait-ForUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$MaxAttempts = 30,
        [int]$DelaySeconds = 1
    )

    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt += 1) {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 6
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                $body = [string]$response.Content
                if ($body -notmatch "No tunnel here" -and $body -notmatch "Tunnel unavailable") {
                    return $true
                }
            }
        }
        catch {
        }

        Start-Sleep -Seconds $DelaySeconds
    }

    return $false
}

function Ensure-Cloudflared {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DestinationPath
    )

    if (Test-Path $DestinationPath) {
        return $DestinationPath
    }

    $downloadUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    Invoke-WebRequest -Uri $downloadUrl -OutFile $DestinationPath
    return $DestinationPath
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend"
$mirrorDir = Join-Path $env:TEMP "authorized-network-assessment-public-frontend"
$logDir = Join-Path $env:TEMP "authorized-network-assessment-logs"
$toolDir = Join-Path $env:TEMP "authorized-network-assessment-tools"
$urlFile = Join-Path $logDir "public-share-url.txt"
$cloudflaredPath = Join-Path $toolDir "cloudflared.exe"
$backendOut = Join-Path $logDir "backend-public.out.log"
$backendErr = Join-Path $logDir "backend-public.err.log"
$tunnelOut = Join-Path $logDir "cloudflared-public.out.log"
$tunnelErr = Join-Path $logDir "cloudflared-public.err.log"
$legacyLogs = @(
    (Join-Path $logDir "cloudflared-static.out.log"),
    (Join-Path $logDir "cloudflared-static.err.log"),
    (Join-Path $logDir "localhostrun-static.out.log"),
    (Join-Path $logDir "localhostrun-static.err.log"),
    (Join-Path $logDir "localhostrun-tunnel.out.log"),
    (Join-Path $logDir "localhostrun-tunnel.err.log"),
    (Join-Path $logDir "public-tunnel.out.log"),
    (Join-Path $logDir "public-tunnel.err.log"),
    (Join-Path $logDir "pinggy-tunnel.out.log"),
    (Join-Path $logDir "pinggy-tunnel.err.log"),
    (Join-Path $logDir "public-static.out.log"),
    (Join-Path $logDir "public-static.err.log")
)

New-Item -Path $logDir -ItemType Directory -Force | Out-Null
New-Item -Path $toolDir -ItemType Directory -Force | Out-Null
New-Item -Path $mirrorDir -ItemType Directory -Force | Out-Null

Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -match "uvicorn app\.main:app" -and
        $_.CommandLine -match "--port $BackendPort"
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -match "http\.server 4173"
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "cloudflared.exe" -and
        $_.CommandLine -match "127\.0\.0\.1:(4173|$BackendPort)"
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "ssh.exe" -and $_.CommandLine -match "localhost\.run" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

foreach ($file in @($backendOut, $backendErr, $tunnelOut, $tunnelErr, $urlFile) + $legacyLogs) {
    if (Test-Path $file) {
        try {
            Remove-Item $file -Force
        }
        catch {
            Clear-Content -Path $file -ErrorAction SilentlyContinue
        }
    }
}

if (!(Test-Path (Join-Path $backendDir ".env"))) {
    Copy-Item (Join-Path $backendDir ".env.example") (Join-Path $backendDir ".env")
}

if (Test-Path $mirrorDir) {
    Get-ChildItem -Path $mirrorDir -Force |
        Where-Object { $_.Name -ne "node_modules" } |
        Remove-Item -Recurse -Force
}

Get-ChildItem -Path $frontendDir -Force |
    Where-Object { $_.Name -notin @("node_modules", "dist") } |
    Copy-Item -Destination $mirrorDir -Recurse -Force

$mirrorPublicDir = Join-Path $mirrorDir "public"
New-Item -Path $mirrorPublicDir -ItemType Directory -Force | Out-Null

$preferredLogoSource = "C:\Users\Gaming\Desktop\ChatGPT Image Jul 7, 2026, 10_36_59 PM.png"
$fallbackLogoSource = Join-Path $env:TEMP "authorized-network-assessment-frontend\public\game-logo.png"
$logoSource = if (Test-Path $preferredLogoSource) { $preferredLogoSource } elseif (Test-Path $fallbackLogoSource) { $fallbackLogoSource } else { $null }
if ($logoSource) {
    Copy-Item -LiteralPath $logoSource -Destination (Join-Path $mirrorPublicDir "game-logo.png") -Force
}

Push-Location $mirrorDir
try {
    npm.cmd install --no-audit --no-fund | Out-Null
    npm.cmd run build | Out-Null
}
finally {
    Pop-Location
}

$distDir = Join-Path $mirrorDir "dist"
$distIndex = Join-Path $distDir "index.html"
if (!(Test-Path $distIndex)) {
    throw "Frontend build output was not created."
}

$null = Ensure-Cloudflared -DestinationPath $cloudflaredPath
$env:FRONTEND_DIST_DIR = $distDir

Start-Process `
    -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $BackendHost, "--port", "$BackendPort" `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendOut `
    -RedirectStandardError $backendErr | Out-Null

if (-not (Wait-ForUrl -Url "http://$BackendHost`:$BackendPort/health" -MaxAttempts 40 -DelaySeconds 1)) {
    throw "Backend server did not start."
}

$publicHost = $null
for ($tunnelAttempt = 0; $tunnelAttempt -lt 3; $tunnelAttempt += 1) {
    foreach ($path in @($tunnelOut, $tunnelErr)) {
        if (Test-Path $path) {
            Clear-Content -Path $path
        }
    }

    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "cloudflared.exe" -and
            $_.CommandLine -match "127\.0\.0\.1:$BackendPort"
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

    Start-Process `
        -FilePath $cloudflaredPath `
        -ArgumentList "tunnel", "--url", "http://127.0.0.1:$BackendPort", "--no-autoupdate", "--loglevel", "info" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $tunnelOut `
        -RedirectStandardError $tunnelErr | Out-Null

    $candidate = $null
    for ($attempt = 0; $attempt -lt 60; $attempt += 1) {
        Start-Sleep -Seconds 1
        $content = ""
        foreach ($path in @($tunnelOut, $tunnelErr)) {
            if (Test-Path $path) {
                $content += "`n" + (Get-Content -Raw $path)
            }
        }

        $match = [regex]::Match($content, 'https://[a-z0-9.-]+\.trycloudflare\.com')
        if ($match.Success) {
            $candidate = $match.Value.Trim().TrimEnd('/').ToLowerInvariant()
            break
        }
    }

    if ($candidate -and (Wait-ForUrl -Url "$candidate/minu" -MaxAttempts 12 -DelaySeconds 2)) {
        $publicHost = $candidate
        break
    }
}

if (!$publicHost) {
    throw "Could not detect a working public URL. Check $tunnelOut and $tunnelErr."
}

$publicMinuUrl = "$publicHost/minu"
Set-Content -Path $urlFile -Value $publicMinuUrl -NoNewline

Write-Output "Public game URL: $publicMinuUrl"
Write-Output "Saved to:         $urlFile"
Write-Output "Backend:          http://$BackendHost`:$BackendPort"
