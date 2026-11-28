"""L'ordinateur hôte : tout ce qui dépend du système (macOS, Windows, Linux), derrière une seule interface.

Le reste du paquet n'appelle jamais une commande ou une bibliothèque propre à un système (`security`,
`osascript`, `fcntl`, `msvcrt`, le registre…) : il passe par ce module, qui choisit l'implémentation
de l'ordinateur où il tourne (`macos.py`, `windows.py`, `linux.py`). `tests/test_plateformes.py`
le vérifie.

Ce qu'une implémentation fournit :
- `dossier_application()` : où vivent les données de la personne quand l'application les range ;
- `prendre(fichier, attendre)` / `lacher(fichier)` : un verrou exclusif sur un fichier ouvert, que
  le système libère à la mort du processus (un passage tué ne garde pas son verrou) ;
- `Coffre` : le coffre à clés du système (trousseau, gestionnaire d'identification, Secret Service) ;
- `notifier(titre, texte, urgent)` et `alerte_action(titre, texte, bouton)` ;
- `region()` : le code du pays des réglages de région (« CH ») ;
- `nom_reseau()` : le nom de l'ordinateur sur le réseau local, d'où le téléphone le joint ;
- `options_groupe()` / `terminer_groupe(proc)` : un processus et ce qu'il lance, arrêtés ensemble ;
- `LIBELLES` : les mots de l'interface qui nomment l'ordinateur et ses réglages.

La machine virtuelle a son propre module (`vm/`), avec un hyperviseur par système.
"""

import sys
from contextlib import contextmanager
from pathlib import Path


def systeme(plateforme=None):
    p = plateforme or sys.platform
    if p == "darwin":
        return "macos"
    if p.startswith("win"):
        return "windows"
    return "linux"


SYSTEME = systeme()

if SYSTEME == "macos":
    from . import macos as _impl
elif SYSTEME == "windows":
    from . import windows as _impl
else:
    from . import linux as _impl

LIBELLES = _impl.LIBELLES
"""Les mots qui nomment l'ordinateur, selon le système. Clés : ordinateur (« Mac »), ton (« ton Mac »),
le (« le Mac »), du (« du Mac »), sur (« sur ton Mac »), coffre (« trousseau »), reglages (« Réglages
Système »), gestionnaire (« Moniteur d'activité »), systeme (« macOS »), en (« your Mac »), de (« Ihrem Mac »),
de_gen (« Ihres Macs »), dictee (l'indice de la dictée du système), cmd (« ⌘ » ou « Ctrl »), courrier (le courrier
local : « Apple Mail », « Outlook », ou vide)."""


def libelle(cle):
    return LIBELLES[cle]


def dossier_application():
    return Path(_impl.dossier_application())


# --- Verrous -------------------------------------------------------------------------------


def prendre(fichier, attendre=False):
    """Verrou exclusif sur `fichier` (ouvert). Vrai s'il est pris ; faux s'il est tenu ailleurs et
    `attendre` est faux. Le système le libère si le processus meurt."""
    return _impl.prendre(fichier, attendre)


def lacher(fichier):
    _impl.lacher(fichier)


@contextmanager
def verrou(chemin, attendre=False):
    """Ouvre `chemin` et tente le verrou : rend vrai (tenu le temps du bloc) ou faux (tenu ailleurs ;
    le bloc s'exécute alors sans verrou, à l'appelant de passer son tour)."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "a+") as f:
        if not prendre(f, attendre):
            yield False
            return
        try:
            yield True
        finally:
            lacher(f)


def tenu(chemin):
    """Vrai si un autre processus tient le verrou de `chemin` (sans le garder soi-même)."""
    chemin = Path(chemin)
    if not chemin.exists():
        return False
    with verrou(chemin) as pris:
        return not pris


# --- Coffre à clés, notifications, région, processus -----------------------------------


def coffre():
    """Le coffre à clés du système. Les tests le remplacent (conftest) : jamais le vrai."""
    return _impl.Coffre()


def notifier(titre, texte, urgent=False):
    """Une notification du système ; vrai si elle est partie."""
    return _impl.notifier(titre, texte, urgent)


def alerte_action(titre, texte, bouton):
    """Une alerte dont le bouton ouvre l'application. Lancée à part : l'appelant ne l'attend pas."""
    return _impl.alerte_action(titre, texte, bouton)


def region(env=None):
    return _impl.region(env)


def nom_reseau():
    return _impl.nom_reseau()


def options_groupe():
    """Les arguments de `subprocess.Popen` qui donnent au processus son propre groupe."""
    return _impl.options_groupe()


def terminer_groupe(proc):
    """Arrête le processus et ce qu'il a lancé."""
    _impl.terminer_groupe(proc)


def partage_deposer(racine, sous, source, nom):
    """Déplace d'un coup le dossier `source` vers `racine/sous/nom` du dossier partagé avec la VM, sans
    suivre aucun lien ; FileExistsError si `nom` (ou un lien) y est déjà : la VM aurait pu le poser."""
    return _impl.partage_deposer(racine, sous, source, nom)


def partage_purger(racine, sous, nom):
    """Efface `racine/sous/nom` du dossier partagé sans suivre aucun lien ; vrai s'il existait."""
    return _impl.partage_purger(racine, sous, nom)


def partage_lire(racine, sous, nom, fichier, maximum):
    """Les octets (au plus `maximum`) de `racine/sous/nom/fichier`, fichier ordinaire atteint sans suivre
    aucun lien ; sinon None."""
    return _impl.partage_lire(racine, sous, nom, fichier, maximum)


def partage_lister(racine, sous):
    """Les noms de `racine/sous` du dossier partagé, atteint sans suivre aucun lien ; None s'il manque."""
    return _impl.partage_lister(racine, sous)


def proteger_dossier(chemin):
    """Le dossier (et ce qu'il contient) réservé au compte de la personne : 0700 sous POSIX ; sous Windows,
    des ACL sans héritage, pour le compte, SYSTEM et les administrateurs seulement."""
    return _impl.proteger_dossier(chemin)


def options_sans_console():
    """Les arguments de `subprocess.Popen` d'un processus sans fenêtre de console (Windows) ; rien ailleurs."""
    return getattr(_impl, "options_sans_console", dict)()


APPLICATION = _impl.APPLICATION
"""L'identifiant de l'application (CFBundleIdentifier sur Mac, AppUserModelID sous Windows)."""
