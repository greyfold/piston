"""Ce qui compte pour toi : un questionnaire fixe, sans IA, une question à la fois.

Chaque question se répond en cliquant (choix prédéfinis) ou dans un champ court. Les réponses
remplissent, sans appel à l'IA, les critères de notation, l'intérêt, les règles du pré-tri,
les intitulés à écarter et les objectifs de postulations ; la personne relit le tout avant
d'enregistrer (page « Ce qui compte pour toi », volet).

Un seul appel à l'IA, lancé en arrière-plan à l'ouverture du questionnaire (`preparer`) : des
exemples adaptés au poste recherché (activités à cliquer, métiers voisins, exemples des champs
libres), et les angles de candidature et le différenciateur tirés du parcours. Sans IA, ou si
l'appel échoue, les questions gardent des exemples génériques.

Les réponses vivent dans la base (table `ce_qui_compte`), jusqu'à ce qu'on recommence. Aucun
montant de salaire n'est demandé ni gardé (`parcours.sans_montants` sur tout texte libre).
"""

import json
import re
import threading

from pydantic import BaseModel, Field

from . import moteur
from .db import maintenant

SCHEMA = """CREATE TABLE IF NOT EXISTS ce_qui_compte (
  cle TEXT PRIMARY KEY, valeur TEXT NOT NULL, repondu_le TEXT
)"""
CONTEXTE = "_contexte"             # ce que l'IA a préparé, gardé avec les réponses
ENREGISTRE = "_enregistre"         # les réponses ont été écrites dans le profil : il fait foi désormais

TRI = "Le tri du matin écarte d'office les autres annonces."

# Formes de contrat, langues, organisations : les mêmes choix que les critères du profil (profil.CRITERES). Le tri
# du matin ne distingue pas toutes les formes de contrat (une règle pour les CDD et missions, une pour le
# freelance) : la nuance passe dans le critère, que l'IA vérifie en notant chaque annonce.
from .profil import (CONTRATS, DEPLACEMENTS, ENCADREMENT, LANGUES, ORGANISATIONS, TELETRAVAIL,  # noqa: E402
                     TRAJETS)

# Métiers voisins sans IA : le libellé montré, et les mots d'intitulé que le tri écarte.
VOISINS = {"Vente ou commercial": ["vente", "commercial", "sales"],
           "Support ou helpdesk": ["support", "helpdesk"],
           "Assistanat": ["assistant", "assistante"],
           "Stage ou apprentissage": ["stage", "stagiaire", "apprenti"]}

# Une question : `choix` [(valeur, libellé)] (un seul, ou plusieurs si `multiple`) et/ou `champs`.
# Un champ peut reprendre une valeur du profil (`defaut` : « ville », « semaine », « mois »).
QUESTIONS = [
    {"cle": "trajet", "question": "Combien de temps de trajet acceptes-tu au plus, porte à porte ?",
     "choix": TRAJETS + [("", "Peu importe")],
     "champs": [{"nom": "depuis", "libelle": "Depuis", "exemple": "ta localité", "defaut": "ville"}]},
    {"cle": "teletravail", "question": "Combien de jours de télétravail par semaine te faut-il au moins ?",
     "choix": [("0", "Pas d'exigence")] + TELETRAVAIL},
    {"cle": "taux", "question": "Quel taux d'activité acceptes-tu ?",
     "aide": "En pourcentage, de … à … (par exemple de 90 à 100). Sous 100 %, le tri du matin garde aussi les "
             "annonces à temps partiel ; l'IA vérifie ta fourchette en les notant.",
     "champs": [{"nom": "min", "libelle": "Au moins (%)", "type": "nombre", "exemple": "80"},
                {"nom": "max", "libelle": "Au plus (%)", "type": "nombre", "exemple": "100", "valeur": 100}]},
    {"cle": "contrat", "question": "Quelles formes de contrat acceptes-tu ?", "multiple": True,
     "aide": "Plusieurs choix possibles. Sans CDD ni mission, le tri du matin les écarte d'office.",
     "choix": [(v, CONTRATS[v][0]) for v in CONTRATS]},
    {"cle": "langues", "question": "Dans quelles langues peux-tu travailler ?", "multiple": True,
     "aide": "Plusieurs choix possibles. Sans l'allemand, le tri du matin écarte les annonces en allemand.",
     "choix": LANGUES},
    {"cle": "contenu", "question": "Quelles activités doivent occuper l'essentiel de ton temps ?", "multiple": True,
     "contexte": "activites", "aide": "Plusieurs choix possibles ; ajoute les tiennes dans le champ.",
     "choix": [(x, x) for x in ("concevoir et créer", "piloter des projets", "conseiller des clients ou des métiers",
                                "analyser des données", "former et transmettre", "développer une offre")],
     "champs": [{"nom": "autres", "libelle": "Autres activités", "exemple": "séparées par des virgules"}]},
    {"cle": "encadrement", "question": "Veux-tu encadrer une équipe ?",
     "choix": [ENCADREMENT[0], ("egal", "Peu importe"), ENCADREMENT[1]]},
    {"cle": "organisation", "question": "Dans quels types d'organisation veux-tu travailler ?", "multiple": True,
     "aide": "Plusieurs choix possibles ; aucun : peu importe.",
     "choix": ORGANISATIONS,
     "champs": [{"nom": "eviter", "libelle": "Secteurs exclus", "exemple": "tabac, armement…"}]},
    {"cle": "metiers", "question": "Les métiers voisins : quels intitulés de poste veux-tu qu'on écarte d'office ?",
     "multiple": True, "contexte": "voisins", "vide": "Rien à écarter",
     "aide": "Coche ceux que le tri du matin doit écarter. Aucune case : rien n'est écarté.",
     "choix": [(x, x) for x in VOISINS],
     "champs": [{"nom": "mots", "libelle": "Autres mots d'intitulés, séparés par des virgules", "exemple": "…"}]},
    {"cle": "conditions", "question": "Quelles limites sur le temps de travail et les déplacements ?",
     "aide": "Remplis ce qui compte ; le reste peut rester vide.",
     "champs": [{"nom": "heures", "libelle": "Heures par semaine, au plus", "type": "nombre", "exemple": "42"},
                {"nom": "vacances", "libelle": "Semaines de vacances, au moins", "type": "nombre", "exemple": "5"}],
     "choix": DEPLACEMENTS + [("", "Peu importe")]},
    {"cle": "interet", "question": "Qu'est-ce qui rend un poste intéressant pour toi ?", "vide": "Rien à ajouter",
     "champs": [{"nom": "texte", "libelle": "En quelques mots", "long": True, "contexte": "interet",
                 "exemple": "un produit utile, une équipe qui expérimente, de l'autonomie…"}]},
    {"cle": "eviter", "question": "Y a-t-il quelque chose que tu ne veux plus vivre dans ton prochain poste ?",
     "vide": "Rien à ajouter",
     "champs": [{"nom": "texte", "libelle": "En quelques mots", "long": True, "contexte": "eviter",
                 "exemple": "des projets sans fin, trop de réunions, un management distant…"}]},
    {"cle": "rythme", "question": "Combien de postulations veux-tu viser ?", "vide": "Pas d'objectif",
     "aide": "L'ORP en demande souvent un nombre par mois. Vide : pas d'objectif.",
     "champs": [{"nom": "semaine", "libelle": "Par semaine", "type": "nombre", "defaut": "semaine"},
                {"nom": "mois", "libelle": "Par mois", "type": "nombre", "defaut": "mois"}]},
]
PAR_CLE = {q["cle"]: q for q in QUESTIONS}


def resolue(q, ctx=None, deja=None):
    """La question avec les exemples adaptés au poste, quand l'IA les a préparés. `deja` : la réponse
    enregistrée ; ses choix absents de la liste (donnés avant que l'IA ait préparé ses exemples) y
    restent, cochés, pour qu'un nouvel enregistrement ne les efface pas."""
    ctx = ctx or {}
    q = dict(q)
    if q.get("contexte") and ctx.get(q["contexte"]):
        q["choix"] = [(x["libelle"], x["libelle"]) if isinstance(x, dict) else (x, x) for x in ctx[q["contexte"]]]
        q["adaptee"] = True
    gardes = (deja or {}).get("choix")
    if q.get("multiple") and isinstance(gardes, list):
        connus, d_origine = {v for v, _ in q.get("choix", [])}, dict(PAR_CLE[q["cle"]].get("choix", []))
        q["choix"] = list(q.get("choix", [])) + [(v, d_origine.get(v, v)) for v in gardes if v not in connus]
    q["champs"] = [dict(c, exemple=ctx.get("exemples", {}).get(c["contexte"]) or c["exemple"]) if c.get("contexte")
                   else c for c in q.get("champs", [])]
    return q

# Les règles du pré-tri que les réponses commandent (celles absentes de la configuration sont ignorées).
REGLES_TAUX = ["taux", "contrat-freelance"]       # ce que la fourchette de taux commande
ANCIENS_TAUX = {"100": (100, 100), "80": (80, 100), "partiel": (50, 100)}   # réponses d'avant les champs chiffrés
CDD = ["contrat-cdd", "contrat-api-duree-determinee"]
REGLES_CONTRAT = {"cdi": [], "cdd-2ans": CDD, "cdd-long": CDD, "cdd-moyen": CDD, "cdd-court": CDD, "cdd-cdi": CDD,
                  "remplacement": CDD, "mission": CDD, "freelance": ["contrat-freelance"]}
# Réponses d'avant le choix multiple.
ANCIENS_CONTRATS = {"fixe": ["cdi"], "tout": ["cdi", "cdd-2ans", "cdd-long", "cdd-moyen", "cdd-court", "mission"]}
REGLES_ALLEMAND = ["titre-allemand", "langue-allemand", "allemand-exige"]


class ReponseInvalide(ValueError):
    pass


def _schema(reg):
    reg.cx.execute(SCHEMA)


def reponses(reg):
    """{clé: {"choix": …, "champs": {…}} ou {"passe": True}} ; plus la proposition d'angles (`ANGLES`)."""
    _schema(reg)
    return {r[0]: json.loads(r[1]) for r in reg.cx.execute("SELECT cle, valeur FROM ce_qui_compte")}


def _ecrire(reg, cle, valeur):
    _schema(reg)
    reg.cx.execute("INSERT INTO ce_qui_compte (cle, valeur, repondu_le) VALUES (?,?,?) "
                   "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur, repondu_le=excluded.repondu_le",
                   (cle, json.dumps(valeur, ensure_ascii=False), maintenant()))
    reg.commit()


def _texte(v, n=300):
    from .parcours import sans_montants
    return sans_montants(re.sub(r"\s+", " ", str(v or "")).strip())[:n]


def _court(v, n):
    """Un texte coupé sur un mot, avec « … », s'il dépasse n caractères."""
    t = _texte(v, 1000)
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def repondre(reg, cle, choix=(), champs=None, passer=False):
    """Enregistre la réponse à une question. `choix` : les valeurs cochées ; `champs` : {nom: texte}."""
    q = PAR_CLE.get(cle)
    if q is None:
        raise ReponseInvalide("question inconnue")
    rep = reponses(reg)
    q = resolue(q, rep.get(CONTEXTE), rep.get(cle))
    if passer:
        _ecrire(reg, cle, {"passe": True})
        return
    permis = [v for v, _ in q.get("choix", [])]
    choix = [c for c in dict.fromkeys(choix or []) if c in permis]
    valeur = {}
    if permis:
        if q.get("multiple"):
            valeur["choix"] = choix
        elif choix:
            valeur["choix"] = choix[0]
        elif not q.get("champs"):
            raise ReponseInvalide("choisis une réponse")
    vus = {}
    for c in q.get("champs", []):
        t = _texte((champs or {}).get(c["nom"]), 600 if c.get("long") else 200)
        if c.get("type") == "nombre" and t:
            if not t.isdigit() or int(t) > 500:
                raise ReponseInvalide("%s : un nombre entier" % c["libelle"])
            t = int(t)
        if t not in ("", None):
            vus[c["nom"]] = t
    valeur["champs"] = vus
    if permis and not q.get("multiple") and "choix" not in valeur and not vus:
        raise ReponseInvalide("choisis une réponse")
    if cle == "taux":
        mini, maxi = vus.get("min"), vus.get("max", 100)
        if mini is None:
            raise ReponseInvalide("indique le taux minimum")
        if not (10 <= mini <= maxi <= 100):
            raise ReponseInvalide("un taux entre 10 et 100 %, le minimum avant le maximum")
        vus["max"] = maxi
    if cle == "langues" and not valeur["choix"]:
        raise ReponseInvalide("choisis au moins une langue")
    _ecrire(reg, cle, valeur)


def questions(reg):
    """Toutes les questions, avec les exemples adaptés au poste et la réponse déjà donnée."""
    rep = reponses(reg)
    ctx = rep.get(CONTEXTE)
    return [{"question": resolue(q, ctx, rep.get(q["cle"])), "deja": rep.get(q["cle"], {})} for q in QUESTIONS]


def recommencer(reg):
    _schema(reg)
    reg.cx.execute("DELETE FROM ce_qui_compte")
    reg.commit()


def libelle(q, r):
    """La réponse telle que le fil la montre."""
    if r.get("passe"):
        return "Passé"
    if q["cle"] == "taux" and (f := _fourchette({"taux": r})):
        return "%d %%" % f[0] if f[0] == f[1] else "De %d à %d %%" % f
    noms = dict(q.get("choix", []))
    morceaux = []
    ch = r.get("choix")
    if isinstance(ch, list):                   # rien de coché : « Peu importe », sauf si un champ dit autre chose
        if ch:
            morceaux.append(", ".join(noms.get(c, c) for c in ch))
    elif ch is not None:
        morceaux.append(noms.get(ch, ch))
    for c in q.get("champs", []):
        if c["nom"] in r.get("champs", {}):
            v = r["champs"][c["nom"]]
            morceaux.append(str(v) if c.get("long") or len(q.get("champs", [])) == 1 and not noms
                            else "%s : %s" % (c["libelle"], v))
    return " · ".join(morceaux) or q.get("vide", "Peu importe")


def etat(reg, modifier=None, p=None):
    """Le fil (questions répondues), la question en cours et ce qu'elle a déjà comme réponse. Une fois les
    réponses enregistrées, celles montrées sont tirées du profil (`vues_du_profil`) : ce qu'on y a changé
    depuis, dans les réglages ou le volet, s'y retrouve."""
    rep = reponses(reg)
    if rep.get(ENREGISTRE):
        rep = vues_du_profil(rep, p)
    ctx = rep.get(CONTEXTE)
    qs = [resolue(q, ctx, rep.get(q["cle"])) for q in QUESTIONS]
    faites = [q for q in qs if q["cle"] in rep]
    cle = modifier if modifier in PAR_CLE else next((q["cle"] for q in qs if q["cle"] not in rep), None)
    courante = next((q for q in qs if q["cle"] == cle), None)
    # Le fil dans l'ordre des questions : une réponse modifiée l'est sur place, à sa position ;
    # la question suivante, elle, vient à la fin.
    fil = [{"question": q, "reponse": None if q["cle"] == cle else libelle(q, rep[q["cle"]]),
            "courante": q["cle"] == cle} for q in faites]
    if courante is not None and cle not in rep:
        fil.append({"question": courante, "reponse": None, "courante": True})
    return {"fil": fil, "courante": courante, "deja": rep.get(cle, {}) if cle else {},
            "modification": cle in rep,
            "faites": len(faites), "total": len(qs), "termine": courante is None,
            "commence": bool(faites), "contexte": ctx, "reponses": rep, "enregistre": bool(rep.get(ENREGISTRE))}


def _r(rep, cle):
    r = rep.get(cle)
    return None if not r or r.get("passe") else r


def _fourchette(rep):
    """(minimum, maximum) du taux d'activité accepté ; None si la question est passée ou sans réponse."""
    t = _r(rep, "taux")
    if not t:
        return None
    if isinstance(t.get("choix"), str):
        return ANCIENS_TAUX.get(t["choix"])
    ch = t.get("champs", {})
    return (ch["min"], ch.get("max", 100)) if "min" in ch else None


def _contrats(rep):
    """Les formes de contrat cochées, dans l'ordre de la question ; [] si la question est passée."""
    t = _r(rep, "contrat")
    ch = (t or {}).get("choix") or []
    ch = ANCIENS_CONTRATS.get(ch, []) if isinstance(ch, str) else ch
    return [c for c in CONTRATS if c in ch]


# Les critères (profil.CRITERES) que chaque question remplit ; « autres » n'en a pas, il reste tel quel.
CRITERES_DE = {"trajet": ["trajet", "trajet_depuis"], "teletravail": ["teletravail"], "taux": ["taux_min", "taux_max"],
               "contrat": ["contrats"], "langues": ["langues"], "encadrement": ["encadrement"],
               "organisation": ["organisations", "secteurs_exclus"],
               "conditions": ["heures_max", "vacances_min", "deplacements"], "eviter": ["a_eviter"]}


def regles_des_criteres(criteres, actives, cles=None):
    """Les règles actives du pré-tri une fois appliqué ce que les critères commandent : le taux d'activité, les
    formes de contrat et les langues. `actives` : les règles actives jusqu'ici ; `cles` : les critères à appliquer
    (tous par défaut). Un critère vide laisse ses règles telles quelles."""
    from . import profil
    au_choix = [i for _, rs in profil.regles_au_choix() for i, _, _ in rs]
    cles = {"taux_min", "contrats", "langues"} if cles is None else set(cles)
    off = set(au_choix) - set(actives)
    # Taux et contrat commandent ensemble les règles de contrat : une règle est coupée dès qu'un critère
    # demande de garder ses annonces (le freelance relève des deux).
    controle, garder = set(), set()
    # Sous 100 %, les annonces à temps partiel restent ; sous 80 %, aussi celles qui le disent en toutes
    # lettres (« temps partiel », règle commune avec le freelance).
    if "taux_min" in cles and (mini := criteres.get("taux_min")):
        controle |= set(REGLES_TAUX)
        garder |= ({"taux"} if mini < 100 else set()) | ({"contrat-freelance"} if mini < 80 else set())
    if "contrats" in cles and (contrats := criteres.get("contrats")):
        controle |= {i for ids in REGLES_CONTRAT.values() for i in ids}
        garder |= {i for c in contrats for i in REGLES_CONTRAT.get(c, [])}
    off = (off - controle) | garder
    if "langues" in cles and (langues := criteres.get("langues")):
        off = (off | set(REGLES_ALLEMAND)) if "allemand" in langues else (off - set(REGLES_ALLEMAND))
    return [i for i in au_choix if i not in off]


def critere_vers_pretri(avant, apres, actives):
    """Réglages › Critères : les règles du pré-tri que commandent les critères qui viennent de changer, et
    seulement eux (une règle réglée à la main dans le pré-tri reste, tant que son critère ne bouge pas)."""
    changes = [k for k in ("taux_min", "contrats", "langues") if (avant or {}).get(k) != (apres or {}).get(k)]
    return regles_des_criteres(apres or {}, actives, changes) if changes else list(actives)


def _vide(r):
    """Une réponse tirée du profil qui ne dit rien : rien de coché qui compte, aucun champ."""
    ch = r.get("choix")
    return ch in (None, "", "0", "egal", []) and not r.get("champs")


def reponses_du_profil(p=None):
    """Les réponses que donne le profil tel qu'il est : critères, activités, intérêt, intitulés écartés,
    objectifs. Le même format que `reponses` ; `proposition` en redonne le profil."""
    from . import profil
    p = profil.charger() if p is None else p
    c = profil.criteres_structures(p)
    remplis = lambda **kw: {k: v for k, v in kw.items() if v not in (None, "", 0)}
    o = profil.objectifs_rythme()
    return {
        "trajet": {"choix": c.get("trajet", ""), "champs": remplis(depuis=c.get("trajet_depuis"))},
        "teletravail": {"choix": c.get("teletravail", "0"), "champs": {}},
        "taux": {"champs": remplis(min=c.get("taux_min"), max=c.get("taux_max", 100) if c.get("taux_min") else None)},
        "contrat": {"choix": list(c.get("contrats") or []), "champs": {}},
        "langues": {"choix": list(c.get("langues") or []), "champs": {}},
        "contenu": {"choix": list(profil.lire(p, "evaluation.activites") or []), "champs": {}},
        "encadrement": {"choix": c.get("encadrement", "egal"), "champs": {}},
        "organisation": {"choix": list(c.get("organisations") or []),
                         "champs": remplis(eviter=", ".join(c.get("secteurs_exclus") or []))},
        "metiers": {"choix": [], "champs": remplis(mots=", ".join(profil.lire(p, "exclusions.mots_titre") or []))},
        "conditions": {"choix": c.get("deplacements", ""),
                       "champs": remplis(heures=c.get("heures_max"), vacances=c.get("vacances_min"))},
        "interet": {"champs": remplis(texte=profil.lire(p, "evaluation.interet"))},
        "eviter": {"champs": remplis(texte=c.get("a_eviter"))},
        "rythme": {"champs": remplis(semaine=o["semaine"], mois=o["mois"])},
    }


def vues_du_profil(rep, p=None):
    """Les réponses `rep`, chacune remplacée par ce que le profil en dit aujourd'hui. Une question passée le
    reste tant que le profil n'a rien pour elle ; le contexte préparé par l'IA est gardé."""
    du_profil = reponses_du_profil(p)
    out = dict(rep)
    for cle, r in du_profil.items():
        if cle in rep and not (rep[cle].get("passe") and _vide(r)):
            out[cle] = r
    return out


def proposition(rep, p=None, regles=True):
    """Ce que les réponses donnent, sans IA : critères, intérêt, règles actives du pré-tri,
    intitulés à écarter, objectifs. On part du profil : une question passée ou sans réponse le garde tel
    quel, une question répondue remplace ce qu'elle commande. `regles` faux : le pré-tri reste celui du profil."""
    from . import profil
    from .prefilter import plier
    p = profil.charger() if p is None else p
    noms = lambda cle: dict(PAR_CLE[cle].get("choix", []))
    # Les critères : un champ par élément (profil.CRITERES). Un choix enregistré qui n'existe plus dans la
    # question ne donne rien.
    repondues = [cle for cle in CRITERES_DE if _r(rep, cle)]
    criteres = {k: v for k, v in profil.criteres_structures(p).items()
                if not any(k in CRITERES_DE[cle] for cle in repondues)}
    if t := _r(rep, "trajet"):
        if t.get("choix") in noms("trajet") and t["choix"]:
            criteres["trajet"] = t["choix"]
        if t["champs"].get("depuis"):
            criteres["trajet_depuis"] = t["champs"]["depuis"]
    if (t := _r(rep, "teletravail")) and t.get("choix") in noms("teletravail") and t["choix"] != "0":
        criteres["teletravail"] = t["choix"]
    if f := _fourchette(rep):
        criteres["taux_min"], criteres["taux_max"] = f
    if contrats := _contrats(rep):
        criteres["contrats"] = contrats
    if (t := _r(rep, "langues")) and t.get("choix"):
        criteres["langues"] = list(t["choix"])
    if (t := _r(rep, "encadrement")) and t.get("choix") in ("non", "oui"):
        criteres["encadrement"] = t["choix"]
    if t := _r(rep, "organisation"):
        if t.get("choix"):
            criteres["organisations"] = list(t["choix"])
        if t["champs"].get("eviter"):
            criteres["secteurs_exclus"] = [x.strip() for x in re.split(r"[,;]", t["champs"]["eviter"]) if x.strip()]
    if t := _r(rep, "conditions"):
        for champ, cle in (("heures", "heures_max"), ("vacances", "vacances_min")):
            if t["champs"].get(champ):
                criteres[cle] = int(t["champs"][champ])
        if t.get("choix") in ("non", "occasionnels"):
            criteres["deplacements"] = t["choix"]
    if (t := _r(rep, "eviter")) and t["champs"].get("texte"):
        criteres["a_eviter"] = t["champs"]["texte"]
    criteres = profil.normaliser_criteres(criteres)
    lignes = profil.lignes_criteres(criteres, avec_cles=True)

    # Pré-tri : on part des règles actives du profil ; les critères que les réponses donnent en coupent ou en
    # remettent (seulement ceux des questions répondues).
    actives = profil.effectif("exclusions.regles", p)
    if regles:
        commandes = [k for k, q in (("taux_min", "taux"), ("contrats", "contrat"), ("langues", "langues"))
                     if q in repondues]
        actives = regles_des_criteres(criteres, actives, commandes)

    mots = profil.lire(p, "exclusions.mots_titre") or []
    if (t := _r(rep, "metiers")) is not None:
        par_libelle = dict(VOISINS)
        par_libelle.update({v["libelle"]: v["mots"] for v in (rep.get(CONTEXTE) or {}).get("voisins", [])
                            if isinstance(v, dict)})
        brut = [m for lib in t.get("choix") or [] for m in par_libelle.get(lib, [lib])]
        brut += re.split(r"[,;\n]", t["champs"].get("mots", ""))
        mots = [m for m in dict.fromkeys(plier(x).replace("|", " ").strip() for x in brut) if m][:15]

    o = profil.objectifs_rythme()
    if (t := _r(rep, "rythme")) is not None:
        o = {"semaine": int(t["champs"].get("semaine") or 0), "mois": int(t["champs"].get("mois") or 0)}

    # Ce qui rend un poste intéressant : les activités (choix multiple) et le texte libre, chacun à part.
    # None : question passée ou sans réponse, le profil garde le sien.
    activites = texte = None
    if t := _r(rep, "contenu"):
        activites = list(t.get("choix") or []) + [x.strip() for x in re.split(r"[,;]", t["champs"].get("autres", ""))]
        activites = [x for x in dict.fromkeys(activites) if x]
    if t := _r(rep, "interet"):
        texte = t["champs"].get("texte", "")
    return {"criteres": criteres, "lignes": [x for _, x in lignes],
            "sources": [VERS_QUESTION.get(k) for k, _ in lignes], "activites": activites, "interet_texte": texte,
            "regles": actives, "mots_titre": mots, "semaine": o["semaine"], "mois": o["mois"]}


def _en_lignes(v):
    return "\n".join(v) if isinstance(v, list) else v or ""


# La question d'où vient chaque critère : « Modifier » sur l'écran de succès la rouvre.
VERS_QUESTION = {"trajet": "trajet", "teletravail": "teletravail", "taux_min": "taux", "contrats": "contrat",
                 "langues": "langues", "encadrement": "encadrement", "organisations": "organisation",
                 "secteurs_exclus": "organisation", "heures_max": "conditions", "vacances_min": "conditions",
                 "deplacements": "conditions", "a_eviter": "eviter"}


def enregistrer(reg, cle=None):
    """Écrit dans le profil ce que les réponses donnent, dès la fin du questionnaire : critères, intérêt,
    angles (ceux de l'IA si le profil n'en a pas), règles du pré-tri, intitulés écartés, objectifs ; le
    différenciateur proposé seulement si le profil n'en a pas. `cle` : une réponse modifiée après coup, qui ne
    réécrit que ce qu'elle commande (le reste du profil, réglé ailleurs depuis, reste). Lève
    profil.ProfilInvalide sans critère."""
    from . import profil
    p = profil.charger()
    rep = reponses(reg)
    if cle is not None:
        rep = {k: v for k, v in rep.items() if k in (cle, CONTEXTE)}
    prop = proposition(rep, p)
    ctx = rep.get(CONTEXTE) or {}
    if not prop["lignes"]:
        raise profil.ProfilInvalide("aucun critère ne ressort de tes réponses : ajoute-en dans le détail")
    garder = lambda cle, v: "\n".join(v) if isinstance(v, list) else v if v is not None else _en_lignes(profil.lire(p, cle))
    profil.enregistrer("criteres", {
        "evaluation.criteres": prop["criteres"],
        "evaluation.activites": garder("evaluation.activites", prop["activites"]),
        "evaluation.interet": garder("evaluation.interet", prop["interet_texte"]),
        "evaluation.angles": "\n".join(profil.lire(p, "evaluation.angles") or ctx.get("angles") or [])})
    profil.enregistrer("pretri", {"exclusions.regles": " ".join(prop["regles"]),
                                  "exclusions.mots_titre": "\n".join(prop["mots_titre"])})
    profil.enregistrer_objectifs(str(prop["semaine"] or ""), str(prop["mois"] or ""))
    if ctx.get("differenciateur") and not profil.lire(profil.charger(), "dossier.differenciateur"):
        profil.poser_valeur("dossier.differenciateur", ctx["differenciateur"])
    _ecrire(reg, ENREGISTRE, maintenant())
    return prop


# --- L'appel unique à l'IA : exemples adaptés au poste, angles et différenciateur -----------------


class Voisin(BaseModel):
    libelle: str = Field(description="le métier voisin tel que montré, court (« Vente ou commercial »)")
    mots: list[str] = Field(description="1 à 4 mots d'intitulé de ce métier, en minuscules sans accents")


class Contexte(BaseModel):
    activites: list[str] = Field(description="5 à 7 activités typiques du poste recherché, quelques mots chacune")
    voisins: list[Voisin] = Field(description="4 à 6 métiers voisins que la personne pourrait vouloir écarter")
    exemple_interet: str = Field(description="3 exemples courts de ce qui peut rendre ce poste intéressant, séparés par des virgules")
    exemple_eviter: str = Field(description="3 exemples courts de ce qu'on peut vouloir éviter dans ce métier, séparés par des virgules")
    angles: list[str] = Field(default_factory=list, description="2 à 4 façons de se présenter, tirées de la base")
    differenciateur: str = Field("", description="un ou deux mots tirés de la base ; vide si rien ne ressort")


CONSIGNE_CONTEXTE = """Tu prépares le questionnaire « Ce qui compte pour toi » de %(prenom)s : des exemples de réponses adaptés au poste qu'il ou elle recherche, à cliquer, pour que les questions parlent de son métier. Tu reçois le poste recherché et sa base d'expérience.
- activites : 5 à 7 activités concrètes et typiques de ce poste, quelques mots chacune, à l'infinitif (« cadrer des cas d'usage d'IA avec les métiers », « former les équipes »). Variées : pas deux fois la même idée.
- voisins : 4 à 6 métiers voisins, souvent mêlés à ce poste dans les résultats de recherche, que la personne pourrait vouloir écarter d'office. Pour chacun : « libelle », court et clair (« Vente ou commercial », « Support ou helpdesk ») ; « mots », 1 à 4 mots ou expressions qui figurent dans les intitulés de ce métier et jamais dans celui du poste recherché, en minuscules sans accents (« vente », « commercial », « sales »).
- exemple_interet, exemple_eviter : 3 exemples courts chacun, séparés par des virgules, propres à ce métier.
- angles : 2 à 4 façons de se présenter à un employeur, 8 mots au plus chacune, tirées de la base : rien d'inventé, aucun chiffre qui n'y soit pas. Vide si la base est vide.
- differenciateur : un ou deux mots, ce qui distingue %(prenom)s d'après la base ; vide si rien ne ressort.
Jamais de montant de salaire, rien sur la santé, la famille, l'origine, la religion ou l'âge."""


_PREPARATION = threading.Lock()      # deux ouvertures à la fois ne lancent qu'un appel


def preparer(reg, c=None):
    """Le seul appel à l'IA du questionnaire, une fois : rend le contexte (le garde). Un second appel
    pendant que le premier tourne attend son résultat au lieu d'en relancer un."""
    if (deja := reponses(reg).get(CONTEXTE)):
        return deja
    with _PREPARATION:
        if (deja := reponses(reg).get(CONTEXTE)):        # préparé pendant l'attente
            return deja
        return _preparer(reg, c)


def _preparer(reg, c=None):
    from . import parcours, profil
    p = profil.charger()
    poste = ", ".join(x["intitule"] for x in profil.postes_saisis(p)) or "(non précisé)"
    niveau = str(profil.lire(p, "recherche.niveau") or "").lower()
    t = moteur.Tache("ce-qui-compte-contexte", CONSIGNE_CONTEXTE % {"prenom": parcours._prenom()},
                     contexte="<base>\n%s\n</base>" % (parcours.rendu(reg) or "(vide)"), schema=Contexte,
                     effort="low", delai_s=180)
    r = (c or moteur.ouvrir()).executer(t, "<poste>\n%s%s\n</poste>" % (poste, " (niveau : %s)" % niveau if niveau else "")).objet
    from .prefilter import plier
    liste = lambda xs, n, m: [x for x in dict.fromkeys(_court(a, n) for a in xs) if x][:m]
    ctx = {"activites": liste(r.activites, 90, 8),
           "voisins": [{"libelle": _texte(v.libelle, 60),
                        "mots": [m for m in dict.fromkeys(plier(_texte(x, 40)).replace("|", " ").strip() for x in v.mots) if m][:4]}
                       for v in r.voisins if _texte(v.libelle, 60) and v.mots][:6],
           "exemples": {"interet": _texte(r.exemple_interet, 160), "eviter": _texte(r.exemple_eviter, 160)},
           "angles": liste(r.angles, 120, 5), "differenciateur": _texte(r.differenciateur, 60)}
    _ecrire(reg, CONTEXTE, ctx)
    return ctx
