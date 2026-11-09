"""Note d'adéquation d'une annonce, par le moteur IA (Claude Code, abonnement de l'utilisateur).

Pour chaque annonce à trancher : une note globale sur 100 (profil × annonce ×
critères), l'adéquation du profil aux exigences en %, l'état de chaque
critère non négociable, et les signaux d'une entreprise malsaine ou de
conditions très contraignantes, cités mot pour mot dans l'annonce.

Le profil vient de la base Parcours de l'application (`parcours.py` : postes, projets,
réalisations, CV d'origine). Il passe en contexte de la tâche, identique pour toutes
les annonces d'un passage : Claude Code le garde en cache. Les exigences et les extraits restent dans la langue
de l'annonce : ce sont les mots-clés du CV et de la lettre.

La note est une aide à la décision : elle ne change jamais un statut.
"""

import hashlib
import json

from pydantic import BaseModel, Field, create_model

from . import moteur
from . import profil as profil_mod
from .db import maintenant

MODELE = moteur.MODELE
VERSION = "2"            # à incrémenter quand la consigne change : les notes seront recalculées

SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluations (
  empreinte TEXT PRIMARY KEY, cree_le TEXT NOT NULL, modele TEXT, version TEXT,
  empreinte_entree TEXT, note_globale INTEGER, adequation_profil INTEGER,
  nb_alertes INTEGER, resultat TEXT NOT NULL, cout_jetons TEXT
);
"""


# --- Schéma de la réponse -------------------------------------------------------


class Critere(BaseModel):
    critere: str = Field(description="un des critères non négociables du profil")
    etat: str = Field(description="ok, ko ou incertain")
    note: str = Field(description="une phrase en français, fondée sur l'annonce")


class Exigence(BaseModel):
    exigence: str = Field(description="exigence majeure, formulée dans la langue de l'annonce")
    couverture: str = Field(description="oui, partiel ou non")
    appui: str = Field(description="preuve du profil (P-xx, poste, certification) ou écart, en français ; vide si rien")


class Alerte(BaseModel):
    gravite: str = Field(description="forte ou moyenne")
    signal: str = Field(description="ce que le passage laisse craindre, en français")
    extrait: str = Field(description="citation exacte de l'annonce, 25 mots au plus, dans sa langue")


class Salaire(BaseModel):
    annonce_l_indique: bool = Field(description="vrai si l'annonce donne elle-même un salaire ou une fourchette")
    minimum: int = Field(description="borne basse annuelle brute en CHF, 100 %, base sans bonus")
    maximum: int = Field(description="borne haute annuelle brute en CHF, 100 %, base sans bonus")
    extrait: str = Field(description="si l'annonce l'indique : la phrase exacte, dans sa langue ; sinon vide")
    raisonnement: str = Field(description="une ou deux phrases en français : niveau du poste, secteur, taille, région, références de marché utilisées")
    confiance: str = Field(description="haute (chiffre de l'annonce ou grille publique), moyenne ou basse")


class Evaluation(BaseModel):
    note_globale: int = Field(description="0 à 100 : profil × annonce × critères")
    adequation_profil: int = Field(description="0 à 100 : part des exigences majeures réellement couvertes par le profil")
    classe_suggeree: str = Field(description="A, B ou C")
    angle: str = Field(description="l'angle de candidature le plus fort pour cette annonce")
    resume: str = Field(description="deux phrases en français : pourquoi cette note")
    criteres: list[Critere]
    exigences: list[Exigence]
    alertes: list[Alerte]
    salaire: Salaire


def modele():
    """Le schéma de la réponse, avec les critères et les angles du profil dans les descriptions."""
    criteres = profil_mod.criteres_evaluation()
    angles = profil_mod.lire(profil_mod.charger(), "evaluation.angles") or []
    if not criteres and not angles:
        return Evaluation
    champs = {}
    if criteres:
        crit = create_model("Critere", __base__=Critere, critere=(str, Field(
            description=", ".join(nom for nom, _ in criteres))))
        champs["criteres"] = (list[crit], ...)
    if angles:
        champs["angle"] = (str, Field(description=profil_mod.liste_ou(angles)))
    return create_model("Evaluation", __base__=Evaluation, **champs)


def consigne():
    """La consigne de notation, écrite pour la personne du profil (page Profil)."""
    p = profil_mod.charger()
    prenom = profil_mod.prenom()
    niveau, region = profil_mod.lire(p, "recherche.niveau"), profil_mod.lire(p, "recherche.region")
    criteres = profil_mod.criteres_evaluation()
    interet = profil_mod.lire(p, "evaluation.interet")
    return CONSIGNE % {
        "nom": profil_mod.nom_complet(), "prenom": prenom,
        "poste": "un poste" + (" " + niveau if niveau else "") + (" en " + region if region else ""),
        "interet": " (%s)" % interet if interet else "",
        "criteres": "chacun des %s critères (%s)" % (
            profil_mod.nombre_fr(len(criteres)), ", ".join(" ".join(x for x in c if x) for c in criteres))
            if criteres else "chacun des critères non négociables du profil",
        "marche": " en " + region if region else "",
    }


CONSIGNE = """Tu évalues des offres d'emploi pour %(nom)s, qui cherche %(poste)s. Tu reçois son profil (critères, compétences, preuves, CV maître, parcours) puis une annonce. Tu ne décides rien : tu aides %(prenom)s à trancher vite.

Règles :
- Rien d'inventé. Une compétence ou un fait n'est couvert que s'il figure dans le profil ; cite la preuve (P-xx, poste, certification). Sinon c'est un écart, dis-le.
- Les exigences et les extraits restent dans la langue de l'annonce, mot pour mot : ils serviront de mots-clés au CV et à la lettre. Tes explications sont en français.
- note_globale (0-100) combine trois choses : l'adéquation du profil, le respect des critères non négociables de %(prenom)s, et l'intérêt du poste au regard de ses critères%(interet)s. Un critère non négociable clairement violé plafonne la note à 40. 80 et plus : à ne pas rater. 60-79 : à regarder. Moins de 60 : probablement non.
- adequation_profil (0-100) : seulement la couverture des exigences majeures de l'annonce (3 à 6 exigences) par le profil, comme un recruteur la mesurerait.
- criteres : évalue %(criteres)s. « incertain » quand l'annonce ne permet pas de conclure.
- alertes : seulement des signaux présents dans le texte, cités mot pour mot, qui font penser à une entreprise malsaine ou à des conditions très contraignantes : disponibilité permanente, heures supplémentaires attendues, astreintes, déplacements très fréquents, « résistance au stress » en exigence, poste fourre-tout (trois métiers en un), objectifs irréalistes, turnover ou poste republié, période d'essai ou clause anormale, rémunération floue ou à la commission, culture « famille » ou « hustle ». Aucune alerte si rien de tel n'apparaît : une liste vide est une bonne réponse. N'invente jamais un signal.
- salaire : fourchette annuelle brute en CHF pour ce poste à 100 %%. Si l'annonce donne un salaire ou une fourchette, reprends-la et cite la phrase exacte (confiance haute) ; une grille publique (canton, ville, administration) annoncée par sa classe se convertit en fourchette, confiance haute. Sinon, estime le marché du poste%(marche)s selon son niveau réel, le secteur, la taille et le type de structure, en t'appuyant sur ce que tu sais des références publiques (enquêtes salariales suisses, grilles publiques) ; dis lesquelles dans le raisonnement et mets la confiance à moyenne ou basse. C'est une estimation du marché pour ce poste, pas une prétention à annoncer : elle ne dépend pas de ce que %(prenom)s demanderait.
- classe_suggeree : A (correspond, à proposer), B (à voir, un doute réel), C (ne correspond pas). C'est une suggestion, %(prenom)s tranche."""


# --- Entrées ----------------------------------------------------------------------


def profil():
    """Le profil envoyé à Claude : la base Parcours de l'application."""
    from . import parcours
    base = parcours.document()
    if not base:
        raise FileNotFoundError("profil vide : importer son CV et faire l'entretien « Ton parcours »")
    return base


def _decisions_recentes(reg, n=30):
    """Les dernières décisions de la personne et leurs raisons : de quoi caler la note."""
    lignes = []
    for r in reg.cx.execute(
            "SELECT entreprise, titre, statut, raison_decision, raison_note FROM annonces "
            "WHERE decision_le IS NOT NULL ORDER BY decision_le DESC LIMIT ?", (n,)):
        raisons = ", ".join(json.loads(r["raison_decision"] or "[]"))
        lignes.append("- %s — %s : %s%s%s" % (r["entreprise"], r["titre"],
                      {"retenue": "Go", "C": "Pas go", "B": "Attendre"}.get(r["statut"], r["statut"]),
                      " (%s)" % raisons if raisons else "", " — « %s »" % r["raison_note"] if r["raison_note"] else ""))
    return "\n".join(lignes)


def _annonce(f):
    return ("<annonce>\nEntreprise : %s\nIntitulé : %s\nLieu : %s\nTaux : %s\nContrat : %s\n"
            "Origine du texte : %s\n\n%s\n</annonce>") % (
        f.get("entreprise"), f.get("titre"), f.get("lieu") or "?",
        "%s–%s %%" % (f.get("taux_min"), f.get("taux_max")) if f.get("taux_max") else "?",
        {True: "durée indéterminée", False: "durée déterminée"}.get(f.get("permanent"), "?"),
        "texte original de l'annonce" if f.get("texte_origine") == "plateforme"
        else "résumé, pas le texte original : rester prudent",
        f.get("texte") or "(texte absent)")


def empreinte_entree(f, texte_profil):
    h = hashlib.sha1()
    for part in (VERSION, MODELE, texte_profil, f.get("titre") or "", f.get("texte") or ""):
        h.update(part.encode("utf-8"))
    return h.hexdigest()[:16]


# --- Appel ------------------------------------------------------------------------


EvaluationImpossible = moteur.MoteurImpossible


def tache(texte_profil, decisions=""):
    """La tâche « evaluer » : consigne et profil fixes, réponse au schéma Evaluation, aucun outil."""
    contexte = texte_profil + ("\n\n<decisions_recentes>\n%s\n</decisions_recentes>"
                               % decisions if decisions else "")
    return moteur.Tache("évaluation", consigne(), contexte=contexte, schema=modele(), modele=MODELE,
                        effort="high", delai_s=600)


def evaluer(c, f, texte_profil, decisions=""):
    """Une évaluation par le moteur `c` ; renvoie (Evaluation, usage)."""
    r = c.executer(tache(texte_profil, decisions), _annonce(f))
    ev = r.objet
    ev.note_globale = max(0, min(100, ev.note_globale))
    ev.adequation_profil = max(0, min(100, ev.adequation_profil))
    return ev, dict(r.usage or {}, cout_usd=r.cout_usd)


# --- Registre ---------------------------------------------------------------------


def _schema(reg):
    reg.cx.executescript(SCHEMA)


def lire(reg, emp):
    _schema(reg)
    r = reg.cx.execute("SELECT * FROM evaluations WHERE empreinte=?", (emp,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["resultat"] = json.loads(d["resultat"])
    return d


def toutes(reg):
    _schema(reg)
    return {r["empreinte"]: dict(r, resultat=json.loads(r["resultat"]))
            for r in reg.cx.execute("SELECT * FROM evaluations")}


def a_evaluer(reg, statuts=("nouvelle", "A", "B", "retenue"), texte_profil=None):
    _schema(reg)
    texte_profil = texte_profil if texte_profil is not None else profil()
    faites = {r[0]: r[1] for r in reg.cx.execute("SELECT empreinte, empreinte_entree FROM evaluations")}
    return [f for f in reg.liste(list(statuts))
            if f.get("texte") and faites.get(f["empreinte"]) != empreinte_entree(f, texte_profil)]


def enregistrer(reg, f, ev, usage, texte_profil):
    _schema(reg)
    jetons = {k: (usage or {}).get(k) for k in
              ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "cout_usd")}
    reg.cx.execute(
        "INSERT INTO evaluations (empreinte, cree_le, modele, version, empreinte_entree, note_globale, "
        "adequation_profil, nb_alertes, resultat, cout_jetons) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(empreinte) DO UPDATE SET cree_le=excluded.cree_le, modele=excluded.modele, "
        "version=excluded.version, empreinte_entree=excluded.empreinte_entree, note_globale=excluded.note_globale, "
        "adequation_profil=excluded.adequation_profil, nb_alertes=excluded.nb_alertes, "
        "resultat=excluded.resultat, cout_jetons=excluded.cout_jetons",
        (f["empreinte"], maintenant(), MODELE, VERSION, empreinte_entree(f, texte_profil), ev.note_globale,
         ev.adequation_profil, len(ev.alertes), ev.model_dump_json(), json.dumps(jetons)))
    reg.noter(f["empreinte"], "évaluation · %d/100 · profil %d %% · %d alerte(s)"
              % (ev.note_globale, ev.adequation_profil, len(ev.alertes)))
    reg.commit()


def evaluer_lot(reg, fiches, c=None, texte_profil=None, budget=None):
    """Évalue une liste d'annonces ; renvoie le bilan. `budget()` peut lever pour arrêter."""
    texte_profil = texte_profil if texte_profil is not None else profil()
    c = c or moteur.ouvrir()
    decisions = _decisions_recentes(reg)
    bilan = {"evaluees": 0, "echecs": 0, "alertes": 0, "erreurs": []}
    for f in fiches:
        if budget:
            budget()
        try:
            ev, usage = evaluer(c, f, texte_profil, decisions)
        except EvaluationImpossible as e:
            bilan["echecs"] += 1
            bilan["erreurs"].append("%s : %s" % (f["empreinte"][:8], e))
            if e.bloquant:              # Claude Code déconnecté, limite d'usage : inutile de continuer
                break
            continue
        enregistrer(reg, f, ev, usage, texte_profil)
        bilan["evaluees"] += 1
        bilan["alertes"] += len(ev.alertes)
    return bilan
