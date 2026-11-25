"""Note d'adéquation d'une annonce, par le moteur IA (Claude Code, abonnement de l'utilisateur).

Pour chaque annonce à trancher : une note globale sur 100 (profil × annonce ×
critères), l'adéquation du profil aux exigences en %, l'état de chaque
critère non négociable, l'analyse par catégorie (compétences, expérience, formation, langues :
`adequation.py`), et les signaux d'une entreprise malsaine ou de
conditions très contraignantes, cités mot pour mot dans l'annonce.

Le profil vient de la base Parcours de l'application (`parcours.py` : postes, projets,
réalisations, CV d'origine). Il passe en contexte de la tâche, identique pour toutes
les annonces d'un passage : Claude Code le garde en cache. Les exigences et les extraits restent dans la langue
de l'annonce : ce sont les mots-clés du CV et de la lettre.

L'avis — oui, peut-être ou non — se calcule ici, pas par l'IA : sur la moyenne de la note globale et
de l'adéquation du profil (`avis`). Autour de 50 %, c'est oui ; un peu en dessous, ou quand les deux
chiffres divergent (le profil couvre bien les exigences mais la note est basse, ou l'inverse), c'est
peut-être : l'IA donne alors les raisons pour, contre, et ce qui doit faire trancher.

La note est une aide à la décision : elle ne change pas un statut. Une seule exception, la
pré-sélection : une annonce « nouvelle » dont le métier n'a rien à voir avec les postes visés
(`perimetre` = hors, jugé par l'IA sur le texte), ou dont l'avis est non, passe en C (non), passe 2, en
« limite » : elle n'est plus proposée, reste consultable dans Offres (filtre « non ») et se remet à
trancher d'un clic depuis sa fiche. Le doute profite à l'annonce : il donne peut-être, pas non.
"""

import hashlib
import json

from pydantic import BaseModel, Field, create_model

from . import adequation, moteur
from . import profil as profil_mod
from .db import maintenant

MODELE = moteur.MODELE
VERSION = "5"            # à incrémenter quand la consigne change : les notes seront recalculées
PASSE_IA = 2             # la passe du pré-tri tenue par l'IA (0 et 1 : règles de prefilter.py)
PERIMETRES = ("dans", "limite", "hors")

# L'avis sur une annonce, tiré de la moyenne de la note globale et de l'adéquation du profil.
SEUIL_OUI = 50           # moyenne à partir de laquelle c'est oui
SEUIL_PEUT_ETRE = 40     # en dessous : non, sauf si l'un des deux chiffres est haut
CHIFFRE_HAUT = 60        # un chiffre haut, même seul, laisse l'annonce en peut-être
CHIFFRE_BAS = 40         # un chiffre bas empêche le oui : il faut trancher avec le détail
MAX_RAISONS = 3          # raisons pour et contre gardées
AVIS = {"oui": ("A", "Oui"), "peut-etre": ("B", "Peut-être"), "non": ("C", "Non")}   # classe de .puce, libellé

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
    categorie: adequation.Cle = Field(description="la catégorie dont relève l'exigence")
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
    perimetre: str = Field(description="dans, limite ou hors : le métier de l'annonce est-il un des postes visés ? "
                                       "hors = une autre profession, sans rapport ; limite = poste voisin ; dans = oui")
    perimetre_motif: str = Field(description="une phrase en français : quel métier l'annonce décrit, et pourquoi il est "
                                             "dans, à la limite ou hors des postes visés")
    note_globale: int = Field(description="0 à 100 : profil × annonce × critères")
    adequation_profil: int = Field(description="0 à 100 : part des exigences majeures réellement couvertes par le profil")
    pour: list[str] = Field(description="au plus 3 raisons concrètes de postuler, en français")
    contre: list[str] = Field(description="au plus 3 raisons concrètes de ne pas postuler, en français ; vide si aucune")
    arbitrage: str = Field(description="une phrase en français : ce qui doit faire trancher, surtout quand la note "
                                       "globale et l'adéquation du profil divergent")
    angle: str = Field(description="l'angle de candidature le plus fort pour cette annonce")
    resume: str = Field(description="deux phrases en français : pourquoi cette note")
    criteres: list[Critere]
    exigences: list[Exigence]
    categories: list[adequation.Categorie]
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
    niveau, region = str(profil_mod.lire(p, "recherche.niveau") or "").lower(), profil_mod.region(p)
    criteres = profil_mod.criteres_evaluation()
    interet = profil_mod.interet_evaluation(p)
    postes = profil_mod.intitules(profil_mod.postes(p))
    return CONSIGNE % {
        "nom": profil_mod.nom_complet(), "prenom": prenom,
        "poste": "un poste" + (" " + niveau if niveau else "") + (" en " + region if region else ""),
        "postes": "Postes visés : %s." % ", ".join(postes) if postes else "Postes visés : ceux que le profil décrit.",
        "interet": " (%s)" % interet if interet else "",
        "criteres": "chacun des %s critères (%s)" % (
            profil_mod.nombre_fr(len(criteres)), ", ".join(" ".join(x for x in c if x) for c in criteres))
            if criteres else "chacun des critères non négociables du profil",
        "marche": " en " + region if region else "",
        "categories": adequation.REGLE,
        "oui": SEUIL_OUI,
    }


CONSIGNE = """Tu évalues des offres d'emploi pour %(nom)s, qui cherche %(poste)s. %(postes)s Tu reçois son profil (critères, compétences, preuves, CV maître, parcours) puis une annonce. Tu ne décides rien : tu aides %(prenom)s à trancher vite.

Règles :
- perimetre : d'abord, le métier que l'annonce décrit est-il un des postes visés ? « hors » seulement si c'est une autre profession, sans rapport avec ces postes ni avec le parcours (soins, cuisine, bâtiment, production industrielle, vente… quand aucun poste visé n'en relève), même si l'intitulé emprunte un mot comme « chef », « responsable » ou « manager ». « limite » pour un poste voisin ou une variante plausible. « dans » sinon. Le doute profite à l'annonce : jamais « hors » sur un doute. Dis en une phrase (perimetre_motif) quel métier c'est.
- Rien d'inventé. Une compétence ou un fait n'est couvert que s'il figure dans le profil ; cite la preuve (P-xx, poste, certification). Sinon c'est un écart, dis-le.
- Les exigences et les extraits restent dans la langue de l'annonce, mot pour mot : ils serviront de mots-clés au CV et à la lettre. Tes explications sont en français.
- note_globale (0-100) combine trois choses : l'adéquation du profil, le respect des critères non négociables de %(prenom)s, et l'intérêt du poste au regard de ses critères%(interet)s. Un critère non négociable clairement violé plafonne la note à 40. 80 et plus : à ne pas rater. 50-79 : à regarder. Moins de 40 : probablement non.
- adequation_profil (0-100) : seulement la couverture des exigences majeures de l'annonce (3 à 6 exigences) par le profil, comme un recruteur la mesurerait.
%(categories)s
- criteres : évalue %(criteres)s. « incertain » quand l'annonce ne permet pas de conclure.
- alertes : seulement des signaux présents dans le texte, cités mot pour mot, qui font penser à une entreprise malsaine ou à des conditions très contraignantes : disponibilité permanente, heures supplémentaires attendues, astreintes, déplacements très fréquents, « résistance au stress » en exigence, poste fourre-tout (trois métiers en un), objectifs irréalistes, turnover ou poste republié, période d'essai ou clause anormale, rémunération floue ou à la commission, culture « famille » ou « hustle ». Aucune alerte si rien de tel n'apparaît : une liste vide est une bonne réponse. N'invente jamais un signal.
- salaire : fourchette annuelle brute en CHF pour ce poste à 100 %%. Si l'annonce donne un salaire ou une fourchette, reprends-la et cite la phrase exacte (confiance haute) ; une grille publique (canton, ville, administration) annoncée par sa classe se convertit en fourchette, confiance haute. Sinon, estime le marché du poste%(marche)s selon son niveau réel, le secteur, la taille et le type de structure, en t'appuyant sur ce que tu sais des références publiques (enquêtes salariales suisses, grilles publiques) ; dis lesquelles dans le raisonnement et mets la confiance à moyenne ou basse. C'est une estimation du marché pour ce poste, pas une prétention à annoncer : elle ne dépend pas de ce que %(prenom)s demanderait.
- L'avis se tire de la moyenne de note_globale et d'adequation_profil : %(oui)d et plus, oui ; un peu en dessous, ou quand les deux chiffres divergent, peut-être ; nettement en dessous, non. Ne force pas les chiffres pour obtenir un avis : mesure, l'avis suit.
- pour, contre (3 au plus chacun) : les raisons concrètes de postuler et de ne pas postuler (critères, intérêt du poste, exigences couvertes ou non, conditions). arbitrage : une phrase qui dit ce qui doit faire trancher %(prenom)s. Quand le profil couvre bien les exigences mais que la note globale est basse (un critère violé, un poste peu intéressant), ou l'inverse, dis précisément d'où vient l'écart. C'est une aide, %(prenom)s tranche."""


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
                      {"retenue": "Oui", "C": "Non", "B": "Peut-être"}.get(r["statut"], r["statut"]),
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


def modele_actif():
    """Le modèle pris en compte pour l'empreinte et le registre : Claude garde la constante MODELE,
    un autre fournisseur porte son identifiant et le modèle choisi dans les réglages."""
    f = moteur.fournisseur()
    if f.id == "claude":
        return MODELE
    return "%s:%s" % (f.id, f.modele(moteur.Tache("évaluation", "")) or "defaut")


def empreinte_entree(f, texte_profil):
    h = hashlib.sha1()
    for part in (VERSION, modele_actif(), texte_profil, f.get("titre") or "", f.get("texte") or ""):
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
    ev.categories = adequation.normaliser(ev.categories)
    propres = lambda points: [p.strip() for p in points or [] if p and p.strip()][:MAX_RAISONS]
    ev.pour, ev.contre, ev.arbitrage = propres(ev.pour), propres(ev.contre), (ev.arbitrage or "").strip()
    return ev, dict(r.usage or {}, cout_usd=r.cout_usd, modele=r.modele)


# --- Registre ---------------------------------------------------------------------


def _schema(reg):
    reg.cx.executescript(SCHEMA)


def avis(note_globale, adequation_profil, criteres=()):
    """Oui, peut-être ou non, tiré des deux chiffres de l'évaluation : {cle, classe, libelle, moyenne,
    divergence}. Oui : moyenne d'au moins SEUIL_OUI, aucun chiffre bas, aucun critère violé. Peut-être :
    moyenne d'au moins SEUIL_PEUT_ETRE, ou un chiffre haut (le profil couvre, la note est basse, ou
    l'inverse) : c'est le détail qui tranche. Non : le reste."""
    g, p = int(note_globale or 0), int(adequation_profil or 0)
    moyenne = round((g + p) / 2)
    viole = any((c.get("etat") if isinstance(c, dict) else getattr(c, "etat", "")) == "ko" for c in criteres or ())
    if moyenne >= SEUIL_OUI and min(g, p) >= CHIFFRE_BAS and not viole:
        cle = "oui"
    elif moyenne >= SEUIL_PEUT_ETRE or max(g, p) >= CHIFFRE_HAUT:
        cle = "peut-etre"
    else:
        cle = "non"
    classe, libelle = AVIS[cle]
    return {"cle": cle, "classe": classe, "libelle": libelle, "moyenne": moyenne,
            "divergence": abs(g - p) >= 20 or (viole and moyenne >= SEUIL_OUI)}


def _lue(r):
    d = dict(r)
    d["resultat"] = json.loads(d["resultat"])
    d["avis"] = avis(d["note_globale"], d["adequation_profil"], d["resultat"].get("criteres"))
    return d


def lire(reg, emp):
    _schema(reg)
    r = reg.cx.execute("SELECT * FROM evaluations WHERE empreinte=?", (emp,)).fetchone()
    return _lue(r) if r else None


def toutes(reg):
    _schema(reg)
    return {r["empreinte"]: _lue(r) for r in reg.cx.execute("SELECT * FROM evaluations")}


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
        (f["empreinte"], maintenant(), (usage or {}).get("modele") or modele_actif(), VERSION,
         empreinte_entree(f, texte_profil), ev.note_globale,
         ev.adequation_profil, len(ev.alertes), ev.model_dump_json(), json.dumps(jetons)))
    a = avis(ev.note_globale, ev.adequation_profil, ev.criteres)
    reg.noter(f["empreinte"], "évaluation · %d/100 · profil %d %% · avis %s · %d alerte(s) · périmètre %s"
              % (ev.note_globale, ev.adequation_profil, a["libelle"].lower(), len(ev.alertes), ev.perimetre or "?"))
    ecartee = preselectionner(reg, f, ev)
    reg.commit()
    return ecartee


def motif_hors_perimetre(ev):
    return "Métier hors des postes visés, selon l'IA : %s" % (ev.perimetre_motif or "autre métier").strip()[:160]


def motif_avis_non(ev):
    a = avis(ev.note_globale, ev.adequation_profil, ev.criteres)
    return "Avis non : moyenne %d %% (note %d/100, profil %d %%)%s" % (
        a["moyenne"], ev.note_globale, ev.adequation_profil,
        " · " + ev.arbitrage.strip()[:120] if (ev.arbitrage or "").strip() else "")


def preselectionner(reg, f, ev):
    """La pré-sélection par l'IA : une annonce « nouvelle » hors du périmètre, ou dont l'avis est non,
    passe en C (passe 2, limite) : elle n'est plus proposée et reste dans Offres. Une annonce que la
    personne a déjà classée (A, B, retenue…) n'est jamais touchée. Renvoie vrai si elle a été écartée."""
    if f.get("statut") != "nouvelle":
        return False
    if ev.perimetre == "hors":
        motif = motif_hors_perimetre(ev)
    elif avis(ev.note_globale, ev.adequation_profil, ev.criteres)["cle"] == "non":
        motif = motif_avis_non(ev)
    else:
        return False
    reg.maj(f["empreinte"], statut="C", passe_ecart=PASSE_IA, motif=motif, limite=True)
    reg.noter(f["empreinte"], "pré-sélection IA · C · passe %d · %s" % (PASSE_IA, motif))
    return True


def evaluer_lot(reg, fiches, c=None, texte_profil=None, budget=None):
    """Évalue une liste d'annonces ; renvoie le bilan. `budget()` peut lever pour arrêter."""
    texte_profil = texte_profil if texte_profil is not None else profil()
    c = c or moteur.ouvrir()
    decisions = _decisions_recentes(reg)
    bilan = {"evaluees": 0, "echecs": 0, "alertes": 0, "ecartees": 0, "erreurs": []}
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
        if enregistrer(reg, f, ev, usage, texte_profil):
            bilan["ecartees"] += 1
        bilan["evaluees"] += 1
        bilan["alertes"] += len(ev.alertes)
    return bilan
