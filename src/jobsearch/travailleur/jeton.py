"""Le jeton partagé entre le Mac et le travailleur : un fichier du seul compte, dans le
dossier partagé avec la machine virtuelle.

La VM ne voit qu'un sous-dossier des données, `partage-vm/` (monté sur /donnees) : le jeton,
les journaux et le cache du travailleur. Ni le profil, ni la base, ni les sauvegardes, ni les
dossiers de candidature, ni la connexion Claude du Mac n'y sont."""

import os
import secrets

from ..config import DATA

PARTAGE = "partage-vm"


def partage():
    """Le dossier vu par la VM : DATA/partage-vm sur le Mac ; dans la VM (JOBSEARCH_VM=1),
    le dossier monté lui-même."""
    return DATA if os.environ.get("JOBSEARCH_VM") else DATA / PARTAGE


def chemin():
    return partage() / "travailleur.jeton"


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
