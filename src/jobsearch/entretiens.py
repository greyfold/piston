"""Préparations d'entretien : une fiche par annonce, dans data/entretiens/<empreinte>.md.

En tête du fichier, entre deux lignes « --- », le rendez-vous en YAML (date, heure,
lieu, acces, avec, convocation) ; le reste est la préparation en Markdown, rédigée en
conversation. Le tableau de bord la montre dans l'onglet « Entretien » de l'annonce,
et l'accueil liste les entretiens à venir. La fiche vit hors de la base : elle se
réécrit librement, rien du registre n'en dépend.
"""

import datetime as dt

import yaml

from .config import DATA


def dossier():
    return DATA / "entretiens"


def _date(v):
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _heure(v):
    if isinstance(v, int):                         # YAML lit 13:30 non cité comme 810 minutes
        return "%d:%02d" % divmod(v, 60)
    return str(v).strip() if v else ""


def lire(emp):
    """La fiche d'entretien d'une annonce, ou None : {date, heure, lieu, avec, convocation, corps}."""
    p = dossier() / ("%s.md" % emp)
    if not p.is_file():
        return None
    texte = p.read_text(encoding="utf-8")
    meta, corps = {}, texte
    if texte.startswith("---\n"):
        tete, sep, reste = texte[4:].partition("\n---\n")
        if sep:
            meta, corps = yaml.safe_load(tete) or {}, reste
    return {**meta, "empreinte": emp, "date": _date(meta.get("date")),
            "heure": _heure(meta.get("heure")), "corps": corps.strip()}


def a_venir(reg, aujourdhui=None):
    """Les entretiens d'aujourd'hui et des jours suivants, le plus proche d'abord,
    avec leur annonce (`_annonce`). Une candidature refusée entre-temps n'y figure plus."""
    auj = aujourdhui or dt.date.today()
    if not dossier().is_dir():
        return []
    out = []
    for p in dossier().glob("*.md"):
        e = lire(p.stem)
        if not e or not e["date"] or e["date"] < auj:
            continue
        f = reg.get(reg.resoudre(p.stem) or p.stem)
        if f and f["statut"] not in ("refus", "C"):
            out.append({**e, "_annonce": f})
    return sorted(out, key=lambda e: (e["date"], e["heure"]))
