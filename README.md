# Obole

*Application personnelle de collection de pièces et de billets, avec identification par photo.*

Tu photographies une pièce ou un billet avec ton téléphone. Un **modèle d'IA local** (Ollama) lit les photos, puis l'application cherche les fiches correspondantes dans le **catalogue Numista**. Tu confirmes, et l'objet rejoint ta collection. Tout est gratuit et tourne chez toi : base SQLite et dossier de photos, exportables à tout moment.

```
Téléphone (PWA, Chrome)  ──HTTPS Tailscale──▶  PC Windows : serveur FastAPI ──▶ Ollama (GPU)
                                                         │                  └──▶ API Numista
                                                         └──▶ SQLite + photos (data/)
```

> **État du projet :** étape préliminaire, POC (Phase 0), backend (Phase 1) et **appli pour le téléphone (Phase 2)** faits. Prochaine étape (Phase 3) : saisie en série, statistiques, doublons, sauvegarde automatique.

## Sommaire

1. [Vue d'ensemble : deux machines](#1-vue-densemble--deux-machines)
2. [Étapes manuelles préalables (comptes et clé Numista)](#2-étapes-manuelles-préalables)
3. [Partie A : poste de développement Ubuntu](#partie-a--poste-de-développement-ubuntu)
4. [Partie B : serveur Windows 11](#partie-b--serveur-windows-11)
5. [Choix du modèle d'IA](#5-choix-du-modèle-dia)
6. [Lancer l'application](#6-lancer-lapplication)
7. [Installer l'appli sur le téléphone Android](#7-installer-lappli-sur-le-téléphone-android)
8. [Mettre à jour le serveur](#8-mettre-à-jour-le-serveur)
9. [Sauvegarder ses données](#9-sauvegarder-ses-données)
10. [Dépannage](#10-dépannage)

---

## 1. Vue d'ensemble : deux machines

| Machine | Rôle | Script |
|---|---|---|
| **Ubuntu** (poste de dev) | Écrire et tester le code, le pousser sur GitHub | `scripts/setup-ubuntu.sh` |
| **PC portable Windows 11** (RTX 3070, 8 Go VRAM) | Faire tourner le serveur, Ollama et le GPU ; contient les données | `scripts/setup-windows.ps1` |

Le code passe d'une machine à l'autre par un **dépôt GitHub privé** : `git push` sous Ubuntu, `git pull` sous Windows. Le téléphone joint le PC Windows par **Tailscale**, un réseau privé chiffré, gratuit pour un usage perso, qui fournit aussi le HTTPS.

Pendant le développement, Ubuntu utilise **l'Ollama du PC Windows** à travers Tailscale. Il suffit de régler `OLLAMA_URL` dans `.env` (voir [A.5](#a5-utiliser-lia-du-pc-windows-depuis-ubuntu)). Les tests automatisés, eux, n'ont besoin ni du GPU ni du réseau.

Les deux scripts sont **idempotents** : tu peux les relancer autant de fois que tu veux. Ils vérifient chaque outil, n'installent que ce qui manque et finissent par un **résumé** :

| Catégorie | Signification | Que faire ? |
|---|---|---|
| **OK** | Déjà en place | Rien |
| **INSTALLÉ** | Installé ou configuré pendant ce passage | Rien. Ouvre peut-être un nouveau terminal pour mettre le PATH à jour. |
| **À FAIRE** | Action manuelle nécessaire (connexion à un compte, clé à saisir…) | Suivre l'indication, puis relancer le script |
| **ÉCHEC** | Une installation a échoué | Lire le message plus haut dans la sortie et consulter [Dépannage](#10-dépannage) |

Le script se termine avec le code 1 s'il reste au moins un ÉCHEC, 0 sinon.

---

## 2. Étapes manuelles préalables

Ces étapes ne s'automatisent pas : elles demandent de créer des comptes et de se connecter. Chacune se fait **une seule fois**.

### 2.1 Compte GitHub et dépôt privé
1. Crée un compte sur <https://github.com> si tu n'en as pas.
2. Le dépôt privé `obole` existe déjà (cloné sous Ubuntu). Les scripts proposeront de te connecter avec `gh auth login` : choisis *GitHub.com*, puis *HTTPS*, puis *Login with a web browser*.

### 2.2 Compte Tailscale
1. Va sur <https://tailscale.com> et clique sur **Get started**. Connecte-toi avec un compte Google, Microsoft ou GitHub (le plan *Personal* est gratuit).
2. Utilise **le même compte** sur les trois appareils : Ubuntu, Windows et le téléphone.
3. Sur la console d'administration (<https://login.tailscale.com/admin/dns>), vérifie que **MagicDNS** est activé. Active aussi **HTTPS Certificates**, nécessaire pour `tailscale serve`.

### 2.3 Compte Numista et clé API
1. Crée un compte sur <https://fr.numista.com>.
2. Une fois connecté, ouvre la page de l'API de Numista (lien « API » en bas de page ; documentation : <https://en.numista.com/api/doc/index.php>). *Le chemin exact peut changer : cherche « API » sur le site.*
3. Demande une **clé API** pour un usage personnel. Le plan gratuit donne **2 000 requêtes par mois**, ce qui suffit grâce au cache de l'appli.
4. Copie la clé dans le fichier `.env`, à la ligne `NUMISTA_API_KEY=` (sans guillemets ni espaces). Fais-le sur **chaque** machine qui en a besoin : `.env` n'est jamais envoyé sur GitHub.
   - Ubuntu : `nano .env`, puis Ctrl+O pour enregistrer et Ctrl+X pour quitter.
   - Windows : `notepad .env`

> ⚠️ La clé est un **secret** : ne la colle jamais dans un fichier suivi par git, dans un ticket ou dans une capture d'écran. N'utilise pas l'endpoint Numista de « recherche par image » : il est **payant**.

---

## Partie A : poste de développement Ubuntu

**Déjà fait à la main :** Git, clonage du dépôt, Claude Code.

### A.1 Lancer le script

```bash
cd ~/Projets/FullStack/obole        # adapter au chemin du dépôt
./scripts/setup-ubuntu.sh
```

Le script demande ton mot de passe `sudo` une fois, pour installer des paquets système. **Ne le lance pas avec `sudo` devant**, car uv et Node s'installent dans ton dossier personnel.

Options :

| Option | Effet |
|---|---|
| *(aucune)* | Poste de dev standard : l'IA tourne sur le PC Windows |
| `--with-ollama` | Installe aussi Ollama et le modèle **sur Ubuntu**. Ne sert que si Ubuntu tourne en dual boot sur une machine avec GPU NVIDIA. |
| `--model NOM` | Modèle à télécharger avec `--with-ollama` (défaut `gemma4:e4b`) |
| `--help` | Aide |

Après un premier passage qui a installé des outils, **ouvre un nouveau terminal** pour que `uv`, `node` et `npm` soient dans le PATH, puis relance le script : le résumé doit être entièrement OK, à part les éventuels À FAIRE.

### A.2 Ce que fait le script, outil par outil

| Étape | Outil | Comment | Pourquoi |
|---|---|---|---|
| 1 | git, curl, ca-certificates, build-essential | `apt-get install` | Outils de base, compilation éventuelle |
| 2 | **GitHub CLI** (`gh`) | Dépôt apt officiel de GitHub (`cli.github.com`), plus récent que celui d'Ubuntu | Authentification GitHub, opérations sur le dépôt |
| 3 | **uv** + **Python 3.12** | Installeur officiel `astral.sh`, puis `uv python install 3.12` | Environnement Python du backend, reproductible |
| 4 | **nvm** + **Node.js LTS** | Installeur officiel de nvm (version figée dans le script), puis `nvm install --lts`. La LTS devient la version par défaut ; les autres versions de Node restent installées. | Build du frontend (React + Vite) |
| 5 | **Claude Code** | Simple vérification (`claude --version`) | Déjà installé à la main |
| 6 | **Tailscale** | Installeur officiel `tailscale.com/install.sh` ; vérifie la connexion | Joindre l'Ollama du PC Windows |
| 7 | **Ollama** *(si `--with-ollama`)* | Vérifie le pilote NVIDIA (`nvidia-smi`), installe Ollama, télécharge le modèle | Cas dual boot uniquement |
| 8 | **`.env`** | Copie de `.env.example` s'il n'existe pas (droits `600`). Vérifie que `NUMISTA_API_KEY` est remplie, **sans l'afficher**, et que `OLLAMA_URL` répond. | Configuration |
| 9 | **Identité Git** et **GitHub** | Demande `user.name` / `user.email` s'ils manquent ; vérifie `gh auth status` | Commits et push |

### A.3 Régler à la main ce que le script n'a pas pu faire

- **Tailscale non connecté :** `sudo tailscale up`, puis ouvre le lien affiché et connecte-toi avec ton compte.
- **GitHub non connecté :** `gh auth login`.
- **Identité Git :**
  ```bash
  git config --global user.name "Prénom Nom"
  git config --global user.email "adresse@exemple.org"
  ```
- **Échec de l'installation de `gh`** (par exemple sur une version d'Ubuntu trop récente pour le dépôt) : en dépannage, `sudo apt install gh` installe la version d'Ubuntu, plus ancienne mais suffisante.
- **Pilote NVIDIA** (seulement avec `--with-ollama`) : `sudo ubuntu-drivers install`, redémarrer, puis vérifier avec `nvidia-smi`.

### A.4 Vérifier rapidement

```bash
uv --version && uv python find 3.12
node --version && npm --version
gh auth status
tailscale status
```

### A.5 Utiliser l'IA du PC Windows depuis Ubuntu

1. Sur le PC Windows, lance `setup-windows.ps1 -RemoteOllama` (voir [B.2](#b2-lancer-le-script)). Ollama devient alors joignable via Tailscale, et uniquement via Tailscale, grâce à des règles de pare-feu.
2. Cherche le nom Tailscale du PC Windows : `tailscale status` sous Ubuntu (2ᵉ colonne).
3. Dans le `.env` d'Ubuntu :
   ```
   OLLAMA_URL=http://<nom-pc-windows>:11434
   ```
4. Test : `curl http://<nom-pc-windows>:11434/api/version` doit renvoyer un JSON avec un numéro de version. Le PC Windows doit être allumé.

---

## Partie B : serveur Windows 11

### B.1 Récupérer le dépôt sur Windows (première fois)

Ouvre **PowerShell** (menu Démarrer, tape « PowerShell »), sans le lancer en administrateur :

```powershell
# 1. Git et GitHub CLI (nécessaires pour cloner un dépôt privé)
winget install --id Git.Git --exact
winget install --id GitHub.cli --exact
# 2. Ferme puis rouvre PowerShell, puis :
gh auth login
cd $HOME
gh repo clone <ton-compte-github>/obole
cd obole
```

> Si `winget` est introuvable, installe **App Installer** depuis le Microsoft Store.

### B.2 Lancer le script

```powershell
cd $HOME\obole
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
```

Pas besoin de droits administrateur. Windows affichera quelques fenêtres **UAC** (« Voulez-vous autoriser… ») pendant certaines installations winget (Node, Tailscale) et pour le pare-feu avec `-RemoteOllama` : accepte-les.

| Option | Effet |
|---|---|
| `-Model NOM` | Modèle vision à télécharger et tester (défaut `gemma4:e4b`) |
| `-RemoteOllama` | Rend Ollama joignable **depuis le poste Ubuntu via Tailscale** (`OLLAMA_HOST=0.0.0.0` + règles de pare-feu qui limitent le port 11434 aux adresses Tailscale) |
| `-Yes` | Répond « oui » aux confirmations (réglages d'alimentation) |

Exemple pour le développement depuis une autre machine Ubuntu :

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1 -RemoteOllama
```

Les messages du script sont **sans accents**, volontairement : PowerShell 5.1 affiche mal l'UTF-8.

### B.3 Ce que fait le script, outil par outil

| Étape | Outil | Comment | Pourquoi |
|---|---|---|---|
| 1 | Git, uv, Node.js LTS, GitHub CLI, Ollama, Tailscale | `winget install --id …` pour ce qui manque, puis rechargement du PATH de la session | Tout le nécessaire du serveur |
| 2 | **Python 3.12** | `uv python install 3.12` | Backend |
| 3 | **GPU** | `nvidia-smi` (nom, VRAM, version du pilote) | Le modèle doit tourner sur le GPU |
| 4 | **Variables d'environnement** (utilisateur) | `OLLAMA_KEEP_ALIVE=30m` (garde le modèle en mémoire entre deux photos) ; avec `-RemoteOllama`, `OLLAMA_HOST=0.0.0.0` | Réactivité ; accès depuis Ubuntu |
| 5 | **Pare-feu** *(si `-RemoteOllama`)* | Deux règles : *autoriser* 11434 depuis `100.64.0.0/10` et `fd7a:115c:a1e0::/48` (Tailscale), *bloquer* 11434 depuis toute autre adresse | Ollama n'a **aucune authentification** : sans ces règles, n'importe qui sur le même Wi-Fi pourrait l'utiliser |
| 6 | **Ollama + modèle** | Redémarre Ollama si les variables ont changé, attend `http://localhost:11434/api/version`, télécharge le modèle s'il manque, fait un essai chronométré et affiche `ollama ps` | Vérifier que tout marche, **sur le GPU** |
| 7 | **Alimentation** | Lit les réglages ; **après confirmation**, applique `powercfg /change standby-timeout-ac 0` et `LIDACTION 0` | Un portable en veille = serveur injoignable |
| 8 | **Tailscale** | Vérifie la connexion et affiche le nom DNS du PC | Accès depuis le téléphone |
| 9 | **Identité Git** et **GitHub** | Demande `user.name` / `user.email` s'ils manquent ; `gh auth status` | `git pull` |
| 10 | **`.env`** | Copie `.env.example` s'il n'existe pas. Vérifie `NUMISTA_API_KEY` (sans l'afficher) et la cohérence de `VISION_MODEL`. | Configuration |

### B.4 Lire `ollama ps`

Après l'essai, le script affiche quelque chose comme :

```
NAME            ID        SIZE     PROCESSOR    UNTIL
gemma4:e4b      …         7.4 GB   100% GPU     29 minutes from now
```

La colonne **PROCESSOR** doit indiquer **`100% GPU`**. Une valeur comme `30%/70% CPU/GPU` veut dire que le modèle déborde de la VRAM : il sera bien plus lent. Ferme alors les applis qui utilisent le GPU (jeux, navigateur avec beaucoup d'onglets vidéo) ou passe à un modèle plus petit (voir [section 5](#5-choix-du-modèle-dia)).

### B.5 Régler à la main ce que le script n'a pas pu faire

**Variables d'environnement Windows**
1. Menu Démarrer, tape « variables d'environnement », puis *Modifier les variables d'environnement pour votre compte*.
2. Dans la partie *Variables utilisateur*, clique sur **Nouvelle…**. Nom `OLLAMA_KEEP_ALIVE`, valeur `30m`.
3. Seulement pour l'accès depuis Ubuntu : nom `OLLAMA_HOST`, valeur `0.0.0.0`. Crée aussi le pare-feu ci-dessous.
4. Pour retirer `OLLAMA_HOST` plus tard : sélectionne-la, puis **Supprimer**.
5. **Quitte Ollama** (icône dans la barre des tâches, clic droit, *Quit Ollama*), puis relance-le depuis le menu Démarrer pour qu'il prenne les nouvelles valeurs.

**Pare-feu Ollama** (si la fenêtre UAC a été refusée). Dans un PowerShell **lancé en administrateur** :

```powershell
New-NetFirewallRule -DisplayName 'Obole - Ollama 11434 (Tailscale uniquement)' -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress '100.64.0.0/10','fd7a:115c:a1e0::/48' -Action Allow -Profile Any
New-NetFirewallRule -DisplayName 'Obole - Ollama 11434 (bloquer hors Tailscale)' -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress '0.0.0.0-100.63.255.255','100.128.0.0-255.255.255.255','::-fd7a:115c:a1df:ffff:ffff:ffff:ffff:ffff','fd7a:115c:a1e1::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff' -Action Block -Profile Any
```

Si Windows affiche une fenêtre « Le Pare-feu Windows a bloqué certaines fonctionnalités d'Ollama », tu peux cliquer *Autoriser* : la règle de blocage ci-dessus l'emporte de toute façon.

**Alimentation** (PC portable utilisé comme serveur)
1. *Paramètres*, puis *Système*, puis *Alimentation* : **Écran et veille**. Mettre *Sur secteur, mettre l'appareil en veille après* : **Jamais**.
2. *Panneau de configuration*, puis *Options d'alimentation*, puis **Choisir l'action qui suit la fermeture du capot** : *Sur secteur* → **Ne rien faire**. Enregistrer.
3. Laisser le PC **branché sur secteur**.

Équivalent en ligne de commande :

```powershell
powercfg /change standby-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

**Tailscale :** clique sur l'icône Tailscale dans la barre des tâches, puis *Log in*, et connecte-toi avec le même compte qu'ailleurs.

---

## 5. Choix du modèle d'IA

Le modèle se règle dans `.env` (`VISION_MODEL`), sans toucher au code. Règle à respecter : il doit tenir **entièrement dans les 8 Go de VRAM**, avec une marge pour l'image.

Résultat du banc d'essai de la Phase 0 (12 objets photographiés, RTX 3070 8 Go, images 768 px, contexte 4096) :

| Modèle | Taille | Trouvés dans le top 5 | Temps moyen | Commentaire |
|---|---|---|---|---|
| **`gemma4:e4b`** | ~6,6 Go | 8/11 | **7,5 s** | **Choix par défaut** : aussi précis que les autres, 3 à 4 fois plus rapide |
| `qwen2.5vl:7b` | ~6 Go | 8/11 | 26,6 s | Bon lecteur de texte, mais lent ; réponse parfois coupée |
| `qwen3.5:9b` | ~6,6 Go | 8/11 | 26,5 s | Aussi précis, mais lent |
| `qwen3.5:4b` | ~3,3 Go | 7/11 | 17,5 s | Oublie souvent la valeur faciale |
| — `gemma3:12b`, `llama3.2-vision:11b` | ~8 Go | — | — | À éviter : trop justes pour 8 Go de VRAM |

Ne pas monter le contexte (`VISION_NUM_CTX`) au-delà de 4096 : le modèle déborde alors de la VRAM sur le CPU et devient beaucoup plus lent.

Les noms exacts et les tailles se vérifient sur <https://ollama.com/library>. Pour changer de modèle :

```powershell
ollama pull qwen2.5vl:7b       # télécharger
# puis VISION_MODEL=qwen2.5vl:7b dans .env, et redémarrer le serveur
ollama rm gemma4:e4b           # (optionnel) libérer de la place
```

---

## 5 bis. Tester l'identification en ligne de commande (POC)

Le script `scripts/poc_identify.py` teste toute la chaîne (photo → modèle → Numista) sans interface. Il **n'ajoute rien à la collection** : il lit les photos et n'écrit que dans le cache Numista (base `data/obole.sqlite3`, partagé avec le serveur) et `data/poc-results/`.

À lancer depuis la racine du dépôt, après avoir renseigné `OLLAMA_URL` et `NUMISTA_API_KEY` dans `.env` :

```bash
# Identifier un objet : photo de l'avers, puis éventuellement du revers
uv run --project backend python scripts/poc_identify.py avers.jpg revers.jpg

# Lecture par le modèle seulement (n'utilise pas le quota Numista)
uv run --project backend python scripts/poc_identify.py avers.jpg --no-numista

# Recherche manuelle dans Numista (repli quand l'IA se trompe)
uv run --project backend python scripts/poc_identify.py --search "20 Kopecks" --issuer ancienne_urss --type coin

# Banc d'essai : toutes les photos de data/test-photos, plusieurs modèles comparés
uv run --project backend python scripts/poc_identify.py --bench --models gemma4:e4b,qwen2.5vl:7b
```

**Photos de test** : `data/test-photos/NN_avers.jpg` et `NN_revers.jpg`. Le fichier `attendu.csv` (colonnes `id,object_type,numista_id,description,photos`) donne la bonne réponse de chaque objet ; le banc d'essai calcule combien sont trouvés dans les 5 premiers candidats et le temps moyen. Le détail de chaque passage est enregistré dans `data/poc-results/bench-<date>.csv`.

**Quota Numista** : chaque réponse est gardée en cache 90 jours (table `numista_cache` de la base SQLite). Relancer le même test ne consomme donc presque rien. Le compteur du mois (table `api_usage`) est visible via `/api/health`. L'ancien cache fichier du POC (`data/cache/`) a été repris automatiquement dans la base au premier lancement ; ce dossier peut être supprimé.

---

## 6. Lancer l'application

L'application a deux parties : le **backend** (Python, FastAPI, dans `backend/`) et l'**appli** affichée par le téléphone (React, dans `frontend/`). En production, le backend sert aussi l'appli compilée (`frontend/dist`) : **un seul processus**.

**En développement (Ubuntu) :**
```bash
./scripts/dev.sh                 # backend (port 8000) + appli Vite (port 5173), rechargement automatique
./scripts/dev.sh --backend-only  # backend seul
```
- Ouvre <http://localhost:5173> : c'est l'appli, qui se recharge à chaque modification du code. Dans Chrome, `F12` puis l'icône téléphone (*Toggle device toolbar*) simule un écran de téléphone.
- <http://127.0.0.1:8000/docs> : **documentation interactive de l'API** ; chaque route peut y être essayée (bouton *Try it out*).
- Les données de dev sont dans `data/` du poste Ubuntu (distinctes de celles du serveur Windows).

**Écrans de l'appli :**
- **Collection** : grille de miniatures, recherche, filtres (type, pays), tri. Le bouton **+** ajoute un objet.
- **Ajouter** : photo de l'avers (et du revers), puis **Identifier**. L'écran **Résultats** montre ce que l'IA a lu (corrigeable, avec « Relancer la recherche ») et les fiches Numista proposées : **C'est celle-ci** ouvre le formulaire pré-rempli (état, quantité…), **Aucune ne correspond** ouvre la recherche Numista manuelle ou la saisie libre. Sans IA (PC sans GPU, Ollama arrêté), l'ajout reste possible par recherche Numista ou saisie manuelle.
- **Fiche** : photos (toucher pour agrandir), lien vers Numista, informations ; **Modifier**, **Ajouter une photo**, suppression (en deux appuis pour éviter les erreurs).
- **Réglages** : état de l'IA et du quota Numista, résumé de la collection, exports et sauvegarde.

**Ce que fait l'API :**

| Route | Rôle |
|---|---|
| `POST /api/identify` | 1 ou 2 photos (avers, revers) → lecture par le modèle + 5 candidats Numista. Répond toujours avec ce qui a pu être obtenu ; en cas d'échec, `errors` explique pourquoi et les photos restent utilisables (`upload_id`, gardé 24 h) |
| `POST /api/items` | Enregistre un objet ; avec `upload_id`, les photos de l'identification y sont rattachées |
| `GET /api/items` | Liste, avec recherche (`q`), filtres (`country`, `object_type`, `year`, `year_min`, `year_max`) et tri (`sort=-year`…) |
| `GET` / `PUT` / `DELETE /api/items/{id}` | Lire / modifier (seuls les champs envoyés changent) / supprimer (photos comprises) |
| `POST /api/items/{id}/photos` | Ajouter une photo à la main |
| `GET /api/numista/search`, `/api/numista/types/{id}` | Recherche manuelle et détail d'une fiche (via le cache) |
| `GET /api/export.csv`, `/api/export.json`, `/api/backup.zip` | Exports et sauvegarde complète |
| `GET /api/health`, `/api/stats` | État d'Ollama et quota Numista ; statistiques de la collection |

**Base de données :** `data/obole.sqlite3`, créée et mise à jour automatiquement au démarrage (migrations). Les photos sont dans `data/photos/<id de l'objet>/`, originaux conservés tels quels + miniatures.

**Tests et vérifications (à lancer avant de pousser) :**
```bash
cd backend
uv run ruff check . ../scripts   # style et erreurs courantes
uv run pytest                    # tests : Ollama et Numista sont simulés (ni GPU, ni réseau, ni quota)
cd ../frontend
npm run lint                     # ESLint
npm run build                    # vérification des types TypeScript + compilation
```
Les mêmes vérifications tournent automatiquement sur GitHub à chaque **pull request** (onglet **Actions** du dépôt, ou bouton *Run workflow* pour les lancer à la main) : backend sous **Ubuntu et Windows**, appli, et analyse des scripts `.sh` (shellcheck) et `.ps1` (syntaxe, PSScriptAnalyzer, caractères ASCII). Une croix rouge signale un problème : clique dessus pour voir le détail.

**En production (Windows), première fois :**
```powershell
cd $HOME\obole                   # dossier du dépôt sur le PC
powershell -ExecutionPolicy Bypass -File .\scripts\update-server.ps1 -NoPull      # compile l'appli et lance le serveur
tailscale serve --bg 8000         # une seule fois : HTTPS pour le téléphone
powershell -ExecutionPolicy Bypass -File .\scripts\install-autostart.ps1          # démarrage automatique
```
- `start-server.ps1` lance le serveur dans la fenêtre courante (`Ctrl+C` pour l'arrêter). Avant de démarrer, il vérifie l'alimentation et affiche un **avertissement bien visible** si le PC risque de se mettre en veille ; tant qu'il tourne, il **empêche la mise en veille**.
- Le serveur écoute sur `127.0.0.1:8000` uniquement : il n'est joignable que depuis le PC lui-même et, grâce à `tailscale serve`, depuis tes appareils Tailscale via `https://<nom-pc>.<tailnet>.ts.net` (affiché par `tailscale serve status`).
- `install-autostart.ps1` crée la tâche planifiée **« Obole - serveur »** : à chaque ouverture de session, 30 s après, le serveur démarre en arrière-plan (fenêtre cachée), avec des journaux dans `data\logs\` et une relance automatique en cas de plantage. Si Windows refuse de créer la tâche, le script met à la place un raccourci dans le dossier *Démarrage* (même effet). Pour le retirer : `install-autostart.ps1 -Remove`.
- La session Windows doit être ouverte (pas forcément déverrouillée) : active l'ouverture de session automatique si le PC redémarre seul, ou ouvre ta session après chaque redémarrage.

---

## 7. Installer l'appli sur le téléphone Android

1. Installe l'appli **Tailscale** depuis le Play Store et connecte-toi avec **le même compte**.
2. Ouvre `https://<nom-pc>.<tailnet>.ts.net` dans **Chrome** (le serveur doit tourner et `tailscale serve --bg 8000` avoir été lancé sur le PC).
3. Menu **⋮**, puis **Installer l'application** (ou « Ajouter à l'écran d'accueil »). L'icône Obole apparaît sur l'écran d'accueil ; l'appli s'ouvre en plein écran, sans barre d'adresse.
4. À la première photo, **autorise l'accès à l'appareil photo**.

**Mises à jour de l'appli :** rien à réinstaller. Après un `update-server.ps1`, l'appli affiche « Nouvelle version disponible » : touche **Mettre à jour**. La version installée est indiquée en bas des *Réglages*.

**PC éteint ou Tailscale déconnecté :** l'appli s'ouvre quand même (elle est gardée sur le téléphone) mais affiche « Serveur injoignable ». Les photos déjà vues restent visibles.

**Astuces d'utilisation :**
- **Mode série** (en bas de l'écran *Ajouter*) : pour numériser un album d'affilée. Après chaque ajout, l'appli revient directement aux photos, et l'emplacement, les tags et la date d'acquisition sont repris de l'objet précédent.
- **Doublons** : une fiche Numista déjà présente dans la collection est signalée (« Déjà dans la collection ») ; après l'avoir choisie, **+1 exemplaire** augmente la quantité de l'objet existant au lieu d'en créer un nouveau. Une fiche couvre souvent plusieurs années : vérifie l'année.
- **Tags** : tape un tag puis Entrée (ou une virgule) ; les tags déjà utilisés sont proposés. Sur une fiche, toucher un tag affiche tous les objets qui le portent.
- **Filtres** (*Collection* > **Filtres**) : période, état, tag, emplacement, avec ou sans fiche Numista. L'onglet **Stats** montre la répartition par type, pays, décennie ou siècle, état et tag ; chaque barre ouvre la collection filtrée.

---

## 8. Mettre à jour le serveur

Sur le PC Windows :
```powershell
cd $HOME\obole
powershell -ExecutionPolicy Bypass -File .\scripts\update-server.ps1
```
Le script :
1. affiche les rappels ci-dessous et vérifie l'alimentation ;
2. fait `git pull` (il refuse si des fichiers du dépôt ont été modifiés sur le PC : le serveur ne doit rien modifier lui-même) ;
3. installe les dépendances Python (`uv sync`) et compile l'appli (`npm ci`, `npm run build`) ;
4. arrête le serveur et le relance (par la tâche planifiée si elle existe, sinon dans une nouvelle fenêtre) ;
5. attend qu'il réponde et affiche l'état de l'IA et du quota Numista.

Options : `-NoPull` (recompiler et redémarrer sans récupérer de code), `-NoRestart`.

> **🔔 Rappel à chaque mise à jour :**
> - PC **branché sur secteur** ;
> - mise en veille sur secteur : **Jamais** ;
> - fermer le capot : **Ne rien faire** ;
> - après la première identification, `ollama ps` doit afficher **100% GPU**.

Si une nouvelle dépendance système apparaît, les scripts `setup-*` et ce README sont mis à jour en même temps. **Relance donc `setup-windows.ps1` après un `git pull`** en cas de doute : c'est sans risque.

---

## 9. Sauvegarder ses données

Toutes tes données sont dans le dossier **`data/`** (base SQLite et photos), réglable via `DATA_DIR`. Ce dossier n'est **pas** sur GitHub.

- **Méthode simple, disponible dès maintenant :** arrête le serveur, puis copie tout le dossier `data/` sur une clé USB ou un cloud personnel.
- **Depuis l'appli (sans arrêter le serveur) :** *Réglages* > **Sauvegarde complète** télécharge `obole-backup-<date>.zip` (copie cohérente de la base + toutes les photos). **Export CSV** (s'ouvre dans Excel) et **Export JSON** donnent la liste lisible. Sur le PC, les mêmes fichiers sont à <http://127.0.0.1:8000/api/backup.zip>, `/api/export.csv` et `/api/export.json`.
- **Sauvegarde automatique (par défaut) :** le serveur crée lui-même `obole-auto-<date>.zip` une fois par jour **si la collection a changé**, et au démarrage s'il a raté l'échéance (PC éteint). Les 5 plus récentes sont gardées. Réglages dans `.env` : `BACKUP_INTERVAL_HOURS` (0 = désactivée), `BACKUP_KEEP`, `BACKUP_DIR` (défaut `data/backups`). *Réglages* > **Sauvegarde automatique** affiche la dernière sauvegarde et propose **Sauvegarder maintenant**.
  - Une copie sur le même disque ne protège pas d'une panne ou d'un vol du PC : mets `BACKUP_DIR` sur un dossier synchronisé (OneDrive…) ou une clé USB toujours branchée, ou copie le dossier de temps en temps.
  - Chaque ZIP contient toutes les photos : prévoir l'espace disque (taille de `data/photos` × `BACKUP_KEEP`).
- **Restaurer :** serveur arrêté, remettre le dossier `data/` en place (ou extraire un `.zip` de sauvegarde dans `data/`).

---

## 10. Dépannage

| Symptôme | Piste |
|---|---|
| `uv`, `node` ou `gh` « introuvable » juste après l'installation | Ouvrir un **nouveau** terminal ou PowerShell, puis relancer le script |
| `setup-windows.ps1` « ne peut pas être chargé car l'exécution de scripts est désactivée » | Lancer avec `powershell -ExecutionPolicy Bypass -File …` comme indiqué |
| Accents illisibles dans PowerShell | Normal pour les fichiers de données ; les messages des scripts sont en ASCII exprès |
| `Ollama ne repond pas` | Lancer Ollama depuis le menu Démarrer, attendre l'icône dans la barre des tâches, relancer le script |
| `ollama ps` n'affiche pas `100% GPU` | Fermer les applis qui utilisent le GPU, vérifier le pilote (`nvidia-smi`), essayer `gemma3:4b` |
| Ubuntu n'arrive pas à joindre `http://<pc>:11434` | PC allumé ? Tailscale connecté des deux côtés ? `setup-windows.ps1 -RemoteOllama` lancé, et Ollama redémarré ensuite ? |
| Erreur Numista 401/403 | Clé absente ou fausse dans `.env` |
| Erreur Numista 429 | Trop de requêtes : l'appli attend automatiquement ; vérifier le quota du mois dans *Réglages* |
| L'appli affiche « Serveur injoignable » | PC allumé et session ouverte ? Serveur lancé (`http://127.0.0.1:8000` sur le PC) ? Journaux dans `data\logs\` |
| « Nouvelle version disponible » ne disparaît pas | Fermer complètement l'appli (liste des applis récentes) et la rouvrir |
| Le téléphone n'ouvre pas l'URL `…ts.net` | Tailscale actif sur le téléphone ? `tailscale serve status` sur le PC ? HTTPS Certificates activé dans la console Tailscale ? |

### Dépannage GPU (Windows)

Symptômes : `nvidia-smi` échoue, `ollama ps` n'affiche pas `100% GPU`, ou l'essai du modèle prend plusieurs dizaines de secondes.

1. Dans PowerShell, lance `nvidia-smi` seul. Il doit afficher un tableau avec « NVIDIA GeForce RTX 3070 Laptop GPU ».
2. S'il échoue : *Gestionnaire de périphériques* > *Cartes graphiques*. La RTX 3070 doit apparaître **sans** triangle jaune et **sans** être désactivée.
3. Réinstalle le pilote **Game Ready** ou **Studio** depuis <https://www.nvidia.com/Download/index.aspx> (choisir *Installation personnalisée* > *Effectuer une installation propre*), puis redémarre.
4. Sur certains portables, un mode « iGPU uniquement » ou « économie d'énergie » désactive la carte NVIDIA. Vérifie l'utilitaire du constructeur (ex. NitroSense / Acer, mode GPU) et le BIOS.
5. Relance Ollama (*Quit Ollama*, puis relance), puis `setup-windows.ps1`. Le résumé doit afficher « Modele charge a 100% sur le GPU ».
