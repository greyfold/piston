"""Ce que macOS et Linux partagent : verrous `flock`, groupes de processus."""

import fcntl
import os
import signal


def prendre(fichier, attendre=False):
    try:
        fcntl.flock(fichier, fcntl.LOCK_EX | (0 if attendre else fcntl.LOCK_NB))
    except BlockingIOError:
        return False
    return True


def lacher(fichier):
    fcntl.flock(fichier, fcntl.LOCK_UN)


def options_groupe():
    return {"start_new_session": True}


def terminer_groupe(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, AttributeError, TypeError, OSError):
        proc.terminate()


# --- Le dossier partagé avec la VM, sans suivre aucun lien ----------------------------------
#
# La VM écrit dans `partage-vm/` : elle pourrait y poser un lien symbolique pour détourner une écriture
# ou une lecture de l'hôte. Tout passe donc par des descripteurs de dossier ouverts sans suivre de lien.

import shutil
import stat

_REP = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _sous_dossier_fd(racine, sous, creer=False):
    if creer:
        os.makedirs(racine, exist_ok=True)
    fr = os.open(racine, os.O_RDONLY | os.O_DIRECTORY)          # le partage lui-même : côté hôte
    try:
        if creer:
            try:
                os.mkdir(sous, 0o755, dir_fd=fr)
            except FileExistsError:
                pass
        return os.open(sous, _REP, dir_fd=fr)
    finally:
        os.close(fr)


def partage_deposer(racine, sous, source, nom):
    ft = _sous_dossier_fd(racine, sous, creer=True)
    try:
        try:
            os.lstat(nom, dir_fd=ft)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(nom)
        os.rename(source, nom, dst_dir_fd=ft)
    finally:
        os.close(ft)


def partage_purger(racine, sous, nom):
    try:
        ft = _sous_dossier_fd(racine, sous)
    except OSError:
        return False
    try:
        try:
            st = os.lstat(nom, dir_fd=ft)
        except FileNotFoundError:
            return False
        if stat.S_ISDIR(st.st_mode):
            shutil.rmtree(nom, dir_fd=ft)
        else:
            os.unlink(nom, dir_fd=ft)
        return True
    finally:
        os.close(ft)


def partage_lire(racine, sous, nom, fichier, maximum):
    try:
        ft = _sous_dossier_fd(racine, sous)
    except OSError:
        return None
    try:
        fd = os.open(nom, _REP, dir_fd=ft)
    except OSError:
        return None
    finally:
        os.close(ft)
    try:
        fe = os.open(fichier, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError:
        return None
    finally:
        os.close(fd)
    with os.fdopen(fe, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return None
        return fh.read(maximum)


def partage_lister(racine, sous):
    try:
        ft = _sous_dossier_fd(racine, sous)
    except OSError:
        return None
    try:
        return os.listdir(ft)
    finally:
        os.close(ft)
