"""La machine virtuelle du travailleur, par Lima (Virtualization.framework d'Apple).

`jobsearch vm creer|demarrer|arreter|etat|connexion|supprimer`.

Ce que la VM voit et fait :
- un seul dossier : celui des données de l'utilisateur (/donnees : profil,
  journaux) ; ni le dossier personnel, ni le code du Mac, ni le trousseau, ni
  Mail, ni Chrome. Le code est copié dans la VM à chaque démarrage ;
- un seul port vers le Mac : celui du travailleur, sur 127.0.0.1 ;
- Ubuntu, Python, uv et Claude Code, installés au premier démarrage depuis leurs
  sources officielles ; le code (src, pyproject, config) est copié puis installé
  dans un environnement de la VM (`~/venv`) à chaque démarrage.

Connexion à Claude : `jobsearch vm connexion` ouvre `claude auth login` dans la VM ;
l'utilisateur se connecte à son propre compte Claude, et les identifiants restent
dans la VM. Aucune clé, aucun jeton Claude ne passe par le Mac.

Le framework de conteneurs d'Apple (macOS 26) pourra remplacer Lima : seul ce
module en dépend.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from .config import DATA, RACINE, reglages

IMAGE = "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-arm64.img"
DONNEES, CODE = "/donnees", "code-jobsearch"     # CODE : relatif au dossier personnel de la VM

PROVISION_SYSTEME = """#!/bin/bash
set -eux
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv curl ca-certificates git
"""

PROVISION_UTILISATEUR = """#!/bin/bash
set -eux
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
command -v claude || test -x "$HOME/.local/bin/claude" || curl -fsSL https://claude.ai/install.sh | bash
"""

# Lancement du travailleur dans la VM : l'environnement de la VM, les données montées,
# le code en lecture seule. Le jeton est lu dans /donnees (fichier du seul compte).
LANCER = """set -e
export PATH="$HOME/.local/bin:$PATH" JOBSEARCH_DATA=%(donnees)s JOBSEARCH_HOME="$HOME/%(code)s"
mkdir -p %(donnees)s/logs
pkill -f "[v]env/bin/jobsearch travailleur" || true    # code réinstallé : on repart ([v] : pas ce script lui-même)
sleep 1
nohup "$HOME/venv/bin/jobsearch" travailleur --hote 0.0.0.0 --port %(port)d >> %(donnees)s/logs/travailleur.log 2>&1 &
"""

INSTALLER = """set -e
export PATH="$HOME/.local/bin:$PATH"
"$HOME/venv/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] < (%(py)s))' 2>/dev/null \
  || uv venv --clear --python %(pyv)s "$HOME/venv"
uv pip install --python "$HOME/venv/bin/python" --reinstall-package jobsearch "$HOME/%(code)s"
"""


class VMImpossible(Exception):
    pass


def r():
    return reglages().get("vm") or {}


def nom():
    return r().get("nom", "jobsearch")


def port():
    return int(r().get("port", 8766))


def limactl():
    exe = shutil.which("limactl") or "/opt/homebrew/bin/limactl"
    if not Path(exe).exists():
        raise VMImpossible("Lima absent : installer « limactl » (brew install lima)")
    return exe


def configuration(donnees=None):
    """Le fichier Lima de la VM : rien de monté hors du dossier des données, un seul port."""
    donnees = Path(donnees or DATA)
    return {
        "vmType": "vz", "os": "Linux", "arch": "aarch64",
        "cpus": int(r().get("cpus", 2)), "memory": str(r().get("memoire", "3GiB")),
        "disk": str(r().get("disque", "20GiB")),
        "images": [{"location": IMAGE, "arch": "aarch64"}],
        "mountType": "virtiofs",
        "mounts": [{"location": str(donnees), "mountPoint": DONNEES, "writable": True}],
        "portForwards": [{"guestPort": port(), "hostIP": "127.0.0.1", "hostPort": port()},
                         {"guestPortRange": [1, 65535], "ignore": True}],
        "containerd": {"system": False, "user": False},
        "provision": [{"mode": "system", "script": PROVISION_SYSTEME},
                      {"mode": "user", "script": PROVISION_UTILISATEUR}],
    }


EXECUTER = subprocess.run          # remplacé dans les tests


def _lima(*args, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
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


def demarrer(attente_s=60):
    """Démarre la VM (la crée au besoin), installe le code, lance le travailleur."""
    from .travailleur import jeton
    jeton.lire()                                  # créé côté Mac, lu par la VM dans /donnees
    if not instance():
        creer()
    if (instance() or {}).get("status") != "Running":
        res = _lima("start", "--tty=false", nom(), timeout=1800)
        if res.returncode != 0:
            raise VMImpossible("démarrage refusé par Lima : %s" % (res.stderr or "").strip()[-300:])
    installer_code()
    _shell(LANCER % {"donnees": DONNEES, "code": CODE, "port": port()}, timeout=60)
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
    _lima("stop", nom(), timeout=120)
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
    return subprocess.call([limactl(), "shell", "--workdir", "/", nom(), "--", "bash", "-lc",
                            'export PATH="$HOME/.local/bin:$PATH"; claude auth login --claudeai'])


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
        elif action == "supprimer":
            print(supprimer())
    except (VMImpossible, subprocess.TimeoutExpired) as e:
        print("Machine virtuelle : %s" % e)
        return 1
    return 0
