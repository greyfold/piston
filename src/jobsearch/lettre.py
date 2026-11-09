"""Date de la lettre : celle du jour tant que la candidature n'est pas partie,
puis figée à la date d'envoi.

La date cible se déduit du registre : la date d'envoi (`postulee_le`) pour une
candidature partie, sinon aujourd'hui. La ligne « Lausanne, le … » est réécrite
dans Lettre.md et dans le Word, dans la langue de la ligne d'origine.
"""

import datetime as dt
import re
import unicodedata
from pathlib import Path

from .db import ENVOYEES

MOIS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
           "November", "Dezember"],
}


def _plier(t):
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


_MOIS_TOUS = sorted({_plier(m) for ms in MOIS.values() for m in ms}, key=len, reverse=True)
# « Lausanne, le 25 octobre 2026 », « Lausanne, novembre 2026 », « Lausanne, 12 October 2026 »,
# « Lausanne, October 12, 2026 », « Lausanne, 25.10.2026 », « Lausanne, 25. Oktober 2026 ».
_LIGNE = re.compile(
    r"^(?P<lieu>[A-ZÀ-Ý][\w'’\- ]{1,40}),\s*(?:le\s+)?"
    r"(?:\d{1,2}(?:er)?\.?\s+)?(?:%s)\.?(?:\s+\d{1,2},)?\s+\d{4}\s*$"
    r"|^(?P<lieu2>[A-ZÀ-Ý][\w'’\- ]{1,40}),\s*(?:le\s+)?\d{1,2}[./]\d{1,2}[./]\d{2,4}\s*$" % "|".join(_MOIS_TOUS),
    re.I)


def date_cible(f, aujourdhui=None):
    """Date d'envoi pour une candidature partie, sinon la date du jour."""
    if f.get("statut") in ENVOYEES and f.get("postulee_le"):
        return dt.date.fromisoformat(f["postulee_le"][:10])
    return aujourdhui or dt.date.today()


def _langue_de(ligne, defaut="fr"):
    p = _plier(ligne)
    for langue in ("en", "de", "fr"):
        if any(re.search(r"\b%s\b" % _plier(m), p) for m in MOIS[langue] if _plier(m) not in ("mai", "august", "september", "november", "april")):
            return langue
    if re.search(r",\s*le\b", ligne):
        return "fr"
    return defaut


def ligne_date(lieu, date, langue="fr"):
    mois = MOIS.get(langue, MOIS["fr"])[date.month - 1]
    if langue == "en":
        return "%s, %d %s %d" % (lieu, date.day, mois, date.year)
    if langue == "de":
        return "%s, %d. %s %d" % (lieu, date.day, mois, date.year)
    return "%s, le %s %s %d" % (lieu, "1er" if date.day == 1 else date.day, mois, date.year)


def est_ligne_date(texte):
    return bool(_LIGNE.match((texte or "").strip()))


def nouvelle_ligne(ancienne, date, langue_lettre="fr"):
    m = _LIGNE.match(ancienne.strip())
    lieu = (m.group("lieu") or m.group("lieu2")).strip() if m else "Lausanne"
    return ligne_date(lieu, date, _langue_de(ancienne, langue_lettre))


def dater_markdown(texte, date, langue="fr"):
    """Réécrit la ligne de date de la lettre (après la note de travail). Ajoute
    « Lausanne, le … » en tête de la lettre s'il n'y en a pas."""
    note, sep, lettre = texte.partition("\n---")
    if not sep:
        note, lettre = "", texte
    lignes = lettre.split("\n")
    for i, l in enumerate(lignes[:20]):
        if est_ligne_date(l):
            lignes[i] = nouvelle_ligne(l, date, langue)
            break
    else:
        debut = 1 if lignes and lignes[0].strip() == "" else 0
        lignes.insert(debut, ligne_date("Lausanne", date, langue))
        lignes.insert(debut + 1, "")
    lettre = "\n".join(lignes)
    return note + sep + lettre if sep else lettre


def dater_docx(source, cible, date, langue="fr"):
    """Copie du Word avec la ligne de date réécrite. Renvoie True si une ligne a été trouvée."""
    from docx import Document
    doc = Document(str(source))
    trouvee = False
    for p in doc.paragraphs:
        lignes = p.text.split("\n")
        idx = next((i for i, l in enumerate(lignes) if est_ligne_date(l)), None)
        if idx is None:
            continue
        lignes[idx] = nouvelle_ligne(lignes[idx], date, langue)
        runs = p.runs
        if runs:
            runs[0].text = "\n".join(lignes)
            for r in runs[1:]:
                r.text = ""
        trouvee = True
        break
    Path(cible).parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(cible))
    return trouvee


def date_du_docx(chemin):
    """La ligne de date actuelle d'un Word, ou None."""
    from docx import Document
    for p in Document(str(chemin)).paragraphs:
        for l in p.text.split("\n"):
            if est_ligne_date(l):
                return l.strip()
    return None
