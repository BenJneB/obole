<#
.SYNOPSIS
    Obole - lance le serveur (backend FastAPI + appli compilee) sur ce PC Windows.

.DESCRIPTION
    - Verifie les reglages d'alimentation et avertit si le PC risque de se mettre en veille.
    - Empeche la mise en veille tant que le serveur tourne (SetThreadExecutionState).
    - Ecoute sur HOST:PORT de .env (defaut 127.0.0.1:8000). Acces depuis le telephone :
      'tailscale serve --bg 8000' (une seule fois).
    - Option -Background (utilisee par le demarrage automatique) : journal dans
      data\logs\ et relance automatique si le serveur s'arrete sur une erreur.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\start-server.ps1
#>
[CmdletBinding()]
param(
    [switch]$Background
)

$ErrorActionPreference = "Continue"
. "$PSScriptRoot\common.ps1"

$address = Get-ServerAddress
$port = $address.Port
$backend = Join-Path $RepoRoot "backend"
$dataDir = Get-EnvFileValue "DATA_DIR" "./data"
if (-not [System.IO.Path]::IsPathRooted($dataDir)) { $dataDir = Join-Path $RepoRoot $dataDir }
$logDir = Join-Path $dataDir "logs"

Write-Host "Obole - demarrage du serveur" -ForegroundColor White
Write-Step "Verifications"
Test-PowerSettings | Out-Null

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Fail "uv introuvable : lancer d'abord scripts\setup-windows.ps1"
    exit 1
}
if (-not (Test-Path (Join-Path $RepoRoot "frontend\dist\index.html"))) {
    Write-Warn "Appli non compilee (frontend\dist absent) : seule l'API sera disponible."
    Write-Info "Lancer scripts\update-server.ps1 pour la compiler."
}
$existing = Get-ListeningProcessId $port
if ($existing) {
    Write-Ok "Un serveur ecoute deja sur le port $port (processus $existing) : rien a faire."
    Write-Info "Pour le redemarrer : scripts\update-server.ps1"
    exit 0
}

# Empeche la mise en veille du systeme tant que ce script (et donc le serveur) tourne.
Add-Type -Namespace Obole -Name Power -MemberDefinition @"
[DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
"@
$ES_CONTINUOUS = [uint32]"0x80000000"
$ES_SYSTEM_REQUIRED = [uint32]"0x00000001"
[Obole.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED) | Out-Null

$env:PYTHONUTF8 = "1"  # accents corrects dans les journaux
$uvArgs = @("run", "--no-dev", "uvicorn", "app.main:app", "--host", $address.Host, "--port", "$port")

try {
    if (-not $Background) {
        Write-Step "Serveur sur http://$($address.Host):$port (Ctrl+C pour arreter)"
        Push-Location $backend
        try { & uv @uvArgs } finally { Pop-Location }
        exit $LASTEXITCODE
    }

    # Mode arriere-plan : journaux dates, relance apres un arret anormal
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Get-ChildItem $logDir -Filter "server-*.log" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force
    $restarts = 0
    while ($true) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $outLog = Join-Path $logDir "server-$stamp.log"
        $errLog = Join-Path $logDir "server-$stamp-err.log"
        $proc = Start-Process -FilePath "uv" -ArgumentList $uvArgs -WorkingDirectory $backend -NoNewWindow `
            -PassThru -Wait -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        if ($proc.ExitCode -eq 0) { break }
        $restarts++
        if ($restarts -gt 5) { break }  # erreur persistante : ne pas boucler indefiniment
        Start-Sleep -Seconds 15
    }
} finally {
    [Obole.Power]::SetThreadExecutionState($ES_CONTINUOUS) | Out-Null
}
