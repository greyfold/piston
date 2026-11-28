"""L'hyperviseur du Mac : Lima, sur Virtualization.framework d'Apple (et, plus tard, QEMU/KVM sous Linux).

Lima est embarqué dans l'application (JOBSEARCH_LIMACTL) et range ses machines dans le dossier des
données (LIMA_HOME), à part de toute autre installation de Lima. La VM ne voit qu'un dossier, monté
par virtiofs (`partage-vm/` → /donnees), et n'ouvre vers le Mac que deux ports, sur 127.0.0.1.

Les noms partagés avec le reste de `vm` (EXECUTER, DATA, limactl, configuration…) sont lus sur le paquet
`jobsearch.vm` au moment de l'appel : les tests les y remplacent.
"""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import yaml

from . import invite

IMAGES = {
    "aarch64": "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-arm64.img",
    "x86_64": "https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img",
}
IMAGE = IMAGES["aarch64"]


def _vm():
    from .. import vm
    return vm


def limactl():
    """Le Lima embarqué dans l'application (JOBSEARCH_LIMACTL, posé par l'application : rien
    d'autre n'est cherché). Hors application seulement (développement dans le dépôt, `uv run
    jobsearch web`), celui du PATH."""
    exe = os.environ.get("JOBSEARCH_LIMACTL") or shutil.which("limactl") or ""
    if not exe or not Path(exe).exists():
        raise _vm().VMImpossible("Lima introuvable : l'application est incomplète (ou, en développement, brew install lima)")
    return exe


def environnement_lima():
    """Les machines de l'application dans son dossier de données, à part de tout autre Lima."""
    v = _vm()
    env = dict(os.environ, LIMA_HOME=str(v.DATA / "lima"))
    (v.DATA / "lima").mkdir(parents=True, exist_ok=True)
    return env


def configuration(partage=None):
    """Le fichier Lima de la VM : un seul dossier monté (partage-vm, jamais le dossier des
    données entier), un seul port vers le travailleur, un vers l'écran."""
    from ..travailleur import jeton as jeton_vm
    v = _vm()
    donnees = Path(partage or jeton_vm.partage())
    donnees.mkdir(parents=True, exist_ok=True)
    os.chmod(donnees, 0o700)
    return {
        "vmType": "vz", "os": "Linux", "arch": "aarch64",
        "cpus": int(v.r().get("cpus", 2)), "memory": str(v.r().get("memoire", "3GiB")),
        "disk": str(v.r().get("disque", "20GiB")),
        "images": [{"location": IMAGE, "arch": "aarch64"}],
        "mountType": "virtiofs",
        "mounts": [{"location": str(donnees), "mountPoint": invite.DONNEES, "writable": True}],
        "portForwards": [{"guestPort": v.port(), "hostIP": "127.0.0.1", "hostPort": v.port()},
                         {"guestPort": v.port_ecran(), "hostIP": "127.0.0.1", "hostPort": v.port_ecran()},
                         {"guestPortRange": [1, 65535], "proto": "any", "ignore": True},
                         # aussi les ports ouverts sur toutes les interfaces de la VM (UDP de Chrome…)
                         {"guestIP": "0.0.0.0", "guestPortRange": [1, 65535], "proto": "any", "ignore": True}],
        "containerd": {"system": False, "user": False},
        "provision": [{"mode": "system", "script": invite.PROVISION_SYSTEME},
                      {"mode": "user", "script": invite.PROVISION_UTILISATEUR}],
    }


def _lima(*args, **kw):
    v = _vm()
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("env", v.environnement_lima())
    return v.EXECUTER([v.limactl(), *args], **kw)


# --- L'interface d'un hyperviseur (vm/__init__.py) ------------------------------------------


def instance():
    """La description Lima de la VM (« status » : Running, Stopped…), ou None si elle n'existe pas."""
    res = _lima("list", "--json")
    for ligne in (res.stdout or "").splitlines():
        try:
            d = json.loads(ligne)
        except ValueError:
            continue
        if d.get("name") == _vm().nom():
            return d
    return None


def shell(script, timeout=600, root=False, **kw):
    """Un script bash dans la VM, au nom de son compte (ou de root : `sudo`, permis sous Lima)."""
    cmd = ["sudo", "bash", "-c", script] if root else ["bash", "-c", script]
    return _lima("shell", "--workdir", "/", _vm().nom(), "--", *cmd, timeout=timeout, **kw)


def creer(timeout=1800):
    v = _vm()
    (v.DATA / "vm").mkdir(parents=True, exist_ok=True)
    fichier = v.DATA / "vm" / ("%s.yaml" % v.nom())
    fichier.write_text(yaml.safe_dump(v.configuration(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    res = _lima("create", "--name", v.nom(), "--tty=false", str(fichier), timeout=timeout)
    if res.returncode != 0:
        raise v.VMImpossible("création refusée par Lima : %s" % (res.stderr or "").strip()[-300:])
    return "Machine virtuelle « %s » créée (%s)." % (v.nom(), fichier)


def allumer():
    """Démarre la VM ; la première fois, Lima installe aussi le système (provision)."""
    v = _vm()
    v._tuer_agents()                              # VM éteinte : un agent resté ne doit pas la doubler
    res = _lima("start", "--tty=false", v.nom(), timeout=1800)
    if res.returncode != 0:
        raise v.VMImpossible("démarrage refusé par Lima : %s" % (res.stderr or "").strip()[-300:])


def copier(dossier, destination):
    """Copie le dossier local `dossier` dans la VM, vers `destination` (relatif à son dossier personnel)."""
    v = _vm()
    res = _lima("copy", "-r", str(dossier), "%s:%s" % (v.nom(), destination), timeout=300)
    if res.returncode != 0:
        raise v.VMImpossible("copie du code refusée : %s" % (res.stderr or "").strip()[-300:])


def ports_a_jour(i):
    """Les ports que la VM ouvre vers le Mac sont-ils ceux des réglages ? (changés après sa création)"""
    vus = [(p.get("guestPort"), p.get("hostPort")) for p in ((i or {}).get("config") or {}).get("portForwards") or []
           if not p.get("ignore")]
    voulus = [(p["guestPort"], p["hostPort"]) for p in _vm().configuration()["portForwards"] if not p.get("ignore")]
    return vus[:len(voulus)] == voulus


def mettre_les_ports_a_jour():
    """Réécrit les ports de la VM (arrêtée le temps de l'écriture)."""
    v = _vm()
    if (v.instance() or {}).get("status") == "Running":
        v._arreter_vm()
    res = _lima("edit", v.nom(), "--tty=false", "--set", ".portForwards = %s" % json.dumps(v.configuration()["portForwards"]),
                timeout=120)
    if res.returncode != 0:
        raise v.VMImpossible("ports de la VM non mis à jour : %s" % (res.stderr or "").strip()[-300:])


def eteindre(attente_s=60):
    """Arrêt propre en quelques secondes : le système de la VM s'éteint lui-même (`poweroff`), et Lima
    le voit s'arrêter. `limactl stop` seul attend de la VM un événement qu'elle ne rend pas : 190 s
    puis échec, VM toujours allumée (constaté le 25.11). Si elle tourne encore après `attente_s`,
    arrêt forcé (rien de la personne n'est dans la VM, qui ne garde que son système, le profil de
    Chrome et la connexion aux moteurs d'IA). Enfin, aucun agent Lima ne doit rester derrière."""
    v = _vm()
    try:
        _lima("shell", "--workdir", "/", v.nom(), "--", "sudo", "systemctl", "poweroff", "--no-block", timeout=20)
    except subprocess.TimeoutExpired:
        pass
    fin = time.monotonic() + attente_s
    while time.monotonic() < fin and (v.instance() or {}).get("status") != "Stopped":
        time.sleep(1)
    if (v.instance() or {}).get("status") != "Stopped":
        _lima("stop", "--force", v.nom(), timeout=60)
    v._tuer_agents()


def agents():
    """Les agents Lima (`limactl hostagent`) de cette VM. Il ne doit y en avoir qu'un, et aucun VM
    éteinte : des agents restés d'arrêts manqués (jusqu'à quatre, le 25.11, dont un du 9.11) faisaient
    croire la VM allumée et bloquaient tout arrêt."""
    v = _vm()
    prise = str(v.DATA / "lima" / v.nom() / "ha.sock")
    r = v.EXECUTER(["/bin/ps", "-axo", "pid=,command="], capture_output=True, text=True)
    out = []
    for ligne in (r.stdout or "").splitlines():
        pid, _, commande = ligne.strip().partition(" ")
        if "hostagent" in commande and prise in commande and pid.isdigit():
            out.append(int(pid))
    return out


def nettoyer():
    """Termine les agents restés après l'arrêt de la VM. Ils ignorent parfois SIGTERM : SIGKILL ensuite."""
    v = _vm()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = v._agents()
        if not pids:
            return
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        time.sleep(3)


def tuer(motif):
    """`pkill -f motif` dans la VM (une connexion de moteur laissée en route)."""
    v = _vm()
    _lima("shell", "--workdir", "/", v.nom(), "--", "pkill", "-f", motif, timeout=30)


def interactif(script):
    """Un script dans la VM sur l'entrée et la sortie de ce processus (la connexion d'un moteur) ; son code."""
    v = _vm()
    return subprocess.call([v.limactl(), "shell", "--workdir", "/", v.nom(), "--", "bash", "-lc", script],
                           env=v.environnement_lima())


def supprimer():
    v = _vm()
    _lima("delete", "--force", v.nom(), timeout=300)


def garder_allumee():
    """Rien à faire : sous Lima, la VM reste allumée tant qu'on ne l'arrête pas."""
    return None
