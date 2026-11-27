<#
.SYNOPSIS
    Obole - met a jour le serveur Windows : git pull, dependances, compilation de l'appli,
    redemarrage.

.DESCRIPTION
    1. git pull (refuse si des fichiers suivis ont ete modifies localement).
    2. uv sync (dependances Python figees par backend\uv.lock).
    3. npm ci puis npm run build (appli du telephone, dans frontend\dist).
    4. Arret du serveur s'il tourne, puis redemarrage : via la tache planifiee ou le
       raccourci de demarrage s'ils existent (install-autostart.ps1), sinon dans une
       nouvelle fenetre PowerShell.
    5. Attend que /api/health reponde et affiche l'etat d'Ollama et du quota Numista.

.PARAMETER NoPull
    Ne fait pas 'git pull' (utile pour recompiler et redemarrer seulement).

.PARAMETER NoRestart
    Ne redemarre pas le serveur.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\update-server.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoPull,
    [switch]$NoRestart
)

$ErrorActionPreference = "Continue"
. "$PSScriptRoot\common.ps1"

$port = (Get-ServerAddress).Port
$failed = $false

function Invoke-Native([string]$Label, [string]$Directory, [scriptblock]$Command) {
    # Lance une commande externe et verifie son code de sortie.
    Push-Location $Directory
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "$Label (code $LASTEXITCODE)"
            return $false
        }
        Write-Ok $Label
        return $true
    } finally {
        Pop-Location
    }
}

Write-Host "Obole - mise a jour du serveur" -ForegroundColor White
Write-Host "Depot : $RepoRoot"
Show-DeployReminder

Write-Step "1. Alimentation"
Test-PowerSettings | Out-Null

foreach ($tool in @("git", "uv", "npm")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Fail "$tool introuvable : lancer d'abord scripts\setup-windows.ps1"
        exit 1
    }
}

if (-not $NoPull) {
    Write-Step "2. Recuperation du code (git pull)"
    $dirty = git -C $RepoRoot status --porcelain --untracked-files=no
    if ($dirty) {
        Write-Fail "Des fichiers suivis ont ete modifies sur ce PC :"
        $dirty | ForEach-Object { Write-Info $_ }
        Write-Info "Les annuler (git checkout -- <fichier>) ou les garder de cote, puis relancer."
        exit 1
    }
    if (-not (Invoke-Native "git pull" $RepoRoot { git pull --ff-only })) {
        Write-Info "Branche divergente ? Verifier avec 'git status' (le serveur ne doit pas avoir de commits propres)."
        exit 1
    }
}

Write-Step "3. Dependances Python (uv sync)"
if (-not (Invoke-Native "uv sync" (Join-Path $RepoRoot "backend") { uv sync --locked --no-dev })) { exit 1 }

Write-Step "4. Compilation de l'appli (npm ci, npm run build)"
$frontend = Join-Path $RepoRoot "frontend"
if (-not (Invoke-Native "npm ci" $frontend { npm ci --no-audit --no-fund })) { exit 1 }
if (-not (Invoke-Native "npm run build" $frontend { npm run build })) { exit 1 }

if ($NoRestart) {
    Write-Step "Termine (serveur non redemarre : option -NoRestart)"
    exit 0
}

Write-Step "5. Redemarrage du serveur"
if (Stop-OboleServer $port) { Write-Ok "Ancien serveur arrete" } else { Write-Info "Aucun serveur en cours" }

$startScript = Join-Path $PSScriptRoot "start-server.ps1"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Info "Relance via la tache planifiee '$TaskName' (journaux : data\logs\)"
} elseif (Test-Path $StartupShortcut) {
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$startScript`"", "-Background")
    Write-Info "Relance en arriere-plan (journaux : data\logs\)"
} else {
    Start-Process powershell.exe -ArgumentList @(
        "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$startScript`"")
    Write-Info "Relance dans une nouvelle fenetre (la laisser ouverte)."
    Write-Info "Demarrage automatique : scripts\install-autostart.ps1"
}

$health = Wait-OboleHealth $port
if (-not $health) {
    Write-Fail "Le serveur ne repond pas sur http://127.0.0.1:$port apres 90 s (voir data\logs\ ou la fenetre du serveur)"
    exit 1
}
Write-Ok "Serveur en ligne : http://127.0.0.1:$port"
if ($health.ollama.ok) {
    Write-Ok "IA locale prete ($($health.ollama.model))"
} else {
    Write-Warn "IA locale indisponible : $($health.ollama.error)"
    $failed = $true
}
if ($health.numista.configured) {
    Write-Ok "Numista : $($health.numista.used) / $($health.numista.limit) requetes ce mois-ci"
} else {
    Write-Warn "Numista : NUMISTA_API_KEY absente de .env"
    $failed = $true
}
Show-DeployReminder
if ($failed) { exit 2 }
