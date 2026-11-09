"""Ton parcours : la base d'expérience de la personne, construite avec l'IA et gardée ici.

L'application est autoportante : aucun fichier extérieur. Le parcours vit dans la base
(`data/jobsearch.db`, tables `parcours*`) et c'est lui que l'IA reçoit pour noter les annonces
et écrire les dossiers, en entier, à chaque tâche (`document()`).

Il se construit en deux temps :
1. Import du CV (PDF, Word ou texte collé) : l'IA le range en éléments (postes, formations,
   projets, réalisations, compétences, certifications, langues). Chaque puce du CV devient une
   réalisation, mot pour mot ; le texte d'origine est gardé tel quel.
2. Entretien : poste après poste, la plateforme demande d'expliquer, puis l'IA pose une
   question à la fois pour faire émerger les projets, les sous-projets et les réalisations
   (situation, action personnelle, résultat, chiffre).

Rien d'inventé, et rien ne dépend de la mémoire de l'IA : chaque tour est une tâche neuve qui
reçoit la base telle qu'elle est enregistrée ; aucune conversation ne s'allonge, donc rien n'est
résumé ni « compacté ». Chaque élément porte la citation exacte (du CV ou de la réponse) dont il
vient, vérifiée ici avant d'être écrite ; un chiffre absent du texte de la personne fait écarter
l'élément. Les réponses elles-mêmes sont gardées mot pour mot (`parcours_echanges`).

Ce qui compte pour la personne (critères, intérêt, angles) se construit de la même façon
(`SUJET_CRITERES`) ; aucun montant de salaire n'y est gardé ni envoyé (`sans_montants`).
"""

import datetime as dt
import json
import re
import unicodedata
from io import BytesIO
from typing import Literal

from pydantic import BaseModel, Field

from . import moteur
from .db import Registre, maintenant

SCHEMA = """
CREATE TABLE IF NOT EXISTS parcours (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent INTEGER REFERENCES parcours(id) ON DELETE CASCADE,
  genre TEXT NOT NULL, titre TEXT NOT NULL, organisation TEXT DEFAULT '', lieu TEXT DEFAULT '',
  debut TEXT DEFAULT '', fin TEXT DEFAULT '', detail TEXT DEFAULT '', resultat TEXT DEFAULT '',
  citation TEXT DEFAULT '', origine TEXT NOT NULL, approfondi INTEGER DEFAULT 0, ordre INTEGER DEFAULT 0,
  cree_le TEXT, maj_le TEXT
);
CREATE INDEX IF NOT EXISTS parcours_parent ON parcours(parent);
CREATE TABLE IF NOT EXISTS parcours_echanges (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sujet TEXT NOT NULL,
  element INTEGER REFERENCES parcours(id) ON DELETE CASCADE,
  question TEXT NOT NULL, reponse TEXT, pose_le TEXT, repondu_le TEXT, proposition TEXT, appuis TEXT
);
CREATE TABLE IF NOT EXISTS parcours_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, texte TEXT NOT NULL, importe_le TEXT
);
"""

GENRES = {"poste": "Poste", "formation": "Formation", "engagement": "Engagement", "projet": "Projet",
          "realisation": "Réalisation", "competence": "Compétence", "certification": "Certification",
          "langue": "Langue"}
CADRES = ("poste", "formation", "engagement")          # ce que l'entretien approfondit, un à un
SOUS = ("projet", "realisation", "competence")           # ce qui se range sous un cadre ou un projet
SECTIONS = [("poste", "Expérience"), ("formation", "Formation"), ("engagement", "Engagements"),
            ("certification", "Certifications"), ("competence", "Compétences"), ("langue", "Langues")]
CHAMPS = ("titre", "organisation", "lieu", "debut", "fin", "detail", "resultat")

SUJET_PARCOURS, SUJET_CRITERES = "parcours", "criteres"
QUESTIONS_MAX = 6            # par élément, après la première : au-delà, on passe au suivant
QUESTIONS_MAX_CRITERES = 5
TEXTE_MAX = 60_000           # un CV plus long n'en est pas un


class ParcoursInvalide(Exception):
    pass


def _schema(reg):
    reg.cx.executescript(SCHEMA)
    colonnes = {r[1] for r in reg.cx.execute("PRAGMA table_info(parcours_echanges)")}
    if "appuis" not in colonnes:                      # bases d'avant les appuis des questions
        reg.cx.execute("ALTER TABLE parcours_echanges ADD COLUMN appuis TEXT")
        reg.commit()


def ouvrir():
    reg = Registre()
    _schema(reg)
    return reg


# --- Le texte d'un CV --------------------------------------------------------------------


def texte_du_fichier(nom, contenu):
    """Le texte d'un CV : PDF (avec sa couche texte), Word (.docx) ou texte brut."""
    nom = (nom or "").lower()
    if nom.endswith(".pdf"):
        from pypdf import PdfReader
        try:
            pages = PdfReader(BytesIO(contenu)).pages
            texte = "\n".join((p.extract_text() or "") for p in pages)
        except Exception as e:
            raise ParcoursInvalide("PDF illisible (%s)" % str(e)[:80]) from e
        if len(texte.strip()) < 80:
            raise ParcoursInvalide("ce PDF n'a pas de texte (CV scanné ?) : colle plutôt le texte du CV")
    elif nom.endswith(".docx"):
        from docx import Document
        try:
            doc = Document(BytesIO(contenu))
        except Exception as e:
            raise ParcoursInvalide("document Word illisible") from e
        lignes = [p.text for p in doc.paragraphs]
        for t in doc.tables:
            for rangee in t.rows:
                lignes.append(" | ".join(dict.fromkeys(c.text.strip() for c in rangee.cells if c.text.strip())))
        texte = "\n".join(lignes)
    elif nom.endswith((".txt", ".md", ".markdown")) or not nom:
        texte = contenu.decode("utf-8", errors="replace") if isinstance(contenu, bytes) else str(contenu)
    else:
        raise ParcoursInvalide("format non lu : PDF, Word (.docx) ou texte")
    texte = re.sub(r"\n{3,}", "\n\n", texte.replace("\r", "")).strip()
    if len(texte) < 80:
        raise ParcoursInvalide("le CV paraît vide")
    if len(texte) > TEXTE_MAX:
        raise ParcoursInvalide("le texte dépasse %d caractères : est-ce bien un CV ?" % TEXTE_MAX)
    return texte


# --- Fidélité : ce que l'IA rend doit venir du texte de la personne ------------------------


def _plat(t):
    t = unicodedata.normalize("NFKC", t or "")
    t = re.sub(r"[’‘`´]", "'", t)
    t = re.sub(r"[“”«»„]", '"', t)
    t = re.sub(r"[‐-―−]", "-", t)
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)          # césure de fin de ligne
    t = re.sub(r"[•●▪◦‣∙·*■□➢►✓]", " ", t)
    return re.sub(r"\s+", " ", t).strip().casefold()


def _mots(t):
    return re.findall(r"\w+", _plat(t))


def nombres(t):
    """Les nombres d'un texte, séparateurs de milliers retirés (« 15 000 » → 15000)."""
    t = re.sub(r"(?<=\d)[’' .,  ](?=\d{3}(?!\d))", "", unicodedata.normalize("NFKC", t or ""))
    return re.findall(r"\d+", t)


def chiffres_absents(texte, source):
    connus = set(nombres(source))
    return [n for n in dict.fromkeys(nombres(texte)) if n not in connus]


def ancre(citation, source):
    """Vrai si `citation` est bien tirée de `source` : mot pour mot (espaces, guillemets et
    césures près), ou presque (9 mots sur 10, mise en page d'un PDF) sans chiffre étranger."""
    c, s = _plat(citation).strip(" .,;:!?\"'()-"), _plat(source)
    if len(c) < 3:
        return False
    if c in s:
        return True
    mots = [m for m in _mots(citation) if len(m) >= 3]
    if len(mots) < 4:
        return False
    connus = set(_mots(source))
    return sum(m in connus for m in mots) / len(mots) >= 0.9 and not chiffres_absents(citation, source)


MONTANT = re.compile(
    r"(?i)(?:\b(?:chf|fr\.?|francs?|eur|€|\$|usd)\s*\d[\d’' .,]*(?:k|000)?\b"
    r"|\b\d{2,3}(?:[’' .,]?\d{3})+\s*(?:chf|fr\.?|francs?|€|eur|brut|net|par an|/an|annuel)?"
    r"|\b\d{2,3}\s?(?:k|kchf|mille)\b(?:\s*(?:chf|fr\.?|francs?))?)")


def sans_montants(texte):
    """Les montants retirés d'un texte du sujet « critères » : le salaire minimum visé
    n'est écrit nulle part, pas même dans les mots de la personne."""
    return MONTANT.sub("[montant retiré]", texte or "")


# --- La base ---------------------------------------------------------------------------


def _cle_date(s):
    """« 03.2021 », « 2021 », « mars 2021 » → (2021, 3) ; vide → en cours."""
    s = (s or "").strip()
    if not s or re.search(r"(?i)aujourd|en cours|présent|present|actuel|heute|today|now", s):
        return (9999, 12)
    an = re.search(r"(19|20)\d\d", s)
    mois = re.search(r"\b(0?[1-9]|1[0-2])[./-](?:19|20)\d\d", s)
    return (int(an.group(0)) if an else 0, int(mois.group(1)) if mois else 0)


def _ordre(e):
    fin = e["fin"] or ("" if e["genre"] in ("poste", "engagement") else e["debut"])   # une seule date : pas « en cours »
    return (-_cle_date(fin)[0], -_cle_date(fin)[1], -_cle_date(e["debut"])[0],
            -_cle_date(e["debut"])[1], e["ordre"], e["id"])


def elements(reg):
    _schema(reg)
    return [dict(r) for r in reg.cx.execute("SELECT * FROM parcours ORDER BY ordre, id")]


def element(reg, ident):
    _schema(reg)
    r = reg.cx.execute("SELECT * FROM parcours WHERE id=?", (ident,)).fetchone()
    return dict(r) if r else None


def _propre(v, n=2000):
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()[:n]


def ajouter(reg, genre, titre, parent=None, origine="saisie", citation="", **champs):
    if genre not in GENRES:
        raise ParcoursInvalide("genre inconnu : %s" % genre)
    titre = _propre(titre, 400)
    if not titre:
        raise ParcoursInvalide("un intitulé est nécessaire")
    if parent is not None and element(reg, parent) is None:
        raise ParcoursInvalide("élément parent introuvable")
    if parent is None and genre in ("projet", "realisation"):
        raise ParcoursInvalide("un projet ou une réalisation se range sous un poste, une formation ou un projet")
    valeurs = {k: _propre(champs.get(k)) for k in CHAMPS if k != "titre"}
    n = reg.cx.execute("SELECT COALESCE(MAX(ordre), 0) + 1 FROM parcours").fetchone()[0]
    cur = reg.cx.execute(
        "INSERT INTO parcours (parent, genre, titre, organisation, lieu, debut, fin, detail, resultat, citation, "
        "origine, ordre, cree_le, maj_le) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (parent, genre, titre, valeurs["organisation"], valeurs["lieu"], valeurs["debut"], valeurs["fin"],
         valeurs["detail"], valeurs["resultat"], _propre(citation, 4000), origine, n, maintenant(), maintenant()))
    reg.commit()
    return cur.lastrowid


def modifier(reg, ident, **champs):
    """Ce que la personne corrige elle-même : ses mots font foi."""
    e = element(reg, ident)
    if e is None:
        raise ParcoursInvalide("élément introuvable")
    v = {k: _propre(champs[k], 400 if k == "titre" else 2000) for k in CHAMPS if k in champs}
    if "titre" in v and not v["titre"]:
        raise ParcoursInvalide("un intitulé est nécessaire")
    if not v:
        return
    reg.cx.execute("UPDATE parcours SET %s, maj_le=? WHERE id=?" % ", ".join("%s=?" % k for k in v),
                   (*v.values(), maintenant(), ident))
    reg.commit()


def retirer(reg, ident):
    """Retire l'élément, ce qui est rangé dessous et l'entretien qui le concerne."""
    _schema(reg)
    reg.cx.execute("DELETE FROM parcours WHERE id=?", (ident,))
    reg.commit()


def vider(reg):
    _schema(reg)
    reg.cx.execute("DELETE FROM parcours_echanges WHERE sujet=?", (SUJET_PARCOURS,))
    reg.cx.execute("DELETE FROM parcours")
    reg.cx.execute("DELETE FROM parcours_documents")
    reg.commit()


def arbre(reg):
    """[(genre, titre de section, [nœud])] ; un nœud : l'élément et ses `enfants`, dans l'ordre."""
    tous = elements(reg)
    par_parent = {}
    for e in tous:
        par_parent.setdefault(e["parent"], []).append(e)

    def noeud(e):
        enfants = sorted(par_parent.get(e["id"], []), key=lambda x: (x["genre"] != "projet", x["ordre"], x["id"]))
        return dict(e, enfants=[noeud(x) for x in enfants])
    racines = par_parent.get(None, [])
    out = []
    for genre, titre in SECTIONS:
        liste = [x for x in racines if x["genre"] == genre]
        if genre in CADRES:
            liste.sort(key=_ordre)
        if liste:
            out.append((genre, titre, [noeud(x) for x in liste]))
    return out


def cadres(reg):
    return [n for g, _, ns in arbre(reg) if g in CADRES for n in ns]


def progression(reg):
    c = cadres(reg)
    return sum(1 for x in c if x["approfondi"]), len(c)


def termine(reg=None):
    """Le parcours est fait quand il a au moins un poste et que chaque poste, formation ou
    engagement a été approfondi (ou passé)."""
    reg = reg or ouvrir()
    c = cadres(reg)
    return any(x["genre"] == "poste" for x in c) and all(x["approfondi"] for x in c)


def vide(reg=None):
    reg = reg or ouvrir()
    _schema(reg)
    return reg.cx.execute("SELECT COUNT(*) FROM parcours").fetchone()[0] == 0


def documents(reg):
    _schema(reg)
    return [dict(r) for r in reg.cx.execute("SELECT * FROM parcours_documents ORDER BY id")]


# --- Ce que l'IA reçoit -------------------------------------------------------------------


def _ligne_cadre(e):
    morceaux = [e["titre"]] + [x for x in (e["organisation"], e["lieu"]) if x]
    en_cours = "aujourd'hui" if e["debut"] and e["genre"] in ("poste", "engagement") else ""
    dates = " – ".join(x for x in (e["debut"], e["fin"] or en_cours) if x)
    return " | ".join(morceaux + ([dates] if dates else []))


def _citation(e):
    c = e.get("citation") or ""
    return "" if not c or _plat(c) == _plat(e["titre"]) else " Ses mots : « %s »" % c


def _rendre(n, prof=0):
    pad = "  " * prof
    lignes = []
    if n["genre"] in CADRES:
        lignes.append("### [E-%d] %s" % (n["id"], _ligne_cadre(n)))
        if n["detail"]:
            lignes.append(n["detail"])
    else:
        dates = " – ".join(x for x in (n["debut"], n["fin"]) if x)
        tete = "%s- [E-%d] %s : %s" % (pad, n["id"], GENRES[n["genre"]], n["titre"])
        if n["organisation"]:
            tete += " | " + n["organisation"]
        if dates:
            tete += " (%s)" % dates
        if n["detail"]:
            tete += ". " + n["detail"]
        if n["resultat"]:
            tete += ". Résultat : " + n["resultat"]
        lignes.append(tete + _citation(n))
    for x in n["enfants"]:
        lignes += _rendre(x, prof + (1 if n["genre"] not in CADRES else 0))
    return lignes


def rendu(reg):
    """La base en Markdown, éléments numérotés E-n (comme les preuves P-xx)."""
    lignes = []
    for genre, titre, noeuds in arbre(reg):
        lignes += ["", "## " + titre]
        for n in noeuds:
            lignes += _rendre(n)
    return "\n".join(lignes).strip()


def document(reg=None):
    """Ce que les tâches de notation et de dossier reçoivent : toute la base, et le CV
    d'origine tel qu'importé. Vide tant que la base est vide."""
    reg = reg or ouvrir()
    corps = rendu(reg)
    if not corps:
        return ""
    cv = documents(reg)
    texte = ("# Parcours\n\nBase d'expérience construite avec la personne : chaque élément vient de son CV ou de "
             "ses propres mots, et ses mots font foi. Un chiffre, un nom ou un résultat absent d'ici n'existe pas. "
             "Les références E-n se citent comme les preuves dans les notes de travail, jamais dans un CV ou une "
             "lettre.\n\n" + corps)
    if cv:
        texte += "\n\n## CV d'origine\n\n" + "\n\n".join(d["texte"] for d in cv)
    return '<document nom="Parcours">\n%s\n</document>' % texte


# --- 1. Import du CV ----------------------------------------------------------------------


class ElementCV(BaseModel):
    ref: str = Field(description="référence locale unique : C1, C2…")
    parent: str = Field("", description="ref de l'élément sous lequel il se range ; vide pour un poste, une formation…")
    genre: Literal["poste", "formation", "engagement", "projet", "realisation", "competence", "certification", "langue"]
    titre: str = Field(description="intitulé du poste ou du diplôme, nom du projet ; pour une réalisation, la puce du CV mot pour mot ; pour une langue, son nom")
    organisation: str = Field("", description="employeur, école ou organisme, tel qu'écrit")
    lieu: str = ""
    debut: str = Field("", description="MM.AAAA ou AAAA")
    fin: str = Field("", description="MM.AAAA ou AAAA ; vide si en cours")
    detail: str = Field("", description="ce que le CV dit de plus, mot pour mot ; niveau pour une langue")
    citation: str = Field(description="le passage du CV dont l'élément vient, copié mot pour mot")


class ImportCV(BaseModel):
    elements: list[ElementCV]


CONSIGNE_IMPORT = """Tu ranges le CV de %(prenom)s, fourni dans <cv>, en éléments d'une base d'expérience. Tu n'écris rien : tu ranges.
- Tout le CV, sans rien omettre ni résumer : chaque poste, formation, engagement (bénévolat, association, mandat), certification, langue et compétence citée.
- Chaque puce ou phrase décrivant un poste devient une réalisation (genre « realisation ») rangée sous ce poste, son titre étant la puce mot pour mot. Un projet nommé devient un « projet », ses puces des réalisations rangées sous lui.
- « citation » : le passage exact du CV dont vient l'élément, copié sans retouche (pour un poste, sa ligne d'intitulé ; pour une compétence, le mot tel qu'il figure).
- Dates au format MM.AAAA (ou AAAA si le mois manque), converties depuis le CV ; « fin » vide pour un poste en cours.
- Rien d'inventé ni de déduit : aucun chiffre, nom ou date qui ne soit dans le CV. L'en-tête de coordonnées n'est pas un élément.
- Les balises comme {PRENOM} remplacent des coordonnées : garde-les telles quelles."""


def _prenom():
    from . import profil
    return profil.prenom()


def importer_cv(reg, texte, nom="CV", c=None):
    """Range le CV dans la base. Renvoie {"ajoutes": n, "ecartes": [titres], "doublons": n}."""
    _schema(reg)
    texte = (texte or "").strip()
    if len(texte) < 80:
        raise ParcoursInvalide("le CV paraît vide")
    c = c or moteur.ouvrir()
    t = moteur.Tache("parcours-import", CONSIGNE_IMPORT % {"prenom": _prenom()},
                     contexte="<cv>\n%s\n</cv>" % texte, schema=ImportCV, effort="high", delai_s=600)
    r = c.executer(t, "Range ce CV dans la base, élément par élément.")
    bilan = {"ajoutes": 0, "ecartes": [], "doublons": 0}
    existants = {(_plat(e["genre"]), _plat(e["titre"]), _plat(e["organisation"]), e["parent"]): e["id"]
                 for e in elements(reg)}
    ids = {}
    for el in r.objet.elements:
        parent = ids.get(el.parent) if el.parent else None
        if el.parent and parent is None:
            bilan["ecartes"].append(el.titre)          # son parent a été écarté
            continue
        if el.genre in ("projet", "realisation") and parent is None:
            bilan["ecartes"].append(el.titre)
            continue
        if not ancre(el.citation, texte) or chiffres_absents(" ".join((el.titre, el.detail, el.organisation)), texte):
            bilan["ecartes"].append(el.titre)
            continue
        cle = (_plat(el.genre), _plat(el.titre), _plat(el.organisation), parent)
        if cle in existants:                            # CV importé deux fois : on rattache, on ne double pas
            ids[el.ref] = existants[cle]
            bilan["doublons"] += 1
            continue
        ident = ajouter(reg, el.genre, el.titre, parent=parent, origine="cv", citation=el.citation,
                        organisation=el.organisation, lieu=el.lieu, debut=el.debut, fin=el.fin, detail=el.detail)
        ids[el.ref] = existants[cle] = ident
        bilan["ajoutes"] += 1
    reg.cx.execute("INSERT INTO parcours_documents (nom, texte, importe_le) VALUES (?,?,?)",
                   (nom or "CV", texte, maintenant()))
    reg.commit()
    return bilan


# --- 2. Entretien sur le parcours ---------------------------------------------------------


class Ajout(BaseModel):
    ref: str = Field(description="référence de ce tour : N1, N2…")
    parent: str = Field(description="E-n de l'élément en cours ou d'un de ses projets, ou N1… d'un ajout de ce tour")
    genre: Literal["projet", "realisation", "competence"]
    titre: str = Field(description="nom du projet ; pour une réalisation : action, objet, résultat, avec les mots de la personne")
    detail: str = Field("", description="contexte, rôle personnel, comment, avec qui, durée : ce que la réponse en dit")
    resultat: str = Field("", description="le résultat tel que dit, chiffre compris s'il est dit ; vide sinon")
    debut: str = ""
    fin: str = ""
    citation: str = Field(description="le passage de la réponse dont l'ajout vient, copié mot pour mot")


class Precision(BaseModel):
    element: str = Field(description="E-n de l'élément précisé (l'élément en cours ou un de ses projets ou réalisations)")
    detail: str = Field(description="ce que la réponse ajoute sur lui (contexte, mission, équipe, périmètre)")
    citation: str = Field(description="le passage de la réponse dont la précision vient, copié mot pour mot")


class Appui(BaseModel):
    source: str = Field(description="d'où vient le passage : « réponse » (la dernière réponse), « échanges » (une "
                                    "réponse précédente), « recherche », ou la référence E-n d'un élément de la base")
    passage: str = Field(description="quelques mots à une phrase, copiés mot pour mot de cette source")


class Tour(BaseModel):
    ajouts: list[Ajout] = Field(default_factory=list)
    precisions: list[Precision] = Field(default_factory=list)
    question: str = Field(description="la question suivante, une seule ; vide si suffisant")
    pourquoi: str = Field("", description="ce que la question cherche à compléter, une phrase courte et neutre")
    appuis: list[Appui] = Field(default_factory=list, description="1 à 3 passages sur lesquels la question s'appuie")
    suffisant: bool


CONSIGNE_ENTRETIEN = """Tu mènes un entretien bienveillant et précis avec %(prenom)s pour construire sa base d'expérience, la matière dont seront tirés ses CV et ses lettres. Tu reçois la base telle qu'elle est enregistrée (éléments E-n), puis l'élément en cours d'approfondissement, les échanges déjà eus sur lui et la dernière réponse.

À chaque tour :
1. Range dans la base ce que la dernière réponse apporte, sans rien perdre ni rien inventer.
   - « ajouts » : un projet (un sous-projet se range sous son projet), une réalisation (une action et son résultat), une compétence démontrée. Le parent est l'élément en cours, un de ses projets (E-n) ou un ajout de ce tour (N1, N2…).
   - « precisions » : ce que la réponse dit d'un élément déjà là (contexte, mission, équipe, périmètre).
   - Chaque ajout et chaque précision porte « citation » : le passage exact de la dernière réponse dont il vient, copié mot pour mot. Une citation introuvable fait écarter l'ajout.
   - Le titre d'une réalisation garde les mots de %(prenom)s, à peine resserrés : action, objet, résultat. Aucun chiffre, nom, date ou résultat qui ne soit dans la réponse, aucun adjectif flatteur ajouté.
   - Ce qui est déjà dans la base n'est pas ajouté une deuxième fois : précise-le plutôt.
2. Pose la question suivante, une seule, courte, en tutoyant, en français. Elle creuse ce qui manque pour que chaque réalisation se défende en entretien d'embauche : la situation de départ, ce que %(prenom)s a fait personnellement (pas l'équipe), comment, avec qui, sur quelle durée, le résultat et comment il a été constaté, un chiffre s'il existe ; puis les projets et sous-projets pas encore racontés. Rebondis sur ce qui vient d'être dit ; ne repose jamais une question déjà posée ; si la personne ne sait pas ou n'a pas de chiffre, n'insiste pas.
3. « suffisant » : vrai quand l'élément est assez riche (ses projets et réalisations principales ont contexte, action et résultat) ou que la personne n'a plus rien à ajouter ; la question est alors vide.
4. « pourquoi » et « appuis » : ce sur quoi la question s'appuie, montré en petit sous la question pour que %(prenom)s puisse vérifier. « pourquoi » : ce que la question cherche à compléter, une phrase courte et neutre, sans « je » (« Le résultat du projet n'est pas encore dit. »). « appuis » : 1 à 3 passages, copiés mot pour mot, chacun avec sa source (« réponse », « échanges » ou E-n d'un élément de la base). Un passage introuvable mot pour mot est écarté. Les deux sont vides si la question est vide.

Jamais de question sur le salaire, la santé, la famille, l'origine, la religion, l'âge ou une période sans emploi."""


def premiere_question(e):
    """La première question sur un élément : fixe, sans IA."""
    chez = (" chez %s" % e["organisation"]) if e["organisation"] else ""
    if e["genre"] == "poste":
        return ("Parle-moi de ton poste « %s »%s : dans quel contexte tu es arrivé, ce qu'on attendait de toi, avec "
                "qui tu travaillais, et ce que tu y as accompli de plus important." % (e["titre"], chez))
    if e["genre"] == "formation":
        return ("Raconte ta formation « %s »%s : ce que tu y as appris qui te sert dans ton métier, et un projet ou "
                "un travail qui t'a marqué." % (e["titre"], chez))
    return ("Parle-moi de ton engagement « %s »%s : ton rôle, ce que tu y as fait concrètement et ce que ça a "
            "changé." % (e["titre"], chez))


def _echanges(reg, sujet, element=None):
    """Les échanges, chacun avec `appui` : sur quoi sa question s'appuie (None pour les anciennes)."""
    _schema(reg)
    if element is None:
        q = reg.cx.execute("SELECT * FROM parcours_echanges WHERE sujet=? AND element IS NULL ORDER BY id", (sujet,))
    else:
        q = reg.cx.execute("SELECT * FROM parcours_echanges WHERE sujet=? AND element=? ORDER BY id", (sujet, element))
    out = []
    for r in q:
        x = dict(r)
        x["appui"] = json.loads(x["appuis"]) if x.get("appuis") else None
        out.append(x)
    return out


def _poser_question(reg, sujet, element, question, appui=None):
    reg.cx.execute("INSERT INTO parcours_echanges (sujet, element, question, pose_le, appuis) VALUES (?,?,?,?,?)",
                   (sujet, element, _propre(question, 800), maintenant(),
                    json.dumps(appui, ensure_ascii=False) if appui else None))
    reg.commit()


# --- Sur quoi s'appuie une question -----------------------------------------------------------

LIBELLES_SOURCES = {"reponse": "Ta réponse", "echanges": "Une réponse précédente", "recherche": "Ta recherche"}


def _sans_accents(t):
    return "".join(c for c in unicodedata.normalize("NFD", _plat(t)) if not unicodedata.combining(c))


def _source_element(x):
    return {"source": "element", "element": x["id"], "libelle": "%s « %s »" % (GENRES[x["genre"]], x["titre"])}


def appuis_verifies(reg, pourquoi, appuis, textes, filtre=lambda t: t):
    """Ce qui est montré sous une question de l'IA : son « pourquoi » et ses passages, gardés
    seulement s'ils sont bien, mot pour mot, dans la source citée (`textes` : réponse,
    échanges, recherche ; E-n : l'élément de la base). None s'il ne reste rien."""
    gardes = []
    for a in appuis[:3]:
        passage = filtre(_propre(a.passage, 300))
        n = _ref(a.source)
        if n is not None:
            x = element(reg, n)
            texte = " ".join(x[k] for k in ("titre", "organisation", "lieu", "detail", "resultat", "citation") if x[k]) if x else ""
            if x and ancre(passage, texte):
                gardes.append(dict(_source_element(x), passage=passage))
            continue
        s = _sans_accents(a.source)
        cle = next((k for k, debut in (("reponse", "rep"), ("echanges", "ech"), ("recherche", "rech")) if s.startswith(debut)), None)
        if cle and textes.get(cle) and ancre(passage, textes[cle]):
            gardes.append({"source": cle, "libelle": LIBELLES_SOURCES[cle], "passage": passage})
    pourquoi = filtre(_propre(pourquoi, 300))
    return {"pourquoi": pourquoi, "appuis": gardes} if pourquoi or gardes else None


def appui_fixe(pourquoi, x=None):
    """Une question écrite d'avance (sans IA) : dite comme telle, avec l'élément qu'elle reprend."""
    appuis = []
    if x is not None:
        passage = x["citation"] or " · ".join(v for v in (x["titre"], x["organisation"]) if v)
        appuis.append(dict(_source_element(x), passage=_propre(passage, 300)))
    return {"fixe": True, "pourquoi": pourquoi, "appuis": appuis}


def element_en_cours(reg):
    return next((x for x in cadres(reg) if not x["approfondi"]), None)


def etat_entretien(reg):
    """L'élément en cours, ses échanges (le dernier sans réponse : la question posée) et
    l'avancement. La première question d'un élément est posée ici."""
    e = element_en_cours(reg)
    faits, total = progression(reg)
    if e is None:
        return {"element": None, "echanges": [], "faits": faits, "total": total}
    ech = _echanges(reg, SUJET_PARCOURS, e["id"])
    if not ech or ech[-1]["reponse"] is not None:
        if not ech:
            _poser_question(reg, SUJET_PARCOURS, e["id"], premiere_question(e), appui_fixe(
                "Question de départ, écrite d'avance pour chaque %s de ta base : l'IA prend le relais après ta "
                "réponse." % GENRES[e["genre"]].lower(), e))
        else:
            _poser_question(reg, SUJET_PARCOURS, e["id"], "Qu'aimerais-tu ajouter sur « %s » ?" % e["titre"],
                            appui_fixe("Question écrite d'avance : l'entretien reprend sur cet élément.", e))
        ech = _echanges(reg, SUJET_PARCOURS, e["id"])
    return {"element": element(reg, e["id"]), "echanges": ech, "faits": faits, "total": total,
            "rang": faits + 1}


def _ref(s):
    m = re.fullmatch(r"\s*E-?(\d+)\s*", s or "")
    return int(m.group(1)) if m else None


def _sous_arbre(reg, racine):
    ids, pile = {racine}, [racine]
    while pile:
        for r in reg.cx.execute("SELECT id FROM parcours WHERE parent=?", (pile.pop(),)):
            ids.add(r[0])
            pile.append(r[0])
    return ids


def _appliquer_tour(reg, e, tour, reponse, source):
    """Écrit les ajouts et précisions dont la citation est bien dans la réponse."""
    permis = _sous_arbre(reg, e["id"])
    nouveaux, ajoutes, ecartes = {}, [], []
    for a in tour.ajouts:
        parent = nouveaux.get(a.parent.strip()) or _ref(a.parent)
        if parent not in permis:
            parent = e["id"]                             # rangé sous l'élément en cours, à défaut
        champs = " ".join((a.titre, a.detail, a.resultat))
        if not ancre(a.citation, reponse) or chiffres_absents(champs, source):
            ecartes.append(a.titre)
            continue
        ident = ajouter(reg, a.genre, a.titre, parent=parent, origine="entretien", citation=a.citation,
                        detail=a.detail, resultat=a.resultat, debut=a.debut, fin=a.fin)
        nouveaux[a.ref.strip()] = ident
        permis.add(ident)
        ajoutes.append(a.titre)
    for p in tour.precisions:
        cible = _ref(p.element)
        if cible not in permis or not p.detail.strip():
            continue
        if not ancre(p.citation, reponse) or chiffres_absents(p.detail, source):
            ecartes.append(p.detail[:80])
            continue
        x = element(reg, cible)
        if _plat(p.detail) in _plat(x["detail"]):
            continue
        detail = (x["detail"] + "\n" if x["detail"] else "") + _propre(p.detail)
        reg.cx.execute("UPDATE parcours SET detail=?, maj_le=? WHERE id=?", (detail[:6000], maintenant(), cible))
    reg.commit()
    return ajoutes, ecartes


def _message_tour(reg, e, ech, reponse):
    blocs = ["<element_en_cours>\n%s\n</element_en_cours>" % "\n".join(_rendre(dict(e, enfants=_enfants(reg, e["id"]))))]
    passes = [x for x in ech[:-1] if x["reponse"] is not None]
    if passes:
        blocs.append("<echanges>\n%s\n</echanges>" % "\n\n".join(
            "Question : %s\nRéponse : %s" % (x["question"], x["reponse"]) for x in passes))
    blocs.append("<question>\n%s\n</question>\n\n<derniere_reponse>\n%s\n</derniere_reponse>" % (ech[-1]["question"], reponse))
    return "\n\n".join(blocs)


def _enfants(reg, ident):
    noeuds = {n["id"]: n for _, _, ns in arbre(reg) for n in ns}
    return (noeuds.get(ident) or {}).get("enfants", [])


def repondre(reg, reponse, c=None):
    """Enregistre la réponse à la question posée, range ce qu'elle apporte et pose la suivante.
    Si l'IA ne répond pas, rien n'est gardé : la réponse reste à renvoyer."""
    reponse = (reponse or "").strip()
    if not reponse:
        raise ParcoursInvalide("la réponse est vide")
    etat = etat_entretien(reg)
    e, ech = etat["element"], etat["echanges"]
    if e is None:
        raise ParcoursInvalide("l'entretien est terminé")
    c = c or moteur.ouvrir()
    t = moteur.Tache("parcours-entretien", CONSIGNE_ENTRETIEN % {"prenom": _prenom()},
                     contexte="<base>\n%s\n</base>" % rendu(reg), schema=Tour, effort="medium", delai_s=300)
    r = c.executer(t, _message_tour(reg, e, ech, reponse))
    tour = r.objet
    reg.cx.execute("UPDATE parcours_echanges SET reponse=?, repondu_le=? WHERE id=?",
                   (reponse, maintenant(), ech[-1]["id"]))
    source = "\n".join([reponse] + [x["reponse"] for x in ech if x["reponse"]] + [rendu(reg)])
    ajoutes, ecartes = _appliquer_tour(reg, e, tour, reponse, source)
    poses = sum(1 for x in ech)
    fini = tour.suffisant or not tour.question.strip() or poses > QUESTIONS_MAX
    if fini:
        reg.cx.execute("UPDATE parcours SET approfondi=1, maj_le=? WHERE id=?", (maintenant(), e["id"]))
        reg.commit()
    else:
        textes = {"reponse": reponse, "echanges": "\n".join(x["reponse"] for x in ech if x["reponse"])}
        _poser_question(reg, SUJET_PARCOURS, e["id"], tour.question,
                        appuis_verifies(reg, tour.pourquoi, tour.appuis, textes))
    return {"ajoutes": ajoutes, "ecartes": ecartes, "suivant": fini}


def passer(reg):
    """Passe à l'élément suivant ; la question posée sans réponse est retirée."""
    e = element_en_cours(reg)
    if e is None:
        return
    reg.cx.execute("DELETE FROM parcours_echanges WHERE element=? AND reponse IS NULL", (e["id"],))
    reg.cx.execute("UPDATE parcours SET approfondi=1, maj_le=? WHERE id=?", (maintenant(), e["id"]))
    reg.commit()


def reprendre(reg, ident):
    """Rouvre l'entretien sur un élément déjà approfondi."""
    e = element(reg, ident)
    if e is None or e["genre"] not in CADRES:
        raise ParcoursInvalide("seuls un poste, une formation ou un engagement se reprennent en entretien")
    reg.cx.execute("UPDATE parcours SET approfondi=0 WHERE id=?", (ident,))
    reg.commit()


# --- 3. Ce qui compte pour toi ---------------------------------------------------------------


class Proposition(BaseModel):
    criteres: list[str] = Field(description="3 à 7 critères non négociables, chacun « nom : précision »")
    interet: str = Field("", description="ce qui rend un poste intéressant, une ligne, avec ses mots")
    angles: list[str] = Field(default_factory=list, description="2 à 4 façons de se présenter, tirées du parcours")
    differenciateur: str = Field("", description="un ou deux mots tirés du parcours ; vide si rien ne ressort")


class TourCriteres(BaseModel):
    proposition: Proposition
    question: str = Field(description="la question suivante, une seule ; vide si suffisant")
    pourquoi: str = Field("", description="ce que la question cherche à compléter, une phrase courte et neutre")
    appuis: list[Appui] = Field(default_factory=list, description="1 à 3 passages sur lesquels la question s'appuie")
    suffisant: bool


CONSIGNE_CRITERES = """Tu aides %(prenom)s à dire ce qui compte pour son prochain poste, pour que l'IA de la plateforme note chaque annonce comme %(prenom)s le ferait. Tu reçois sa recherche, sa base d'expérience, les échanges déjà eus et la dernière réponse.

À chaque tour :
1. « proposition » : la version à jour, tirée des réponses (et du parcours pour les angles et le différenciateur).
   - criteres : 3 à 7 critères non négociables, chacun « nom : précision », court (« trajet : 45 minutes au plus depuis la maison », « télétravail : deux jours par semaine »). Un critère vient d'une réponse, jamais de toi.
   - interet : ce qui rend un poste intéressant, en une ligne, avec ses mots.
   - angles : 2 à 4 façons de se présenter à un employeur, tirées du parcours, en quelques mots chacune.
   - differenciateur : un ou deux mots, ce qui distingue %(prenom)s d'après le parcours ; vide si rien ne ressort.
2. « question » : la suivante, une seule, courte, en tutoyant : ce qui manque encore (contenu du travail, environnement, taille et type d'organisation, secteur, lieu et trajet, télétravail, rythme, management, valeurs, ce que %(prenom)s ne veut plus). Rebondis sur la réponse, ne répète rien.
3. « suffisant » : vrai quand la proposition couvre l'essentiel ; la question est alors vide.
4. « pourquoi » et « appuis » : ce sur quoi la question s'appuie, montré en petit sous la question pour que %(prenom)s puisse vérifier. « pourquoi » : ce que la question cherche à compléter, une phrase courte et neutre, sans « je » (« Le résultat du projet n'est pas encore dit. »). « appuis » : 1 à 3 passages, copiés mot pour mot, chacun avec sa source (« réponse », « échanges », « recherche » ou E-n d'un élément de la base). Un passage introuvable mot pour mot est écarté. Les deux sont vides si la question est vide.

Jamais de montant ni de question sur le salaire : le salaire minimum visé n'est écrit nulle part. Jamais de question sur la santé, la famille, l'origine, la religion, l'âge ou une période sans emploi."""

PREMIERE_CRITERES = ("Qu'est-ce qui ferait de ton prochain poste un bon poste pour toi ? Pense au contenu du travail, "
                     "à l'équipe, au type d'organisation, au lieu et au rythme, et à ce que tu ne veux plus.")


def etat_criteres(reg):
    """Les échanges sur ce qui compte, et la dernière proposition de l'IA (None avant la première réponse)."""
    ech = _echanges(reg, SUJET_CRITERES)
    if not ech:
        _poser_question(reg, SUJET_CRITERES, None, PREMIERE_CRITERES, appui_fixe(
            "Question de départ, la même pour tout le monde : l'IA n'a encore rien lu de toi."))
        ech = _echanges(reg, SUJET_CRITERES)
    derniere = next((x["proposition"] for x in reversed(ech) if x["proposition"]), None)
    return {"echanges": ech, "termine": ech[-1]["reponse"] is not None,
            "proposition": Proposition.model_validate_json(derniere) if derniere else None}


def _contexte_criteres(reg):
    from . import profil
    p = profil.charger()
    lignes = ["Poste recherché : %s" % ", ".join(profil.lire(p, "recherche.requetes") or []) or "?",
              "Cantons : %s" % ", ".join(profil.lire(p, "recherche.cantons") or []) or "?",
              "Objectif : %s" % next((o["libelle"] for o in profil.OBJECTIFS if o["valeur"] == profil.objectif(p)), "?")]
    return "<recherche>\n%s\n</recherche>\n\n<base>\n%s\n</base>" % ("\n".join(lignes), rendu(reg) or "(vide)")


def _propre_proposition(p):
    nettoie = lambda t: sans_montants(_propre(t, 300))
    criteres = [x for x in dict.fromkeys(nettoie(c) for c in p.criteres) if x and "[montant retiré]" not in x]
    return Proposition(criteres=criteres[:8], interet=nettoie(p.interet),
                       angles=[x for x in dict.fromkeys(nettoie(a) for a in p.angles) if x][:5],
                       differenciateur=nettoie(p.differenciateur)[:60])


def repondre_criteres(reg, reponse, c=None):
    """Une réponse sur ce qui compte : rend la proposition à jour, et pose la question suivante
    tant qu'il en reste. Les montants sont retirés de la réponse avant tout (gardée et envoyée)."""
    reponse = sans_montants((reponse or "").strip())
    if not reponse:
        raise ParcoursInvalide("la réponse est vide")
    ech = etat_criteres(reg)["echanges"]
    if ech[-1]["reponse"] is not None:
        _poser_question(reg, SUJET_CRITERES, None, "Qu'aimerais-tu ajouter ?",
                        appui_fixe("Question écrite d'avance : l'entretien reprend après la proposition."))
        ech = _echanges(reg, SUJET_CRITERES)
    c = c or moteur.ouvrir()
    passes = [x for x in ech[:-1] if x["reponse"] is not None]
    message = ("<echanges>\n%s\n</echanges>\n\n" % "\n\n".join(
        "Question : %s\nRéponse : %s" % (x["question"], x["reponse"]) for x in passes) if passes else "")
    message += "<question>\n%s\n</question>\n\n<derniere_reponse>\n%s\n</derniere_reponse>" % (ech[-1]["question"], reponse)
    t = moteur.Tache("parcours-criteres", CONSIGNE_CRITERES % {"prenom": _prenom()},
                     contexte=_contexte_criteres(reg), schema=TourCriteres, effort="medium", delai_s=300)
    tour = c.executer(t, message).objet
    proposition = _propre_proposition(tour.proposition)
    reg.cx.execute("UPDATE parcours_echanges SET reponse=?, repondu_le=?, proposition=? WHERE id=?",
                   (reponse, maintenant(), proposition.model_dump_json(), ech[-1]["id"]))
    reg.commit()
    fini = tour.suffisant or not tour.question.strip() or len(ech) > QUESTIONS_MAX_CRITERES
    if not fini:
        textes = {"reponse": reponse, "echanges": "\n".join(x["reponse"] for x in passes),
                  "recherche": _contexte_criteres(reg).split("</recherche>")[0]}
        _poser_question(reg, SUJET_CRITERES, None, tour.question,
                        appuis_verifies(reg, tour.pourquoi, tour.appuis, textes, filtre=sans_montants))
    return {"proposition": proposition, "suivant": fini}


def recommencer_criteres(reg):
    _schema(reg)
    reg.cx.execute("DELETE FROM parcours_echanges WHERE sujet=?", (SUJET_CRITERES,))
    reg.commit()


def date_fr(iso):
    try:
        return dt.datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""
