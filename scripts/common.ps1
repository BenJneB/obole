# Obole - fonctions communes aux scripts Windows (charge par start-server.ps1,
# update-server.ps1 et install-autostart.ps1 avec : . "$PSScriptRoot\common.ps1").
# Messages en ASCII sans accents : PowerShell 5.1 lit mal l'UTF-8 sans BOM.

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TaskName = "Obole - serveur"
$StartupShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Obole - serveur.lnk"
$StartScript = Join-Path $PSScriptRoot "start-server.ps1"
# Lancement du serveur en arriere-plan (tache planifiee, raccourci de demarrage, relance)
$BackgroundArguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`" -Background"

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Info([string]$Text) { Write-Host "    $Text" }
function Write-Ok([string]$Text) { Write-Host "    [OK] $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "    [ATTENTION] $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text) { Write-Host "    [ECHEC] $Text" -ForegroundColor Red }

function Get-EnvFileValue([string]$Key, [string]$Default) {
    # Lit une valeur de .env sans l'afficher ; $Default si absente.
    $file = Join-Path $RepoRoot ".env"
    if (Test-Path $file) {
        $line = Get-Content $file | Where-Object { $_ -match "^\s*$Key=" } | Select-Object -Last 1
        if ($line) {
            $value = ($line -replace "^\s*$Key=", "").Trim().Trim('"').Trim("'")
            if ($value) { return $value }
        }
    }
    return $Default
}

function Get-ServerAddress {
    # Adresse d'ecoute du serveur (HOST / PORT de .env)
    return @{
        Host = Get-EnvFileValue "HOST" "127.0.0.1"
        Port = [int](Get-EnvFileValue "PORT" "8000")
    }
}

# --- Alimentation -------------------------------------------------------------------

function Get-AcPowerValue([string]$SubGroup, [string]$Setting) {
    # Valeur "secteur" d'un reglage : avant-derniere valeur hexadecimale de
    # 'powercfg /query' (la derniere est la valeur batterie). Independant de la langue.
    $out = powercfg /query SCHEME_CURRENT $SubGroup $Setting 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $hex = [regex]::Matches(($out -join "`n"), "0x[0-9a-fA-F]+") | ForEach-Object { $_.Value }
    if ($hex.Count -lt 2) { return $null }
    return [Convert]::ToInt64($hex[$hex.Count - 2], 16)
}

function Test-PowerSettings {
    # Affiche un avertissement bien visible si le PC risque de se mettre en veille.
    # Renvoie $true si tout est bon.
    $standby = Get-AcPowerValue "SUB_SLEEP" "STANDBYIDLE"
    $lid = Get-AcPowerValue "SUB_BUTTONS" "LIDACTION"
    $problems = @()
    if ($standby -ne 0) {
        $shown = if ($null -eq $standby) { "inconnue" } else { "$([int]($standby / 60)) min" }
        $problems += "Mise en veille sur secteur : $shown (il faut : Jamais)"
    }
    if ($lid -ne 0) { $problems += "Fermer le capot sur secteur : ne fait pas 'Rien' (il faut : Ne rien faire)" }
    $status = Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($status -and $status.BatteryStatus -eq 1) { $problems += "Le PC est sur batterie : branche-le sur secteur" }

    if ($problems.Count -eq 0) {
        Write-Ok "Alimentation : pas de mise en veille sur secteur, capot sans effet"
        return $true
    }
    Write-Host ""
    Write-Host "    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Yellow
    Write-Host "    !! Le serveur risque de s'arreter (mise en veille du PC) :" -ForegroundColor Yellow
    foreach ($p in $problems) { Write-Host "    !!   - $p" -ForegroundColor Yellow }
    Write-Host "    !! Correction : setup-windows.ps1 (etape Alimentation), ou Parametres >" -ForegroundColor Yellow
    Write-Host "    !! Systeme > Alimentation, et Panneau de configuration > Options" -ForegroundColor Yellow
    Write-Host "    !! d'alimentation > Choisir l'action qui suit la fermeture du capot." -ForegroundColor Yellow
    Write-Host "    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Yellow
    Write-Host ""
    return $false
}

function Show-DeployReminder {
    Write-Host ""
    Write-Host "    RAPPEL a chaque mise a jour :" -ForegroundColor Magenta
    Write-Host "      - PC branche sur secteur ;" -ForegroundColor Magenta
    Write-Host "      - mise en veille sur secteur : Jamais ;" -ForegroundColor Magenta
    Write-Host "      - fermer le capot : Ne rien faire ;" -ForegroundColor Magenta
    Write-Host "      - apres la premiere identification, 'ollama ps' doit afficher 100% GPU." -ForegroundColor Magenta
}

# --- Processus du serveur ------------------------------------------------------------

function Get-ListeningProcessId([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return $null
}

function Stop-OboleServer([int]$Port) {
    # Arrete le serveur : d'abord les scripts start-server.ps1 (sinon leur boucle le
    # relancerait), puis le processus qui ecoute sur le port et son parent "uv".
    $stopped = $false
    $scripts = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'pwsh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*start-server.ps1*" -and $_.ProcessId -ne $PID }
    foreach ($p in $scripts) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped = $true
    }
    $listener = Get-ListeningProcessId $Port
    if ($listener) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $listener" -ErrorAction SilentlyContinue
        if ($proc) {
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.ParentProcessId)" -ErrorAction SilentlyContinue
            Stop-Process -Id $listener -Force -ErrorAction SilentlyContinue
            if ($parent -and $parent.Name -eq "uv.exe") {
                Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue
            }
            $stopped = $true
        }
    }
    # Attendre que le port soit libere
    for ($i = 0; $i -lt 20 -and (Get-ListeningProcessId $Port); $i++) { Start-Sleep -Milliseconds 500 }
    return $stopped
}

function Start-OboleServer([switch]$Background) {
    # Lance le serveur : via la tache planifiee si elle existe, sinon en arriere-plan
    # (-Background), sinon dans une nouvelle fenetre a laisser ouverte.
    # Renvoie "task", "background" ou "window".
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $TaskName
        return "task"
    }
    if ($Background) {
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList $BackgroundArguments
        return "background"
    }
    Start-Process powershell.exe -ArgumentList "-NoProfile -NoExit -ExecutionPolicy Bypass -File `"$StartScript`""
    return "window"
}

function Wait-OboleHealth([int]$Port, [int]$TimeoutSeconds = 90) {
    # Attend que /api/health reponde ; renvoie la reponse ou $null.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 10
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $null
}
