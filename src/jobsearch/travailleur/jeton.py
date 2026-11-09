"""Le jeton partagé entre le Mac et le travailleur : un fichier du seul compte, dans
le dossier de l'utilisateur (le seul que la machine virtuelle voit)."""

import os
import secrets

from ..config import DATA


def chemin():
    return DATA / "travailleur.jeton"


def lire(creer=True):
    p = chemin()
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    if not creer:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    jeton = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(jeton)
    return jeton
