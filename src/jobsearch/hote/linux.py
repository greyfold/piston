"""Linux (version à venir) : Secret Service (`secret-tool`), `notify-send`, dossiers XDG.

Ce qui suit marche dès aujourd'hui pour le développement et les tests sous Linux ; l'enveloppe
Linux (plateformes/linux) viendra après Windows.
"""

import os
import socket
import subprocess
from pathlib import Path

from .commun import region_env

LIBELLES = {
    "ordinateur": "ordinateur", "ton": "ton ordinateur", "le": "l'ordinateur", "du": "de l'ordinateur",
    "sur": "sur ton ordinateur", "coffre": "trousseau de clés", "reglages": "Paramètres",
    "gestionnaire": "Moniteur système", "systeme": "Linux", "en": "your computer", "de": "Ihrem Computer",
    "de_gen": "Ihres Computers", "dictee": "", "cmd": "Ctrl", "courrier": "",
}

APPLICATION = "app.rechercheemploi.piston"


def dossier_application():
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "recherche-emploi"


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


def _run(*a, **k):
    return subprocess.run(*a, **k)


class Coffre:
    """Secret Service (GNOME Keyring, KWallet) par `secret-tool` ; la clé passe par l'entrée standard."""

    def __init__(self, executer=None):
        self.executer = executer or _run

    def lire(self, service):
        try:
            r = self.executer(["secret-tool", "lookup", "service", service], capture_output=True, text=True)
        except OSError:
            return None
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

    def ecrire(self, service, compte, valeur):
        try:
            r = self.executer(["secret-tool", "store", "--label=Piston (%s)" % service, "service", service,
                               "account", compte], input=valeur, capture_output=True, text=True)
        except OSError:
            return False
        return r.returncode == 0

    def retirer(self, service):
        try:
            return self.executer(["secret-tool", "clear", "service", service], capture_output=True,
                                 text=True).returncode == 0
        except OSError:
            return False


def notifier(titre, texte, urgent=False):
    try:
        subprocess.run(["notify-send", "--app-name=Piston", "--urgency=%s" % ("critical" if urgent else "normal"),
                        titre, texte[:900]], check=False, timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def alerte_action(titre, texte, bouton):
    return notifier(titre, texte[:400], urgent=True)


def region(env=None):
    return region_env(os.environ if env is None else env)


def nom_reseau():
    try:
        nom = socket.gethostname().split(".")[0]
    except OSError:
        return None
    return (nom + ".local") if nom else None


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


def proteger_dossier(chemin):
    os.makedirs(chemin, exist_ok=True)
    os.chmod(chemin, 0o700)
    return True
