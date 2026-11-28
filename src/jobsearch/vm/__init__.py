"""La machine virtuelle du travailleur : ce qui est commun à tous les hôtes, et l'hyperviseur de chacun.

`jobsearch vm creer|demarrer|arreter|etat|connexion|supprimer`.

L'hyperviseur dépend du système (`hyperviseur()`) : Lima sur Virtualization.framework sur Mac
(`vm/lima.py`, et QEMU/KVM sous Linux plus tard), une distribution WSL2 dédiée sous Windows
(`vm/wsl.py`). L'invité est le même partout (`vm/invite.py`) : Ubuntu, avec Python, uv, Claude Code,
Google Chrome, Node.js et le serveur MCP de Chrome (chrome-devtools-mcp, version figée), installés au
premier démarrage depuis leurs sources officielles ; Codex, Antigravity CLI et Grok Build (versions
figées) au démarrage, s'ils manquent (moteur/fournisseurs.py) ; un écran virtuel (TigerVNC, Openbox)
où tourne Chrome, avec son propre profil dans la VM.

Ce que la VM voit et fait, quel que soit l'hôte :
- un seul dossier : `partage-vm/` des données (/donnees : jeton, journaux et cache du travailleur) ;
  ni le profil, ni la base, ni les sauvegardes, ni les dossiers de candidature, ni le dossier
  personnel, ni le coffre à clés, ni le courrier. Le code est copié dans la VM quand il a changé ;
- deux ports vers l'hôte, sur 127.0.0.1 seulement : le travailleur (jeton exigé) et l'écran (noVNC,
  mot de passe exigé), que l'application montre dans une fenêtre.

Les moteurs d'IA ne tournent que dans la VM : rien de ce que fait l'IA ne touche l'ordinateur hôte.

Connexion à l'IA : `jobsearch vm connexion [--fournisseur …]` ouvre la connexion du moteur dans la VM
(`claude auth login`, `codex login --device-auth`, Antigravity, `grok login --device-code`) ; la personne
se connecte à son propre compte, et les identifiants restent dans la VM.

Les fonctions propres à l'hyperviseur sont appelées par leur nom sur ce module (instance, creer,
_shell…) : les tests les y remplacent.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import hote
from ..config import DATA, RACINE, reglages
from ..travailleur import jeton as jeton_vm
from . import invite
from .invite import (CODE, DONNEES, INSTALLER, LANCER, MCP_CHROME, POLITIQUE_CHROME, PORT_DEVTOOLS, PORT_VNC,  # noqa: F401
                     POSER_POLITIQUE, PROVISION_SYSTEME, PROVISION_UTILISATEUR)
from .lima import IMAGE, configuration, environnement_lima, limactl  # noqa: F401  (propres à Lima)

HYPERVISEUR = None
"""« lima » ou « wsl » ; None : celui du système (Lima sur Mac et Linux, WSL sous Windows)."""


def hyperviseur():
    from . import lima, wsl
    choix = HYPERVISEUR or os.environ.get("JOBSEARCH_HYPERVISEUR") or ("wsl" if hote.SYSTEME == "windows" else "lima")
    return wsl if choix == "wsl" else lima


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
    return ("le disque de %s est presque plein (%s libres) : la machine virtuelle a besoin d'au moins "
            "%d Go. Libère de la place, puis quitte et rouvre l'application"
            % (hote.libelle("ton"), ("%.1f Go" % libre) if libre >= 1 else ("%d Mo" % (libre * 1024)), besoin_go))


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


def ecran_motdepasse():
    """Le mot de passe de l'écran (VNC), dans le dossier partagé : lu par la VM au lancement,
    par le tableau de bord pour ouvrir l'écran. VNC n'en garde que 8 caractères."""
    import secrets
    from ..travailleur.jeton import partage
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



EXECUTER = subprocess.run          # remplacé dans les tests


# --- Ce que l'hyperviseur fait (vm/lima.py, vm/wsl.py) ----------------------------------------


def instance():
    """La description de la VM (« status » : Running, Stopped…), ou None si elle n'existe pas."""
    return hyperviseur().instance()


def _shell(script, timeout=600, root=False, **kw):
    """Un script bash dans la VM (au nom de son compte, ou de root) ; VMImpossible s'il échoue."""
    res = hyperviseur().shell(script, timeout=timeout, root=root, **kw)
    if res.returncode != 0:
        raise VMImpossible("commande en échec dans la VM : %s" % ((res.stderr or res.stdout or "").strip()[-300:]))
    return res


def creer(timeout=1800):
    if instance():
        return "La machine virtuelle « %s » existe déjà." % nom()
    return hyperviseur().creer(timeout=timeout)


def ports_a_jour(i):
    """Les ports que la VM ouvre vers l'hôte sont-ils ceux des réglages ? (changés après sa création)"""
    return hyperviseur().ports_a_jour(i)


def mettre_les_ports_a_jour():
    return hyperviseur().mettre_les_ports_a_jour()


def _arreter_vm(attente_s=60):
    return hyperviseur().eteindre(attente_s)


def _agents():
    return hyperviseur().agents()


def _tuer_agents():
    return hyperviseur().nettoyer()


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
        hyperviseur().copier(dossier, CODE)
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
    from ..moteur import fournisseurs
    try:
        _shell(fournisseurs.installer_vm(), timeout=900)
    except (VMImpossible, subprocess.TimeoutExpired) as e:
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        with open(DATA / "logs" / "vm.log", "a", encoding="utf-8") as fh:
            fh.write("installation des moteurs d'IA : %s\n" % e)



# --- Avancement du démarrage -----------------------------------------------------------

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
    return hote.tenu(DATA / "vm" / "demarrer.lock")


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
    with hote.verrou(DATA / "vm" / "demarrer.lock", attendre=True):
        return _demarrer(attente_s, annoncer or (lambda texte: None))


def _demarrer(attente_s, annoncer):
    from ..travailleur import jeton
    jeton.lire()                                  # créé côté Mac, lu par la VM dans /donnees
    try:
        i = instance()
    except BaseException as ex:
        Avancement(["creation", "systeme", "installation", "lancement"], True, annoncer).finir(
            erreur=str(ex) or type(ex).__name__)
        raise
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
        # La première fois, le premier démarrage installe aussi le système.
        a.etape("systeme" if a.etat["premiere"] else "demarrage")
        hyperviseur().allumer()
    if code_installe() != empreinte_code():
        a.etape("installation", remplace="verification")
        installer_code()
    else:
        a.etape("verification")
        installer_moteurs()                       # s'ils manquent seulement
    a.etape("lancement")
    ecran_motdepasse()
    _shell(POSER_POLITIQUE, root=True, timeout=60)
    _shell(LANCER % {"donnees": DONNEES, "code": CODE, "port": port(), "ecran": port_ecran(),
                     "vnc": PORT_VNC, "devtools": PORT_DEVTOOLS}, timeout=120)
    hyperviseur().garder_allumee()
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
    from ..travailleur.client import Distant
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
    bord les lit : adresse, code). Une connexion laissée en route (fenêtre annulée côté hôte) garde
    son propre code : on la termine d'abord, sinon le code collé pourrait venir d'une autre tentative."""
    from ..moteur import fournisseurs
    f = fournisseurs.get(fournisseur)
    if (instance() or {}).get("status") != "Running":
        raise VMImpossible("démarrer d'abord la machine virtuelle : jobsearch vm demarrer")
    exiger_espace(LIBRE_MIN_GO["demarrer"])
    hyperviseur().tuer(MOTIFS_CONNEXION[f.id])
    return hyperviseur().interactif(f.connexion())


# Le processus d'une connexion en route, par moteur (pkill -f) : jamais le script qui le lance.
# Les crochets : le motif ne se reconnaît pas lui-même dans la commande qui le porte.
MOTIFS_CONNEXION = {"claude": "^claude.auth.login", "codex": "[c]odex login", "antigravity": "[a]ntigravity/agy$",
                    "grok": "[g]rok login"}



def deconnexion(fournisseur="claude"):
    """Déconnecte le moteur dans la VM : l'application n'utilise plus l'abonnement de la personne.
    Une connexion encore en route est arrêtée d'abord."""
    from ..moteur import fournisseurs
    f = fournisseurs.get(fournisseur)
    if (instance() or {}).get("status") != "Running":
        raise VMImpossible("la machine virtuelle est arrêtée : démarre-la, puis recommence")
    hyperviseur().tuer(MOTIFS_CONNEXION[f.id])
    _shell(f.deconnexion(), timeout=60)
    return "%s est déconnecté." % f.nom


def supprimer():
    if not instance():
        return "Aucune machine virtuelle « %s »." % nom()
    hyperviseur().supprimer()
    return "Machine virtuelle supprimée (les données de l'utilisateur, %s, restent)." % hote.libelle("sur")


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

