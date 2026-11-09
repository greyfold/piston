"""Pré-tri mécanique : passes 0 et 1, là où la règle se décide sans jugement.

Une annonce qui tombe sous une règle passe en C avec sa passe et son motif
(R-52). Le doute profite à l'annonce : rien ne s'écarte sur un mot ambigu,
elle reste « nouvelle » pour la personne (R-51). Les règles vivent dans
`config/criteres.yaml`, pas dans le code.
"""

import re
import unicodedata
from functools import lru_cache

from .config import criteres


def plier(t):
    """Minuscules, sans accents, ponctuation conservée."""
    t = unicodedata.normalize("NFKD", str(t or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _compiler(motif):
    # Les blocs YAML repliés insèrent des espaces aux retours à la ligne.
    motif = re.sub(r"\s*\|\s*", "|", motif.strip())
    return re.compile(motif, re.I)


@lru_cache
def regles():
    out = []
    for r in criteres().get("regles", []):
        out.append(dict(r, _re=_compiler(r["regex"]),
                        _sauf=_compiler(r["sauf"]) if r.get("sauf") else None))
    return out


@lru_cache
def familles():
    return _compiler(criteres()["familles_cibles"])


_MOTS_DE = {"und", "der", "die", "das", "mit", "fur", "wir", "sie", "ihre", "eine", "ein",
            "unsere", "zur", "auf", "sind", "werden", "bei", "als", "oder", "nicht"}
_MOTS_FR = {"et", "le", "la", "les", "des", "pour", "avec", "nous", "vous", "une", "un",
            "dans", "votre", "notre", "sur", "est", "sont", "du", "au"}
_MOTS_EN = {"and", "the", "for", "with", "you", "our", "your", "are", "will", "we", "of",
            "to", "in", "is", "be", "this", "as"}


def langue(texte):
    """« de », « fr » ou « en » d'après les mots les plus courants ; None si trop court.

    La langue déclarée par Job-Room n'est pas fiable (« de » pour une annonce
    rédigée en français, constaté le 24.10).
    """
    mots = re.findall(r"[a-z]+", plier(texte))
    if len(mots) < 40:
        return None
    n = {l: sum(1 for m in mots if m in s) for l, s in
         (("de", _MOTS_DE), ("fr", _MOTS_FR), ("en", _MOTS_EN))}
    meilleure = max(n, key=n.get)
    return meilleure if n[meilleure] >= 5 else None


def _valeur(f, champ):
    if champ == "titre_texte":
        return plier((f.get("titre") or "") + "\n" + (f.get("texte") or ""))
    v = f.get(champ)
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    return plier(v) if champ not in ("canton", "langue") else str(v)


def evaluer(f):
    """La première règle qui écarte l'annonce, ou None si elle reste à trier."""
    for r in regles():
        v = _valeur(f, r["champ"])
        if v is None or v == "":
            continue
        if not r["_re"].search(v):
            continue
        if r["_sauf"]:
            # L'exception se lit dans un autre champ quand la règle le dit (`sauf_champ`) :
            # « permanent: false » ne dit pas la durée, le texte de l'annonce, si.
            vs = _valeur(f, r["sauf_champ"]) if r.get("sauf_champ") else v
            if vs and r["_sauf"].search(vs):
                continue
        return r
    if f.get("titre") and not familles().search(plier(f.get("titre"))):
        return {"id": "hors-familles", "passe": 0,
                "motif": "Intitulé hors des postes visés", "limite": False}
    return None


def pre_trier(reg, emps=None, essai=False):
    """Applique le pré-tri aux annonces « nouvelle ». Renvoie [(annonce, règle)]."""
    if emps is None:
        cibles = reg.liste(["nouvelle"])
    else:
        cibles = [f for f in (reg.get(e) for e in emps) if f and f["statut"] == "nouvelle"]
    ecartees = []
    for f in cibles:
        if f.get("langue") is None and f.get("texte"):
            f["langue"] = langue(f["texte"])
            if not essai:
                reg.maj(f["empreinte"], langue=f["langue"])
        r = evaluer(f)
        if not r:
            continue
        ecartees.append((f, r))
        if essai:
            continue
        reg.maj(f["empreinte"], statut="C", passe_ecart=r["passe"], motif=r["motif"],
                limite=bool(r.get("limite")))
        reg.noter(f["empreinte"], "pré-tri · C · passe %s · %s (règle %s)"
                  % (r["passe"], r["motif"], r["id"]))
    if not essai:
        reg.commit()
    return ecartees
