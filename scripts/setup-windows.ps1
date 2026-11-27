<#
.SYNOPSIS
    Obole - installation / verification du serveur Windows 11.

.DESCRIPTION
    Script idempotent : il verifie chaque outil et n'installe que ce qui manque.
    Il peut etre relance sans risque et se termine par un resume
    (OK / INSTALLE / A FAIRE / ECHEC).

    Messages volontairement en ASCII sans accents : PowerShell 5.1 lit mal
    les fichiers UTF-8 sans BOM.

.PARAMETER Model
    Modele vision Ollama a telecharger (defaut : gemma4:e4b).

.PARAMETER RemoteOllama
    Rend Ollama joignable depuis le poste de dev Ubuntu via Tailscale
    (OLLAMA_HOST=0.0.0.0) et cree des regles de pare-feu qui limitent le port
    11434 aux adresses Tailscale (demande l'elevation administrateur pour
    cette seule etape).

.PARAMETER Yes
    Repond "oui" automatiquement aux confirmations (reglages d'alimentation).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1 -RemoteOllama -Model gemma3:4b
#>
[CmdletBinding()]
param(
    [string]$Model = "gemma4:e4b",
    [switch]$RemoteOllama,
    [switch]$Yes
)

$ErrorActionPreference = "Continue"
. "$PSScriptRoot\common.ps1"  # $RepoRoot, Write-Step/Info, Get-EnvFileValue, Get-AcPowerValue...
$OllamaApi = "http://localhost:11434"
$FirewallRuleAllow = "Obole - Ollama 11434 (Tailscale uniquement)"
$FirewallRuleBlock = "Obole - Ollama 11434 (bloquer hors Tailscale)"

# --- Affichage et resume -----------------------------------------------------
$Summary = @{ Ok = @(); Installed = @(); Todo = @(); Failed = @() }

function Add-Ok([string]$Text)        { Write-Host "    [OK] $Text" -ForegroundColor Green;       $script:Summary.Ok += $Text }
function Add-Installed([string]$Text) { Write-Host "    [INSTALLE] $Text" -ForegroundColor Green; $script:Summary.Installed += $Text }
function Add-Todo([string]$Text)      { Write-Host "    [A FAIRE] $Text" -ForegroundColor Yellow; $script:Summary.Todo += $Text }
function Add-Failed([string]$Text)    { Write-Host "    [ECHEC] $Text" -ForegroundColor Red;      $script:Summary.Failed += $Text }

function Test-Command([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

# Recharge le PATH de la session a partir des variables Machine + Utilisateur
# (necessaire juste apres une installation winget).
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ";"
}

function Confirm-Action([string]$Question) {
    if ($Yes) { return $true }
    $answer = Read-Host "    $Question [o/N]"
    return ($answer -match '^(o|oui|y|yes)$')
}

function Test-OllamaUp {
    try { Invoke-RestMethod -Uri "$OllamaApi/api/version" -TimeoutSec 3 | Out-Null; return $true }
    catch { return $false }
}

function Wait-Ollama([int]$Seconds = 60) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-OllamaUp) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

Write-Host "Obole - preparation du serveur Windows" -ForegroundColor White
Write-Host "Depot  : $RepoRoot"
Write-Host "Modele : $Model"
if ($RemoteOllama) { Write-Host "Option : -RemoteOllama" }

# =============================================================================
# 1. Outils via winget
# =============================================================================
Write-Step "1. Outils (winget)"
$Packages = @(
    @{ Id = "Git.Git";             Cmd = "git";       Name = "Git" },
    @{ Id = "astral-sh.uv";        Cmd = "uv";        Name = "uv" },
    @{ Id = "OpenJS.NodeJS.LTS";   Cmd = "node";      Name = "Node.js LTS" },
    @{ Id = "GitHub.cli";          Cmd = "gh";        Name = "GitHub CLI" },
    @{ Id = "Ollama.Ollama";       Cmd = "ollama";    Name = "Ollama" },
    @{ Id = "tailscale.tailscale"; Cmd = "tailscale"; Name = "Tailscale" }
)

if (-not (Test-Command "winget")) {
    Add-Failed "winget introuvable : installer 'App Installer' depuis le Microsoft Store, puis relancer"
} else {
    foreach ($p in $Packages) {
        if (Test-Command $p.Cmd) {
            Add-Ok $p.Name
            continue
        }
        # Peut etre installe mais absent du PATH de cette session
        winget list --id $p.Id --exact --accept-source-agreements *> $null
        if ($LASTEXITCODE -eq 0) {
            Update-SessionPath
            if (Test-Command $p.Cmd) { Add-Ok $p.Name; continue }
        }
        Write-Info "Installation de $($p.Name) ($($p.Id))... (Windows peut demander une confirmation UAC)"
        winget install --id $p.Id --exact --silent --accept-package-agreements --accept-source-agreements
        $code = $LASTEXITCODE
        Update-SessionPath
        if (Test-Command $p.Cmd) {
            Add-Installed $p.Name
        } elseif ($code -eq 0) {
            Add-Todo "$($p.Name) installe mais pas encore dans le PATH : ouvrir un nouveau PowerShell et relancer le script"
        } else {
            Add-Failed "$($p.Name) : 'winget install --id $($p.Id)' a echoue (code $code)"
        }
    }
}

# =============================================================================
# 2. Python 3.12 via uv
# =============================================================================
Write-Step "2. Python 3.12 (via uv)"
if (Test-Command "uv") {
    uv python find 3.12 *> $null
    if ($LASTEXITCODE -eq 0) {
        Add-Ok "Python 3.12"
    } else {
        uv python install 3.12
        if ($LASTEXITCODE -eq 0) { Add-Installed "Python 3.12 (via uv)" }
        else { Add-Failed "Python 3.12 : 'uv python install 3.12' a echoue" }
    }
} else {
    Add-Failed "Python 3.12 : uv absent"
}

# =============================================================================
# 3. GPU NVIDIA
# =============================================================================
Write-Step "3. GPU NVIDIA"
if (Test-Command "nvidia-smi") {
    # Sortie capturee d'abord : un "| Select-Object -First 1" directement sur la commande
    # peut l'interrompre et fausser $LASTEXITCODE sous PowerShell 5.1.
    $gpuOut = @(& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1 | ForEach-Object { "$_" })
    $gpuCode = $LASTEXITCODE
    if ($gpuCode -eq 0 -and $gpuOut.Count -gt 0) {
        Add-Ok "GPU : $($gpuOut[0])"
    } else {
        Add-Todo "nvidia-smi a echoue (code $gpuCode) : '$($gpuOut -join ' ')'. Voir README, 'Depannage GPU'"
    }
} else {
    Add-Todo "nvidia-smi introuvable : installer le pilote NVIDIA (nvidia.com/drivers)"
}

# =============================================================================
# 4. Variables d'environnement Ollama
# =============================================================================
Write-Step "4. Variables d'environnement Ollama (utilisateur)"
$OllamaEnvChanged = $false

function Set-UserEnv([string]$Name, [string]$Value) {
    $current = [Environment]::GetEnvironmentVariable($Name, "User")
    if ($current -eq $Value) {
        Add-Ok "$Name=$Value"
    } else {
        [Environment]::SetEnvironmentVariable($Name, $Value, "User")
        Add-Installed "$Name=$Value (avant : '$current')"
        $script:OllamaEnvChanged = $true
    }
    Set-Item -Path "Env:$Name" -Value $Value
}

Set-UserEnv "OLLAMA_KEEP_ALIVE" "30m"

if ($RemoteOllama) {
    Set-UserEnv "OLLAMA_HOST" "0.0.0.0"
} else {
    $currentHost = [Environment]::GetEnvironmentVariable("OLLAMA_HOST", "User")
    if ($currentHost) {
        Write-Info "OLLAMA_HOST=$currentHost deja defini (laisse tel quel ; voir README pour le retirer)."
    }
}

# =============================================================================
# 5. Pare-feu (seulement avec -RemoteOllama)
# =============================================================================
Write-Step "5. Pare-feu Windows pour Ollama"
if (-not $RemoteOllama) {
    Write-Info "Ignore (pas d'option -RemoteOllama) : Ollama n'ecoute que sur localhost."
} else {
    $haveAllow = [bool](Get-NetFirewallRule -DisplayName $FirewallRuleAllow -ErrorAction SilentlyContinue)
    $haveBlock = [bool](Get-NetFirewallRule -DisplayName $FirewallRuleBlock -ErrorAction SilentlyContinue)
    if ($haveAllow -and $haveBlock) {
        Add-Ok "Regles de pare-feu Ollama (port 11434 limite a Tailscale)"
    } else {
        Write-Info "Creation de 2 regles : autoriser 11434 depuis Tailscale (100.64.0.0/10, fd7a:115c:a1e0::/48),"
        Write-Info "bloquer 11434 depuis toute autre adresse. Une fenetre UAC (administrateur) va s'ouvrir."
        # Plages hors Tailscale, a bloquer (les regles de blocage l'emportent sur les autorisations,
        # par exemple celle creee si on clique "Autoriser" dans la popup du pare-feu).
        $cmd = @"
Remove-NetFirewallRule -DisplayName '$FirewallRuleAllow' -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName '$FirewallRuleBlock' -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName '$FirewallRuleAllow' -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress '100.64.0.0/10','fd7a:115c:a1e0::/48' -Action Allow -Profile Any | Out-Null
New-NetFirewallRule -DisplayName '$FirewallRuleBlock' -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress '0.0.0.0-100.63.255.255','100.128.0.0-255.255.255.255','::-fd7a:115c:a1df:ffff:ffff:ffff:ffff:ffff','fd7a:115c:a1e1::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff' -Action Block -Profile Any | Out-Null
"@
        try {
            Start-Process powershell -Verb RunAs -Wait -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $cmd)
        } catch {
            Write-Info "Elevation refusee ou impossible."
        }
        $haveAllow = [bool](Get-NetFirewallRule -DisplayName $FirewallRuleAllow -ErrorAction SilentlyContinue)
        $haveBlock = [bool](Get-NetFirewallRule -DisplayName $FirewallRuleBlock -ErrorAction SilentlyContinue)
        if ($haveAllow -and $haveBlock) {
            Add-Installed "Regles de pare-feu Ollama (port 11434 limite a Tailscale)"
        } else {
            Add-Todo "Regles de pare-feu non creees : Ollama est expose au reseau local ! Voir README, 'Pare-feu Ollama'"
        }
    }
}

# =============================================================================
# 6. Service Ollama et modele
# =============================================================================
Write-Step "6. Ollama et modele $Model"
$OllamaApp = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"

function Start-OllamaApp {
    if (Test-Path $OllamaApp) { Start-Process -FilePath $OllamaApp }
    elseif (Test-Command "ollama") { Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden }
}

if (-not (Test-Command "ollama")) {
    Add-Failed "Ollama absent : etape ignoree"
} else {
    if ($OllamaEnvChanged -and (Test-OllamaUp)) {
        Write-Info "Variables modifiees : redemarrage d'Ollama..."
        Get-Process -Name "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Seconds 2
    }
    if (-not (Test-OllamaUp)) {
        Write-Info "Demarrage d'Ollama..."
        Start-OllamaApp
    }
    if (-not (Wait-Ollama 60)) {
        Add-Failed "Ollama ne repond pas sur $OllamaApi/api/version (le lancer depuis le menu Demarrer)"
    } else {
        $version = (Invoke-RestMethod -Uri "$OllamaApi/api/version").version
        Add-Ok "Ollama $version repond sur $OllamaApi"

        $modelReady = $false
        $modelRef = if ($Model -match ":") { $Model } else { "$($Model):latest" }
        $tags = (Invoke-RestMethod -Uri "$OllamaApi/api/tags").models | ForEach-Object { $_.name }
        if ($tags -contains $modelRef) {
            Add-Ok "Modele $Model deja telecharge"
            $modelReady = $true
        } else {
            Write-Info "Telechargement de $Model (plusieurs Go, patience)..."
            ollama pull $Model
            if ($LASTEXITCODE -eq 0) { Add-Installed "Modele $Model"; $modelReady = $true }
            else { Add-Failed "Telechargement de $Model impossible (verifier le nom sur https://ollama.com/library)" }
        }

        if ($modelReady) {
            Write-Info "Essai du modele (premier chargement en VRAM : quelques secondes)..."
            $sw = [Diagnostics.Stopwatch]::StartNew()
            $body = @{ model = $Model; prompt = "Reply with the single word OK."; stream = $false; options = @{ temperature = 0 } } | ConvertTo-Json
            try {
                $resp = Invoke-RestMethod -Uri "$OllamaApi/api/generate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 300
                $sw.Stop()
                Add-Ok ("Essai du modele : reponse '{0}' en {1:N1} s" -f $resp.response.Trim(), $sw.Elapsed.TotalSeconds)
            } catch {
                Add-Failed "Essai du modele echoue : $($_.Exception.Message)"
            }
            Write-Host ""
            Write-Host "    --- ollama ps ---"
            ollama ps | ForEach-Object { Write-Host "    $_" }
            # Verification automatique : part du modele chargee en VRAM
            try {
                $loaded = (Invoke-RestMethod -Uri "$OllamaApi/api/ps").models | Where-Object { $_.name -eq $modelRef } | Select-Object -First 1
                if ($loaded -and $loaded.size -gt 0) {
                    $pct = [math]::Round(100 * $loaded.size_vram / $loaded.size)
                    if ($pct -ge 100) { Add-Ok "Modele charge a 100% sur le GPU" }
                    else { Add-Todo "Modele charge a $pct% seulement sur le GPU (le reste sur le CPU : lent). Voir README, 'Depannage GPU'" }
                }
            } catch { }
            Write-Host "    >>> Verifier que la colonne PROCESSOR affiche '100% GPU'." -ForegroundColor Yellow
        }
    }
}

# =============================================================================
# 7. Alimentation (PC portable utilise comme serveur)
# =============================================================================
Write-Step "7. Alimentation (sur secteur)"
$standby = Get-AcPowerValue "SUB_SLEEP" "STANDBYIDLE"
$lid = Get-AcPowerValue "SUB_BUTTONS" "LIDACTION"
$standbyOk = ($standby -eq 0)
$lidOk = ($lid -eq 0)

if ($standbyOk) { Add-Ok "Mise en veille sur secteur : jamais" }
if ($lidOk) { Add-Ok "Fermeture du capot sur secteur : ne rien faire" }

if (-not ($standbyOk -and $lidOk)) {
    Write-Info "Reglages actuels : mise en veille sur secteur = $(if ($null -eq $standby) {'inconnu'} else {"$standby s"}), action capot = $(if ($null -eq $lid) {'inconnu'} else {$lid}) (0 = ne rien faire)"
    if (Confirm-Action "Desactiver la mise en veille et l'action 'fermer le capot' sur secteur ?") {
        $okAll = $true
        if (-not $standbyOk) {
            powercfg /change standby-timeout-ac 0
            if ($LASTEXITCODE -ne 0) { $okAll = $false; Write-Info "powercfg /change standby-timeout-ac a echoue (code $LASTEXITCODE)" }
        }
        if (-not $lidOk) {
            powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
            if ($LASTEXITCODE -ne 0) { $okAll = $false; Write-Info "powercfg /setacvalueindex LIDACTION a echoue (code $LASTEXITCODE)" }
        }
        powercfg /setactive SCHEME_CURRENT
        if ($okAll -and (Get-AcPowerValue "SUB_SLEEP" "STANDBYIDLE") -eq 0 -and (Get-AcPowerValue "SUB_BUTTONS" "LIDACTION") -eq 0) {
            Add-Installed "Alimentation : veille sur secteur = jamais, capot = ne rien faire"
        } else {
            Add-Todo "Alimentation : reglage impossible, le faire a la main (voir README, 'Alimentation')"
        }
    } else {
        Add-Todo "Alimentation : desactiver la veille et l'action du capot sur secteur (voir README, 'Alimentation')"
    }
}

# =============================================================================
# 8. Tailscale
# =============================================================================
Write-Step "8. Tailscale"
if (Test-Command "tailscale") {
    tailscale status *> $null
    if ($LASTEXITCODE -eq 0) {
        $dns = $null
        try { $dns = ((tailscale status --json | ConvertFrom-Json).Self.DNSName).TrimEnd(".") } catch { }
        Add-Ok "Tailscale connecte$(if ($dns) { " ($dns)" })"
    } else {
        Add-Todo "Connecter Tailscale : icone Tailscale dans la barre des taches > Log in"
    }
} else {
    Add-Failed "Tailscale absent"
}

# =============================================================================
# 9. Git et GitHub
# =============================================================================
Write-Step "9. Git et GitHub"
if (Test-Command "git") {
    foreach ($field in @("name", "email")) {
        $value = git config --global "user.$field"
        if ($value) {
            Add-Ok "git user.$field = $value"
        } else {
            $value = Read-Host "    git user.$field manquant. Valeur a utiliser"
            if ($value) { git config --global "user.$field" "$value"; Add-Installed "git user.$field configure" }
            else { Add-Todo "git config --global user.$field `"...`"" }
        }
    }
}
if (Test-Command "gh") {
    gh auth status *> $null
    if ($LASTEXITCODE -eq 0) { Add-Ok "gh authentifie sur GitHub" }
    else { Add-Todo "Se connecter a GitHub : gh auth login" }
}

# =============================================================================
# 10. Fichier .env
# =============================================================================
Write-Step "10. Configuration (.env)"
$envFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $RepoRoot ".env.example") $envFile
    if (Test-Path $envFile) { Add-Installed ".env cree a partir de .env.example" }
    else { Add-Failed ".env : copie impossible" }
} else {
    Add-Ok ".env present"
}
if (Get-EnvFileValue "NUMISTA_API_KEY") {
    Add-Ok "NUMISTA_API_KEY renseignee (valeur non affichee)"
} else {
    Add-Todo "Renseigner NUMISTA_API_KEY dans $envFile (notepad .env)"
}
# (PowerShell ignore la casse : ne pas nommer cette variable $model, cela ecraserait $Model)
$envModel = Get-EnvFileValue "VISION_MODEL"
if ($envModel -and $envModel -ne $Model) {
    Add-Todo "VISION_MODEL=$envModel dans .env, mais ce script a prepare $Model : aligner les deux"
}

# =============================================================================
# Resume
# =============================================================================
Write-Host ""
Write-Host "=============================== RESUME ===============================" -ForegroundColor White
function Write-List([string]$Title, $Items, [string]$Color) {
    if ($Items.Count -eq 0) { return }
    Write-Host "$Title ($($Items.Count))" -ForegroundColor $Color
    foreach ($i in $Items) { Write-Host "  - $i" }
}
Write-List "OK" $Summary.Ok "Green"
Write-List "INSTALLE" $Summary.Installed "Green"
Write-List "A FAIRE" $Summary.Todo "Yellow"
Write-List "ECHEC" $Summary.Failed "Red"
Write-Host "======================================================================" -ForegroundColor White
Write-Host ""
Show-DeployReminder

if ($Summary.Failed.Count -gt 0) { exit 1 }
exit 0
