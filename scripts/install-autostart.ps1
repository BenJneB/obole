<#
.SYNOPSIS
    Obole - demarre le serveur automatiquement a l'ouverture de session Windows.

.DESCRIPTION
    Cree une tache planifiee "Obole - serveur" (declencheur : ouverture de session de
    l'utilisateur courant, 30 s apres, fenetre cachee) qui lance
    start-server.ps1 -Background. Les journaux vont dans data\logs\.
    La tache n'est pas limitee dans le temps et continue sur batterie.

    Si Windows refuse la creation de la tache (droits), un raccourci est cree a la place
    dans le dossier Demarrage de l'utilisateur (aucun droit administrateur requis).
    Fonctionne sous Windows Famille.

.PARAMETER Remove
    Supprime le demarrage automatique (tache et raccourci).

.PARAMETER NoStart
    Ne lance pas le serveur tout de suite apres l'installation.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install-autostart.ps1
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$NoStart
)

$ErrorActionPreference = "Continue"
. "$PSScriptRoot\common.ps1"

$startScript = Join-Path $PSScriptRoot "start-server.ps1"
$arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$startScript`" -Background"
$user = "$env:USERDOMAIN\$env:USERNAME"

if ($Remove) {
    Write-Step "Suppression du demarrage automatique"
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Ok "Tache planifiee '$TaskName' supprimee"
    }
    if (Test-Path $StartupShortcut) {
        Remove-Item $StartupShortcut -Force
        Write-Ok "Raccourci de demarrage supprime"
    }
    Write-Info "Le serveur en cours (s'il tourne) n'est pas arrete."
    exit 0
}

Write-Step "Demarrage automatique du serveur a l'ouverture de session ($user)"
$installed = $null
try {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = "PT30S"  # laisser Ollama et Tailscale demarrer
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description "Serveur Obole (collection de pieces et billets)" -Force `
        -ErrorAction Stop | Out-Null
    Write-Ok "Tache planifiee '$TaskName' creee (visible dans le Planificateur de taches)"
    $installed = "task"
    if (Test-Path $StartupShortcut) { Remove-Item $StartupShortcut -Force }  # pas de double lancement
} catch {
    Write-Warn "Tache planifiee impossible : $($_.Exception.Message)"
    Write-Info "Repli : raccourci dans le dossier Demarrage."
    try {
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($StartupShortcut)
        $link.TargetPath = "powershell.exe"
        $link.Arguments = $arguments
        $link.WorkingDirectory = $RepoRoot
        $link.WindowStyle = 7  # reduite
        $link.Description = "Serveur Obole"
        $link.Save()
        Write-Ok "Raccourci cree : $StartupShortcut"
        $installed = "shortcut"
    } catch {
        Write-Fail "Raccourci impossible : $($_.Exception.Message)"
        exit 1
    }
}

Test-PowerSettings | Out-Null

if ($NoStart) { exit 0 }
$port = (Get-ServerAddress).Port
if (Get-ListeningProcessId $port) {
    Write-Info "Un serveur tourne deja : il sera gere par le demarrage automatique a la prochaine session."
    Write-Info "Pour basculer tout de suite : scripts\update-server.ps1 -NoPull"
    exit 0
}
Write-Step "Lancement du serveur maintenant"
if ($installed -eq "task") {
    Start-ScheduledTask -TaskName $TaskName
} else {
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$startScript`"", "-Background")
}
$health = Wait-OboleHealth $port
if ($health) {
    Write-Ok "Serveur en ligne : http://127.0.0.1:$port (IA : $(if ($health.ollama.ok) { 'prete' } else { 'indisponible' }))"
    Write-Info "Depuis le telephone : 'tailscale serve --bg $port' une fois, puis https://<nom-pc>.<tailnet>.ts.net"
} else {
    Write-Fail "Le serveur ne repond pas apres 90 s : voir data\logs\"
    exit 1
}
