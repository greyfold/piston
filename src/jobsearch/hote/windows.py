"""Windows : gestionnaire d'identification (Credential Manager), notifications toast, %LOCALAPPDATA%.

Importable sur tout système (les tests de chaque système le vérifient) : `msvcrt` et les appels à
l'API Windows (ctypes) ne sont faits qu'à l'appel.
"""

import os
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path

from .commun import code_pays, echapper_xml, region_env

LIBELLES = {
    "ordinateur": "PC", "ton": "ton PC", "le": "le PC", "du": "du PC", "sur": "sur ton PC",
    "coffre": "gestionnaire d'identification", "reglages": "Paramètres Windows",
    "gestionnaire": "Gestionnaire des tâches", "systeme": "Windows", "en": "your PC", "de": "Ihrem PC",
    "de_gen": "Ihres PCs", "cmd": "Ctrl", "courrier": "Outlook",
    "dictee": "Saisie vocale de Windows : touche Windows + H",
}

APPLICATION = "app.rechercheemploi.piston"
"""L'AppUserModelID de l'application : l'identifiant de l'enveloppe (plateformes/windows/app/tauri.conf.json),
posé sur son raccourci par l'installateur ; une notification toast s'affiche à son nom."""
SCHEMA = "piston"
"""Le schéma d'adresse de l'application (piston://ouvrir) : un clic sur une notification la ramène."""

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def dossier_application():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Recherche emploi"


# --- Verrous -------------------------------------------------------------------------------


def prendre(fichier, attendre=False):
    """Verrou sur le premier octet du fichier (`msvcrt.locking`) ; Windows le libère à la mort du processus."""
    import msvcrt
    while True:
        try:
            fichier.seek(0)
            msvcrt.locking(fichier.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            if not attendre:
                return False
            time.sleep(0.2)


def lacher(fichier):
    import msvcrt
    try:
        fichier.seek(0)
        msvcrt.locking(fichier.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


# --- Processus -------------------------------------------------------------------------------


def options_sans_console():
    return {"creationflags": CREATE_NO_WINDOW}


def options_groupe():
    return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}


def terminer_groupe(proc, executer=None):
    """Le processus et tout ce qu'il a lancé (`taskkill /T`)."""
    try:
        (executer or subprocess.run)(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                                     timeout=15, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        proc.terminate()


# --- Gestionnaire d'identification ---------------------------------------------------------


class ApiIdentification:
    """CredReadW / CredWriteW / CredDeleteW (advapi32), identifiants génériques du compte. La clé ne
    passe par aucune ligne de commande ni aucun fichier ; Windows la chiffre pour le compte (DPAPI)."""

    TYPE_GENERIQUE = 1
    PERSISTANCE_LOCALE = 2          # CRED_PERSIST_LOCAL_MACHINE : sur ce PC, pas dans le profil itinérant

    def _api(self):
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                        ("UserName", wintypes.LPWSTR)]
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        advapi.CredReadW.restype = wintypes.BOOL
        advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        advapi.CredWriteW.restype = wintypes.BOOL
        advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        advapi.CredDeleteW.restype = wintypes.BOOL
        advapi.CredFree.argtypes = [ctypes.c_void_p]
        return ctypes, CREDENTIALW, advapi

    def lire(self, cible):
        ctypes, CREDENTIALW, advapi = self._api()
        p = ctypes.POINTER(CREDENTIALW)()
        if not advapi.CredReadW(cible, self.TYPE_GENERIQUE, 0, ctypes.byref(p)):
            return None
        try:
            c = p.contents
            octets = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
            return octets.decode("utf-16-le")
        finally:
            advapi.CredFree(p)

    def ecrire(self, cible, compte, valeur):
        ctypes, CREDENTIALW, advapi = self._api()
        octets = valeur.encode("utf-16-le")
        tampon = (ctypes.c_ubyte * len(octets)).from_buffer_copy(octets)
        c = CREDENTIALW()
        c.Type = self.TYPE_GENERIQUE
        c.TargetName = cible
        c.UserName = compte
        c.CredentialBlobSize = len(octets)
        c.CredentialBlob = ctypes.cast(tampon, ctypes.POINTER(ctypes.c_ubyte))
        c.Persist = self.PERSISTANCE_LOCALE
        return bool(advapi.CredWriteW(ctypes.byref(c), 0))

    def supprimer(self, cible):
        _, _, advapi = self._api()
        return bool(advapi.CredDeleteW(cible, self.TYPE_GENERIQUE, 0))


class Coffre:
    """Le gestionnaire d'identification de Windows. `api` : l'accès à Windows (les tests en donnent un faux)."""

    def __init__(self, api=None):
        self.api = api or ApiIdentification()

    def lire(self, service):
        v = self.api.lire(service)
        return v.strip() if v and v.strip() else None

    def ecrire(self, service, compte, valeur):
        return self.api.ecrire(service, compte, valeur)

    def retirer(self, service):
        return self.api.supprimer(service)


# --- Notifications toast ---------------------------------------------------------------------

SCRIPT_TOAST = """$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml(@'
%(xml)s
'@)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('%(app)s').Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def xml_toast(titre, texte, urgent=False, bouton=None):
    """Le contenu d'une notification toast. Échappé en XML : ni le titre ni le texte ne peuvent fermer la
    chaîne PowerShell qui le porte (`'@` devient `&apos;@`). Un clic sur la notification, ou sur son
    bouton, ouvre l'application (piston://ouvrir) ; jamais Chrome, jamais une adresse web."""
    ouvrir = "%s://ouvrir" % SCHEMA
    attributs = ' launch="%s" activationType="protocol"' % ouvrir
    if bouton:
        attributs += ' scenario="reminder"'                   # reste affichée jusqu'à ce qu'on réponde
    son = '<audio src="ms-winsoundevent:Notification.Reminder"/>' if urgent else ""
    actions = ""
    if bouton:
        actions = ('<actions><action content="Plus tard" arguments="dismiss" activationType="system"/>'
                   '<action content="%s" arguments="%s" activationType="protocol"/></actions>'
                   % (echapper_xml(bouton), ouvrir))
    return ('<toast%s><visual><binding template="ToastGeneric"><text>%s</text><text>%s</text></binding></visual>%s%s</toast>'
            % (attributs, echapper_xml(titre), echapper_xml(texte), son, actions))


def _toast(xml, attendre):
    """Le script passe par l'entrée standard de PowerShell, jamais par sa ligne de commande."""
    script = SCRIPT_TOAST % {"xml": xml, "app": APPLICATION}
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]
    try:
        if attendre:
            r = subprocess.run(cmd, input=script, text=True, capture_output=True, timeout=20,
                               creationflags=CREATE_NO_WINDOW)
            return r.returncode == 0
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             text=True, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        p.stdin.write(script)
        p.stdin.close()
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def notifier(titre, texte, urgent=False):
    return _toast(xml_toast(titre, texte[:900], urgent), attendre=True)


def alerte_action(titre, texte, bouton):
    return _toast(xml_toast(titre, texte[:400], bouton=bouton), attendre=False)


# --- Région, réseau --------------------------------------------------------------------


def region(env=None, lire_locale=None):
    """Le pays des réglages de région de Windows (GetUserDefaultLocaleName : « fr-CH »)."""
    env = os.environ if env is None else env

    def lire():
        import ctypes
        tampon = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(tampon, 85):
            return tampon.value
        return ""
    try:
        valeur = (lire_locale or lire)()
    except (OSError, AttributeError, ValueError):
        valeur = ""
    return code_pays(valeur) or region_env(env)


def nom_reseau():
    """Le nom du PC sur le réseau local (<nom>.local : Windows 10 et 11 répondent en mDNS)."""
    try:
        nom = socket.gethostname().split(".")[0]
    except OSError:
        return None
    return (nom + ".local") if nom else None


# --- Le dossier partagé avec la VM, sans suivre aucun lien ----------------------------------
#
# La VM écrit dans `partage-vm/` (monté par drvfs) : elle pourrait y poser un lien (un lien symbolique de
# WSL est un point d'analyse NTFS) pour détourner une écriture ou une lecture du PC. Windows n'a pas les
# descripteurs de dossier de POSIX : chaque élément du chemin est vérifié (ni lien, ni jonction, ni point
# d'analyse) juste avant l'opération. La fenêtre entre la vérification et l'opération est courte ; la VM n'a
# pas de compte root qui puisse l'élargir (vm/wsl.py : moteurs et Chrome sans sudo).


def _sans_lien(chemin, dossier=None):
    st = os.lstat(chemin)
    if _est_lien(st):
        raise OSError("lien refusé dans le dossier partagé : %s" % chemin)
    if dossier is True and not stat.S_ISDIR(st.st_mode):
        raise OSError("pas un dossier : %s" % chemin)
    if dossier is False and not stat.S_ISREG(st.st_mode):
        raise OSError("pas un fichier ordinaire : %s" % chemin)
    return st


def partage_deposer(racine, sous, source, nom):
    racine = Path(racine)
    racine.mkdir(parents=True, exist_ok=True)
    d = racine / sous
    try:
        d.mkdir()
    except FileExistsError:
        pass
    _sans_lien(d, dossier=True)
    cible = d / nom
    if os.path.lexists(cible):
        raise FileExistsError(nom)
    os.rename(source, cible)


def _est_lien(st):
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0)
                                             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def partage_purger(racine, sous, nom):
    d = Path(racine) / sous
    try:
        _sans_lien(d, dossier=True)
        st = os.lstat(d / nom)
    except OSError:
        return False
    if _est_lien(st):                     # le lien seul, jamais ce vers quoi il pointe
        try:
            os.unlink(d / nom)
        except OSError:
            os.rmdir(d / nom)             # lien de dossier ou jonction
    elif stat.S_ISDIR(st.st_mode):
        shutil.rmtree(d / nom)            # rmtree ne traverse ni lien ni jonction (Python 3.12 et plus)
    else:
        os.unlink(d / nom)
    return True


def partage_lire(racine, sous, nom, fichier, maximum):
    d = Path(racine) / sous
    try:
        _sans_lien(d, dossier=True)
        _sans_lien(d / nom, dossier=True)
        _sans_lien(d / nom / fichier, dossier=False)
        with open(d / nom / fichier, "rb") as fh:
            return fh.read(maximum)
    except OSError:
        return None


def partage_lister(racine, sous):
    d = Path(racine) / sous
    try:
        _sans_lien(d, dossier=True)
        return os.listdir(d)
    except OSError:
        return None
