"""Import du registre d'un coffre Obsidian, en lecture seule.

Lit les 267 notes de `7 - Dynamique/Annonces/` avec le même lecteur YAML que
`annonces.py` (L106), retrouve les dossiers de `Candidatures/` et les dates des
notes de `Veille/`. N'écrit jamais dans le coffre.

`--rafraichir` (période d'essai en parallèle de l'ancienne chaîne) : le coffre
gagne pour les annonces qu'il connaît ; celles que seule la nouvelle plateforme
a vues sont gardées.
"""

import os
import re
from pathlib import Path

from . import config
from .db import COLONNES, maintenant
from .fingerprint import normaliser

ANNONCES = Path("7 - Dynamique") / "Annonces"
CANDIDATURES = Path("2 - Casquettes") / "Recherche de poste" / "Candidatures"
VEILLES = Path("2 - Casquettes") / "Recherche de poste" / "Veille"

LISTES_NOTE = {"sources", "ids_source", "exigences"}

# --- Lecteur YAML de annonces.py, à l'identique -----------------------------


def _deechappe(s):
    s = s.strip()
    if s == "":
        return None
    if s.startswith('"') and s.endswith('"') and len(s) > 1:
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if s in ("true", "false"):
        return s == "true"
    if s == "null":
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


def lire_frontmatter(txt):
    if not txt.startswith("---"):
        return {}, txt
    fin = txt.find("\n---", 3)
    if fin == -1:
        return {}, txt
    bloc, corps = txt[3:fin], txt[fin + 4:].lstrip("\n")
    props, cle = {}, None
    for ligne in bloc.split("\n"):
        if not ligne.strip():
            continue
        if ligne.lstrip().startswith("- ") and cle:
            props.setdefault(cle, [])
            if isinstance(props[cle], list):
                props[cle].append(_deechappe(ligne.lstrip()[2:]))
            continue
        m = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", ligne)
        if not m:
            continue
        cle, brut = m.group(1), m.group(2)
        props[cle] = [] if brut.strip() == "" and cle in LISTES_NOTE else _deechappe(brut)
    return props, corps


def _section(corps, titre):
    m = re.search(r"^##\s+%s\s*$\n(.*?)(?=^##\s|\Z)" % re.escape(titre), corps, re.M | re.S)
    return m.group(1).strip() if m else ""


def lire_note(chemin):
    with open(chemin, encoding="utf-8") as fh:
        props, corps = lire_frontmatter(fh.read())
    if not props.get("empreinte"):
        return None
    f = dict(props)
    f["angle"] = _section(corps, "Angle") or None
    f["exigences"] = [l[2:].strip() for l in _section(corps, "Exigences majeures").split("\n")
                      if l.strip().startswith("- ")]
    f["texte"] = re.sub(r"^> ?", "", _section(corps, "Texte de l'annonce"), flags=re.M).strip()
    f["journal"] = [l[2:].strip() for l in _section(corps, "Journal").split("\n")
                    if l.strip().startswith("- ")]
    for k in ("sources", "ids_source"):
        if not isinstance(f.get(k), list):
            f[k] = [f[k]] if f.get(k) else []
    f["_fichier"] = str(chemin)
    return f


# --- Rapprochements --------------------------------------------------------

_JOURNAL = re.compile(r"^(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)\s*·\s*(.*)$", re.S)
_PREUVE = re.compile(r"Job-Room\s*:\s*saisie le (\d{4}-\d{2}-\d{2})"
                     r"|^(\d{4}-\d{2}-\d{2})\s*:\s*candidature saisie dans Job-Room", re.I)


def date_preuve_jobroom(journal):
    """Date de la première preuve Job-Room inscrite au journal, ou None.

    « Job-Room : à saisir » ne compte pas : seule une saisie constatée vaut.
    """
    for ligne in journal:
        m = _JOURNAL.match(ligne)
        texte = m.group(2) if m else ligne
        p = _PREUVE.search(texte)
        if p:
            return p.group(1) or p.group(2)
    return None


def trouver_dossier(f, noms):
    """Comme `trouver_dossier` (annonces.py L792)."""
    if f.get("dossier") and f["dossier"] in noms:
        return f["dossier"]
    ent, tit = normaliser(f.get("entreprise")), normaliser(f.get("titre"))
    for nom in sorted(noms, reverse=True):
        n = normaliser(nom)
        if ent and tit and ent in n and tit[:30] in n:
            return nom
    return None


def _dossier_a_un_cv(chemin):
    try:
        noms = os.listdir(chemin)
    except OSError:
        return False
    from .dossier import prefixe_fichier
    return any(n.startswith(prefixe_fichier() + "-CV") or n == "CV.md" for n in noms)


# --- Import ----------------------------------------------------------------


def lire_coffre(coffre):
    coffre = Path(coffre)
    dossier = coffre / ANNONCES
    if not dossier.is_dir():
        raise FileNotFoundError("registre introuvable : %s" % dossier)
    notes = []
    for nom in sorted(os.listdir(dossier)):
        if nom.endswith(".md"):
            f = lire_note(dossier / nom)
            if f:
                notes.append(f)
    cand = coffre / CANDIDATURES
    noms = [n for n in os.listdir(cand) if (cand / n).is_dir() and not n.startswith("_")] \
        if cand.is_dir() else []
    veilles = sorted(n[:-3] for n in os.listdir(coffre / VEILLES)
                     if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", n)) \
        if (coffre / VEILLES).is_dir() else []
    return notes, noms, veilles


class CoffreInterdit(Exception):
    """L'application (installation neuve) ne lit aucun coffre."""


def importer(reg, coffre, rafraichir=False, essai=False):
    if config.neutre():
        raise CoffreInterdit("l'application ne se connecte à aucun coffre : ses données restent locales")
    notes, noms, veilles = lire_coffre(coffre)
    bilan = {"lues": len(notes), "creees": 0, "mises_a_jour": 0, "ignorees": 0,
             "dossiers": 0, "preuves_jobroom": 0, "veilles": len(veilles)}
    connues = {r[0] for r in reg.cx.execute("SELECT empreinte FROM annonces")}
    for n in notes:
        emp = n["empreinte"]
        if emp in connues and not rafraichir:
            bilan["ignorees"] += 1
            continue
        f = {k: n.get(k) for k in COLONNES if k in n}
        f["extra"] = {k: v for k, v in n.items()
                      if k not in COLONNES and k not in ("journal", "_fichier")}
        f["extra"]["note_coffre"] = os.path.relpath(n["_fichier"], coffre)
        # Le texte du coffre est souvent un résumé de l'ancienne chaîne, parfois
        # traduit : on le garde, mais il sera remplacé par l'original dès que possible.
        if not rafraichir or not (reg.get(emp) or {}).get("texte_origine") == "plateforme":
            f["texte_origine"] = "coffre" if f.get("texte") else None
        else:
            f.pop("texte", None)
        nom = trouver_dossier(n, noms)
        if nom:
            f["dossier"] = nom
            f["cv_genere"] = bool(n.get("cv_genere")) or _dossier_a_un_cv(Path(coffre) / CANDIDATURES / nom)
            bilan["dossiers"] += 1
        preuve = date_preuve_jobroom(n["journal"])
        if preuve:
            f["jobroom_saisie_le"] = preuve
            bilan["preuves_jobroom"] += 1
        if essai:
            continue
        reg.enregistrer(f)
        if emp in connues:
            bilan["mises_a_jour"] += 1
            reg.cx.execute("DELETE FROM journal WHERE empreinte=? AND texte LIKE '[coffre] %'", (emp,))
        else:
            bilan["creees"] += 1
        for ligne in n["journal"]:
            m = _JOURNAL.match(ligne)
            quand, texte = (m.group(1), m.group(2)) if m else ("", ligne)
            reg.noter(emp, "[coffre] " + texte, quand=quand or maintenant())
    if not essai:
        for d in veilles:
            reg.cx.execute("INSERT OR IGNORE INTO veilles (date, origine) VALUES (?, 'coffre')", (d,))
        reg.poser_meta("import_coffre_le", maintenant())
        reg.poser_meta("import_coffre_chemin", str(coffre))
        reg.commit()
    return bilan
