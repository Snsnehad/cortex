<#
.SYNOPSIS
    Cortex developer commands (TASK-01.08). Windows PowerShell 5.1+ compatible.

.DESCRIPTION
    Run from anywhere:
        scripts\dev.cmd <command> [options]        (works under any execution policy)
        .\scripts\dev.ps1 <command> [options]      (needs a policy that allows local scripts)

    Commands
        help                  Show this help.
        up [-App]             Start PostgreSQL, Redis, Qdrant and wait until healthy.
                              -App also builds and starts the containerised API (:18000).
        down                  Stop all Cortex containers. Data is KEPT.
        ps                    Show container status.
        logs [service] [-Follow]
                              Show logs (all services, or postgres|redis|qdrant|api).
        api [-Port 8000]      Run the API on the host with auto-reload.
        test [pytest args]    Run the backend test suite, e.g.: test -m "not integration"
        lint                  ruff check + ruff format --check + mypy (what CI runs).
        format                Apply ruff formatting and safe lint fixes.
        check                 lint, then test: everything CI checks.
        migrate [revision]    Apply migrations: latest by default; "-1" = one step back,
                              "base" = remove all, or a revision id.
        revision "message"    Autogenerate a migration from model changes (review it!).
        reset                 DESTRUCTIVE: delete all local Cortex data (volumes).
                              Asks you to type a confirmation phrase.
#>
# No param() block on purpose: PowerShell parameter binding would capture pytest flags
# (-m -> unknown parameter, -p -> -Port, -v -> -Verbose). Arguments are parsed by hand and
# everything not meant for this script is passed through untouched.

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Commands = @("help", "up", "down", "ps", "logs", "api", "test", "lint", "format", "check", "migrate", "revision", "reset")
$Command = "help"
$App = $false
$Follow = $false
$Port = 8000
$Rest = @()

$remaining = @($args)
if ($remaining.Count -gt 0) {
    $Command = [string]$remaining[0]
    $remaining = @($remaining | Select-Object -Skip 1)
}
if ($Commands -notcontains $Command) {
    Write-Host "Unknown command '$Command'. Valid: $($Commands -join ', ')" -ForegroundColor Red
    exit 2
}
# test/check forward every remaining argument to pytest verbatim.
if (@("test", "check") -contains $Command) {
    $Rest = $remaining
}
else {
    for ($i = 0; $i -lt $remaining.Count; $i++) {
        $arg = [string]$remaining[$i]
        if ($arg -eq "-App") { $App = $true }
        elseif ($arg -eq "-Follow") { $Follow = $true }
        elseif ($arg -eq "-Port" -and $i + 1 -lt $remaining.Count) { $i++; $Port = [int]$remaining[$i] }
        else { $Rest += $arg }
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$ComposeFile = Join-Path $RepoRoot "infra\compose\docker-compose.yml"
$EnvFile = Join-Path $RepoRoot ".env"
$ResetPhrase = "delete cortex data"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Native {
    # Runs a native command and stops on a non-zero exit code (PowerShell 5.1 does not).
    param([Parameter(Mandatory = $true)][string]$FilePath, [string[]]$Arguments = @())
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "'$FilePath $($Arguments -join ' ')' failed with exit code $LASTEXITCODE"
    }
}

function Get-ComposeArgs([switch]$WithAppProfile) {
    $composeArgs = @("compose", "-f", $ComposeFile)
    if (Test-Path $EnvFile) { $composeArgs += @("--env-file", $EnvFile) }
    # The api service lives in the "app" profile; include it so down/ps/logs see it too.
    if ($WithAppProfile) { $composeArgs += @("--profile", "app") }
    return $composeArgs
}

function Assert-Docker {
    docker info --format "{{.ServerVersion}}" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not running. Start Docker Desktop and try again."
    }
}

function Invoke-InBackend([string]$FilePath, [string[]]$Arguments) {
    Push-Location $BackendDir
    try { Invoke-Native -FilePath $FilePath -Arguments $Arguments }
    finally { Pop-Location }
}

function Invoke-Lint {
    Write-Step "ruff check"
    Invoke-InBackend "uv" @("run", "ruff", "check")
    Write-Step "ruff format --check"
    Invoke-InBackend "uv" @("run", "ruff", "format", "--check")
    Write-Step "mypy (strict)"
    Invoke-InBackend "uv" @("run", "mypy")
}

function Invoke-Tests([string[]]$PytestArgs) {
    Write-Step "pytest $($PytestArgs -join ' ')"
    Invoke-InBackend "uv" (@("run", "pytest") + $PytestArgs)
}

try {
    switch ($Command) {
        "help" {
            Get-Help $PSCommandPath -Full | Out-String -Stream |
                Where-Object { $_ -notmatch "^\s*(PARAMETERS|INPUTS|OUTPUTS|RELATED LINKS|REMARKS)" } |
                Select-Object -First 40 | ForEach-Object { Write-Host $_ }
        }
        "up" {
            Assert-Docker
            $composeArgs = Get-ComposeArgs -WithAppProfile:$App
            $composeArgs += @("up", "-d", "--wait")
            if ($App) { $composeArgs += "--build" }
            Write-Step "Starting infrastructure$(if ($App) { ' + API container' })"
            Invoke-Native "docker" $composeArgs
            Invoke-Native "docker" ((Get-ComposeArgs -WithAppProfile) + @("ps"))
            Write-Host ""
            Write-Host "PostgreSQL 127.0.0.1:15432 | Redis 127.0.0.1:16379 | Qdrant http://127.0.0.1:16333"
            if ($App) { Write-Host "API (container) http://127.0.0.1:18000/api/v1/docs" }
        }
        "down" {
            Assert-Docker
            Write-Step "Stopping Cortex containers (data is kept)"
            Invoke-Native "docker" ((Get-ComposeArgs -WithAppProfile) + @("down"))
        }
        "ps" {
            Assert-Docker
            Invoke-Native "docker" ((Get-ComposeArgs -WithAppProfile) + @("ps"))
        }
        "logs" {
            Assert-Docker
            $logArgs = (Get-ComposeArgs -WithAppProfile) + @("logs", "--tail", "200")
            if ($Follow) { $logArgs += "--follow" }
            $logArgs += $Rest
            Invoke-Native "docker" $logArgs
        }
        "api" {
            Write-Step "API on http://127.0.0.1:$Port/api/v1/docs (Ctrl+C to stop)"
            # --reload makes uvicorn use the selector event loop psycopg needs on Windows.
            Invoke-InBackend "uv" @(
                "run", "uvicorn", "app.main:create_app", "--factory", "--reload",
                "--host", "127.0.0.1", "--port", "$Port"
            )
        }
        "test" { Invoke-Tests $Rest }
        "migrate" {
            $target = "head"
            if ($Rest.Count -gt 0) { $target = [string]$Rest[0] }
            # Alembic moves backwards only with "downgrade" (relative "-N" or "base").
            $direction = "upgrade"
            if ($target -eq "base" -or $target.StartsWith("-")) { $direction = "downgrade" }
            Write-Step "alembic $direction $target"
            Invoke-InBackend "uv" @("run", "alembic", $direction, $target)
            Invoke-InBackend "uv" @("run", "alembic", "current")
        }
        "revision" {
            if ($Rest.Count -eq 0) { throw 'A message is required: revision "add documents table"' }
            $message = ($Rest -join " ")
            Write-Step "alembic revision --autogenerate -m `"$message`""
            Invoke-InBackend "uv" @("run", "alembic", "revision", "--autogenerate", "-m", $message)
            Write-Host "Review the new file in backend\migrations\versions before committing." -ForegroundColor Yellow
        }
        "lint" { Invoke-Lint }
        "format" {
            Write-Step "ruff format"
            Invoke-InBackend "uv" @("run", "ruff", "format")
            Write-Step "ruff check --fix"
            Invoke-InBackend "uv" @("run", "ruff", "check", "--fix")
        }
        "check" {
            Invoke-Lint
            Invoke-Tests $Rest
            Write-Host ""
            Write-Host "All checks passed." -ForegroundColor Green
        }
        "reset" {
            Assert-Docker
            Write-Host "This permanently deletes ALL local Cortex data:" -ForegroundColor Yellow
            Write-Host "  volumes cortex_postgres_data, cortex_redis_data, cortex_qdrant_data, cortex_storage_data" -ForegroundColor Yellow
            Write-Host "Type '$ResetPhrase' to continue, anything else to abort:"
            # [Console]::ReadLine (not Read-Host) so the answer can also be piped in.
            $answer = [Console]::ReadLine()
            if ($answer -ne $ResetPhrase) {
                Write-Host "Aborted. Nothing was deleted."
                exit 1
            }
            Write-Step "Removing containers and volumes"
            Invoke-Native "docker" ((Get-ComposeArgs -WithAppProfile) + @("down", "--volumes"))
            Write-Host "Done. Run 'up' to start with empty databases."
        }
    }
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
exit 0
