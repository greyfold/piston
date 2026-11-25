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
  sources officielles ; Codex, Antigravity CLI et Grok Build (versions figées) au démarrage, s'ils
  manquent (moteur/fournisseurs.py) ; un écran virtuel (TigerVNC, Openbox) où tourne Chrome, avec son
  propre profil dans la VM : la personne s'y connecte aux sites (Job-Room…) en le voyant
  dans l'application, et le moteur d'IA le pilote par le protocole DevTools (port local de la VM) ;
- le code (src, pyproject, config) est copié puis installé dans un environnement de la VM
  (`~/venv`) à chaque démarrage.

Les moteurs d'IA ne tournent que dans la VM : rien de ce que fait l'IA ne touche le Mac.
Lima est embarqué dans l'application (JOBSEARCH_LIMACTL) et range ses machines dans le
dossier des données (LIMA_HOME), à part de toute autre installation de Lima.

Connexion à l'IA : `jobsearch vm connexion [--fournisseur …]` ouvre la connexion du moteur
dans la VM (`claude auth login`, `codex login --device-auth`, Antigravity, `grok login
--device-code`) ; la personne se connecte à son propre compte, et les identifiants restent dans
la VM. Aucune clé, aucun jeton ne passe par le Mac.

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
apt-get install -y --no-install-recommends python3 python3-venv curl ca-certificates git xz-utils bubblewrap \\
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


class VMImpossible(Exception):
    pass


# Place libre exigée sur le disque du Mac, en Go : le disque de la VM y grandit à mesure qu'elle
# écrit. Disque plein, Linux ne reçoit plus que des erreurs d'entrée-sortie (« Input/output
# error ») : mieux vaut le dire avant, en clair.
LIBRE_MIN_GO = {"creer": 15, "demarrer": 3}


def espace_libre_go():
    try:
        return shutil.disk_usage(DATA if DATA.exists() else Path.home()).free / 1024 ** 3
    except OSError:
        return None


def disque_plein(besoin_go=LIBRE_MIN_GO["demarrer"]):
    """Le message à montrer si le Mac n'a pas la place qu'il faut à la VM, sinon None."""
    libre = espace_libre_go()
    if libre is None or libre >= besoin_go:
        return None
    return ("le disque de ton Mac est presque plein (%s libres) : la machine virtuelle a besoin d'au moins "
            "%d Go. Libère de la place, puis quitte et rouvre l'application"
            % (("%.1f Go" % libre) if libre >= 1 else ("%d Mo" % (libre * 1024)), besoin_go))


def exiger_espace(besoin_go):
    message = disque_plein(besoin_go)
    if message:
        raise VMImpossible(message)


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


def _fichiers_du_code(racine):
    """Les fichiers que `copie_du_code` emporte, dans un ordre stable."""
    racine = Path(racine)
    yield racine / "pyproject.toml"
    for d in ("src", "config"):
        for f in sorted((racine / d).rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                yield f


def empreinte_code(racine=None):
    """L'empreinte du code qu'on copie dans la VM. Le travailleur rend la sienne (`/etat`) : si elle
    diffère de celle du Mac, il tourne sur un code périmé et `demarrer` le réinstalle."""
    import hashlib
    racine = Path(racine or RACINE)
    h = hashlib.sha256()
    for f in _fichiers_du_code(racine):
        h.update(f.relative_to(racine).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:16]


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
    # Posée à la fin seulement : une installation interrompue sera refaite au démarrage suivant.
    _shell('echo %s > "$HOME/%s/%s"' % (empreinte_code(), CODE, MARQUE_CODE), timeout=30)
    installer_moteurs()


MARQUE_CODE = ".empreinte-installee"     # hors de src/ et config/ : n'entre pas dans l'empreinte


def code_installe():
    """L'empreinte du code installé dans la VM (posée par `installer_code`), ou None. Une VM éteinte
    à la fermeture de l'application garde son code : rien à réinstaller s'il n'a pas changé."""
    try:
        r = _shell('cat "$HOME/%s/%s" 2>/dev/null || true' % (CODE, MARQUE_CODE), timeout=30)
    except (VMImpossible, subprocess.TimeoutExpired):
        return None
    return (r.stdout or "").strip() or None


def installer_moteurs():
    """Codex, Antigravity CLI et Grok Build, s'ils manquent. Un échec n'empêche pas le démarrage : les
    autres moteurs restent utilisables, et la page IA dit lequel n'est pas installé."""
    from .moteur import fournisseurs
    try:
        _shell(fournisseurs.installer_vm(), timeout=900)
    except (VMImpossible, subprocess.TimeoutExpired) as e:
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        with open(DATA / "logs" / "vm.log", "a", encoding="utf-8") as fh:
            fh.write("installation des moteurs d'IA : %s\n" % e)


def ports_a_jour(i):
    """Les ports que la VM ouvre vers le Mac sont-ils ceux des réglages ? (changés après sa création)"""
    vus = [(p.get("guestPort"), p.get("hostPort")) for p in ((i or {}).get("config") or {}).get("portForwards") or []
           if not p.get("ignore")]
    voulus = [(p["guestPort"], p["hostPort"]) for p in configuration()["portForwards"] if not p.get("ignore")]
    return vus[:len(voulus)] == voulus


def _arreter_vm(attente_s=60):
    """Arrêt propre en quelques secondes : le système de la VM s'éteint lui-même (`poweroff`), et Lima
    le voit s'arrêter. `limactl stop` seul attend de la VM un événement qu'elle ne rend pas : 190 s
    puis échec, VM toujours allumée (constaté le 25.11). Si elle tourne encore après `attente_s`,
    arrêt forcé (rien de la personne n'est dans la VM, qui ne garde que son système, le profil de
    Chrome et la connexion aux moteurs d'IA). Enfin, aucun agent Lima ne doit rester derrière."""
    try:
        _lima("shell", "--workdir", "/", nom(), "--", "sudo", "systemctl", "poweroff", "--no-block", timeout=20)
    except subprocess.TimeoutExpired:
        pass
    fin = time.monotonic() + attente_s
    while time.monotonic() < fin and (instance() or {}).get("status") != "Stopped":
        time.sleep(1)
    if (instance() or {}).get("status") != "Stopped":
        _lima("stop", "--force", nom(), timeout=60)
    _tuer_agents()


def _agents():
    """Les agents Lima (`limactl hostagent`) de cette VM. Il ne doit y en avoir qu'un, et aucun VM
    éteinte : des agents restés d'arrêts manqués (jusqu'à quatre, le 25.11, dont un du 9.11) faisaient
    croire la VM allumée et bloquaient tout arrêt."""
    prise = str(DATA / "lima" / nom() / "ha.sock")
    r = EXECUTER(["/bin/ps", "-axo", "pid=,command="], capture_output=True, text=True)
    out = []
    for ligne in (r.stdout or "").splitlines():
        pid, _, commande = ligne.strip().partition(" ")
        if "hostagent" in commande and prise in commande and pid.isdigit():
            out.append(int(pid))
    return out


def _tuer_agents():
    """Termine les agents restés après l'arrêt de la VM. Ils ignorent parfois SIGTERM : SIGKILL ensuite."""
    import signal
    for sig in (signal.SIGTERM, signal.SIGKILL):
        agents = _agents()
        if not agents:
            return
        for pid in agents:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        time.sleep(3)


def mettre_les_ports_a_jour():
    """Réécrit les ports de la VM (arrêtée le temps de l'écriture)."""
    import json as _json
    if (instance() or {}).get("status") == "Running":
        _arreter_vm()
    res = _lima("edit", nom(), "--tty=false", "--set", ".portForwards = %s" % _json.dumps(configuration()["portForwards"]),
                timeout=120)
    if res.returncode != 0:
        raise VMImpossible("ports de la VM non mis à jour : %s" % (res.stderr or "").strip()[-300:])


# --- Avancement du démarrage -----------------------------------------------------------
#
# L'écran de démarrage (page /demarrage) et le bandeau des autres pages montrent où en est le
# démarrage : les étapes prévues, l'étape en cours, un pourcentage. Chaque étape a une durée
# attendue : celle qu'elle a prise la dernière fois sur ce Mac, sinon une valeur par défaut. Dans
# une étape, la barre avance avec le temps sans jamais dépasser 95 % de l'étape : elle n'annonce
# jamais fini ce qui ne l'est pas.

ETAPES = {
    # id : (libellé court du Stepper, ce qui se passe, durée attendue par défaut en secondes)
    "creation": ("Création", "Téléchargement d'Ubuntu et création de la machine virtuelle", 240),
    "systeme": ("Système", "Installation du système, de Chrome et des moteurs d'IA", 900),
    "demarrage": ("Démarrage", "Démarrage de la machine virtuelle", 45),
    "installation": ("Application", "Installation de l'application dans la machine virtuelle", 300),
    "verification": ("Vérification", "Vérification des moteurs d'IA", 20),
    "lancement": ("Lancement", "Lancement du travailleur", 15),
}
PLAFOND_ETAPE = .95


def _fichier_avancement():
    return DATA / "vm" / "demarrage.json"


def _fichier_durees():
    return DATA / "vm" / "durees.json"


def _lire_json(f, defaut):
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaut


class Avancement:
    """Le démarrage en cours, écrit dans `vm/demarrage.json` à chaque étape (le tableau de bord le lit)."""

    def __init__(self, plan, premiere, annoncer):
        self.durees = _lire_json(_fichier_durees(), {})
        self.annoncer = annoncer
        self.etat = {"debut": time.time(), "premiere": premiere, "fin": None, "erreur": None,
                     "plan": list(plan), "faites": [], "etape": None, "debut_etape": None}
        self._ecrire()

    def _ecrire(self):
        f = _fichier_avancement()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.etat, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

    def _clore_etape(self):
        e = self.etat
        if e["etape"]:
            self.durees[e["etape"]] = round(time.time() - e["debut_etape"])
            e["faites"].append(e["etape"])

    def etape(self, id_, remplace=None):
        """Passe à l'étape `id_` ; `remplace` : l'étape prévue qu'elle prend à sa place (le code a changé)."""
        e = self.etat
        self._clore_etape()
        if remplace in e["plan"]:
            e["plan"][e["plan"].index(remplace)] = id_
        elif id_ not in e["plan"]:
            e["plan"].append(id_)
        e["etape"], e["debut_etape"] = id_, time.time()
        e["attendues"] = {i: self.durees.get(i, ETAPES[i][2]) for i in e["plan"]}
        self._ecrire()
        self.annoncer(ETAPES[id_][1])

    def finir(self, erreur=None):
        e = self.etat
        if erreur is None:
            self._clore_etape()
            try:
                _fichier_durees().write_text(json.dumps(self.durees), encoding="utf-8")
            except OSError:
                pass
        e["etape"], e["fin"], e["erreur"] = (e["etape"] if erreur else None), time.time(), erreur
        self._ecrire()


def en_demarrage():
    """Vrai pendant que `demarrer` tourne (il tient le verrou `vm/demarrer.lock`), dans ce processus
    ou dans un autre (l'application lance `jobsearch vm demarrer`)."""
    import fcntl
    f = DATA / "vm" / "demarrer.lock"
    if not f.exists():
        return False
    with open(f, "a") as verrou:
        try:
            fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(verrou, fcntl.LOCK_UN)
    return False


def avancement(maintenant=None):
    """Où en est le démarrage de la VM, pour l'écran de démarrage et le bandeau ; None si aucun n'a
    jamais été suivi. `en_cours`, `pourcentage`, `etapes` [{id, libelle, etat}], `detail` (l'étape en
    cours, en clair), `reste_min` (estimation), `erreur` (le dernier démarrage a échoué)."""
    e = _lire_json(_fichier_avancement(), None)
    if not e:
        return None
    maintenant = maintenant or time.time()
    en_cours = e.get("fin") is None and en_demarrage()
    attendues = e.get("attendues") or {i: ETAPES[i][2] for i in e.get("plan", [])}
    total = sum(attendues.values()) or 1
    fait = sum(attendues.get(i, 0) for i in e.get("faites", []))
    courante = e.get("etape")
    reste = total - fait
    if courante and en_cours:
        attendue = attendues.get(courante, ETAPES[courante][2])
        ecoule = maintenant - (e.get("debut_etape") or maintenant)
        fait += min(ecoule, attendue * PLAFOND_ETAPE)
        reste = max(attendue - ecoule, 0) + sum(attendues.get(i, 0) for i in e["plan"]
                                                if i not in e.get("faites", []) and i != courante)
    fini = e.get("fin") is not None and not e.get("erreur")
    etapes = [{"id": i, "libelle": ETAPES[i][0],
               "etat": "faite" if fini or i in e.get("faites", []) else "en_cours" if i == courante else "a_faire"}
              for i in e.get("plan", []) if i in ETAPES]
    return {"en_cours": en_cours, "premiere": bool(e.get("premiere")), "fini": fini,
            "erreur": e.get("erreur") or (None if fini or en_cours else "le démarrage s'est interrompu"),
            "pourcentage": 100 if fini else min(99, int(100 * fait / total)),
            "etapes": etapes, "detail": ETAPES[courante][1] if courante in ETAPES else None,
            "reste_min": max(1, round(reste / 60)) if en_cours else 0}


def demarrer(attente_s=60, annoncer=None):
    """Démarre la VM (la crée au besoin), installe le code s'il a changé, lance le travailleur. Un seul
    à la fois : l'ouverture de l'application et un passage peuvent le demander ensemble ; le second
    attend, puis trouve le travailleur à jour et n'a rien à refaire. `annoncer(texte)` reçoit chaque
    étape ; `vm/demarrage.json` suit l'avancement (écran de démarrage, bandeau des pages)."""
    import fcntl
    (DATA / "vm").mkdir(parents=True, exist_ok=True)
    with open(DATA / "vm" / "demarrer.lock", "w") as verrou:
        fcntl.flock(verrou, fcntl.LOCK_EX)
        return _demarrer(attente_s, annoncer or (lambda texte: None))


def _demarrer(attente_s, annoncer):
    from .travailleur import jeton
    jeton.lire()                                  # créé côté Mac, lu par la VM dans /donnees
    i = instance()
    if i and i.get("status") == "Running":
        # VM déjà allumée (l'application a été quittée sans l'éteindre) : rien à refaire si son
        # travailleur tourne sur le code de ce Mac ; aucun avancement à montrer.
        e = etat_a_jour()
        if e is not None:
            return e
    if not i:
        a = Avancement(["creation", "systeme", "installation", "lancement"], True, annoncer)
    elif i.get("status") != "Running":
        a = Avancement(["demarrage", "verification", "lancement"], False, annoncer)
    else:
        a = Avancement(["verification", "lancement"], False, annoncer)
    try:
        e = _demarrer_suivi(a, i, attente_s)
    except BaseException as ex:
        a.finir(erreur=str(ex) or type(ex).__name__)
        raise
    a.finir()
    return e


def _demarrer_suivi(a, i, attente_s):
    exiger_espace(LIBRE_MIN_GO["demarrer" if i else "creer"])
    if not i:
        a.etape("creation")
        creer()
    elif not ports_a_jour(i):
        mettre_les_ports_a_jour()
    if (instance() or {}).get("status") != "Running":
        # La première fois, le premier démarrage installe aussi le système (provision de Lima).
        a.etape("systeme" if a.etat["premiere"] else "demarrage")
        _tuer_agents()                            # VM éteinte : un agent resté ne doit pas la doubler
        res = _lima("start", "--tty=false", nom(), timeout=1800)
        if res.returncode != 0:
            raise VMImpossible("démarrage refusé par Lima : %s" % (res.stderr or "").strip()[-300:])
    if code_installe() != empreinte_code():
        a.etape("installation", remplace="verification")
        installer_code()
    else:
        a.etape("verification")
        installer_moteurs()                       # s'ils manquent seulement
    a.etape("lancement")
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


def etat_a_jour():
    """L'état du travailleur s'il répond et tourne sur le code de ce Mac ; sinon None."""
    try:
        e = travailleur_etat()
    except Exception:
        return None
    return e if e.get("code") == empreinte_code() else None


def assurer_a_jour():
    """Avant de confier une tâche à la VM : réinstalle le code et relance le travailleur s'il
    tourne sur un autre code que celui du Mac (application mise à jour, VM restée allumée)."""
    if etat_a_jour() is None:
        demarrer()


def travailleur_etat():
    from .travailleur.client import Distant
    return Distant("http://127.0.0.1:%d" % port()).etat()


def arreter():
    """À la fermeture de l'application, et par le menu."""
    i = instance()
    if not i:
        return "Aucune machine virtuelle « %s »." % nom()
    if i.get("status") == "Stopped":
        return "Machine virtuelle déjà arrêtée."
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


def connexion(fournisseur="claude"):
    """La connexion du moteur dans la VM, sur l'entrée et la sortie de ce processus (le tableau de
    bord les lit : adresse, code). Une connexion laissée en route (fenêtre annulée côté Mac) garde
    son propre code : on la termine d'abord, sinon le code collé pourrait venir d'une autre tentative."""
    from .moteur import fournisseurs
    f = fournisseurs.get(fournisseur)
    if (instance() or {}).get("status") != "Running":
        raise VMImpossible("démarrer d'abord la machine virtuelle : jobsearch vm demarrer")
    exiger_espace(LIBRE_MIN_GO["demarrer"])
    _lima("shell", "--workdir", "/", nom(), "--", "pkill", "-f", MOTIFS_CONNEXION[f.id], timeout=30)
    return subprocess.call([limactl(), "shell", "--workdir", "/", nom(), "--", "bash", "-lc", f.connexion()],
                           env=environnement_lima())


# Le processus d'une connexion en route, par moteur (pkill -f) : jamais le script qui le lance.
# Les crochets : le motif ne se reconnaît pas lui-même dans la commande qui le porte.
MOTIFS_CONNEXION = {"claude": "^claude.auth.login", "codex": "[c]odex login", "antigravity": "[a]ntigravity/agy$",
                    "grok": "[g]rok login"}


def deconnexion(fournisseur="claude"):
    """Déconnecte le moteur dans la VM : l'application n'utilise plus l'abonnement de la personne.
    Une connexion encore en route est arrêtée d'abord."""
    from .moteur import fournisseurs
    f = fournisseurs.get(fournisseur)
    if (instance() or {}).get("status") != "Running":
        raise VMImpossible("la machine virtuelle est arrêtée : démarre-la, puis recommence")
    _lima("shell", "--workdir", "/", nom(), "--", "pkill", "-f", MOTIFS_CONNEXION[f.id], timeout=30)
    _shell(f.deconnexion(), timeout=60)
    return "%s est déconnecté." % f.nom


def supprimer():
    if not instance():
        return "Aucune machine virtuelle « %s »." % nom()
    _lima("delete", "--force", nom(), timeout=300)
    return "Machine virtuelle supprimée (les données de l'utilisateur, sur le Mac, restent)."


def commande(action, fournisseur="claude"):
    try:
        if action == "creer":
            print(creer())
        elif action == "demarrer":
            # Les étapes, une ligne chacune, au fil de l'eau : l'application les montre en attendant.
            annoncer = lambda texte: print("étape : %s" % texte, flush=True)
            print(json.dumps(demarrer(annoncer=annoncer), ensure_ascii=False, indent=1))
        elif action == "arreter":
            print(arreter())
        elif action == "etat":
            print(json.dumps(etat(), ensure_ascii=False, indent=1))
        elif action == "connexion":
            return connexion(fournisseur)
        elif action == "deconnexion":
            print(deconnexion(fournisseur))
        elif action == "supprimer":
            print(supprimer())
    except (VMImpossible, subprocess.TimeoutExpired) as e:
        print("Machine virtuelle : %s" % e)
        return 1
    return 0
