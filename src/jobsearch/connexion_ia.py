"""Connexion à l'IA depuis le tableau de bord, sans terminal ni fenêtre à part.

Le tableau de bord lance `jobsearch connexion --fournisseur <id>` (dans la machine virtuelle :
`claude auth login`, `codex login --device-auth`, Antigravity, `grok login --device-code`) et lit
ce qu'il affiche :
- mode « code » (Claude, Antigravity) : une adresse ; la personne s'y connecte, puis colle le code
  obtenu dans la modale de la page, qui le transmet au processus ;
- mode « appareil » (Codex, Grok) : une adresse et un code court ; la personne ouvre l'adresse,
  tape ce code chez le fournisseur, et la connexion se termine seule.
Une seule connexion à la fois. Le code collé n'est ni journalisé ni gardé : il passe seulement
sur l'entrée du processus. Connecté : ce moteur devient celui de l'application (profil).
"""

import os
import re
import subprocess
import sys
import threading

from . import hote

ADRESSE = re.compile(r"https://[^\s\"'<>│|]+")
CODE_APPAREIL = re.compile(r"\b[A-Z0-9]{4,5}-[A-Z0-9]{4,5}\b")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")

_verrou = threading.Lock()
_proc = None
_etat = {"etat": "aucune"}      # aucune · preparation · attente_code · verification · connecte · echec


def etat():
    with _verrou:
        return dict(_etat)


def _poser(**kw):
    with _verrou:
        _etat.update(kw)


def _fournisseur(fid):
    from .moteur import fournisseurs
    return fournisseurs.get(fid)          # ValueError si inconnu


def commande(fournisseur="claude"):
    f = _fournisseur(fournisseur)
    return [sys.executable, "-c", "import sys; from jobsearch.cli import main; "
            "sys.exit(main(['connexion', '--fournisseur', %r]))" % f.id]


def demarrer(fournisseur="claude", popen=subprocess.Popen):
    """Lance la connexion (ou reprend celle qui est en cours). Renvoie l'état."""
    global _proc
    f = _fournisseur(fournisseur)
    with _verrou:
        if _proc is not None and _proc.poll() is None:
            return dict(_etat)
        _etat.clear()
        _etat.update(etat="preparation", fournisseur=f.id, nom=f.nom, mode=f.connexion_mode, pty=f.pty)
        _proc = popen(commande(f.id), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, env=dict(os.environ), cwd=os.path.expanduser("~"),
                      **hote.options_groupe())      # son propre groupe : Annuler arrête aussi le moteur
        proc = _proc
    threading.Thread(target=_lire, args=(proc, list(f.invites)), daemon=True).start()
    return etat()


def nettoyer(texte):
    """Le texte affiché, sans les codes de terminal (couleurs, curseur)."""
    return ANSI.sub("", texte or "").replace("\r", "\n")


def _lire(proc, invites=()):
    vu, invites = "", list(invites)
    while True:
        morceau = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not morceau:
            break
        vu += morceau.decode("utf-8", "replace") if isinstance(morceau, bytes) else morceau
        propre = nettoyer(vu)
        for invite in list(invites):     # un menu du moteur, auquel l'application répond seule
            if invite[0] in propre:
                invites.remove(invite)
                try:
                    proc.stdin.write(invite[1].encode())
                    proc.stdin.flush()
                except (OSError, ValueError):
                    pass
        e = etat()
        if e.get("etat") != "preparation":
            continue
        m = ADRESSE.search(propre)
        if not m:
            continue
        url = m.group(0).rstrip(".,;)")
        if e.get("mode") == "appareil":
            c = CODE_APPAREIL.search(propre[m.end():]) or CODE_APPAREIL.search(propre)
            if c:
                _poser(etat="attente_appareil", url=url, code=c.group(0))
        else:
            _poser(etat="attente_code", url=url)
    code = proc.wait()
    if code == 0:
        _poser(etat="connecte", url=None, code=None)
        _choisir(etat().get("fournisseur"))
    elif etat().get("etat") != "annulee":
        _journaliser(etat().get("fournisseur"), code, vu)
        dernier = [l for l in nettoyer(vu).strip().splitlines() if l.strip() and "http" not in l][-1:] or ["sans message"]
        message = dernier[0].strip()[:200]
        if "Input/output error" in message:        # disque du Mac plein : la VM ne peut plus écrire
            from . import vm
            message = vm.disque_plein() or message
        _poser(etat="echec", url=None, code=None, message=message)


def _journaliser(fournisseur, code, sortie):
    """Une connexion ratée, telle que la VM l'a affichée, dans `logs/connexion.log` : l'écran n'en
    montre que la dernière ligne. Les adresses (qui portent l'état de la connexion) sont retirées ;
    le code collé n'y est jamais, il ne passe que sur l'entrée du processus."""
    import datetime as dt
    from . import config
    try:
        journal = config.DATA / "logs" / "connexion.log"
        journal.parent.mkdir(parents=True, exist_ok=True)
        texte = ADRESSE.sub("[adresse]", nettoyer(sortie)).strip()
        with open(journal, "a", encoding="utf-8") as fh:
            fh.write("== %s · %s · code %s\n%s\n\n" % (dt.datetime.now().isoformat(timespec="seconds"),
                                                       fournisseur, code, texte[-4000:]))
    except OSError:
        pass


def _choisir(fid):
    """Le moteur qui vient de se connecter devient celui de l'application."""
    if not fid:
        return
    try:
        from . import profil
        profil.poser_fournisseur(fid)
    except Exception:
        pass


def envoyer_code(code):
    code = (code or "").strip()
    with _verrou:
        proc = _proc
        if not code or proc is None or proc.poll() is not None or _etat.get("etat") != "attente_code":
            return False
        pty = bool(_etat.get("pty"))
        _etat["etat"] = "verification"
    # « \r » : un moteur qui lit le code dans un pseudo-terminal (Antigravity), où Entrée est un retour chariot.
    try:
        proc.stdin.write((code + ("\r" if pty else "\n")).encode())
        proc.stdin.flush()
    except (OSError, ValueError):
        return False
    return True


def annuler():
    global _proc
    with _verrou:
        proc = _proc
        _proc = None
        _etat.clear()
        _etat["etat"] = "annulee"
    if proc is not None and proc.poll() is None:
        hote.terminer_groupe(proc)                 # jobsearch connexion et la connexion qu'il a lancée
