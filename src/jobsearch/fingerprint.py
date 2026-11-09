"""Empreinte d'une annonce : entreprise et titre normalisés.

Ne pas modifier à la légère : une empreinte qui change, c'est une annonce déjà vue qui
revient (la base de la personne garde les empreintes calculées).
"""

import hashlib
import re
import unicodedata

_BRUIT = re.compile(
    r"\((?:[mwfhdxa/\s\-]+|all genders?|tous genres?)\)"
    r"|\b\d{1,3}\s?[-–]\s?\d{1,3}\s?%|\b\d{1,3}\s?%"
    r"|\bm/w/d\b|\bh/f/x\b|\bf/h/d\b|\bh/f\b|\bw/m\b", re.IGNORECASE)


def normaliser(t):
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = _BRUIT.sub(" ", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def empreinte(entreprise, titre):
    cle = normaliser(entreprise) + "|" + normaliser(titre)
    return hashlib.sha1(cle.encode("utf-8")).hexdigest()[:12]
