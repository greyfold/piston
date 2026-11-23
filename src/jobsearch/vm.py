"""La machine virtuelle du travailleur, par Lima (Virtualization.framework d'Apple).

`jobsearch vm creer|demarrer|arreter|etat|connexion|supprimer`.

Ce que la VM voit et fait :
- un seul dossier : `partage-vm/` des données (/donnees : jeton, journaux et cache du
  travailleur) ; ni le profil, ni la base, ni les sauvegardes, ni les dossiers de
  candidature, ni le dossier personnel, ni le trousseau, ni Mail. Le code est copié
  dans la VM à chaque démarrage ;
- deux ports vers le Mac, sur 127.0.0.1 seulement : le travailleur (jeton exigé) et
  l'écran (noVNC, mot de passe exigé), que l'application montre dans une fenêtre ;
- Ubuntu, Python, uv, Claude Code, Google Chrome, Node.js et le serveur MCP de Chrome
  (chrome-devtools-mcp, version figée), installés au premier démarrage depuis leurs
  sources officielles ; un écran virtuel (TigerVNC, Openbox) où tourne Chrome, avec son
  propre profil dans la VM : la personne s'y connecte aux sites (Job-Room…) en le voyant
  dans l'application, et Claude le pilote par le protocole DevTools (port local de la VM) ;
- le code (src, pyproject, config) est copié puis installé dans un environnement de la VM
  (`~/venv`) à chaque démarrage.

Claude Code ne tourne que dans la VM : rien de ce que fait l'IA ne touche le Mac.
Lima est embarqué dans l'application (JOBSEARCH_LIMACTL) et range ses machines dans le
dossier des données (LIMA_HOME), à part de toute autre installation de Lima.

Connexion à Claude : `jobsearch vm connexion` ouvre `claude auth login` dans la VM ;
l'utilisateur se connecte à son propre compte Claude, et les identifiants restent
dans la VM. Aucune clé, aucun jeton Claude ne passe par le Mac.

Le framework de conteneurs d'Apple (macOS 26) pourra remplacer Lima : seul ce
module en dépend.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from .config import DATA, RACINE, reglages
from .travailleur import jeton as jeton_vm

IMAGE = "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-arm64.img"
DONNEES, CODE = "/donnees", "code-jobsearch"     # CODE : relatif au dossier personnel de la VM

MCP_CHROME = "chrome-devtools-mcp@1.12.0"     # version figée, relue avant chaque changement
PORT_DEVTOOLS = 9222                           # Chrome, sur 127.0.0.1 dans la VM seulement
PORT_VNC = 5901                                # l'écran, sur 127.0.0.1 dans la VM seulement

PROVISION_SYSTEME = """#!/bin/bash
set -eux
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv curl ca-certificates git xz-utils \\
  tigervnc-standalone-server tigervnc-tools openbox novnc websockify \\
  fonts-dejavu fonts-liberation fonts-noto-color-emoji
# Google Chrome pour Linux arm64, depuis Google (le paquet ajoute aussi le dépôt de mises à jour).
if ! command -v google-chrome >/dev/null; then
  curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb
  apt-get install -y /tmp/chrome.deb
  rm -f /tmp/chrome.deb
fi
# Node.js 22 (pour le serveur MCP de Chrome), depuis nodejs.org, somme de contrôle vérifiée.
if ! /opt/node/bin/node --version 2>/dev/null | grep -q '^v22'; then
  cd /tmp
  curl -fsSLO https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt
  F=$(grep -oE 'node-v22[0-9.]+-linux-arm64.tar.xz' SHASUMS256.txt | head -1)
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

# Lancement du travailleur dans la VM : l'environnement de la VM, les données montées,
# le code en lecture seule. Le jeton est lu dans /donnees (fichier du seul compte).
POLITIQUE_CHROME = {
    "PasswordManagerEnabled": False, "AutofillAddressEnabled": False, "AutofillCreditCardEnabled": False,
    "BrowserSignin": 0, "SyncDisabled": True, "MetricsReportingEnabled": False, "SearchSuggestEnabled": False,
    "PromotionalTabsEnabled": False, "BackgroundModeEnabled": False, "RestoreOnStartup": 5,
    "DefaultNotificationsSetting": 2, "DefaultGeolocationSetting": 2,
    # Aucune page locale : ni les fichiers de la VM (dont /donnees : CV, coordonnées, jeton), ni
    # leur source. Les documents joints passent par le champ de fichier (DevTools), pas par une
    # adresse file://, et le PDF s'imprime depuis un contenu posé dans la page : ni l'un ni
    # l'autre n'est touché. Reposée à chaque démarrage (LANCER), donc aussi sur une VM déjà créée.
    "URLBlocklist": ["file://*", "view-source:*"],
}

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
# Stratégies de Chrome : rien de la personne n'y est gardé hors des cookies de connexion aux sites
# (ni mots de passe, ni adresses, ni cartes), ni compte Google, ni synchronisation, ni rapports.
sudo mkdir -p /etc/opt/chrome/policies/managed
echo '%(politique)s' | sudo tee /etc/opt/chrome/policies/managed/recherche-emploi.json >/dev/null
# Par nom exact (-x) : la ligne de commande de ce script contient elle-même ces mots.
pgrep -x chrome >/dev/null || { nohup google-chrome --remote-debugging-port=%(devtools)d \\
  --remote-debugging-address=127.0.0.1 --user-data-dir="$HOME/chrome-profil" --no-first-run \\
  --no-default-browser-check --hide-crash-restore-bubble --start-maximized --lang=fr-CH about:blank >> %(donnees)s/logs/chrome.log 2>&1 & }
pkill -x websockify || true
nohup websockify --web /usr/share/novnc 127.0.0.1:%(ecran)d 127.0.0.1:%(vnc)d >> %(donnees)s/logs/ecran.log 2>&1 &
pkill -f "[v]env/bin/jobsearch travailleur" || true    # code réinstallé : on repart ([v] : pas ce script lui-même)
sleep 1
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


class VMImpossible(Exception):
    pass


def r():
    return reglages().get("vm") or {}


def nom():
    return r().get("nom", "jobsearch")


def port():
    return int(r().get("port", 8776))


def port_ecran():
    return int(r().get("port_ecran", 8777))


def limactl():
    """Le Lima embarqué dans l'application (JOBSEARCH_LIMACTL, posé par l'application : rien
    d'autre n'est cherché). Hors application seulement (développement dans le dépôt, `uv run
    jobsearch web`), celui du PATH."""
    exe = os.environ.get("JOBSEARCH_LIMACTL") or shutil.which("limactl") or ""
    if not exe or not Path(exe).exists():
        raise VMImpossible("Lima introuvable : l'application est incomplète (ou, en développement, brew install lima)")
    return exe


def environnement_lima():
    """Les machines de l'application dans son dossier de données, à part de tout autre Lima."""
    import os
    env = dict(os.environ, LIMA_HOME=str(DATA / "lima"))
    (DATA / "lima").mkdir(parents=True, exist_ok=True)
    return env


def ecran_motdepasse():
    """Le mot de passe de l'écran (VNC), dans le dossier partagé : lu par la VM au lancement,
    par le tableau de bord pour ouvrir l'écran. VNC n'en garde que 8 caractères."""
    import secrets
    from .travailleur.jeton import partage
    f = partage() / "ecran.motdepasse"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_urlsafe(6)[:8])
    return f.read_text(encoding="utf-8").strip()


def adresse_ecran():
    return "http://127.0.0.1:%d/vnc.html?autoconnect=1&resize=scale&reconnect=1&password=%s" % (
        port_ecran(), ecran_motdepasse())


def configuration(partage=None):
    """Le fichier Lima de la VM : un seul dossier monté (partage-vm, jamais le dossier des
    données entier), un seul port."""
    donnees = Path(partage or jeton_vm.partage())
    donnees.mkdir(parents=True, exist_ok=True)
    os.chmod(donnees, 0o700)
    return {
        "vmType": "vz", "os": "Linux", "arch": "aarch64",
        "cpus": int(r().get("cpus", 2)), "memory": str(r().get("memoire", "3GiB")),
        "disk": str(r().get("disque", "20GiB")),
        "images": [{"location": IMAGE, "arch": "aarch64"}],
        "mountType": "virtiofs",
        "mounts": [{"location": str(donnees), "mountPoint": DONNEES, "writable": True}],
        "portForwards": [{"guestPort": port(), "hostIP": "127.0.0.1", "hostPort": port()},
                         {"guestPort": port_ecran(), "hostIP": "127.0.0.1", "hostPort": port_ecran()},
                         {"guestPortRange": [1, 65535], "proto": "any", "ignore": True},
                         # aussi les ports ouverts sur toutes les interfaces de la VM (UDP de Chrome…)
                         {"guestIP": "0.0.0.0", "guestPortRange": [1, 65535], "proto": "any", "ignore": True}],
        "containerd": {"system": False, "user": False},
        "provision": [{"mode": "system", "script": PROVISION_SYSTEME},
                      {"mode": "user", "script": PROVISION_UTILISATEUR}],
    }


EXECUTER = subprocess.run          # remplacé dans les tests


def _lima(*args, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("env", environnement_lima())
    return EXECUTER([limactl(), *args], **kw)


def instance():
    """La description Lima de la VM, ou None si elle n'existe pas."""
    res = _lima("list", "--json")
    for ligne in (res.stdout or "").splitlines():
        try:
            d = json.loads(ligne)
        except ValueError:
            continue
        if d.get("name") == nom():
            return d
    return None


def _shell(script, timeout=600, **kw):
    res = _lima("shell", "--workdir", "/", nom(), "--", "bash", "-c", script, timeout=timeout, **kw)
    if res.returncode != 0:
        raise VMImpossible("commande en échec dans la VM : %s" % ((res.stderr or res.stdout or "").strip()[-300:]))
    return res


def creer(timeout=1800):
    if instance():
        return "La machine virtuelle « %s » existe déjà." % nom()
    (DATA / "vm").mkdir(parents=True, exist_ok=True)
    fichier = DATA / "vm" / ("%s.yaml" % nom())
    fichier.write_text(yaml.safe_dump(configuration(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    res = _lima("create", "--name", nom(), "--tty=false", str(fichier), timeout=timeout)
    if res.returncode != 0:
        raise VMImpossible("création refusée par Lima : %s" % (res.stderr or "").strip()[-300:])
    return "Machine virtuelle « %s » créée (%s)." % (nom(), fichier)


def copie_du_code(dest):
    """Ce qu'il faut au travailleur : le paquet et la configuration par défaut, rien d'autre."""
    import shutil as sh
    dest = Path(dest)
    sh.copy2(RACINE / "pyproject.toml", dest / "pyproject.toml")
    for d in ("src", "config"):
        sh.copytree(RACINE / d, dest / d, ignore=sh.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def version_python():
    """La version de Python que le projet exige (requires-python), installée par uv dans la VM."""
    import re
    m = re.search(r'requires-python\s*=\s*">=\s*(\d+\.\d+)', (RACINE / "pyproject.toml").read_text(encoding="utf-8"))
    return m.group(1) if m else "3.14"


def installer_code():
    import tempfile
    _shell('rm -rf "$HOME/%s"' % CODE, timeout=60)
    with tempfile.TemporaryDirectory(prefix="jobsearch-code-") as tmp:
        dossier = Path(tmp) / "code"
        dossier.mkdir()
        copie_du_code(dossier)
        res = _lima("copy", "-r", str(dossier), "%s:%s" % (nom(), CODE), timeout=300)
        if res.returncode != 0:
            raise VMImpossible("copie du code refusée : %s" % (res.stderr or "").strip()[-300:])
    v = version_python()
    _shell(INSTALLER % {"code": CODE, "pyv": v, "py": v.replace(".", ", ")}, timeout=900)


def ports_a_jour(i):
    """Les ports que la VM ouvre vers le Mac sont-ils ceux des réglages ? (changés après sa création)"""
    vus = [(p.get("guestPort"), p.get("hostPort")) for p in ((i or {}).get("config") or {}).get("portForwards") or []
           if not p.get("ignore")]
    voulus = [(p["guestPort"], p["hostPort"]) for p in configuration()["portForwards"] if not p.get("ignore")]
    return vus[:len(voulus)] == voulus


def _arreter_vm():
    """Arrêt propre ; s'il traîne au-delà de deux minutes, arrêt forcé (rien de la personne
    n'est dans la VM, qui ne garde que son système, Chrome et la connexion à Claude)."""
    try:
        _lima("stop", nom(), timeout=120)
    except subprocess.TimeoutExpired:
        _lima("stop", "--force", nom(), timeout=60)


def mettre_les_ports_a_jour():
    """Réécrit les ports de la VM (arrêtée le temps de l'écriture)."""
    import json as _json
    if (instance() or {}).get("status") == "Running":
        _arreter_vm()
    res = _lima("edit", nom(), "--tty=false", "--set", ".portForwards = %s" % _json.dumps(configuration()["portForwards"]),
                timeout=120)
    if res.returncode != 0:
        raise VMImpossible("ports de la VM non mis à jour : %s" % (res.stderr or "").strip()[-300:])


def demarrer(attente_s=60):
    """Démarre la VM (la crée au besoin), installe le code, lance le travailleur."""
    from .travailleur import jeton
    jeton.lire()                                  # créé côté Mac, lu par la VM dans /donnees
    if not instance():
        creer()
    elif not ports_a_jour(instance()):
        mettre_les_ports_a_jour()
    if (instance() or {}).get("status") != "Running":
        res = _lima("start", "--tty=false", nom(), timeout=1800)
        if res.returncode != 0:
            raise VMImpossible("démarrage refusé par Lima : %s" % (res.stderr or "").strip()[-300:])
    installer_code()
    ecran_motdepasse()
    _shell(LANCER % {"donnees": DONNEES, "code": CODE, "port": port(), "ecran": port_ecran(),
                     "vnc": PORT_VNC, "devtools": PORT_DEVTOOLS, "politique": json.dumps(POLITIQUE_CHROME)}, timeout=120)
    fin = time.monotonic() + attente_s
    while time.monotonic() < fin:
        try:
            return travailleur_etat()
        except Exception:
            time.sleep(1)
    raise VMImpossible("le travailleur ne répond pas sur le port %d (journal : %s)"
                       % (port(), DATA / "logs" / "travailleur.log"))


def travailleur_etat():
    from .travailleur.client import Distant
    return Distant("http://127.0.0.1:%d" % port()).etat()


def arreter():
    if not instance():
        return "Aucune machine virtuelle « %s »." % nom()
    _arreter_vm()
    return "Machine virtuelle arrêtée."


def etat():
    i = instance()
    out = {"existe": bool(i), "statut": (i or {}).get("status"), "travailleur": None}
    if i and i.get("status") == "Running":
        try:
            out["travailleur"] = travailleur_etat()
        except Exception as e:
            out["travailleur"] = {"erreur": str(e)}
    return out


def connexion():
    """`claude auth login` dans la VM, dans le terminal de l'utilisateur (interactif)."""
    if (instance() or {}).get("status") != "Running":
        raise VMImpossible("démarrer d'abord la machine virtuelle : jobsearch vm demarrer")
    # Une connexion laissée en route (fenêtre annulée côté Mac) garde son propre code : on la
    # termine d'abord, sinon le code collé pourrait venir d'une autre tentative.
    _lima("shell", "--workdir", "/", nom(), "--", "pkill", "-f", "^claude.auth.login", timeout=30)
    return subprocess.call([limactl(), "shell", "--workdir", "/", nom(), "--", "bash", "-lc",
                            'export PATH="$HOME/.local/bin:$PATH"; exec claude auth login --claudeai'],
                           env=environnement_lima())


def deconnexion():
    """`claude auth logout` dans la VM : l'application n'utilise plus l'abonnement de la personne.
    Une connexion encore en route est arrêtée d'abord."""
    if (instance() or {}).get("status") != "Running":
        raise VMImpossible("la machine virtuelle est arrêtée : démarre-la, puis recommence")
    _lima("shell", "--workdir", "/", nom(), "--", "pkill", "-f", "^claude.auth.login", timeout=30)
    _shell('export PATH="$HOME/.local/bin:$PATH"; exec claude auth logout', timeout=60)
    return "Claude est déconnecté."


def supprimer():
    if not instance():
        return "Aucune machine virtuelle « %s »." % nom()
    _lima("delete", "--force", nom(), timeout=300)
    return "Machine virtuelle supprimée (les données de l'utilisateur, sur le Mac, restent)."


def commande(action):
    try:
        if action == "creer":
            print(creer())
        elif action == "demarrer":
            print(json.dumps(demarrer(), ensure_ascii=False, indent=1))
        elif action == "arreter":
            print(arreter())
        elif action == "etat":
            print(json.dumps(etat(), ensure_ascii=False, indent=1))
        elif action == "connexion":
            return connexion()
        elif action == "deconnexion":
            print(deconnexion())
        elif action == "supprimer":
            print(supprimer())
    except (VMImpossible, subprocess.TimeoutExpired) as e:
        print("Machine virtuelle : %s" % e)
        return 1
    return 0
