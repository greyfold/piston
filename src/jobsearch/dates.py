"""Délai de postulation, tiré du texte de l'annonce.

Les annonces romandes l'écrivent de mille façons : « Délai de postulation :
22 octobre 2026 », « Délai de candidature : 23.09.2026 », « Postuler jusqu'au
1er novembre », « Application deadline: 15 November 2026 », « Bewerbungsfrist
30.11.2026 ». On cherche la formule, puis la première date qui la suit.
Sans formule explicite, pas de délai : on n'en invente pas.
"""

import datetime as dt
import re
import unicodedata

MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "januar": 1, "februar": 2, "marz": 3, "juni": 6, "juli": 7, "oktober": 10, "dezember": 12,
    "janv": 1, "fevr": 2, "avr": 4, "juil": 7, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
}

_FORMULE = re.compile(
    r"(delai (?:de |d.)?(?:postulation|candidature|depot|inscription|soumission)"
    r"|date limite(?: de| pour| d.)?(?: postulation| candidature| depot| envoi| inscription| postuler)?"
    r"|postuler (?:jusqu.(?:au|a)|avant(?: le)?|d.ici(?: au| le)?)"
    r"|(?:candidatures?|dossiers?|postulations?)[^.\n]{0,60}?(?:jusqu.(?:au|a)|avant le|d.ici (?:au|le)|au plus tard le)"
    r"|(?:closing|application|applications?) (?:date|deadline)|deadline(?: for applications?)?"
    r"|apply (?:by|before|until)|applications? (?:close|closes|will close|must be received)(?: on| by)?"
    r"|bewerbungsfrist|bewerbungsschluss|einsendeschluss)",
    re.I)

_DATE = re.compile(
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<j>\d{1,2})[./](?P<m>\d{1,2})[./](?P<a>\d{2,4})"
    r"|(?P<j2>\d{1,2})(?:er|st|nd|rd|th)?\.?\s+(?P<mois>[a-z]{3,9})\.?(?:\s+(?P<a2>\d{4}))?"
    r"|(?P<mois3>[a-z]{3,9})\.?\s+(?P<j3>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<a3>\d{4})",
    re.I)


def _plier(t):
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _date(m, reference):
    try:
        if m.group("iso"):
            return dt.date.fromisoformat(m.group("iso"))
        if m.group("j"):
            a = int(m.group("a"))
            return dt.date(a + 2000 if a < 100 else a, int(m.group("m")), int(m.group("j")))
        if m.group("j2"):
            mois = MOIS.get(m.group("mois").lower().rstrip("."))
            if not mois:
                return None
            a = int(m.group("a2")) if m.group("a2") else reference.year
            d = dt.date(a, mois, int(m.group("j2")))
            if not m.group("a2") and d < reference - dt.timedelta(days=60):
                d = dt.date(a + 1, mois, int(m.group("j2")))     # « 15 janvier » écrit en novembre
            return d
        if m.group("mois3"):
            mois = MOIS.get(m.group("mois3").lower())
            return dt.date(int(m.group("a3")), mois, int(m.group("j3"))) if mois else None
    except ValueError:
        return None
    return None


def delai_postulation(texte, reference=None):
    """Le délai de postulation écrit dans l'annonce (AAAA-MM-JJ), ou None.

    `reference` sert aux dates sans année : la date de publication de l'annonce."""
    if not texte:
        return None
    ref = reference or dt.date.today()
    t = _plier(texte)
    for f in _FORMULE.finditer(t):
        fenetre = t[f.end():f.end() + 80]
        for m in _DATE.finditer(fenetre):
            d = _date(m, ref)
            if d and ref - dt.timedelta(days=400) <= d <= ref + dt.timedelta(days=400):
                return d.isoformat()
    return None


def fin_publication(valeur, publiee_le=None):
    """Fin de publication donnée par la plateforme, si elle est plausible (moins d'un an)."""
    if not valeur:
        return None
    try:
        d = dt.date.fromisoformat(str(valeur)[:10])
    except ValueError:
        return None
    base = dt.date.fromisoformat(publiee_le[:10]) if publiee_le else dt.date.today()
    return d.isoformat() if base <= d <= base + dt.timedelta(days=366) else None
