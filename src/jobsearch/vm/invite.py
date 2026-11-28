"""L'invité : ce qui s'installe et se lance dans la machine virtuelle Ubuntu, quel que soit l'hôte.

Les mêmes scripts servent à Lima (Mac, Linux) et à WSL (Windows). Ils choisissent eux-mêmes
l'architecture de la VM (`dpkg --print-architecture` : arm64 sur un Mac Apple silicon ou un PC ARM,
amd64 sur un PC) pour Chrome et Node.js ; moteur/fournisseurs.py fait de même pour les moteurs d'IA.

Ce qui demande root (paquets, stratégies de Chrome) est séparé de ce qui tourne au nom du compte de la
VM (le travailleur, l'écran, Chrome, les moteurs) : chaque hyperviseur lance le premier comme root
(`sudo` sous Lima, `wsl -u root` sous Windows), et sous Windows le compte de la VM n'a pas sudo.
"""

import json

DONNEES, CODE = "/donnees", "code-jobsearch"     # CODE : relatif au dossier personnel de la VM

MCP_CHROME = "chrome-devtools-mcp@1.12.0"     # version figée, relue avant chaque changement
PORT_DEVTOOLS = 9222                           # Chrome, sur 127.0.0.1 dans la VM seulement
PORT_VNC = 5901                                # l'écran, sur 127.0.0.1 dans la VM seulement

PROVISION_SYSTEME = """#!/bin/bash
set -eux
export DEBIAN_FRONTEND=noninteractive
ARCH=$(dpkg --print-architecture)              # arm64 ou amd64
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv curl ca-certificates git xz-utils bubblewrap \\
  tigervnc-standalone-server tigervnc-tools openbox novnc websockify iproute2 \\
  fonts-dejavu fonts-liberation fonts-noto-color-emoji
# Google Chrome pour Linux, depuis Google (le paquet ajoute aussi le dépôt de mises à jour).
if ! command -v google-chrome >/dev/null; then
  curl -fsSL -o /tmp/chrome.deb "https://dl.google.com/linux/direct/google-chrome-stable_current_${ARCH}.deb"
  apt-get install -y /tmp/chrome.deb
  rm -f /tmp/chrome.deb
fi
# Node.js 22 (pour le serveur MCP de Chrome), depuis nodejs.org, somme de contrôle vérifiée.
NODE_ARCH=$([ "$ARCH" = amd64 ] && echo x64 || echo arm64)
if ! /opt/node/bin/node --version 2>/dev/null | grep -q '^v22'; then
  cd /tmp
  curl -fsSLO https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt
  F=$(grep -oE "node-v22[0-9.]+-linux-${NODE_ARCH}.tar.xz" SHASUMS256.txt | head -1)
  curl -fsSLO "https://nodejs.org/dist/latest-v22.x/$F"
  grep " $F\\$" SHASUMS256.txt | sha256sum -c -
  rm -rf /opt/node && mkdir -p /opt/node && tar -xJf "$F" -C /opt/node --strip-components=1
  rm -f "$F" SHASUMS256.txt
fi
/opt/node/bin/npm install -g --prefix /opt/node %(mcp)s
""" % {"mcp": MCP_CHROME}

PROVISION_UTILISATEUR = """#!/bin/bash
set -eux
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
command -v claude || test -x "$HOME/.local/bin/claude" || curl -fsSL https://claude.ai/install.sh | bash
"""

POLITIQUE_CHROME = {
    "PasswordManagerEnabled": False, "AutofillAddressEnabled": False, "AutofillCreditCardEnabled": False,
    "BrowserSignin": 0, "SyncDisabled": True, "MetricsReportingEnabled": False, "SearchSuggestEnabled": False,
    "PromotionalTabsEnabled": False, "BackgroundModeEnabled": False, "RestoreOnStartup": 5,
    "DefaultNotificationsSetting": 2, "DefaultGeolocationSetting": 2,
    # Aucune page locale : ni les fichiers de la VM (dont /donnees : CV, coordonnées, jeton), ni
    # leur source. Les documents joints passent par le champ de fichier (DevTools), pas par une
    # adresse file://, et le PDF s'imprime depuis un contenu posé dans la page : ni l'un ni
    # l'autre n'est touché. Reposée à chaque démarrage (POSER_POLITIQUE), donc aussi sur une VM déjà créée.
    "URLBlocklist": ["file://*", "view-source:*"],
}

# Stratégies de Chrome : rien de la personne n'y est gardé hors des cookies de connexion aux sites
# (ni mots de passe, ni adresses, ni cartes), ni compte Google, ni synchronisation, ni rapports. Comme root.
POSER_POLITIQUE = """set -e
mkdir -p /etc/opt/chrome/policies/managed
cat > /etc/opt/chrome/policies/managed/recherche-emploi.json <<'POLITIQUE'
%(politique)s
POLITIQUE
""" % {"politique": json.dumps(POLITIQUE_CHROME)}

# Lancement du travailleur dans la VM : l'environnement de la VM, les données montées, le code en
# lecture seule. Le jeton est lu dans /donnees (fichier du seul compte). Sans sudo.
LANCER = """set -e
export PATH="$HOME/.local/bin:/opt/node/bin:$PATH" JOBSEARCH_DATA=%(donnees)s JOBSEARCH_HOME="$HOME/%(code)s" JOBSEARCH_VM=1
mkdir -p %(donnees)s/logs
# L'écran : Xvnc (VNC sur 127.0.0.1 de la VM, mot de passe du dossier partagé), Openbox,
# Chrome (profil de la VM, DevTools sur 127.0.0.1), noVNC pour l'application.
export DISPLAY=:1
mkdir -p "$HOME/.vnc" && chmod 700 "$HOME/.vnc"
vncpasswd -f < %(donnees)s/ecran.motdepasse > "$HOME/.vnc/passwd" && chmod 600 "$HOME/.vnc/passwd"
pgrep -x Xvnc >/dev/null || { nohup Xvnc :1 -geometry 1440x900 -depth 24 -localhost yes -rfbport %(vnc)d \\
  -SecurityTypes VncAuth -rfbauth "$HOME/.vnc/passwd" >> %(donnees)s/logs/ecran.log 2>&1 & sleep 2; }
pgrep -x openbox >/dev/null || { nohup openbox >> %(donnees)s/logs/ecran.log 2>&1 & }
# Par nom exact (-x) : la ligne de commande de ce script contient elle-même ces mots.
pgrep -x chrome >/dev/null || { nohup google-chrome --remote-debugging-port=%(devtools)d \\
  --remote-debugging-address=127.0.0.1 --user-data-dir="$HOME/chrome-profil" --no-first-run \\
  --no-default-browser-check --hide-crash-restore-bubble --start-maximized --lang=fr-CH about:blank >> %(donnees)s/logs/chrome.log 2>&1 & }
pkill -x websockify || true
# L'écran est sur la boucle locale de la VM, ou dans l'espace isolé de Chrome sous WSL (vm/wsl.py).
nohup websockify --web /usr/share/novnc 127.0.0.1:%(ecran)d "${JOBSEARCH_CHROME_HOTE:-127.0.0.1}":%(vnc)d >> %(donnees)s/logs/ecran.log 2>&1 &
pkill -f "[v]env/bin/jobsearch travailleur" || true    # code réinstallé : on repart ([v] : pas ce script lui-même)
# Un travailleur pris dans une longue tâche met du temps à s'arrêter : sans attendre, le nouveau ne
# trouve pas son port libre et s'arrête aussitôt (constaté le 25.11.2026). 10 s, puis arrêt forcé.
for i in 1 2 3 4 5 6 7 8 9 10; do pgrep -f "[v]env/bin/jobsearch travailleur" >/dev/null || break; sleep 1; done
pkill -9 -f "[v]env/bin/jobsearch travailleur" || true
nohup "$HOME/venv/bin/jobsearch" travailleur --hote 127.0.0.1 --port %(port)d >> %(donnees)s/logs/travailleur.log 2>&1 &
"""

INSTALLER = """set -e
export PATH="$HOME/.local/bin:$PATH"
"$HOME/venv/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] < (%(py)s))' 2>/dev/null \
  || uv venv --clear --python %(pyv)s "$HOME/venv"
uv pip install --python "$HOME/venv/bin/python" --reinstall-package jobsearch "$HOME/%(code)s"
# La commande des sessions Claude dans Chrome (session_vm.py), seule commande qu'elles peuvent lancer.
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\\nexec "$HOME/venv/bin/python" -m jobsearch.session_vm "$@"\\n' > "$HOME/.local/bin/jobsearch-session"
chmod 755 "$HOME/.local/bin/jobsearch-session"
"""


def hote_chrome(env=None, fichier="/etc/piston/chrome-hote"):
    """Dans la VM : l'adresse où joindre Chrome (DevTools) et l'écran. 127.0.0.1 sous Lima ; sous WSL,
    l'espace de noms réseau isolé (10.200.0.2), noté par vm/wsl.py dans /etc/piston/chrome-hote et dans
    JOBSEARCH_CHROME_HOTE (les sessions de l'IA n'héritent pas toujours de l'environnement)."""
    import os
    env = os.environ if env is None else env
    if env.get("JOBSEARCH_CHROME_HOTE"):
        return env["JOBSEARCH_CHROME_HOTE"]
    try:
        with open(fichier, encoding="utf-8") as f:
            return f.read().strip() or "127.0.0.1"
    except OSError:
        return "127.0.0.1"


def adresse_devtools():
    return "http://%s:%d" % (hote_chrome(), PORT_DEVTOOLS)


def lancer(port, port_ecran):
    return LANCER % {"donnees": DONNEES, "code": CODE, "port": port, "ecran": port_ecran, "vnc": PORT_VNC,
                     "devtools": PORT_DEVTOOLS}
