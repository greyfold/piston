"""L'hyperviseur de Windows : une distribution WSL2 dédiée, rangée dans le dossier des données.

La distribution (« Piston-<nom> ») est importée d'une image Ubuntu 24.04 de Canonical, somme SHA-256
vérifiée, dans `DATA/wsl/` : son disque (ext4.vhdx) vit avec les données de l'application, et
`wsl --unregister` l'efface. Rien d'autre n'est installé ni modifié : le `.wslconfig` de la personne
(réglages communs à toutes ses distributions) n'est jamais touché.

WSL isole moins que Virtualization.framework : root dans la distribution peut monter les disques de
Windows (drvfs) et, si l'interopérabilité est permise, lancer des programmes Windows. D'où ce durcissement,
reposé à chaque démarrage :
- `/etc/wsl.conf` : pas de montage automatique des disques, pas d'interopérabilité (aucun .exe), pas de
  PATH de Windows ; le compte par défaut est `piston` ;
- le compte `piston`, qui fait tourner le travailleur, les moteurs d'IA et Chrome, n'a pas sudo : ce qui
  demande root est lancé depuis Windows (`wsl -u root`), jamais depuis la VM ;
- seul `partage-vm/` est monté (drvfs, fichiers du seul compte), sur /donnees ;
- WSL relaie vers Windows tous les ports qu'une distribution ouvre sur sa boucle locale : Chrome (DevTools)
  et l'écran (VNC) tournent donc dans un espace de noms réseau à part (`piston-isole`), joint depuis la
  distribution par 10.200.0.2 seulement ; seuls le travailleur et noVNC (mot de passe exigé) écoutent sur
  la boucle locale, donc sur celle de Windows.

Une distribution sans session ouverte s'éteint au bout de quelques secondes : une session `sleep infinity`
la garde allumée tant que l'application tourne (`garder_allumee`), et `eteindre` l'arrête.

Limites connues, propres à WSL : le nombre de processeurs et la mémoire se règlent pour toutes les
distributions à la fois (`.wslconfig`), pas pour celle-ci ; `vm.cpus` et `vm.memoire` n'ont pas d'effet.
"""

import hashlib
import io
import os
import platform
import subprocess
import tarfile
from pathlib import Path

from . import invite

COMPTE = "piston"
NETNS = "piston-isole"
ADRESSE_HOTE, ADRESSE_ISOLEE = "10.200.0.1", "10.200.0.2"
MARQUE_SYSTEME = "/var/lib/piston/systeme-installe"
IMAGES = "https://cloud-images.ubuntu.com/wsl/releases/24.04/current/"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

CONFIGURER = """set -e
# Sans UID imposé : l'image d'Ubuntu a déjà un compte « ubuntu » (UID 1000).
id -u %(compte)s >/dev/null 2>&1 || useradd -m -s /bin/bash %(compte)s
# Le compte de la VM n'a pas sudo : ce qui demande root vient de Windows (wsl -u root).
gpasswd -d %(compte)s sudo >/dev/null 2>&1 || true
rm -f /etc/sudoers.d/%(compte)s
cat > /etc/wsl.conf.piston <<'WSL'
[boot]
systemd=false

[automount]
enabled=false
mountFsTab=false

[interop]
enabled=false
appendWindowsPath=false

[user]
default=%(compte)s
WSL
# wsl.conf ne vaut qu'au démarrage de la distribution : « change » demande de l'arrêter.
if cmp -s /etc/wsl.conf.piston /etc/wsl.conf; then rm -f /etc/wsl.conf.piston; else mv /etc/wsl.conf.piston /etc/wsl.conf; echo change; fi
mkdir -p %(donnees)s /var/lib/piston /etc/piston
"""

# Le dossier partagé, seul dossier de Windows que voit la VM ; fichiers du seul compte de la VM.
MONTER = """set -e
mkdir -p %(donnees)s
U=$(id -u %(compte)s); G=$(id -g %(compte)s)
mountpoint -q %(donnees)s || mount -t drvfs '%(source)s' %(donnees)s -o metadata,uid=$U,gid=$G,umask=077,fmask=077
"""

# L'espace de noms réseau de Chrome et de l'écran : une paire veth vers la distribution, la sortie vers
# Internet par NAT ; DevTools et VNC restent sur la boucle locale de l'espace isolé, relayés (socat) sur
# 10.200.0.2 seulement, que Windows ne voit pas. Puis l'écran, Openbox et Chrome, au nom du compte.
ISOLER = """set -e
if ! ip netns list | grep -qw %(netns)s; then
  ip netns add %(netns)s
  ip link add pi-hote type veth peer name pi-isole
  ip link set pi-isole netns %(netns)s
  ip addr add %(hote)s/30 dev pi-hote && ip link set pi-hote up
  ip netns exec %(netns)s ip addr add %(isolee)s/30 dev pi-isole
  ip netns exec %(netns)s ip link set pi-isole up
  ip netns exec %(netns)s ip link set lo up
  ip netns exec %(netns)s ip route add default via %(hote)s
  sysctl -qw net.ipv4.ip_forward=1
  nft add table ip piston
  nft add chain ip piston sortie '{ type nat hook postrouting priority 100 ; }'
  nft add rule ip piston sortie ip saddr %(isolee)s masquerade
  mkdir -p /etc/netns/%(netns)s
  cp -L /etc/resolv.conf /etc/netns/%(netns)s/resolv.conf
fi
echo %(isolee)s > /etc/piston/chrome-hote
U() { ip netns exec %(netns)s setpriv --reuid=%(compte)s --regid=%(compte)s --init-groups \\
      env HOME=/home/%(compte)s DISPLAY=:1 PATH=/usr/local/bin:/usr/bin:/bin "$@"; }
mkdir -p %(donnees)s/logs
U bash -c 'mkdir -p "$HOME/.vnc" && chmod 700 "$HOME/.vnc" && vncpasswd -f < %(donnees)s/ecran.motdepasse > "$HOME/.vnc/passwd" && chmod 600 "$HOME/.vnc/passwd"'
pgrep -x Xvnc >/dev/null || { U nohup Xvnc :1 -geometry 1440x900 -depth 24 -localhost yes -rfbport %(vnc)d \\
  -SecurityTypes VncAuth -rfbauth /home/%(compte)s/.vnc/passwd >> %(donnees)s/logs/ecran.log 2>&1 & sleep 2; }
pgrep -x openbox >/dev/null || { U nohup openbox >> %(donnees)s/logs/ecran.log 2>&1 & }
pgrep -x chrome >/dev/null || { U nohup google-chrome --remote-debugging-port=%(devtools)d \\
  --remote-debugging-address=127.0.0.1 --user-data-dir=/home/%(compte)s/chrome-profil --no-first-run \\
  --no-default-browser-check --hide-crash-restore-bubble --start-maximized --lang=fr-CH about:blank >> %(donnees)s/logs/chrome.log 2>&1 & }
pkill -f "[s]ocat TCP-LISTEN" || true
U nohup socat TCP-LISTEN:%(devtools)d,bind=%(isolee)s,fork,reuseaddr TCP:127.0.0.1:%(devtools)d >/dev/null 2>&1 &
U nohup socat TCP-LISTEN:%(vnc)d,bind=%(isolee)s,fork,reuseaddr TCP:127.0.0.1:%(vnc)d >/dev/null 2>&1 &
"""


def _vm():
    from .. import vm
    return vm


def distribution():
    return "Piston-%s" % _vm().nom()


def architecture():
    """amd64 (PC) ou arm64 (PC ARM), d'après le processeur de Windows."""
    m = (platform.machine() or "").lower()
    return "arm64" if m in ("arm64", "aarch64") else "amd64"


def _env():
    return dict(os.environ, WSL_UTF8="1")       # wsl.exe écrit ses propres messages en UTF-8, pas UTF-16


def _wsl(*args, input=None, **kw):
    """wsl.exe, en octets : en mode texte, Python traduirait sous Windows les fins de ligne de l'entrée en
    CRLF, et bash refuserait le script (« $'\\r': command not found »). Les sorties reviennent en texte."""
    v = _vm()
    kw.setdefault("capture_output", True)
    kw.setdefault("env", _env())
    kw.setdefault("creationflags", CREATE_NO_WINDOW)
    kw.pop("text", None)
    if isinstance(input, str):
        input = input.encode("utf-8")
    try:
        r = v.EXECUTER(["wsl.exe", *args], input=input, **kw)
    except FileNotFoundError:
        raise v.VMImpossible(PAS_DE_WSL)
    texte = lambda b: b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else (b or "")
    return subprocess.CompletedProcess(r.args, r.returncode, stdout=texte(r.stdout), stderr=texte(r.stderr))


PAS_DE_WSL = ("WSL n'est pas installé sur ce PC : ouvre PowerShell en administrateur, tape "
              "« wsl --install --no-distribution », redémarre le PC, puis rouvre l'application")


def _noms(*options):
    r = _wsl("--list", "--quiet", *options, timeout=60)
    if r.returncode != 0:
        if not options:
            texte = (r.stdout or "") + (r.stderr or "")
            if "--install" in texte or "not installed" in texte.lower() or "n'est pas installé" in texte.lower():
                raise _vm().VMImpossible(PAS_DE_WSL)
        return []
    return [l.strip().strip("\x00") for l in (r.stdout or "").replace("\x00", "").splitlines() if l.strip()]


def _hote_chrome():
    return ADRESSE_ISOLEE if isolation() else "127.0.0.1"


def isolation():
    """Chrome et l'écran dans leur espace de noms réseau (réglage vm.isolation_reseau, vrai par défaut)."""
    return bool(_vm().r().get("isolation_reseau", True))


# --- L'interface d'un hyperviseur (vm/__init__.py) ------------------------------------------


def instance():
    """{"name", "status": Running|Stopped}, ou None si la distribution n'existe pas. Les noms seuls
    (`--quiet`) : l'état écrit en clair par wsl.exe est traduit dans la langue de Windows."""
    d = distribution()
    if d not in _noms():
        return None
    return {"name": d, "status": "Running" if d in _noms("--running") else "Stopped"}


def shell(script, timeout=600, root=False, **kw):
    """Un script bash dans la distribution, par l'entrée standard (aucune limite de ligne de commande,
    aucun guillemet à échapper), au nom de `piston` ou de root."""
    entete = "export JOBSEARCH_CHROME_HOTE=%s\n" % _hote_chrome() if not root else ""
    return _wsl("-d", distribution(), "-u", "root" if root else COMPTE, "--cd", "~", "-e", "bash", "-s",
                input=entete + script, timeout=timeout, **kw)


def _image(dossier):
    """L'image Ubuntu de Canonical pour WSL, somme SHA-256 vérifiée contre SHA256SUMS (HTTPS)."""
    import httpx
    v = _vm()
    nom = "ubuntu-noble-wsl-%s-wsl.rootfs.tar.gz" % architecture()
    dossier.mkdir(parents=True, exist_ok=True)
    cible = dossier / nom
    with httpx.Client(timeout=60, follow_redirects=True) as http:
        sommes = http.get(IMAGES + "SHA256SUMS").text
        attendue = next((l.split()[0] for l in sommes.splitlines() if l.strip().endswith(nom)), None)
        if not attendue:
            raise v.VMImpossible("image Ubuntu introuvable dans SHA256SUMS (%s)" % nom)
        if not cible.exists() or hashlib.sha256(cible.read_bytes()).hexdigest() != attendue:
            h = hashlib.sha256()
            partiel = cible.with_suffix(".part")
            with http.stream("GET", IMAGES + nom, timeout=600) as r, open(partiel, "wb") as f:
                r.raise_for_status()
                for bloc in r.iter_bytes(1 << 20):
                    h.update(bloc)
                    f.write(bloc)
            if h.hexdigest() != attendue:
                partiel.unlink(missing_ok=True)
                raise v.VMImpossible("image Ubuntu : somme de contrôle fausse, téléchargement rejeté")
            partiel.replace(cible)
    return cible


def creer(timeout=1800):
    v = _vm()
    _noms()                                          # WSL absent : message clair avant tout téléchargement
    image = _image(v.DATA / "vm" / "images")
    dossier = v.DATA / "wsl" / distribution()
    dossier.mkdir(parents=True, exist_ok=True)
    r = _wsl("--import", distribution(), str(dossier), str(image), "--version", "2", timeout=timeout)
    if r.returncode != 0:
        raise v.VMImpossible("import refusé par WSL : %s" % ((r.stderr or r.stdout or "").strip()[-300:]))
    _durcir()
    return "Machine virtuelle « %s » créée (%s)." % (distribution(), dossier)


def _durcir():
    """Le compte sans sudo et /etc/wsl.conf ; s'il a changé, la distribution est arrêtée pour qu'il s'applique."""
    v = _vm()
    r = shell(CONFIGURER % {"compte": COMPTE, "donnees": invite.DONNEES}, root=True, timeout=120)
    if r.returncode != 0:
        raise v.VMImpossible("configuration de la VM refusée : %s" % ((r.stderr or "").strip()[-300:]))
    if "change" in (r.stdout or ""):
        _wsl("--terminate", distribution(), timeout=60)


def allumer():
    """Démarre la distribution, monte le dossier partagé ; la première fois, installe le système."""
    from ..travailleur import jeton as jeton_vm
    v = _vm()
    _durcir()                                        # reposé à chaque démarrage (une VM modifiée est remise)
    source = str(Path(jeton_vm.partage()).resolve()).replace("'", "'\\''")
    v._shell(MONTER % {"donnees": invite.DONNEES, "source": source, "compte": COMPTE}, root=True, timeout=120)
    if shell("test -e %s" % MARQUE_SYSTEME, root=True, timeout=60).returncode != 0:
        v._shell(invite.PROVISION_SYSTEME + "\napt-get install -y --no-install-recommends socat nftables\n",
                 root=True, timeout=3600)
        v._shell(invite.PROVISION_UTILISATEUR, timeout=1800)
        v._shell("mkdir -p /var/lib/piston && touch %s" % MARQUE_SYSTEME, root=True, timeout=60)
    if isolation():
        v.ecran_motdepasse()
        v._shell(ISOLER % {"netns": NETNS, "hote": ADRESSE_HOTE, "isolee": ADRESSE_ISOLEE, "compte": COMPTE,
                           "donnees": invite.DONNEES, "vnc": invite.PORT_VNC, "devtools": invite.PORT_DEVTOOLS},
                 root=True, timeout=120)
    else:
        v._shell("rm -f /etc/piston/chrome-hote", root=True, timeout=30)


def copier(dossier, destination):
    """Le dossier local vers `~/destination` de la VM, en archive tar sur l'entrée standard."""
    v = _vm()
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as t:
        t.add(str(dossier), arcname=".")
    r = _wsl("-d", distribution(), "-u", COMPTE, "--cd", "~", "-e", "bash", "-c",
             'rm -rf "$HOME/%s" && mkdir -p "$HOME/%s" && tar -xf - -C "$HOME/%s"' % ((destination,) * 3),
             input=tampon.getvalue(), timeout=300)
    if r.returncode != 0:
        raise v.VMImpossible("copie du code refusée : %s" % (r.stderr or "").strip()[-300:])


def ports_a_jour(i):
    """WSL relaie les ports de la boucle locale de la distribution : rien à régler."""
    return True


def mettre_les_ports_a_jour():
    return None


def _fichier_garde():
    return _vm().DATA / "vm" / "garde.pid"


def garder_allumee():
    """Une session `sleep infinity`, détachée : la distribution reste allumée après la fin de
    `jobsearch vm demarrer`, tant que l'application ne l'arrête pas."""
    f = _fichier_garde()
    try:
        pid = int(f.read_text())
        import psutil
        if psutil.pid_exists(pid) and "wsl" in psutil.Process(pid).name().lower():
            return pid
    except (OSError, ValueError, ImportError, Exception):
        pass
    p = subprocess.Popen(["wsl.exe", "-d", distribution(), "-u", COMPTE, "-e", "sleep", "infinity"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=_env(), close_fds=True,
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(p.pid))
    return p.pid


def eteindre(attente_s=60):
    """`wsl --terminate` : la distribution s'arrête aussitôt (rien de la personne n'y est gardé hors du
    profil de Chrome et de la connexion aux moteurs), la session de garde avec elle."""
    _wsl("--terminate", distribution(), timeout=max(attente_s, 30))
    try:
        pid = int(_fichier_garde().read_text())
        import psutil
        p = psutil.Process(pid)
        if "wsl" in p.name().lower():
            p.kill()
    except Exception:
        pass
    _fichier_garde().unlink(missing_ok=True)


def agents():
    return []


def nettoyer():
    return None


def tuer(motif):
    shell("pkill -f %s || true" % _guillemets(motif), timeout=30)


def _guillemets(s):
    return "'" + s.replace("'", "'\\''") + "'"


def interactif(script):
    """Un script dans la VM sur l'entrée et la sortie de ce processus (la connexion d'un moteur) ; son code."""
    return subprocess.call(["wsl.exe", "-d", distribution(), "-u", COMPTE, "--cd", "~", "-e", "bash", "-lc", script],
                           env=_env(), creationflags=CREATE_NO_WINDOW)


def supprimer():
    """Désinscrit la distribution : WSL efface son disque (DATA/wsl/…)."""
    v = _vm()
    eteindre()
    _wsl("--unregister", distribution(), timeout=300)
    import shutil
    shutil.rmtree(v.DATA / "wsl" / distribution(), ignore_errors=True)
