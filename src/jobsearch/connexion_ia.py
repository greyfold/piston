"""Connexion à l'IA depuis le tableau de bord, sans terminal ni fenêtre à part.

Le tableau de bord lance `jobsearch connexion` (Claude Code : `claude auth login`, sur ce Mac
ou dans la machine virtuelle), lit l'adresse de connexion qu'il affiche, et lui transmet le
code que la personne colle dans la modale de la page. Une seule connexion à la fois.
Le code n'est ni journalisé ni gardé : il passe seulement sur l'entrée du processus.
"""

import os
import re
import signal
import subprocess
import sys
import threading

ADRESSE = re.compile(r"https://[^\s\"'<>]+")

_verrou = threading.Lock()
_proc = None
_etat = {"etat": "aucune"}      # aucune · preparation · attente_code · verification · connecte · echec


def etat():
    with _verrou:
        return dict(_etat)


def _poser(**kw):
    with _verrou:
        _etat.update(kw)


def commande(fournisseur="claude"):
    if fournisseur != "claude":
        raise ValueError("fournisseur inconnu : %s" % fournisseur)
    return [sys.executable, "-c", "import sys; from jobsearch.cli import main; sys.exit(main(['connexion']))"]


def demarrer(fournisseur="claude", popen=subprocess.Popen):
    """Lance la connexion (ou reprend celle qui est en cours). Renvoie l'état."""
    global _proc
    with _verrou:
        if _proc is not None and _proc.poll() is None:
            return dict(_etat)
        _etat.clear()
        _etat.update(etat="preparation", fournisseur=fournisseur)
        _proc = popen(commande(fournisseur), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, env=dict(os.environ), cwd=os.path.expanduser("~"),
                      start_new_session=True)      # son propre groupe : Annuler arrête aussi `claude`
        proc = _proc
    threading.Thread(target=_lire, args=(proc,), daemon=True).start()
    return etat()


def _lire(proc):
    vu = ""
    while True:
        morceau = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not morceau:
            break
        vu += morceau.decode("utf-8", "replace") if isinstance(morceau, bytes) else morceau
        m = ADRESSE.search(vu)
        if m and etat().get("etat") == "preparation":
            _poser(etat="attente_code", url=m.group(0).rstrip(".,;)"))
    code = proc.wait()
    if code == 0:
        _poser(etat="connecte", url=None)
    elif etat().get("etat") != "annulee":
        dernier = [l for l in vu.strip().splitlines() if l.strip() and "http" not in l][-1:] or ["sans message"]
        _poser(etat="echec", url=None, message=dernier[0][:200])


def envoyer_code(code):
    code = (code or "").strip()
    with _verrou:
        proc = _proc
        if not code or proc is None or proc.poll() is not None or _etat.get("etat") != "attente_code":
            return False
        _etat["etat"] = "verification"
    proc.stdin.write((code + "\n").encode())
    proc.stdin.flush()
    return True


def annuler():
    with _verrou:
        proc = _proc
        _etat.clear()
        _etat["etat"] = "annulee"
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)    # jobsearch connexion et le `claude auth login` qu'il a lancé
        except (ProcessLookupError, PermissionError, AttributeError, TypeError):
            proc.terminate()
