[CmdletBinding()]
param(
    [string]$RemoteUrl = "",
    [string]$Branch = "",
    [string]$CommitMessage = "",
    [string]$Scope = "minugame",
    [string]$Project = "minu",
    [string]$HealthPath = "/health",
    [switch]$SkipPush,
    [switch]$SkipDeploy,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$deployScript = Join-Path $PSScriptRoot "deploy-vercel-prod.ps1"

if (-not (Test-Path $deployScript)) {
    throw "Deploy script not found at $deployScript"
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed."
    }
}

function Test-HasHeadCommit {
    & git rev-parse --verify HEAD *> $null
    return $LASTEXITCODE -eq 0
}

function Get-UpstreamBranch {
    $upstream = (& git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        return ""
    }

    return $upstream
}

function Get-DeployUrlFromOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string]$OutputText
    )

    $matches = [regex]::Matches($OutputText, "https://[A-Za-z0-9.-]+\.vercel\.app")
    if ($matches.Count -eq 0) {
        return ""
    }

    return $matches[$matches.Count - 1].Value
}

Push-Location $repoRoot
try {
    $currentBranch = $Branch.Trim()
    if (-not $currentBranch) {
        $currentBranch = (& git branch --show-current | Out-String).Trim()
    }
    if (-not $currentBranch) {
        throw "Could not determine the current git branch."
    }

    $originUrl = (& git config --get remote.origin.url | Out-String).Trim()
    $requestedRemoteUrl = $RemoteUrl.Trim()
    if (-not $originUrl) {
        if (-not $requestedRemoteUrl -and -not $SkipPush) {
            throw "No GitHub remote is configured. Re-run with -RemoteUrl https://github.com/<you>/<repo>.git"
        }
        elseif ($requestedRemoteUrl) {
            Invoke-Git -Arguments @("remote", "add", "origin", $requestedRemoteUrl)
            $originUrl = $requestedRemoteUrl
        }
    }
    elseif ($requestedRemoteUrl -and $requestedRemoteUrl -ne $originUrl) {
        Invoke-Git -Arguments @("remote", "set-url", "origin", $requestedRemoteUrl)
        $originUrl = $requestedRemoteUrl
    }

    $statusOutput = (& git status --porcelain | Out-String)
    $hasChanges = -not [string]::IsNullOrWhiteSpace($statusOutput)
    $hasHeadCommit = Test-HasHeadCommit

    if ($hasChanges) {
        $trimmedMessage = $CommitMessage.Trim()
        if (-not $trimmedMessage) {
            throw "Working tree has changes. Re-run with -CommitMessage 'your message' so the workflow can commit, push, deploy, and verify in one flow."
        }

        Invoke-Git -Arguments @("add", "-A")
        & git commit -m $trimmedMessage
        if ($LASTEXITCODE -ne 0) {
            throw "git commit failed."
        }
    }
    elseif (-not $hasHeadCommit) {
        throw "This repository has no commit yet. Re-run with -CommitMessage 'Initial commit' to create the first commit before pushing."
    }

    $pushMode = "skipped"
    if (-not $SkipPush) {
        $upstream = Get-UpstreamBranch
        if (-not $upstream) {
            Invoke-Git -Arguments @("push", "-u", "origin", $currentBranch)
        }
        else {
            Invoke-Git -Arguments @("push", "origin", $currentBranch)
        }
        $pushMode = "done"
    }

    $deployUrl = ""
    $deployMode = "skipped"
    if (-not $SkipDeploy) {
        & npx.cmd vercel whoami *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Vercel CLI is not authenticated on this machine."
        }

        $deployOutput = & $deployScript -Scope $Scope -Project $Project 2>&1
        if ($LASTEXITCODE -ne 0) {
            $joinedError = ($deployOutput | ForEach-Object { $_.ToString() }) -join "`n"
            throw "Vercel deployment failed.`n$joinedError"
        }

        $joinedOutput = ($deployOutput | ForEach-Object { $_.ToString() }) -join "`n"
        $deployUrl = Get-DeployUrlFromOutput -OutputText $joinedOutput
        $deployMode = "done"
    }

    $verifyMode = "skipped"
    if (-not $SkipVerify -and $deployUrl) {
        $healthUrl = "{0}{1}" -f $deployUrl.TrimEnd("/"), $HealthPath
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 30
            if ($health.status -ne "ok") {
                throw "Unexpected health response."
            }
        }
        catch {
            $rootResponse = Invoke-WebRequest -Uri $deployUrl -TimeoutSec 30
            if ($rootResponse.StatusCode -lt 200 -or $rootResponse.StatusCode -ge 400) {
                throw "Live site verification failed for $deployUrl"
            }
        }

        $verifyMode = "done"
    }

    Write-Host ""
    Write-Host "GitHub remote: $originUrl"
    Write-Host "Branch: $currentBranch"
    Write-Host "Push: $pushMode"
    Write-Host "Deploy: $deployMode"
    Write-Host "Verify: $verifyMode"
    if ($deployUrl) {
        Write-Host "Live URL: $deployUrl"
    }
}
finally {
    Pop-Location
}
