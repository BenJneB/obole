#!/usr/bin/env bash
# =============================================================================
# Obole — installation / vérification du poste de développement Ubuntu
#
# Usage :
#   ./scripts/setup-ubuntu.sh                      # poste de dev standard
#   ./scripts/setup-ubuntu.sh --with-ollama        # + Ollama local (cas dual boot)
#   ./scripts/setup-ubuntu.sh --with-ollama --model gemma3:4b
#
# Le script est idempotent : il vérifie chaque outil et n'installe que ce qui
# manque. Il peut être relancé sans risque. Il se termine par un résumé
# (OK / installé / à faire / échec).
# =============================================================================

set -o pipefail   # pas de "set -e" : chaque étape gère ses erreurs et le résumé les liste

# --- Paramètres ---------------------------------------------------------------
NVM_VERSION="v0.41.0"
DEFAULT_MODEL="gemma4:e4b"
WITH_OLLAMA=0
MODEL="$DEFAULT_MODEL"

usage() {
    cat <<EOF
Usage : $0 [--with-ollama] [--model NOM] [--help]

  --with-ollama   Installe aussi Ollama et le modèle vision sur cette machine
                  (utile seulement si Ubuntu est en dual boot sur le PC avec GPU).
  --model NOM     Modèle vision à télécharger avec --with-ollama
                  (défaut : $DEFAULT_MODEL).
  --help          Affiche cette aide.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-ollama) WITH_OLLAMA=1 ;;
        --model)
            [[ -n "${2:-}" ]] || { echo "Erreur : --model attend un nom de modèle." >&2; exit 2; }
            MODEL="$2"; shift ;;
        --model=*) MODEL="${1#*=}" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Option inconnue : $1" >&2; usage; exit 2 ;;
    esac
    shift
done

# --- Chemins --------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Affichage et résumé ------------------------------------------------------------
if [[ -t 1 ]]; then
    C_BLUE=$'\e[1;34m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'; C_RED=$'\e[31m'; C_BOLD=$'\e[1m'; C_RESET=$'\e[0m'
else
    C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_RESET=""
fi

SUMMARY_OK=(); SUMMARY_INSTALLED=(); SUMMARY_TODO=(); SUMMARY_FAILED=()

step()      { echo; echo "${C_BLUE}==> $*${C_RESET}"; }
info()      { echo "    $*"; }
ok()        { echo "    ${C_GREEN}[OK]${C_RESET} $*";        SUMMARY_OK+=("$*"); }
installed() { echo "    ${C_GREEN}[INSTALLÉ]${C_RESET} $*";  SUMMARY_INSTALLED+=("$*"); }
todo()      { echo "    ${C_YELLOW}[À FAIRE]${C_RESET} $*";  SUMMARY_TODO+=("$*"); }
failed()    { echo "    ${C_RED}[ÉCHEC]${C_RESET} $*";       SUMMARY_FAILED+=("$*"); }

have() { command -v "$1" >/dev/null 2>&1; }

# Lit une valeur dans .env sans l'afficher (utilisé pour des valeurs non secrètes,
# ou seulement pour tester si une valeur est renseignée).
env_value() {
    local key="$1" file="$REPO_ROOT/.env"
    [[ -f "$file" ]] || return 1
    grep -E "^[[:space:]]*${key}=" "$file" | tail -n1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# --- Garde-fous ---------------------------------------------------------------------
if [[ $EUID -eq 0 ]]; then
    echo "Ne lance pas ce script avec sudo : il installe uv et Node dans ton dossier"
    echo "personnel et demandera lui-même le mot de passe sudo quand c'est nécessaire."
    exit 1
fi

if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == "ubuntu" ]] || echo "${C_YELLOW}Attention : système détecté = ${PRETTY_NAME:-inconnu} (script prévu pour Ubuntu).${C_RESET}"
fi

echo "${C_BOLD}Obole — préparation du poste de développement Ubuntu${C_RESET}"
echo "Dépôt : $REPO_ROOT"
[[ $WITH_OLLAMA -eq 1 ]] && echo "Option : --with-ollama (modèle $MODEL)"

SUDO_OK=0
ensure_sudo() {
    [[ $SUDO_OK -eq 1 ]] && return 0
    info "Le mot de passe sudo peut être demandé (installation de paquets système)."
    if sudo -v; then SUDO_OK=1; return 0; fi
    return 1
}

# =============================================================================
# 1. Paquets apt de base
# =============================================================================
step "1. Paquets système (apt) : git, curl, ca-certificates, build-essential"
APT_PACKAGES=(git curl ca-certificates build-essential)
missing=()
for p in "${APT_PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed"; then
        ok "$p"
    else
        missing+=("$p")
    fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
    info "À installer : ${missing[*]}"
    if ensure_sudo && sudo apt-get update -qq && sudo apt-get install -y "${missing[@]}"; then
        for p in "${missing[@]}"; do installed "$p"; done
    else
        failed "apt : installation de ${missing[*]} impossible"
    fi
fi

# =============================================================================
# 2. GitHub CLI (dépôt apt officiel : la version des dépôts Ubuntu est ancienne)
# =============================================================================
step "2. GitHub CLI (gh)"
if have gh; then
    ok "gh $(gh --version | head -n1 | awk '{print $3}')"
else
    info "Ajout du dépôt apt officiel de GitHub CLI…"
    if ensure_sudo \
        && sudo install -d -m 0755 /etc/apt/keyrings \
        && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
            | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null \
        && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
        && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
            | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null \
        && sudo apt-get update -qq \
        && sudo apt-get install -y gh; then
        installed "gh $(gh --version | head -n1 | awk '{print $3}')"
    else
        failed "gh : installation impossible (voir README, section « Ubuntu > GitHub CLI »)"
    fi
fi

# =============================================================================
# 3. uv + Python 3.12
# =============================================================================
step "3. uv (gestionnaire Python) et Python 3.12"
export PATH="$HOME/.local/bin:$PATH"   # emplacement de l'installeur officiel de uv
if have uv; then
    ok "uv $(uv --version | awk '{print $2}')"
else
    info "Installation de uv (installeur officiel astral.sh)…"
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        export PATH="$HOME/.local/bin:$PATH"
        if have uv; then installed "uv $(uv --version | awk '{print $2}')"
        else failed "uv installé mais introuvable dans le PATH (ouvre un nouveau terminal)"; fi
    else
        failed "uv : installation impossible"
    fi
fi
if have uv; then
    if uv python find 3.12 >/dev/null 2>&1; then
        ok "Python 3.12 ($(uv python find 3.12))"
    elif uv python install 3.12; then
        installed "Python 3.12 (via uv)"
    else
        failed "Python 3.12 : 'uv python install 3.12' a échoué"
    fi
fi

# =============================================================================
# 4. Node.js LTS via nvm
# =============================================================================
step "4. Node.js LTS (via nvm)"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
    info "Installation de nvm $NVM_VERSION…"
    if curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" | bash; then
        installed "nvm $NVM_VERSION"
    else
        failed "nvm : installation impossible"
    fi
else
    ok "nvm présent ($NVM_DIR)"
fi
if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    . "$NVM_DIR/nvm.sh"
    # Dernière LTS publiée (consulte la liste distante de nvm)
    lts_remote="$(nvm version-remote --lts 2>/dev/null)"
    if [[ -z "$lts_remote" || "$lts_remote" == "N/A" ]]; then
        failed "Node LTS : impossible de connaître la dernière version LTS (réseau ?)"
    elif [[ "$(nvm version "$lts_remote")" == "$lts_remote" ]]; then
        ok "Node $lts_remote (LTS) déjà installé"
    elif nvm install --lts; then
        installed "Node $lts_remote (LTS)"
    else
        failed "Node LTS : 'nvm install --lts' a échoué"
    fi
    if [[ -n "$lts_remote" && "$lts_remote" != "N/A" && "$(nvm version default)" != "$lts_remote" ]]; then
        nvm alias default "$lts_remote" >/dev/null && info "Version par défaut de Node : $lts_remote"
    fi
fi

# =============================================================================
# 5. Claude Code (installé à la main : simple vérification)
# =============================================================================
step "5. Claude Code"
if have claude; then
    ok "Claude Code $(claude --version 2>/dev/null | head -n1)"
else
    todo "Claude Code introuvable : voir https://docs.claude.com/claude-code pour l'installer"
fi

# =============================================================================
# 6. Tailscale
# =============================================================================
step "6. Tailscale"
if have tailscale; then
    ok "Tailscale $(tailscale version 2>/dev/null | head -n1)"
else
    info "Installation de Tailscale (installeur officiel)…"
    if ensure_sudo && curl -fsSL https://tailscale.com/install.sh | sh; then
        installed "Tailscale"
    else
        failed "Tailscale : installation impossible"
    fi
fi
if have tailscale; then
    if tailscale status >/dev/null 2>&1; then
        ok "Tailscale connecté ($(tailscale status --peers=false 2>/dev/null | awk 'NR==1{print $2}'))"
    else
        todo "Connecter Tailscale : sudo tailscale up   (puis se connecter dans le navigateur)"
    fi
fi

# =============================================================================
# 7. Ollama local (option --with-ollama, cas dual boot)
# =============================================================================
step "7. Ollama local"
if [[ $WITH_OLLAMA -eq 0 ]]; then
    info "Ignoré (pas d'option --with-ollama) : le modèle tourne sur le PC Windows."
else
    if have nvidia-smi && nvidia-smi >/dev/null 2>&1; then
        ok "GPU NVIDIA : $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -n1)"
    else
        todo "Pilote NVIDIA absent ou inactif : sudo ubuntu-drivers install   puis redémarrer"
    fi
    if have ollama; then
        ok "Ollama $(ollama --version 2>/dev/null | awk '{print $NF}')"
    else
        info "Installation d'Ollama (installeur officiel)…"
        if ensure_sudo && curl -fsSL https://ollama.com/install.sh | sh; then
            installed "Ollama"
        else
            failed "Ollama : installation impossible"
        fi
    fi
    if have ollama; then
        info "Attente du service Ollama sur http://localhost:11434…"
        for _ in $(seq 1 30); do
            curl -fsS http://localhost:11434/api/version >/dev/null 2>&1 && break
            sleep 1
        done
        if ! curl -fsS http://localhost:11434/api/version >/dev/null 2>&1; then
            failed "Ollama ne répond pas (essayer : sudo systemctl restart ollama)"
        else
            model_ref="$MODEL"; [[ "$model_ref" == *:* ]] || model_ref="$model_ref:latest"
            if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$model_ref"; then
                ok "Modèle $MODEL déjà téléchargé"
            elif ollama pull "$MODEL"; then
                installed "Modèle $MODEL"
            else
                failed "Téléchargement du modèle $MODEL impossible (nom exact sur https://ollama.com/library ?)"
            fi
        fi
    fi
fi

# =============================================================================
# 8. Fichier .env
# =============================================================================
step "8. Configuration (.env)"
if [[ ! -f "$REPO_ROOT/.env" ]]; then
    if cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env" && chmod 600 "$REPO_ROOT/.env"; then
        installed ".env créé à partir de .env.example"
    else
        failed ".env : copie de .env.example impossible"
    fi
else
    ok ".env présent"
fi
if [[ -f "$REPO_ROOT/.env" ]]; then
    if [[ -n "$(env_value NUMISTA_API_KEY)" ]]; then
        ok "NUMISTA_API_KEY renseignée (valeur non affichée)"
    else
        todo "Renseigner NUMISTA_API_KEY dans $REPO_ROOT/.env (voir README : « Obtenir une clé Numista »)"
    fi
    # Accès au modèle : en dev sur une autre machine, OLLAMA_URL pointe vers le PC Windows
    ollama_url="$(env_value OLLAMA_URL)"; ollama_url="${ollama_url:-http://localhost:11434}"
    if curl -fsS --max-time 5 "${ollama_url%/}/api/version" >/dev/null 2>&1; then
        ok "Ollama joignable sur $ollama_url"
    else
        todo "Ollama injoignable sur $ollama_url : mettre OLLAMA_URL=http://<nom-pc-windows>:11434 dans .env et lancer setup-windows.ps1 -RemoteOllama sur le PC (le PC doit être allumé)"
    fi
fi

# =============================================================================
# 9. Identité Git et GitHub
# =============================================================================
step "9. Git et GitHub"
for field in name email; do
    if [[ -n "$(git config --global "user.$field")" ]]; then
        ok "git user.$field = $(git config --global "user.$field")"
    elif [[ -t 0 ]]; then
        read -r -p "    git user.$field manquant. Valeur à utiliser : " value
        if [[ -n "$value" ]] && git config --global "user.$field" "$value"; then
            installed "git user.$field configuré"
        else
            todo "git config --global user.$field \"...\""
        fi
    else
        todo "git config --global user.$field \"...\""
    fi
done
if have gh; then
    if gh auth status >/dev/null 2>&1; then
        ok "gh authentifié sur GitHub"
    else
        todo "Se connecter à GitHub : gh auth login"
    fi
fi

# =============================================================================
# Résumé
# =============================================================================
echo
echo "${C_BOLD}=============================== RÉSUMÉ ===============================${C_RESET}"
print_list() {
    local title="$1" color="$2"; shift 2
    [[ $# -eq 0 ]] && return
    echo "${color}${title} ($#)${C_RESET}"
    for line in "$@"; do echo "  - $line"; done
}
print_list "OK"        "$C_GREEN"  "${SUMMARY_OK[@]}"
print_list "INSTALLÉ"  "$C_GREEN"  "${SUMMARY_INSTALLED[@]}"
print_list "À FAIRE"   "$C_YELLOW" "${SUMMARY_TODO[@]}"
print_list "ÉCHEC"     "$C_RED"    "${SUMMARY_FAILED[@]}"
echo "${C_BOLD}======================================================================${C_RESET}"
if [[ ${#SUMMARY_INSTALLED[@]} -gt 0 ]]; then
    echo "Ouvre un nouveau terminal pour que uv et nvm soient dans le PATH."
fi
if [[ ${#SUMMARY_FAILED[@]} -gt 0 ]]; then
    exit 1
fi
exit 0
