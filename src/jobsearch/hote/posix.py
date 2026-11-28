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
