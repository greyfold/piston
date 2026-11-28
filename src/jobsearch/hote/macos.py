"""macOS : trousseau (`security`), notifications et alertes (`osascript`), Application Support.

Importable sur tout système (les tests de chaque système le vérifient) : ce qui n'existe que sur un
système POSIX (`fcntl`) est importé à l'appel.
"""

import os
import subprocess
from pathlib import Path

from .commun import code_pays, region_env

LIBELLES = {
    "ordinateur": "Mac", "ton": "ton Mac", "le": "le Mac", "du": "du Mac", "sur": "sur ton Mac",
    "coffre": "trousseau", "reglages": "Réglages Système", "gestionnaire": "Moniteur d'activité",
    "systeme": "macOS", "en": "your Mac", "de": "Ihrem Mac", "de_gen": "Ihres Macs",
    "dictee": "Dictée du Mac : Fn deux fois", "cmd": "⌘", "courrier": "Apple Mail",
}

APPLICATION = "app.rechercheemploi.mac"     # CFBundleIdentifier (plateformes/macos/construire.sh)


def dossier_application():
    return Path.home() / "Library" / "Application Support" / "Recherche emploi"


def prendre(fichier, attendre=False):
    from .posix import prendre as p
    return p(fichier, attendre)


def lacher(fichier):
    from .posix import lacher as l
    l(fichier)


def options_groupe():
    return {"start_new_session": True}


def terminer_groupe(proc):
    from .posix import terminer_groupe as t
    t(proc)


# --- Trousseau ---------------------------------------------------------------------------


def _run(*a, **k):
    return subprocess.run(*a, **k)


class Coffre:
    """Le trousseau de macOS par `security`. Une clé ne passe jamais en argument : on l'écrit par
    l'entrée standard de `security -i` (une ligne de commande se voit dans `ps`). `executer` : le
    lanceur de commandes (les tests en donnent un faux)."""

    def __init__(self, executer=None):
        self.executer = executer or _run

    def lire(self, service):
        r = self.executer(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

    def retirer(self, service):
        """Retire toutes les entrées du service (anciennes entrées créées à la main comprises)."""
        retire = False
        for _ in range(10):
            if self.executer(["security", "delete-generic-password", "-s", service],
                             capture_output=True, text=True).returncode != 0:
                break
            retire = True
        return retire

    def ecrire(self, service, compte, valeur):
        r = self.executer(["security", "-i"], input='add-generic-password -U -a %s -s %s -w "%s"\n' % (compte, service, valeur),
                          capture_output=True, text=True)
        return r.returncode == 0


# --- Notifications -----------------------------------------------------------------------


def _osa(texte):
    return texte.replace("\\", "\\\\").replace('"', '\\"')


def alerte_action(titre, texte, bouton):
    """Fenêtre avec un bouton qui ouvre l'application (une notification envoyée par osascript ne
    fait rien quand on clique dessus). Lancée à part : la commande qui l'appelle ne l'attend pas."""
    script = ('display alert "%s" message "%s" buttons {"Plus tard", "%s"} default button 2 giving up after 900\n'
              'if button returned of result is "%s" then tell application id "%s" to activate'
              % (_osa(titre), _osa(texte[:400]), _osa(bouton), _osa(bouton), APPLICATION))
    try:
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


def notifier(titre, texte, urgent=False):
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s"%s'
                        % (_osa(texte[:900]), _osa(titre), ' sound name "Basso"' if urgent else "")],
                       check=False, timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# --- Région, réseau --------------------------------------------------------------------


def region(env=None, lire_defaults=None):
    """Le pays des réglages de région (`defaults read -g AppleLocale` : « fr_CH »), sinon des variables de langue."""
    env = os.environ if env is None else env
    try:
        valeur = (lire_defaults or (lambda: subprocess.run(
            ["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=3).stdout))()
    except (OSError, subprocess.SubprocessError):
        valeur = ""
    return code_pays(valeur) or region_env(env)


def nom_reseau():
    """Le nom du Mac sur le réseau local (<nom>.local)."""
    try:
        r = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() + ".local" if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def partage_deposer(racine, sous, source, nom):
    from .posix import partage_deposer as f
    return f(racine, sous, source, nom)


def partage_purger(racine, sous, nom):
    from .posix import partage_purger as f
    return f(racine, sous, nom)


def partage_lire(racine, sous, nom, fichier, maximum):
    from .posix import partage_lire as f
    return f(racine, sous, nom, fichier, maximum)


def partage_lister(racine, sous):
    from .posix import partage_lister as f
    return f(racine, sous)
