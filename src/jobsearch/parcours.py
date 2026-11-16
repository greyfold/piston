"""Ton parcours : la base d'expérience de la personne, construite avec l'IA et gardée ici.

L'application est autoportante : aucun fichier extérieur. Le parcours vit dans la base
(`data/jobsearch.db`, tables `parcours*`) et c'est lui que l'IA reçoit pour noter les annonces
et écrire les dossiers, en entier, à chaque tâche (`document()`).

Il se construit en deux temps :
1. Import du CV (PDF, Word ou texte collé) : l'IA le range en éléments (postes, formations,
   projets, réalisations, compétences, certifications, langues). Chaque puce d'un poste devient
   une réalisation, mot pour mot ; le texte d'origine est gardé tel quel. Une formation reste une
   ligne (intitulé, école, lieu, dates) : rien ne se range dessous, aucun détail.
2. Entretien : expérience après expérience (postes, engagements), la plateforme demande
   d'expliquer, puis l'IA pose une question à la fois pour faire émerger les projets, les
   sous-projets et les réalisations (situation, action personnelle, résultat, chiffre). Les
   formations n'y passent pas.

Rien d'inventé, et rien ne dépend de la mémoire de l'IA : chaque tour est une tâche neuve qui
reçoit la base telle qu'elle est enregistrée ; aucune conversation ne s'allonge, donc rien n'est
résumé ni « compacté ». Chaque élément porte la citation exacte (du CV ou de la réponse) dont il
vient, vérifiée ici avant d'être écrite ; un chiffre absent du texte de la personne fait écarter
l'élément. Les réponses elles-mêmes sont gardées mot pour mot (`parcours_echanges`).

Ce qui compte pour la personne (critères, pré-tri, objectifs) se règle à part, par un questionnaire
sans IA (`ce_qui_compte.py`) ; aucun montant de salaire n'y est gardé ni envoyé (`sans_montants`).
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
CADRES = ("poste", "engagement")       # les expériences : l'entretien les approfondit, une à une
DATES = CADRES + ("formation",)        # premier niveau classé par date ; une formation reste une ligne
SOUS = ("projet", "realisation", "competence")           # ce qui se range sous un cadre ou un projet
SECTIONS = [("poste", "Expérience"), ("formation", "Formation"), ("engagement", "Engagements"),
            ("certification", "Certifications"), ("competence", "Compétences"), ("langue", "Langues")]
CHAMPS = ("titre", "organisation", "lieu", "debut", "fin", "detail", "resultat")

SUJET_PARCOURS = "parcours"
QUESTIONS_MAX = 6            # par élément, après la première : au-delà, on passe au suivant
TEXTE_MAX = 60_000           # un CV plus long n'en est pas un


class ParcoursInvalide(Exception):
    pass


def _schema(reg):
    """Une fois par connexion : `executescript` valide toute transaction en cours, ce qui casserait
    un enregistrement fait d'un bloc (`importer_export`)."""
    if getattr(reg, "_schema_parcours", False):
        return
    reg.cx.executescript(SCHEMA)
    colonnes = {r[1] for r in reg.cx.execute("PRAGMA table_info(parcours_echanges)")}
    if "appuis" not in colonnes:                      # bases d'avant les appuis des questions
        reg.cx.execute("ALTER TABLE parcours_echanges ADD COLUMN appuis TEXT")
        reg.commit()
    reg._schema_parcours = True


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


def ajouter(reg, genre, titre, parent=None, origine="saisie", citation="", valider=True, **champs):
    """`valider` : faux quand l'appelant enregistre tout d'un bloc (import d'un export)."""
    if genre not in GENRES:
        raise ParcoursInvalide("genre inconnu : %s" % genre)
    titre = _propre(titre, 400)
    if not titre:
        raise ParcoursInvalide("un intitulé est nécessaire")
    if parent is not None:
        p = element(reg, parent)
        if p is None:
            raise ParcoursInvalide("élément parent introuvable")
        if p["genre"] == "formation":
            raise ParcoursInvalide("une formation reste une ligne : rien ne se range dessous")
    if parent is None and genre in ("projet", "realisation"):
        raise ParcoursInvalide("un projet ou une réalisation se range sous un poste, un engagement ou un projet")
    valeurs = {k: _propre(champs.get(k)) for k in CHAMPS if k != "titre"}
    if genre == "formation":
        valeurs["detail"] = valeurs["resultat"] = ""
    n = reg.cx.execute("SELECT COALESCE(MAX(ordre), 0) + 1 FROM parcours").fetchone()[0]
    cur = reg.cx.execute(
        "INSERT INTO parcours (parent, genre, titre, organisation, lieu, debut, fin, detail, resultat, citation, "
        "origine, ordre, cree_le, maj_le) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (parent, genre, titre, valeurs["organisation"], valeurs["lieu"], valeurs["debut"], valeurs["fin"],
         valeurs["detail"], valeurs["resultat"], _propre(citation, 4000), origine, n, maintenant(), maintenant()))
    if valider:
        reg.commit()
    return cur.lastrowid


def modifier(reg, ident, **champs):
    """Ce que la personne corrige elle-même : ses mots font foi."""
    e = element(reg, ident)
    if e is None:
        raise ParcoursInvalide("élément introuvable")
    v = {k: _propre(champs[k], 400 if k == "titre" else 2000) for k in CHAMPS if k in champs
         and not (e["genre"] == "formation" and k in ("detail", "resultat"))}
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


def vider(reg, valider=True):
    _schema(reg)
    reg.cx.execute("DELETE FROM parcours_echanges WHERE sujet=?", (SUJET_PARCOURS,))
    reg.cx.execute("DELETE FROM parcours")
    reg.cx.execute("DELETE FROM parcours_documents")
    if valider:
        reg.commit()


def arbre(reg):
    """[(genre, titre de section, [nœud])] ; un nœud : l'élément et ses `enfants`, dans l'ordre.
    Une formation n'a ni enfants ni détail (ceux d'avant restent en base, ni montrés ni envoyés)."""
    tous = elements(reg)
    par_parent = {}
    for e in tous:
        par_parent.setdefault(e["parent"], []).append(e)

    def noeud(e):
        if e["genre"] == "formation":
            return dict(e, detail="", resultat="", enfants=[])
        enfants = sorted(par_parent.get(e["id"], []), key=lambda x: (x["genre"] != "projet", x["ordre"], x["id"]))
        return dict(e, enfants=[noeud(x) for x in enfants])
    racines = par_parent.get(None, [])
    out = []
    for genre, titre in SECTIONS:
        liste = [x for x in racines if x["genre"] == genre]
        if genre in DATES:
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
    """Le parcours est fait quand il a au moins un poste et que chaque poste ou engagement a
    été approfondi (ou passé). Les formations n'y comptent pas."""
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


# --- Export et import de la base (page Réglages) -------------------------------------------
# Un fichier JSON lisible : les éléments en arbre (ce qui est rangé dessous dans `enfants`),
# l'entretien de chaque expérience mot pour mot, et les CV importés. Rien ne passe par l'IA :
# ce qui a été vérifié à l'écriture est repris tel quel.

FORMAT_EXPORT, VERSION_EXPORT = "jobsearch-parcours", 1
ORIGINES = ("cv", "entretien", "saisie")
DETAIL_MAX = 6000                      # un détail grossit des précisions de l'entretien
EXPORT_MAX = 5_000_000                 # octets : bien au-delà d'une vie de travail


class EchangeExporte(BaseModel):
    question: str
    reponse: str | None = None
    pose_le: str = ""
    repondu_le: str = ""
    appui: dict | None = None


class ElementExporte(BaseModel):
    genre: Literal["poste", "formation", "engagement", "projet", "realisation", "competence", "certification", "langue"]
    titre: str
    organisation: str = ""
    lieu: str = ""
    debut: str = ""
    fin: str = ""
    detail: str = ""
    resultat: str = ""
    citation: str = ""
    origine: str = "saisie"
    approfondi: bool = False
    cree_le: str = ""
    entretien: list[EchangeExporte] = Field(default_factory=list)
    enfants: list["ElementExporte"] = Field(default_factory=list)


class DocumentExporte(BaseModel):
    nom: str = "CV"
    texte: str
    importe_le: str = ""


class Export(BaseModel):
    format: str
    version: int = VERSION_EXPORT
    exporte_le: str = ""
    elements: list[ElementExporte] = Field(default_factory=list)
    documents: list[DocumentExporte] = Field(default_factory=list)


def exporter(reg):
    """Toute la base « Ton parcours », prête à écrire en JSON."""
    tous = elements(reg)
    par_parent = {}
    for e in tous:
        par_parent.setdefault(e["parent"], []).append(e)
    entretiens = {}
    for x in _echanges_parcours(reg):
        entretiens.setdefault(x["element"], []).append(
            {k: v for k, v in (("question", x["question"]), ("reponse", x["reponse"]), ("pose_le", x["pose_le"]),
                               ("repondu_le", x["repondu_le"]), ("appui", x["appui"])) if v is not None})

    def noeud(e):
        formation = e["genre"] == "formation"      # une ligne, comme dans arbre() : rien de ce qui est caché
        n = {"genre": e["genre"], "titre": e["titre"]}
        n.update({k: e[k] for k in CHAMPS[1:] + ("citation",)
                  if e[k] and not (formation and k in ("detail", "resultat"))})
        n.update({"origine": e["origine"], "approfondi": bool(e["approfondi"]), "cree_le": e["cree_le"] or ""})
        if formation:
            return n
        if e["id"] in entretiens:
            n["entretien"] = entretiens[e["id"]]
        if e["id"] in par_parent:
            n["enfants"] = [noeud(x) for x in par_parent[e["id"]]]
        return n
    return {"format": FORMAT_EXPORT, "version": VERSION_EXPORT, "exporte_le": maintenant(),
            "elements": [noeud(e) for e in par_parent.get(None, [])],
            "documents": [{"nom": d["nom"], "texte": d["texte"], "importe_le": d["importe_le"] or ""}
                          for d in documents(reg)]}


def exporter_json(reg):
    return json.dumps(exporter(reg), ensure_ascii=False, indent=2)


def _echanges_parcours(reg):
    _schema(reg)
    out = []
    for r in reg.cx.execute("SELECT * FROM parcours_echanges WHERE sujet=? AND element IS NOT NULL ORDER BY id",
                            (SUJET_PARCOURS,)):
        x = dict(r)
        x["appui"] = json.loads(x["appuis"]) if x.get("appuis") else None
        out.append(x)
    return out


def lire_export(contenu):
    """Le contenu d'un fichier exporté, vérifié ; ParcoursInvalide sinon."""
    if len(contenu or b"") > EXPORT_MAX:
        raise ParcoursInvalide("fichier trop gros pour un export du parcours")
    try:
        brut = json.loads(contenu)
    except (ValueError, UnicodeDecodeError) as e:
        raise ParcoursInvalide("ce fichier n'est pas un export du parcours (JSON illisible)") from e
    if not isinstance(brut, dict) or brut.get("format") != FORMAT_EXPORT:
        raise ParcoursInvalide("ce fichier n'est pas un export du parcours")
    if (brut.get("version") or 0) > VERSION_EXPORT:
        raise ParcoursInvalide("export fait par une version plus récente de la plateforme")
    try:
        return Export.model_validate(brut)
    except Exception as e:
        err = (getattr(e, "errors", lambda: [{}])() or [{}])[0]
        ou = ".".join(str(x) for x in err.get("loc", ()))
        raise ParcoursInvalide("export illisible%s" % (" (%s : %s)" % (ou, err.get("msg", "")) if ou else "")) from e


def importer_export(reg, export, remplacer=False):
    """Reprend une base exportée. `remplacer` vide d'abord la base (entretiens compris) ; sinon
    tout s'ajoute à l'existant, un élément déjà là (même genre, intitulé, organisation et parent)
    servant d'attache à ce qui se range dessous. Les règles de la base valent toujours : rien sous
    une formation, un projet ou une réalisation toujours sous un cadre (sinon : `ecartes`).
    Tout s'enregistre d'un bloc : sur une erreur, ou si « remplacer » n'aurait rien repris, rien
    n'est touché (la base d'avant reste entière).
    Renvoie {"ajoutes": n, "doublons": n, "ecartes": [titres], "documents": n}."""
    if not isinstance(export, Export):
        export = lire_export(export)
    _schema(reg)
    reg.commit()
    try:
        bilan = _importer(reg, export, remplacer)
        if remplacer and export.elements and not bilan["ajoutes"]:
            raise ParcoursInvalide("aucun élément de ce fichier n'a pu être repris ; ta base est restée telle quelle")
    except BaseException:
        reg.cx.rollback()
        raise
    reg.commit()
    return bilan


def _importer(reg, export, remplacer):
    if remplacer:
        vider(reg, valider=False)
    bilan = {"ajoutes": 0, "doublons": 0, "ecartes": [], "documents": 0}
    existants = {(_plat(e["genre"]), _plat(e["titre"]), _plat(e["organisation"]), e["parent"]): e["id"]
                 for e in elements(reg)}

    def ecarter(n):
        bilan["ecartes"].append(n.titre)
        for x in n.enfants:
            ecarter(x)

    def poser(n, parent):
        cle = (_plat(n.genre), _plat(n.titre), _plat(n.organisation), parent)
        if cle in existants:
            ident = existants[cle]
            bilan["doublons"] += 1
        else:
            try:
                ident = ajouter(reg, n.genre, n.titre, parent=parent, citation=n.citation, valider=False,
                                origine=n.origine if n.origine in ORIGINES else "saisie",
                                **{k: getattr(n, k) for k in CHAMPS[1:]})
            except ParcoursInvalide:
                return ecarter(n)
            detail = "" if n.genre == "formation" else _propre(n.detail, DETAIL_MAX)
            reg.cx.execute("UPDATE parcours SET detail=?, approfondi=?, cree_le=COALESCE(NULLIF(?, ''), cree_le) "
                           "WHERE id=?", (detail, int(n.approfondi and n.genre in CADRES), n.cree_le, ident))
            for x in n.entretien:
                reg.cx.execute("INSERT INTO parcours_echanges (sujet, element, question, reponse, pose_le, repondu_le, "
                               "appuis) VALUES (?,?,?,?,?,?,?)",
                               (SUJET_PARCOURS, ident, _propre(x.question, 800), x.reponse, x.pose_le or maintenant(),
                                x.repondu_le or None, json.dumps(x.appui, ensure_ascii=False) if x.appui else None))
            existants[cle] = ident
            bilan["ajoutes"] += 1
        for x in n.enfants:
            poser(x, ident)
    for n in export.elements:
        poser(n, None)
    deja = {_plat(d["texte"]) for d in documents(reg)}
    for d in export.documents:
        if d.texte.strip() and _plat(d.texte) not in deja:
            reg.cx.execute("INSERT INTO parcours_documents (nom, texte, importe_le) VALUES (?,?,?)",
                           (_propre(d.nom, 200) or "CV", d.texte, d.importe_le or maintenant()))
            deja.add(_plat(d.texte))
            bilan["documents"] += 1
    return bilan


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
    if n["genre"] == "formation":
        return ["- [E-%d] %s" % (n["id"], _ligne_cadre(n))]
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
    parent: str = Field("", description="ref du poste, de l'engagement ou du projet sous lequel il se range ; vide pour un poste, une formation…")
    genre: Literal["poste", "formation", "engagement", "projet", "realisation", "competence", "certification", "langue"]
    titre: str = Field(description="intitulé du poste ou du diplôme, nom du projet ; pour une réalisation, la puce du CV mot pour mot ; pour une langue, son nom")
    organisation: str = Field("", description="employeur, école ou organisme, tel qu'écrit")
    lieu: str = ""
    debut: str = Field("", description="MM.AAAA ou AAAA")
    fin: str = Field("", description="MM.AAAA ou AAAA ; vide si en cours")
    detail: str = Field("", description="ce que le CV dit de plus, mot pour mot ; niveau pour une langue ; vide pour une formation")
    citation: str = Field(description="le passage du CV dont l'élément vient, copié mot pour mot")


class ImportCV(BaseModel):
    elements: list[ElementCV]
    niveau: Literal["", "Junior", "Confirmé", "Senior", "Cadre", "Direction"] = Field(
        "", description="le niveau de poste que le parcours permet de viser ; vide si le CV ne permet pas d'en juger")


CONSIGNE_IMPORT = """Tu ranges le CV de %(prenom)s, fourni dans <cv>, en éléments d'une base d'expérience. Tu n'écris rien : tu ranges.
- Tout le CV, sans rien omettre ni résumer : chaque poste, formation, engagement (bénévolat, association, mandat), certification, langue et compétence citée.
- Chaque puce ou phrase décrivant un poste ou un engagement devient une réalisation (genre « realisation ») rangée sous lui, son titre étant la puce mot pour mot. Un projet nommé devient un « projet », ses puces des réalisations rangées sous lui.
- Une formation est une seule ligne : intitulé du diplôme, école, lieu, dates. Rien ne se range sous elle et son « detail » reste vide : ni cours, ni travail de diplôme, ni mention, ni projet d'études.
- « citation » : le passage exact du CV dont vient l'élément, copié sans retouche (pour un poste, sa ligne d'intitulé ; pour une compétence, le mot tel qu'il figure).
- Dates au format MM.AAAA (ou AAAA si le mois manque), converties depuis le CV ; « fin » vide pour un poste en cours.
- Rien d'inventé ni de déduit : aucun chiffre, nom ou date qui ne soit dans le CV. L'en-tête de coordonnées n'est pas un élément.
- Les balises comme {PRENOM} remplacent des coordonnées : garde-les telles quelles.
- « niveau » est la seule chose que tu juges : le niveau de poste que ce parcours permet de viser (années d'expérience, responsabilités, encadrement), parmi Junior, Confirmé, Senior, Cadre et Direction."""


def _prenom():
    from . import profil
    return profil.prenom()


def importer_cv(reg, texte, nom="CV", c=None):
    """Range le CV dans la base. Renvoie {"ajoutes": n, "ecartes": [titres], "doublons": n, "profil": {genre: n}}.

    Les formations, les certifications et les langues du CV remplissent aussi leur onglet du profil (Réglages), où elles font
    foi pour les dossiers : seules celles qui n'y sont pas encore s'ajoutent. Le niveau que le CV permet de
    viser devient le niveau visé de la recherche s'il n'y en a pas encore ("niveau" du bilan)."""
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
    ids, formations, pour_profil = {}, set(), []
    for el in r.objet.elements:
        if el.parent in formations:                     # une formation reste une ligne : rien dessous
            continue
        parent = ids.get(el.parent) if el.parent else None
        if el.parent and parent is None:
            bilan["ecartes"].append(el.titre)          # son parent a été écarté
            continue
        if el.genre in ("projet", "realisation") and parent is None:
            bilan["ecartes"].append(el.titre)
            continue
        detail = "" if el.genre == "formation" else el.detail
        if not ancre(el.citation, texte) or chiffres_absents(" ".join((el.titre, detail, el.organisation)), texte):
            bilan["ecartes"].append(el.titre)
            continue
        if el.genre == "formation":
            formations.add(el.ref)
        if el.genre in ("formation", "certification", "langue"):
            pour_profil.append(el.model_dump())
        cle = (_plat(el.genre), _plat(el.titre), _plat(el.organisation), parent)
        if cle in existants:                            # CV importé deux fois : on rattache, on ne double pas
            ids[el.ref] = existants[cle]
            bilan["doublons"] += 1
            continue
        ident = ajouter(reg, el.genre, el.titre, parent=parent, origine="cv", citation=el.citation,
                        organisation=el.organisation, lieu=el.lieu, debut=el.debut, fin=el.fin, detail=detail)
        ids[el.ref] = existants[cle] = ident
        bilan["ajoutes"] += 1
    reg.cx.execute("INSERT INTO parcours_documents (nom, texte, importe_le) VALUES (?,?,?)",
                   (nom or "CV", texte, maintenant()))
    reg.commit()
    from . import profil
    bilan["profil"] = profil.completer_depuis_cv(pour_profil)
    bilan["niveau"] = profil.niveau_depuis_cv(r.objet.niveau)
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
        raise ParcoursInvalide("seuls un poste ou un engagement se reprennent en entretien")
    reg.cx.execute("UPDATE parcours SET approfondi=0 WHERE id=?", (ident,))
    reg.commit()


def date_fr(iso):
    try:
        return dt.datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""
