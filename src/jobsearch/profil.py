"""Le profil de la personne qui cherche : identité, recherche, critères, dossier, coffre.

Vit dans `data/profil.yaml` (hors git), rempli par l'assistant de la page Profil du
tableau de bord. C'est ce qui rend la plateforme utilisable par quelqu'un d'autre :
le nom, la ville, les critères et les angles des consignes Claude viennent d'ici,
plus du code.

Ce qui existe déjà dans `config/settings.yaml` ou `config/criteres.yaml` (coffre,
objectifs, listes du sourcing, lieux LinkedIn et Indeed, mots des intitulés) n'est
écrit dans le profil que
s'il en diffère : les fichiers de config donnent les valeurs par défaut, le profil
ce qui est propre à la personne, et `config.reglages()` / `config.criteres()`
rendent la superposition des deux. Les règles du pré-tri restent dans
`criteres.yaml` : le profil n'en garde que celles qu'il désactive, et ses propres mots
d'intitulés à écarter, sans expression régulière à écrire.

Le plancher salarial n'a pas de champ : il n'apparaît nulle part.
"""

import copy
import datetime as dt
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml

from . import config, pays, telephone


def champ(cle, libelle, type="texte", requis=False, aide="", exemple="", choix=None, prefixe="",
          en_tete=None, vide="—"):
    """Un champ de l'assistant. `choix` : la liste d'une liste déroulante, `en_tete` : les choix
    proposés d'abord, `vide` : le libellé de l'option sans valeur, `prefixe` : le début fixe d'une adresse."""
    return {"cle": cle, "libelle": libelle, "type": type, "requis": requis, "aide": aide, "exemple": exemple,
            "choix": choix or [], "prefixe": prefixe, "en_tete": en_tete or [], "vide": vide}


LINKEDIN = "https://www.linkedin.com/in/"


ETAPES = [
    {"id": "identite", "titre": "Identité",
     "intro": "Ce qui figure en tête du CV et des lettres, et sert à pré-remplir les formulaires des employeurs. "
              "L'IA ne le voit jamais : elle écrit avec des balises, remplacées sur ce Mac.",
     "champs": [
         champ("identite.civilite", "Civilité", type="choix", choix=["Madame", "Monsieur", "Mx"],
               vide="Ne pas préciser",
               aide="Mx : forme neutre. Reprise dans les listes « Civilité » des formulaires qui la proposent."),
         champ("identite.prenom", "Prénom", requis=True),
         champ("identite.nom", "Nom", requis=True),
         champ("identite.adresse", "Rue et numéro"),
         champ("identite.npa", "NPA"),
         champ("identite.ville", "Localité", requis=True, aide="Aussi la ville en tête des lettres."),
         champ("identite.pays", "Pays", type="choix", choix=list(pays.PAYS_DE_RECHERCHE.values()),
               aide="Proposé d'après les réglages de région de ton ordinateur."),
         champ("identite.telephone", "Téléphone", type="tel",
               aide="Choisis l'indicatif, puis tape le numéro : il se met en forme tout seul."),
         champ("identite.email", "E-mail", type="email", requis=True),
         champ("identite.site", "Site web", type="url", prefixe="https://"),
         champ("identite.linkedin", "Profil LinkedIn", type="url", prefixe=LINKEDIN,
               aide="La fin de l'adresse de ton profil ; tu peux aussi coller l'adresse entière."),
         champ("identite.nationalite", "Nationalité", type="choix", choix=pays.TOUS, en_tete=pays.FREQUENTES),
         champ("identite.date_naissance", "Date de naissance", type="date_naissance",
               aide="Seulement pour les formulaires qui l'exigent ; jamais dans le CV ni les lettres."),
     ]},
    {"id": "recherche", "titre": "Recherche",
     "intro": "Ce que le passage du matin cherche, et ce que le pré-tri écarte d'office.",
     "champs": [
         champ("recherche.niveau", "Niveau visé", exemple="senior", aide="Repris dans la consigne de notation."),
         champ("recherche.region", "Région visée", exemple="Suisse romande",
               aide="Sert aussi à estimer le salaire du marché pour chaque annonce."),
         champ("recherche.cantons", "Cantons", type="liste", requis=True,
               aide="Abréviations, une par ligne (VD, GE…). Une annonce hors de ces cantons est écartée."),
         champ("recherche.jobup_lieux", "Lieux jobup", type="liste",
               aide="Cantons ou villes, écrits comme jobup les connaît (Vaud, Genève…)."),
         champ("recherche.requetes", "Requêtes", type="liste", requis=True,
               aide="Une recherche par ligne, lancée sur chaque plateforme."),
         champ("recherche.entreprises", "Entreprises visées", type="liste",
               aide="Une recherche de plus par entreprise nommée."),
         champ("recherche.familles", "Mots des intitulés visés", type="liste", requis=True,
               aide="Un intitulé qui ne contient aucun de ces mots est écarté d'office. Un mot ou une expression "
                    "par ligne, sans accents ni majuscules."),
         champ("recherche.linkedin_requetes", "Requêtes LinkedIn", type="liste",
               aide="Une par ligne, dans la syntaxe de LinkedIn (« OR » entre les variantes). "
                    "Chaque requête est un run Apify payant."),
         champ("recherche.linkedin_lieu", "Lieu LinkedIn", exemple="Lausanne, Vaud, Switzerland",
               aide="Écrit comme LinkedIn le propose dans sa recherche."),
         champ("recherche.indeed_lieux", "Lieux Indeed", type="liste",
               aide="Villes, une par ligne : chaque requête est lancée dans chaque lieu."),
     ]},
    {"id": "pretri", "titre": "Pré-tri",
     "intro": "Ce que le pré-tri écarte d'office, avant toute note. Une case décochée désactive la règle : "
              "les annonces qu'elle écartait restent à trier.",
     "champs": [
         champ("exclusions.regles", "Annonces écartées d'office", type="regles",
               aide="« limite » : écartée, mais montrée dans « Écartées limites » pour vérifier le filtre."),
         champ("exclusions.mots_titre", "Autres intitulés à écarter", type="liste",
               aide="Un intitulé qui contient un de ces mots est écarté comme métier exclu. Un mot ou une "
                    "expression par ligne, sans accents ni majuscules."),
     ]},
    {"id": "criteres", "titre": "Critères",
     "intro": "Ce que Claude vérifie en notant chaque annonce. Aucun montant ici : le salaire minimum "
              "visé n'est écrit nulle part.",
     "champs": [
         champ("evaluation.criteres", "Critères non négociables", type="liste", requis=True,
               aide="Un par ligne ; une précision facultative après « : » (trajet : depuis Lausanne)."),
         champ("evaluation.interet", "Ce qui rend un poste intéressant", exemple="mandat de cadrage, dimension IA, structure"),
         champ("evaluation.angles", "Angles de candidature", type="liste",
               aide="Les façons de te présenter, une par ligne : Claude en propose une par annonce."),
     ]},
    {"id": "dossier", "titre": "Dossier",
     "intro": "Ce que Claude suit en écrivant le CV et la lettre, en plus de la méthode du coffre.",
     "champs": [
         champ("dossier.differenciateur", "Différenciateur", exemple="IA",
               aide="Le troisième paragraphe de chaque lettre le met en avant."),
         champ("dossier.regles", "Règles particulières", type="liste",
               aide="Une consigne par ligne (Brevet en cours : dans la formation, pas dans les certifications)."),
         champ("dossier.certifications", "Certifications", type="liste",
               exemple="Certified Data Professional | The Analytics Institute | 02.2025",
               aide="Une par ligne : nom | organisme | MM.AAAA. Elles font foi dans chaque CV, à la place de celles "
                    "du CV maître ; un même nom n'est gardé qu'une fois."),
         champ("dossier.pages_cv", "Pages du CV au plus", type="nombre", exemple="2",
               aide="Un CV plus long est raccourci par Claude jusqu'à tenir sur ce nombre de pages A4 (2 si vide)."),
     ]},
    {"id": "coffre", "titre": "Coffre et objectifs",
     "intro": "Où vivent le CV maître, les preuves et les modèles, et le rythme de postulation à tenir.",
     "champs": [
         champ("coffre", "Dossier du coffre", type="chemin",
               aide="Le coffre Obsidian : lu seulement, jamais écrit.", exemple="~/Documents/Coffre"),
         champ("objectifs.semaine_min", "Objectif par semaine", type="nombre",
               aide="Nombre de postulations, du lundi au dimanche. Facultatif : vide, pas d'objectif."),
         champ("objectifs.mois_min", "Objectif par mois", type="nombre",
               aide="Nombre de postulations dans le mois civil. Facultatif : vide, pas d'objectif."),
         champ("courrier.comptes", "Comptes Apple Mail", type="liste",
               aide="Les comptes où chercher les réponses des employeurs, un par ligne."),
     ]},
]

# Clé du profil → (fichier de config qu'elle remplace, clé dans ce fichier).
SUPERPOSES = {
    "coffre": ("reglages", "coffre"),
    "objectifs.semaine_min": ("reglages", "objectifs.semaine_min"),
    "objectifs.mois_min": ("reglages", "objectifs.mois_min"),
    "courrier.comptes": ("reglages", "courrier.comptes"),
    "recherche.cantons": ("criteres", "sourcing.cantons"),
    "recherche.jobup_lieux": ("criteres", "sourcing.jobup_lieux"),
    "recherche.requetes": ("criteres", "sourcing.requetes"),
    "recherche.entreprises": ("criteres", "sourcing.entreprises"),
    "recherche.familles": ("criteres", "familles_cibles"),
    "recherche.linkedin_requetes": ("criteres", "sourcing.linkedin_requetes"),
    "recherche.linkedin_lieu": ("criteres", "sourcing.linkedin_lieu"),
    "recherche.indeed_lieux": ("reglages", "indeed.lieux"),
}
REGLES_HORS_CHOIX = {"canton"}          # suit les cantons de l'étape Recherche
REGLE_MOTS = "titre-profil"
FICHIERS_CONFIG = {"reglages": "settings.yaml", "criteres": "criteres.yaml"}

ENTETE = ("# Profil de la recherche d'emploi, rempli par la page Profil du tableau de bord.\n"
          "# Hors git. Ni secret ni plancher salarial ici.\n")


class ProfilInvalide(Exception):
    pass


# --- Lecture ------------------------------------------------------------------------


ACCUEIL = "accueil"            # clé du profil : premier remplissage pas encore terminé


def en_accueil(p=None):
    """Premier remplissage : pas encore de profil, ou l'assistant commencé mais pas terminé.

    Un profil d'avant l'assistant (sans la clé) n'y est jamais.
    """
    if not existe():
        return True
    p = charger() if p is None else p
    return p.get(ACCUEIL) == "en_cours"


def terminer_accueil():
    """Fin de l'accueil : le tableau de bord s'ouvre, sa checklist porte le reste du profil."""
    p = copy.deepcopy(charger())
    if p.pop(ACCUEIL, None) is not None or not existe():
        p[PREMIERS_PAS] = "affichee"
        ecrire(p)


# --- Accueil : l'objectif et la recherche, en un écran -------------------------------

PREMIERS_PAS = "premiers_pas"          # checklist du tableau de bord : affichee · masquee

# L'objectif de l'écran « Ta recherche » : ce qu'il change au pré-tri (règles désactivées).
OBJECTIFS = [
    {"valeur": "fixe", "libelle": "Un poste fixe à plein temps",
     "description": "Les CDD, les missions et les temps partiels sont écartés au tri.", "desactive": []},
    {"valeur": "partiel", "libelle": "Un poste fixe, temps partiel bienvenu",
     "description": "Les temps partiels restent ; les CDD et les missions sont écartés.",
     "desactive": ["taux", "contrat-freelance"]},
    {"valeur": "tout", "libelle": "Toutes les formes de contrat",
     "description": "Rien n'est écarté sur le contrat ni sur le taux d'activité.",
     "desactive": ["taux", "contrat-freelance", "contrat-cdd", "contrat-api-duree-determinee"]},
]
REGLES_OBJECTIF = {"taux", "contrat-freelance", "contrat-cdd", "contrat-api-duree-determinee"}


def objectif(p=None):
    """L'objectif qui correspond aux règles désactivées du profil."""
    off = set(lire(charger() if p is None else p, "exclusions.desactivees") or []) & REGLES_OBJECTIF
    return next((o["valeur"] for o in OBJECTIFS if set(o["desactive"]) == off), "fixe")


def enregistrer_recherche(valeur_objectif, poste, cantons):
    """L'écran « Ta recherche » de l'accueil : un intitulé, des cantons, un objectif."""
    o = next((x for x in OBJECTIFS if x["valeur"] == valeur_objectif), None)
    poste = re.sub(r"\s+", " ", str(poste or "")).strip()
    cantons = [c for c in dict.fromkeys(str(x).upper() for x in cantons or []) if c in pays.CANTONS]
    erreurs = [m for m, ok in (("l'objectif", o), ("le poste recherché", poste), ("au moins un canton", cantons)) if not ok]
    if erreurs:
        raise ProfilInvalide("à choisir : " + ", ".join(erreurs))
    p = copy.deepcopy(charger())
    if not existe():
        p[ACCUEIL] = "en_cours"
    requetes = lire(p, "recherche.requetes") or []
    _poser(p, "recherche.requetes", [poste] + [q for q in requetes if q.lower() != poste.lower()])
    _poser(p, "recherche.cantons", cantons)
    _poser(p, "recherche.jobup_lieux", [pays.CANTONS[c] for c in cantons])
    # Les mots de l'intitulé deviennent les mots des intitulés visés : le tri garde les annonces
    # dont le titre en contient un (« Product designer » → product, designer).
    familles = lire(p, "recherche.familles") or []
    _poser(p, "recherche.familles", familles + [m for m in mots_intitule(poste) if m not in familles])
    off = [i for i in (lire(p, "exclusions.desactivees") or []) if i not in REGLES_OBJECTIF] + o["desactive"]
    if off:
        _poser(p, "exclusions.desactivees", off)
    else:
        _retirer(p, "exclusions.desactivees")
    ecrire(p)
    return p


MOTS_VIDES = {"de", "du", "des", "la", "le", "les", "et", "en", "au", "aux", "pour", "un", "une", "of", "and", "the",
              "for", "in", "h", "f", "m", "w", "d", "hf", "fh", "mw", "wm", "e"}


def mots_intitule(poste):
    from .prefilter import plier
    mots = re.findall(r"[a-z0-9]+", plier(poste or ""))
    return list(dict.fromkeys(m for m in mots if len(m) >= 3 and m not in MOTS_VIDES))


# Les tâches de la checklist : chacune est un seul écran, avec les seuls champs qu'elle demande.
TACHES = {
    "identite": {"titre": "Tes coordonnées", "etapes": ["identite"], "icone": "identite",
                 "intro": "Elles figurent en tête de tes CV et de tes lettres, et servent à pré-remplir "
                          "les formulaires des employeurs. Rien n'est envoyé sans toi, et l'IA ne les voit "
                          "jamais : elle écrit avec des balises, remplacées sur ton Mac."},
    "criteres": {"titre": "Ce qui compte pour toi", "etapes": ["criteres"], "icone": "criteres", "entretien": True,
                 "intro": "L'IA note chaque annonce d'après ces critères, comme tu le ferais. "
                          "Tu pourras les ajuster quand tu veux."},
    "dossier": {"titre": "Ton parcours", "etapes": ["dossier"], "icone": "dossier", "entretien": True,
                "intro": "La matière de tes CV et de tes lettres : l'IA n'écrit que ce que tu as vraiment fait."},
}


def enregistrer_sources(choisis):
    """L'écran « Tes sources » de l'accueil : les sites du socle à lire chaque matin."""
    from .sources.catalogue import SOCLE
    ids = [s["id"] for s in SOCLE]
    choisis = [x for x in ids if x in set(choisis or [])]
    if not choisis:
        raise ProfilInvalide("au moins un site")
    p = copy.deepcopy(charger())
    if not existe():
        p[ACCUEIL] = "en_cours"
    _poser(p, "sources.socle", choisis)
    ecrire(p)
    return p


def premiers_pas(p=None, ia_connectee=False):
    """La checklist du tableau de bord après l'accueil : [(id, libellé, pourquoi, durée, lien, fait)].

    Une étape du profil est faite quand ses champs obligatoires sont remplis et qu'au moins
    un de ses champs a été enregistré.
    """
    p = charger() if p is None else p
    if p.get(PREMIERS_PAS) != "affichee":
        return []
    return _taches_checklist(p, ia_connectee)


def _taches_checklist(p, ia_connectee):
    ids_manquants = {e for e, _ in manquants(p)}
    saisie = lambda *etapes: all(e not in ids_manquants for e in etapes) and any(
        lire(p, c["cle"]) not in (None, "", []) for e in etapes for c in etape(e)["champs"])
    recherche = bool(lire(p, "recherche.requetes")) and bool(lire(p, "recherche.cantons"))
    return [
        ("ia", "Connecter ton IA", "Pour noter les annonces et écrire tes dossiers", "1 min", "/ia", ia_connectee),
        ("recherche", "Définir ta recherche", "Ce que la plateforme cherche chaque matin", "1 min", "/profil/recherche", recherche),
        ("identite", "Tes coordonnées", "En tête de tes CV et lettres, et pour les formulaires", "2 min", "/premiers-pas/identite", saisie("identite")),
        ("dossier", "Ton parcours", "Ton CV, puis un entretien avec l'IA sur chaque poste : des CV et des lettres "
         "fidèles à ton expérience", "15 min", "/premiers-pas/dossier", saisie("dossier") or _parcours_termine()),
        ("criteres", "Ce qui compte pour toi", "Quelques questions de l'IA, pour qu'elle note les annonces comme tu "
         "le ferais", "5 min", "/premiers-pas/criteres", saisie("criteres")),
    ]


def _parcours_termine():
    from . import parcours
    try:
        return parcours.termine()
    except Exception:
        return False


# --- Ce que l'interface montre : avancement de la configuration et options -------------

# Options d'interface, au choix de la personne (page « Ton interface »).
OPTIONS = [
    {"id": "jobroom", "titre": "Preuves de recherche Job-Room",
     "description": "Pour l'ORP : la plateforme prépare la saisie de chaque postulation dans Job-Room et te rappelle les délais."},
    {"id": "objectifs", "titre": "Objectifs de postulations",
     "description": "Un nombre de postulations par semaine, par mois, ou les deux, au choix : suivi sur le tableau "
                    "de bord, avec un rappel le vendredi si l'objectif de la semaine n'est pas atteint."},
]

# Ce que chaque fonction demande d'avoir configuré (identifiants de la checklist).
DEBLOCAGES = [
    {"id": "offres", "titre": "Voir les offres trouvées", "demande": ["recherche"]},
    {"id": "noter", "titre": "La note de chaque annonce", "demande": ["ia", "criteres"]},
    {"id": "postuler", "titre": "Postuler : CV, lettre et suivi", "demande": ["ia", "identite", "dossier"]},
]


def progressif(p=None):
    """Nouvel utilisateur : l'interface s'ouvre au fil de la configuration. Un profil
    d'avant l'accueil (installation de développement) a tout, tout de suite."""
    p = charger() if p is None else p
    return p.get(PREMIERS_PAS) is not None or p.get(ACCUEIL) is not None


def option(nom, p=None):
    p = charger() if p is None else p
    v = lire(p, "interface." + nom)
    return (not progressif(p)) if v is None else bool(v)


def poser_option(nom, valeur):
    if nom not in {o["id"] for o in OPTIONS}:
        raise ProfilInvalide("option inconnue")
    p = copy.deepcopy(charger())
    _poser(p, "interface." + nom, bool(valeur))
    ecrire(p)


def fonctions(p=None, ia_connectee=False):
    """{fonction: ouverte} et, pour chacune, ce qui manque : [(id, libellé, lien)]."""
    p = charger() if p is None else p
    out = {o["id"]: option(o["id"], p) for o in OPTIONS}
    if not progressif(p):
        out.update({d["id"]: True for d in DEBLOCAGES})
        out["manque"] = {d["id"]: [] for d in DEBLOCAGES}
        return out
    faits = {x[0]: x for x in _taches_checklist(p, ia_connectee)}
    out["manque"] = {}
    for d in DEBLOCAGES:
        manque = [(i, faits[i][1], faits[i][4]) for i in d["demande"] if not faits[i][5]]
        out[d["id"]] = not manque
        out["manque"][d["id"]] = manque
    return out


OBJECTIFS_RYTHME = ("objectifs.semaine_min", "objectifs.mois_min")


def objectifs_rythme():
    """{"semaine": n, "mois": n} ; 0 : pas d'objectif pour cette période."""
    o = config.reglages().get("objectifs") or {}
    return {"semaine": int(o.get("semaine_min") or 0), "mois": int(o.get("mois_min") or 0)}


def enregistrer_objectifs(semaine, mois):
    """Les objectifs seuls (page « Ton interface ») : chacun facultatif, vide = aucun."""
    p = copy.deepcopy(charger())
    champs = {c["cle"]: c for e in ETAPES for c in e["champs"] if c["cle"] in OBJECTIFS_RYTHME}
    valeurs = {}
    for cle, brut in zip(OBJECTIFS_RYTHME, (semaine, mois)):
        valeurs[cle] = _convertir(champs[cle], brut)            # lève ProfilInvalide sans rien écrire
    for cle, v in valeurs.items():
        if _egal_base(cle, v):
            _retirer(p, cle)
        else:
            _poser(p, cle, v)
    ecrire(p)
    return p


VALEURS_SEULES = {"dossier.differenciateur"}       # ce qu'un entretien pose seul, hors de son étape


def poser_valeur(cle, valeur):
    if cle not in VALEURS_SEULES:
        raise ProfilInvalide("valeur inconnue")
    v = re.sub(r"\s+", " ", str(valeur or "")).strip()
    p = copy.deepcopy(charger())
    if v:
        _poser(p, cle, v)
    else:
        _retirer(p, cle)
    ecrire(p)


def masquer_premiers_pas():
    p = copy.deepcopy(charger())
    if p.get(PREMIERS_PAS):
        p[PREMIERS_PAS] = "masquee"
        ecrire(p)


def existe():
    return config.chemin_profil().exists()


@lru_cache
def charger():
    p = config.chemin_profil()
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def lire(d, cle, defaut=None):
    for k in cle.split("."):
        if not isinstance(d, dict) or k not in d:
            return defaut
        d = d[k]
    return d


def _poser(d, cle, v):
    *chemin, fin = cle.split(".")
    for k in chemin:
        d = d.setdefault(k, {})
    d[fin] = v


def _retirer(d, cle):
    *chemin, fin = cle.split(".")
    pile = [d]
    for k in chemin:
        if not isinstance(pile[-1].get(k), dict):
            return
        pile.append(pile[-1][k])
    pile[-1].pop(fin, None)
    for parent, k in zip(reversed(pile[:-1]), reversed(chemin)):     # sections vidées : retirées
        if parent.get(k) == {}:
            parent.pop(k)


def familles_en_liste(motif):
    """« \\b(ux|design|…)\\b » (familles_cibles de criteres.yaml) → ["ux", "design", …]."""
    motif = re.sub(r"\s*\|\s*", "|", (motif or "").strip())
    m = re.fullmatch(r"\\b\((.*)\)\\b", motif, re.S)
    return [w.strip() for w in (m.group(1) if m else motif).split("|") if w.strip()]


def familles_en_motif(mots):
    return r"\b(%s)\b" % "|".join(mots)


def base(cle):
    """La valeur par défaut d'une clé superposée, lue dans config/ (sans le profil)."""
    fichier, cible = SUPERPOSES[cle]
    v = lire(config.lire_yaml(FICHIERS_CONFIG[fichier]), cible)
    return familles_en_liste(v) if cle == "recherche.familles" else v


def _regle_cantons(c, cantons):
    for r in c.get("regles") or []:
        if r.get("id") == "canton":
            r["regex"] = "^(?!(%s)$)[A-Z]{2}$" % "|".join(cantons)
            r["motif"] = ("Lieu : hors du canton %s" % cantons[0] if len(cantons) == 1
                          else "Lieu : hors des cantons %s" % ", ".join(cantons))


def regles_au_choix():
    """Les règles de criteres.yaml que le profil peut désactiver : [(groupe, [(id, libellé, limite)])].

    « Langue : allemand exigé » → groupe « Langue », libellé « allemand exigé ».
    """
    groupes, autres = {}, []
    for r in config.lire_yaml("criteres.yaml").get("regles") or []:
        if r["id"] in REGLES_HORS_CHOIX:
            continue
        groupe, _, libelle = r["motif"].partition(" : ")
        (groupes.setdefault(groupe, []) if libelle else autres).append((r["id"], libelle or r["motif"], bool(r.get("limite"))))
    return list(groupes.items()) + ([("Autres", autres)] if autres else [])


def _ids_au_choix():
    return [i for _, rs in regles_au_choix() for i, _, _ in rs]


def _regle_mots(mots):
    return {"id": REGLE_MOTS, "passe": 0, "champ": "titre", "motif": "Métier exclu : intitulé écarté par le profil",
            "regex": r"\b(%s)\b" % "|".join(re.escape(m) for m in mots)}


def _exclusions(c, p):
    off = set(lire(p, "exclusions.desactivees") or [])
    regles = [r for r in c.get("regles") or [] if r.get("id") not in off]
    if lire(p, "exclusions.mots_titre"):
        i = next((n for n, r in enumerate(regles) if r.get("passe") != 0), len(regles))   # à la fin de la passe 0
        regles.insert(i, _regle_mots(lire(p, "exclusions.mots_titre")))
    c["regles"] = regles


def appliquer(defauts, fichier, p=None):
    """Les valeurs du profil posées sur celles d'un fichier de config."""
    p = charger() if p is None else p
    out = copy.deepcopy(defauts)
    for cle, (f, cible) in SUPERPOSES.items():
        v = lire(p, cle)
        if f != fichier or v is None:
            continue
        _poser(out, cible, familles_en_motif(v) if cle == "recherche.familles" else v)
    if fichier == "criteres" and lire(p, "recherche.cantons"):
        _regle_cantons(out, lire(p, "recherche.cantons"))
    if fichier == "criteres":
        _exclusions(out, p)
    return out


def identite(p=None):
    """Coordonnées : le profil, sinon l'ancien data/coordonnees.yaml, sinon l'en-tête du dernier CV."""
    p = charger() if p is None else p
    if lire(p, "identite"):
        return dict(lire(p, "identite"))
    ancien = config.DATA / "coordonnees.yaml"
    if ancien.exists():
        return yaml.safe_load(ancien.read_text(encoding="utf-8")) or {}
    cv = next(iter(sorted((config.DATA / "dossiers").glob("*/CV.md"), reverse=True)), None)
    if cv:
        from .coordonnees import _depuis_cv
        return _depuis_cv(cv.read_text(encoding="utf-8"))
    return {}


def effectif(cle, p=None):
    """Ce qui s'applique pour une clé : le profil, sinon la valeur par défaut."""
    p = charger() if p is None else p
    if cle.startswith("identite."):
        return identite(p).get(cle.split(".", 1)[1], "")
    if cle == "exclusions.regles":                 # les règles actives ; le profil garde les désactivées
        off = set(lire(p, "exclusions.desactivees") or [])
        return [i for i in _ids_au_choix() if i not in off]
    v = lire(p, cle)
    if v is None and cle in SUPERPOSES:
        v = base(cle)
    return "" if v is None else v


def affiche(c, p=None):
    """La valeur d'un champ telle que le récapitulatif la montre."""
    v = effectif(c["cle"], p)
    if c["type"] == "regles":
        off = [libelle for _, rs in regles_au_choix() for i, libelle, _ in rs if i not in v]
        return "toutes" if not off else "toutes sauf : " + ", ".join(off)
    return v


def manquants(p=None):
    """[(étape, libellé)] des champs obligatoires encore vides."""
    p = charger() if p is None else p
    return [(e["id"], c["libelle"]) for e in etapes() for c in e["champs"]
            if c["requis"] and effectif(c["cle"], p) in ("", [], None)]


# --- Ce que les consignes et les fichiers en tirent ---------------------------------


def prenom():
    return identite().get("prenom") or "la personne"


def nom_complet():
    i = identite()
    return " ".join(x for x in (i.get("prenom"), i.get("nom")) if x) or "la personne"


def criteres_evaluation():
    """[(nom, précision)] : « trajet : depuis Lausanne » → ("trajet", "depuis Lausanne")."""
    out = []
    for ligne in lire(charger(), "evaluation.criteres") or []:
        nom, _, precision = str(ligne).partition(":")
        out.append((nom.strip(), precision.strip()))
    return out


def liste_ou(items):
    """« A », « A ou B », « A, B, ou C »."""
    items = list(items)
    if len(items) <= 2:
        return " ou ".join(items)
    return ", ".join(items[:-1]) + ", ou " + items[-1]


NOMBRES = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix",
           "onze", "douze"]


def nombre_fr(n):
    return NOMBRES[n] if 0 <= n < len(NOMBRES) else str(n)


def documents_coffre(coffre=None):
    """Ce que la plateforme lit dans le coffre, avec ce qui manque : [(rôle, chemin relatif, présent)].
    Rien dans l'application (installation neuve), qui ne lit aucun coffre."""
    from . import dossier, evaluation
    if config.neutre():
        return []
    coffre = Path(os.path.expanduser(str(coffre if coffre is not None else config.reglages()["coffre"])))
    attendus = [("Profil : " + n.removesuffix(".md"), evaluation.PROFIL / n) for n in evaluation.FICHIERS_PROFIL]
    attendus += [("Méthode des dossiers", dossier.METHODE),
                 ("Style d'écriture", dossier.SUR_MOI / "Style d'écriture.md"),
                 ("Forces", dossier.SUR_MOI / "Forces VIA.md"),
                 ("Modèle Word du CV", dossier.TEMPLATES / "CV-site.docx"),
                 ("Modèle Word de la lettre", dossier.TEMPLATES / "Lettre-site.docx"),
                 ("Mesure des mots-clés", dossier.ATS)]
    return [(role, str(rel), (coffre / rel).exists()) for role, rel in attendus]


# --- Écriture -----------------------------------------------------------------------


def _convertir(c, brut):
    t = c["type"]
    if t == "regles":                              # cases cochées : liste (formulaire) ou une par ligne
        cochees = set(brut if isinstance(brut, list) else str(brut or "").split())
        return [i for i in _ids_au_choix() if i in cochees]
    if t == "liste":
        vus, out = set(), []
        for ligne in str(brut or "").splitlines():
            ligne = re.sub(r"\s+", " ", ligne).strip()
            if c["cle"] == "recherche.cantons":
                ligne = ligne.upper()
                if ligne and not re.fullmatch(r"[A-Z]{2}", ligne):
                    raise ProfilInvalide("%s : « %s » n'est pas une abréviation de canton" % (c["libelle"], ligne))
            elif c["cle"] in ("recherche.familles", "exclusions.mots_titre"):
                from .prefilter import plier
                ligne = plier(ligne).replace("|", " ").strip()
            cle = ligne
            if c["cle"] == "dossier.certifications":       # même certification, autre date : la première reste
                from .prefilter import plier
                cle = re.sub(r"[^a-z0-9]+", " ", plier(ligne.split("|")[0])).strip()
            if ligne and cle not in vus:
                vus.add(cle)
                out.append(ligne)
        return out
    v = re.sub(r"\s+", " ", str(brut or "")).strip()
    if t == "nombre":
        if not v:
            return 0 if c["cle"] in OBJECTIFS_RYTHME else None      # objectif facultatif : vide, aucun
        if not v.isdigit():
            raise ProfilInvalide("%s : un nombre entier, 0 ou plus" % c["libelle"])
        return int(v)
    if t == "email" and v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
        raise ProfilInvalide("%s : adresse invalide" % c["libelle"])
    if t == "tel":
        return telephone.normaliser(v, defaut=pays.code_telephone_defaut())
    if t in ("date", "date_naissance") and v:
        iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)          # le calendrier du navigateur
        m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", v)
        try:
            j, mo, a = (iso.group(3), iso.group(2), iso.group(1)) if iso else (m.group(1), m.group(2), m.group(3))
            v = dt.date(int(a), int(mo), int(j)).strftime("%d.%m.%Y")
        except (AttributeError, ValueError):
            raise ProfilInvalide("%s : une date jj.mm.aaaa" % c["libelle"]) from None
    if t == "choix" and v and v not in c["choix"]:
        raise ProfilInvalide("%s : choisir dans la liste" % c["libelle"])
    if t == "url" and v and c["prefixe"] == LINKEDIN:
        ident = re.sub(r"^(https?://)?([a-z]{2,3}\.)?(www\.)?linkedin\.com/", "", v, flags=re.I)
        ident = re.sub(r"^/?in/", "", ident).split("?")[0].split("#")[0].strip("/")
        if not re.fullmatch(r"[^\s/]+", ident):
            raise ProfilInvalide("%s : la fin de l'adresse, par exemple prenom-nom" % c["libelle"])
        return LINKEDIN + ident
    if t == "url" and v and not v.startswith(("http://", "https://")):
        v = "https://" + v
    return v


def en_saisie(c, v):
    """La valeur telle qu'elle s'écrit dans le champ : sans le préfixe affiché devant."""
    v = "" if v is None else str(v)
    if c["cle"] == "identite.pays" and not v:
        return pays.pays_detecte()
    if c.get("type") in ("date", "date_naissance"):   # jj.mm.aaaa enregistré → aaaa-mm-jj du calendrier
        m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", v)
        return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1)) if m else v
    if c.get("prefixe") == LINKEDIN:
        m = re.search(r"linkedin\.com/in/([^/?#\s]+)", v, re.I)
        return m.group(1) if m else v
    if c.get("prefixe"):
        return re.sub(r"^https?://", "", v)
    return v


def _egal_base(cle, v):
    b = base(cle)
    if cle == "coffre":
        return os.path.expanduser(v or "") == os.path.expanduser(b or "")
    return v == b


def etapes():
    """Les étapes de l'assistant. L'application (installation neuve) est autoportante : pas de
    coffre à indiquer, son parcours vit dans sa base (`parcours.py`)."""
    if not config.neutre():
        return ETAPES
    return [dict(e, titre="Objectifs et courrier",
                 intro="Le rythme de postulation à tenir, et les comptes où chercher les réponses des employeurs.",
                 champs=[c for c in e["champs"] if c["cle"] != "coffre"]) if e["id"] == "coffre" else e
            for e in ETAPES]


def etape(ident):
    return next((e for e in etapes() if e["id"] == ident), None)


def enregistrer(ident, formulaire):
    """Les champs d'une étape, tels que saisis. Lève ProfilInvalide sans rien écrire."""
    e = etape(ident)
    if e is None:
        raise ProfilInvalide("étape inconnue")
    p = copy.deepcopy(charger())
    if not existe():
        p[ACCUEIL] = "en_cours"                 # premier enregistrement : l'assistant commence
    if not lire(p, "identite") and ident != "identite":
        _poser(p, "identite", identite(p))        # coordonnées reprises une fois de l'ancien fichier
    erreurs = []
    for c in e["champs"]:
        multiple = c["type"] == "regles" and hasattr(formulaire, "getlist")
        try:
            v = _convertir(c, formulaire.getlist(c["cle"]) if multiple else formulaire.get(c["cle"], ""))
        except ProfilInvalide as err:
            erreurs.append(str(err))
            continue
        if c["type"] == "regles":
            off = [i for i in _ids_au_choix() if i not in v]
            if off:
                _poser(p, "exclusions.desactivees", off)
            else:
                _retirer(p, "exclusions.desactivees")
        elif c["requis"] and v in ("", [], None):
            erreurs.append("%s : obligatoire" % c["libelle"])
        elif c["cle"] in SUPERPOSES and _egal_base(c["cle"], v):
            _retirer(p, c["cle"])               # même valeur que config/ : le fichier de config reste maître
        elif v in ("", None) or (v == [] and c["cle"] not in SUPERPOSES):
            _retirer(p, c["cle"])
        else:
            _poser(p, c["cle"], v)
    if erreurs:
        raise ProfilInvalide(" ; ".join(erreurs))
    ecrire(p)
    return p


def ecrire(p):
    chemin = config.chemin_profil()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(ENTETE + yaml.safe_dump(p, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(chemin)
    oublier()


def oublier():
    """Vide les caches : la valeur saisie s'applique tout de suite au tableau de bord."""
    from . import prefilter
    for f in (charger, config.reglages, config.criteres, prefilter.regles, prefilter.familles):
        f.cache_clear()
