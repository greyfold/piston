"""Le profil de la personne qui cherche : identité, recherche, critères, dossier, objectifs.

Vit dans `data/profil.yaml` (hors git), rempli par l'assistant de la page Profil du
tableau de bord. C'est ce qui rend la plateforme utilisable par quelqu'un d'autre :
le nom, la ville, les critères et les angles des consignes Claude viennent d'ici,
plus du code.

Ce qui existe déjà dans `config/settings.yaml` ou `config/criteres.yaml` (objectifs, listes du sourcing,
postes recherchés) n'est écrit dans le profil que s'il en diffère : les fichiers de config donnent les valeurs par défaut, le profil
ce qui est propre à la personne, et `config.reglages()` / `config.criteres()`
rendent la superposition des deux. Des postes recherchés se déduisent les requêtes de chaque
plateforme, les recherches LinkedIn et les mots des intitulés du pré-tri ; des cantons, les lieux jobup et Indeed et la région ; de l'adresse, le lieu LinkedIn. Les règles du pré-tri restent dans
`criteres.yaml` : le profil n'en garde que celles qu'il désactive, et ses propres mots
d'intitulés à écarter, sans expression régulière à écrire.

Le plancher salarial n'a pas de champ : il n'apparaît nulle part.
"""

import copy
import datetime as dt
import os
import re
from functools import lru_cache

import yaml

from . import config, pays, telephone


# Les niveaux de poste que la recherche peut viser : la liste du champ et ce que l'import du CV peut en déduire.
NIVEAUX = ["Junior", "Confirmé", "Senior", "Cadre", "Direction"]


def niveau_connu(v):
    """Le niveau de NIVEAUX qu'une valeur désigne, majuscules et espaces ignorés ; "" sinon."""
    v = re.sub(r"\s+", " ", str(v or "")).strip().lower()
    return next((n for n in NIVEAUX if n.lower() == v), "")


def champ(cle, libelle, type="texte", requis=False, aide="", exemple="", choix=None, prefixe="",
          en_tete=None, vide="—", virgules=False, large=False, colonnes=None, section=""):
    """Un champ de l'assistant. `choix` : la liste d'une liste déroulante, `en_tete` : les choix
    proposés d'abord, `vide` : le libellé de l'option sans valeur, `prefixe` : le début fixe d'une adresse.
    Types de liste : « liste » (une par ligne), « liste_choix » (pilules à cocher et un champ pour en ajouter,
    séparées par des virgules si `virgules`), « criteres » (les critères structurés, CRITERES), « postes »
    (une ligne par poste : son intitulé et ses autres intitulés, POSTES), « lignes » (une ligne par élément, une
    case par colonne de `colonnes` : [(sous-clé, libellé, exemple)] ; enregistrée « a | b | c », comme « liste »).
    `large` : toute la largeur du formulaire. `section` : un titre de section montré avant le champ."""
    return {"cle": cle, "libelle": libelle, "type": type, "requis": requis, "aide": aide, "exemple": exemple,
            "choix": choix or [], "prefixe": prefixe, "en_tete": en_tete or [], "vide": vide, "virgules": virgules,
            "large": large, "colonnes": colonnes or [], "section": section}


LINKEDIN = "https://www.linkedin.com/in/"

# Niveaux de langue proposés : l'échelle européenne (CECR), qui sert aussi à s'évaluer soi-même ; un niveau
# choisi n'est pas un certificat (colonne « Attesté par » pour celui qui en a un).
NIVEAUX_LANGUE = ["Langue maternelle", "Bilingue", "Maîtrise (C2)", "Courant (C1)", "Avancé (B2)",
                  "Intermédiaire (B1)", "Élémentaire (A2)", "Débutant (A1)"]


def niveau_langue(v):
    """Le niveau proposé qu'une saisie désigne sans rien y ajouter : « courant (C1) », « C1 », « c1 courant »,
    « maternelle » ; sinon la saisie telle quelle (« notions » ne devient pas un niveau du CECR)."""
    from .prefilter import plier
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    cle = " ".join(re.findall(r"[a-z0-9]+", plier(v)))
    for n in NIVEAUX_LANGUE:
        m = re.fullmatch(r"(.+) \((\w\d)\)", n)
        mot, code = (plier(m.group(1)), m.group(2).lower()) if m else (plier(n), None)
        formes = {mot, plier(n).split()[-1]} if not code else {code, mot + " " + code, code + " " + mot}
        if cle in formes:
            return n
    return v


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
         champ("recherche.niveau", "Niveau visé", type="choix", choix=NIVEAUX, vide="Non précisé", large=True,
               aide="Déduit de ton CV à son import ; corrige-le si besoin. Repris dans la consigne de notation."),
         champ("recherche.cantons", "Cantons", type="cantons", requis=True,
               aide="Chaque plateforme cherche dans ces cantons ; une annonce hors de ces cantons est écartée."),
         champ("recherche.postes", "Postes recherchés", type="postes", requis=True,
               aide="Un poste par ligne, avec les autres intitulés sous lesquels il est publié. Chaque intitulé est "
                    "cherché sur chaque plateforme ; sur LinkedIn, un poste fait une seule recherche, ses intitulés "
                    "reliés par « OR ». Une annonce dont le titre ne contient aucun de leurs mots est écartée."),
         champ("recherche.entreprises", "Entreprises visées", type="liste",
               aide="Une recherche de plus par entreprise nommée."),
     ]},
    {"id": "pretri", "titre": "Pré-tri",
     "intro": "Ce que le pré-tri écarte d'office, avant toute note. Une case décochée désactive la règle : "
              "les annonces qu'elle écartait restent à trier.",
     "champs": [
         champ("exclusions.regles", "Annonces écartées d'office", type="regles",
               aide="« limite » : écartée, mais montrée dans « Écartées limites » pour vérifier le filtre."),
         champ("exclusions.mots_titre", "Autres intitulés à écarter", type="liste_choix", virgules=True,
               exemple="ajouter des mots, séparés par des virgules",
               aide="Un intitulé qui contient un de ces mots est écarté comme métier exclu. Décoche un mot pour "
                    "le retirer."),
     ]},
    {"id": "criteres", "titre": "Critères",
     "intro": "Ce que l'IA vérifie en notant chaque annonce ; laisse vide ce qui t'est égal. Une annonce qui viole "
              "clairement un critère est notée 40 au plus. Aucun montant de salaire ici.",
     "champs": [
         champ("evaluation.criteres", "Critères non négociables", type="criteres", requis=True),
         champ("evaluation.activites", "Activités qui doivent occuper l'essentiel du temps", type="liste_choix",
               virgules=True, exemple="ajouter des activités, séparées par des virgules",
               section="Ce qui rend un poste intéressant"),
         champ("evaluation.interet", "En quelques mots", exemple="un produit utile, de l'autonomie…", large=True),
         champ("evaluation.angles", "Angles de candidature", type="liste_choix", exemple="ajouter un angle",
               aide="Les façons de te présenter : l'IA en propose une par annonce.", section="Pour te présenter"),
     ]},
    # Certifications et langues : remplies à l'import du CV (`completer_depuis_cv`), corrigées ici.
    {"id": "certifications", "titre": "Certifications",
     "intro": "Remplies à l'import de ton CV, dans Ton parcours. Corrige-les ici : elles font foi dans chaque CV.",
     "champs": [
         champ("dossier.certifications", "Certifications obtenues", type="lignes",
               colonnes=[("titre", "Titre de la certification", "Certified Data Professional"),
                         ("organisme", "Organisme", "The Analytics Institute"),
                         ("date", "Obtenue en", "MM.AAAA")],
               aide="Une ligne par certification : son titre, l'organisme qui l'a délivrée et le mois où tu l'as "
                    "obtenue (MM.AAAA, ou l'année seule). Elles remplacent celles du CV maître ; un même titre "
                    "n'est gardé qu'une fois."),
     ]},
    {"id": "langues", "titre": "Langues",
     "intro": "Les langues que tu parles, remplies à l'import de ton CV, dans Ton parcours. Corrige-les ici : "
              "elles font foi dans chaque CV.",
     "champs": [
         champ("dossier.langues", "Langues parlées", type="lignes",
               colonnes=[("langue", "Langue", "Anglais"), ("niveau", "Niveau", "Choisir un niveau", NIVEAUX_LANGUE),
                         ("attestation", "Attesté par (facultatif)", "Cambridge C1 Advanced, 2022")],
               aide="Le niveau est ton estimation, sur l'échelle européenne : pas besoin d'un certificat pour le "
                    "choisir. Si un diplôme ou un test l'atteste, indique-le : sans lui, le CV ne parle d'aucun "
                    "certificat. Une même langue n'est gardée qu'une fois ; ces lignes remplacent celles du CV maître."),
     ]},
    # Le différenciateur vient de l'entretien « Ce qui compte pour toi » ; les règles particulières et le
    # nombre de pages du CV (`dossier.*`) restent lus s'ils sont dans le profil, sans onglet pour les saisir.
    {"id": "objectifs", "titre": "Objectifs de postulation",
     "intro": "Le rythme de postulation à tenir, par semaine, par mois, ou les deux : suivi sur le tableau de "
              "bord, avec un rappel le vendredi si l'objectif de la semaine n'est pas atteint. Vide : pas de suivi.",
     "champs": [
         champ("objectifs.semaine_min", "Objectif par semaine", type="nombre",
               aide="Nombre de postulations, du lundi au dimanche. Facultatif : vide, pas d'objectif."),
         champ("objectifs.mois_min", "Objectif par mois", type="nombre",
               aide="Nombre de postulations dans le mois civil. Facultatif : vide, pas d'objectif."),
     ]},
]
# Les comptes Apple Mail se choisissent sur la page Connecteurs (`poser_comptes_courrier`).
COMPTES_COURRIER = "courrier.comptes"

# Clé du profil → (fichier de config qu'elle remplace, clé dans ce fichier).
SUPERPOSES = {
    "objectifs.semaine_min": ("reglages", "objectifs.semaine_min"),
    "objectifs.mois_min": ("reglages", "objectifs.mois_min"),
    "courrier.comptes": ("reglages", "courrier.comptes"),
    "recherche.cantons": ("criteres", "sourcing.cantons"),
    "recherche.entreprises": ("criteres", "sourcing.entreprises"),
}
POSTES = "recherche.postes"            # requêtes, recherches LinkedIn et mots du pré-tri en sont déduits
# Clés d'avant les postes : lues tant que le profil n'a pas de postes, retirées à son premier enregistrement.
ANCIENNES = ["recherche.requetes", "recherche.familles", "recherche.linkedin_requetes", "recherche.indeed_lieux",
             "recherche.jobup_lieux", "recherche.region", "recherche.linkedin_lieu"]
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

PREMIERS_PAS = "premiers_pas"          # checklist du tableau de bord : affichee · masquee · terminee
PREMIERS_PAS_FAITS = "premiers_pas_faits"   # étapes déjà vues faites : une étape faite ne redevient pas « à faire »

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
    # Le poste passe en tête des postes recherchés ; ses mots deviennent ceux du pré-tri (postes()).
    ps = postes_saisis(p)
    meme = next((x for x in ps if x["intitule"].lower() == poste.lower()), {"intitule": poste, "variantes": []})
    _poser(p, POSTES, [meme] + [x for x in ps if x is not meme])
    for cle in ANCIENNES:
        _retirer(p, cle)
    _poser(p, "recherche.cantons", cantons)
    off = [i for i in (lire(p, "exclusions.desactivees") or []) if i not in REGLES_OBJECTIF] + o["desactive"]
    if off:
        _poser(p, "exclusions.desactivees", off)
    else:
        _retirer(p, "exclusions.desactivees")
    ecrire(p)
    return p


MOTS_VIDES = {"de", "du", "des", "la", "le", "les", "et", "ou", "en", "au", "aux", "pour", "par", "sur", "avec",
              "un", "une", "of", "and", "or", "the", "for", "in", "on", "at", "to", "with",
              "h", "f", "m", "w", "d", "hf", "fh", "mw", "wm", "mwd", "wmd", "e"}


def mots_intitule(poste):
    """Les mots d'un intitulé qui comptent pour le pré-tri : « UX designer (H/F) » → ux, designer."""
    from .prefilter import plier
    mots = re.findall(r"[a-z0-9]+", plier(poste or "").lower())
    return list(dict.fromkeys(m for m in mots if len(m) >= 2 and m not in MOTS_VIDES))


# --- Postes recherchés : ce que le sourcing et le pré-tri en tirent ----------------------


def _intitule(t):
    return re.sub(r"\s+", " ", str(t or "")).strip().strip(",;")[:120]


def normaliser_postes(v):
    """[{intitule, variantes}] : chaque intitulé une seule fois, d'un poste à l'autre ; une chaîne vaut un poste."""
    vus, out = set(), []
    for x in v or []:
        x = {"intitule": x} if isinstance(x, str) else dict(x or {})
        titres = []
        for t in [x.get("intitule")] + list(x.get("variantes") or []):
            t = _intitule(t)
            if t and t.lower() not in vus:
                vus.add(t.lower())
                titres.append(t)
        if titres:
            out.append({"intitule": titres[0], "variantes": titres[1:]})
    return out


def postes_saisis(p=None):
    """Les postes du profil seul (ou ses anciennes requêtes, un poste chacune) ; [] s'il n'en a pas."""
    p = charger() if p is None else p
    v = lire(p, POSTES)
    return normaliser_postes(lire(p, "recherche.requetes") if v is None else v)


def postes_de_base():
    return normaliser_postes(lire(config.lire_yaml("criteres.yaml"), "sourcing.requetes") or [])


def postes(p=None):
    """Les postes recherchés qui s'appliquent : ceux du profil, sinon les requêtes de config/."""
    p = charger() if p is None else p
    return postes_saisis(p) or postes_de_base()


def intitules(ps):
    """Tous les intitulés, variantes comprises : une recherche chacun sur chaque plateforme."""
    return [t for x in ps for t in [x["intitule"]] + list(x.get("variantes") or [])]


def requete_linkedin(x):
    """Un poste, une recherche LinkedIn (un run payant) : ses intitulés entre guillemets, reliés par « OR »."""
    titres = [x["intitule"]] + list(x.get("variantes") or [])
    return titres[0] if len(titres) == 1 else " OR ".join('"%s"' % t.replace('"', "") for t in titres)


# Mots de fonction et de niveau, communs à tous les métiers : « chef », « responsable », « manager »
# se lisent aussi dans « cheffe de cuisine » ou « responsable de ligne de production ». Ils ne
# suffisent pas à dire qu'un intitulé vise un des postes recherchés (constat du 13.11 : 31 annonces
# à trancher, dont une cheffe de partie et des chefs de projet en génie civil).
MOTS_FONCTION = {"chef", "chefs", "cheffe", "cheffes", "responsable", "responsables", "manager", "managers",
                 "management", "head", "lead", "leader", "leaders", "directeur", "directrice", "director",
                 "direction", "coordinateur", "coordinatrice", "coordinator", "specialiste", "specialist",
                 "consultant", "consultante", "expert", "experte", "officer", "executive", "associate",
                 "assistant", "assistante", "adjoint", "adjointe", "charge", "chargee", "gestionnaire",
                 "collaborateur", "collaboratrice", "ingenieur", "ingenieure", "engineer", "analyste",
                 "analyst", "senior", "sr", "junior", "principal", "global", "international", "group",
                 "team", "equipe", "projet", "projets", "project", "projects", "poste", "fonction"}
_FLEXION = "(?:s|e|es|aux|ale|ales)?"       # produit(s), digital(e, es, aux), innovation(s)


def mots_postes(ps):
    """Ce qui, dans les intitulés visés, dit le métier : leurs mots qui ne sont pas de simples mots de
    fonction (« innovation », « ux », « digital », pas « chef » ni « manager »). Un poste fait de mots
    de fonction seuls (« Chef de projet ») reste entier : l'intitulé devra en porter tous les mots.
    Une annonce dont le titre ne touche rien de cette liste est écartée (règle « hors-familles »)."""
    out = []
    for t in intitules(ps):
        mots = mots_intitule(t)
        if not mots:
            continue
        distinctifs = [m for m in mots if m not in MOTS_FONCTION]
        for x in distinctifs or [" ".join(mots)]:
            if x not in out:
                out.append(x)
    return out


def lieux_cantons(p=None):
    """Les noms des cantons choisis (« Vaud »), tels que jobup et Indeed les attendent comme lieu."""
    p = charger() if p is None else p
    cantons = lire(p, "recherche.cantons") or lire(config.lire_yaml("criteres.yaml"), "sourcing.cantons") or []
    return [pays.CANTONS[c] for c in cantons if c in pays.CANTONS]


def region(p=None):
    """La région visée, déduite des cantons choisis : consigne et salaire du marché. Sans canton, la région
    d'avant (champ retiré, effacée au prochain enregistrement de la recherche), sinon le pays du profil."""
    p = charger() if p is None else p
    return (pays.region_des_cantons(lire(p, "recherche.cantons")) or lire(p, "recherche.region")
            or identite(p).get("pays") or "")


def niveau_depuis_cv(niveau):
    """Le niveau que l'import du CV en déduit, posé s'il n'y en a pas encore : un niveau déjà écrit, corrigé
    à la main, reste. Renvoie le niveau posé, "" sinon."""
    niveau = niveau_connu(niveau)
    p = copy.deepcopy(charger())
    if not niveau or lire(p, "recherche.niveau"):
        return ""
    if not existe():
        p[ACCUEIL] = "en_cours"
    _poser(p, "recherche.niveau", niveau)
    ecrire(p)
    return niveau


def lieu_linkedin(p=None):
    """Où LinkedIn cherche : la localité de l'adresse du profil et son pays, en anglais comme LinkedIn
    l'écrit (« Lausanne, Switzerland ») ; "" sans localité."""
    p = charger() if p is None else p
    i = identite(p)
    ville = i.get("ville") or ""
    pays_ = i.get("pays") or pays.pays_detecte()
    return ", ".join(x for x in (ville, pays.EN_ANGLAIS.get(pays_, pays_)) if x) if ville else ""


def lieux_indeed(p=None):
    """Où lancer chaque recherche Indeed : les cantons choisis, sinon la localité du profil."""
    p = charger() if p is None else p
    ville = identite(p).get("ville")
    return lieux_cantons(p) or ([ville] if ville else [])


def postes_du_formulaire(f, cle=POSTES):
    """Les lignes du champ : `cle.intitule` et `cle.variantes` (formulaire web, une ligne par poste),
    sinon `cle` lui-même, liste de postes ou texte « intitulé | variante, variante » par ligne."""
    brut = f.get(cle)
    if isinstance(brut, list):
        return normaliser_postes(brut)
    if isinstance(brut, str):
        lignes = [l.split("|", 1) + [""] for l in brut.splitlines()]
        return normaliser_postes({"intitule": a, "variantes": re.split(r"[,;]", b)} for a, b, *_ in lignes)
    liste = lambda n: (f.getlist(n) if hasattr(f, "getlist") else [f.get(n)] if isinstance(f.get(n), str)
                       else list(f.get(n) or []))
    intit, var = liste(cle + ".intitule"), liste(cle + ".variantes")
    var += [""] * (len(intit) - len(var))
    return normaliser_postes({"intitule": i, "variantes": re.split(r"[,;]", v or "")} for i, v in zip(intit, var))


# Les tâches de la checklist : chacune est un seul écran, avec les seuls champs qu'elle demande.
TACHES = {
    "identite": {"titre": "Tes coordonnées", "etapes": ["identite"], "icone": "identite",
                 "intro": "Elles figurent en tête de tes CV et de tes lettres, et servent à pré-remplir "
                          "les formulaires des employeurs. Rien n'est envoyé sans toi, et l'IA ne les voit "
                          "jamais : elle écrit avec des balises, remplacées sur ton Mac."},
    "criteres": {"titre": "Ce qui compte pour toi", "etapes": ["criteres"], "icone": "criteres", "entretien": True,
                 "intro": "L'IA note chaque annonce d'après ces critères, comme tu le ferais. "
                          "Tu pourras les ajuster quand tu veux."},
    "dossier": {"titre": "Ton parcours", "etapes": [], "icone": "dossier", "entretien": True,
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
    un de ses champs a été enregistré. Une étape vue faite une fois (noter_premiers_pas) le reste :
    l'IA qui se déconnecte ne ramène pas la checklist, le tableau de bord le dit autrement.
    """
    p = charger() if p is None else p
    if p.get(PREMIERS_PAS) != "affichee":
        return []
    faits = set(p.get(PREMIERS_PAS_FAITS) or [])
    return [t[:5] + (bool(t[5]) or t[0] in faits,) for t in _taches_checklist(p, ia_connectee)]


def noter_premiers_pas(pas):
    """Retient les étapes faites de la checklist ; toutes faites : « Bien démarrer » est terminé et ne
    revient plus, même si une étape (la connexion de l'IA) se défait ensuite."""
    p = copy.deepcopy(charger())
    if p.get(PREMIERS_PAS) != "affichee":
        return
    if all(x[5] for x in pas):
        p[PREMIERS_PAS] = "terminee"
        p.pop(PREMIERS_PAS_FAITS, None)
        ecrire(p)
        return
    faits = sorted({*(p.get(PREMIERS_PAS_FAITS) or []), *(x[0] for x in pas if x[5])})
    if faits != sorted(p.get(PREMIERS_PAS_FAITS) or []):
        p[PREMIERS_PAS_FAITS] = faits
        ecrire(p)


def _taches_checklist(p, ia_connectee):
    ids_manquants = {e for e, _ in manquants(p)}
    saisie = lambda *etapes: all(e not in ids_manquants for e in etapes) and any(
        lire(p, c["cle"]) not in (None, "", []) for e in etapes for c in etape(e)["champs"])
    recherche = bool(postes_saisis(p)) and bool(lire(p, "recherche.cantons"))
    return [
        ("ia", "Connecter ton IA", "Pour noter les annonces et écrire tes dossiers", "1 min", "/ia", ia_connectee),
        ("recherche", "Définir ta recherche", "Ce que la plateforme cherche chaque matin", "1 min", "/profil/recherche", recherche),
        ("identite", "Tes coordonnées", "En tête de tes CV et lettres, et pour les formulaires", "2 min", "/premiers-pas/identite", saisie("identite")),
        ("dossier", "Ton parcours", "Ton CV, puis un entretien avec l'IA sur chaque poste : des CV et des lettres "
         "fidèles à ton expérience", "15 min", "/premiers-pas/dossier", _parcours_termine()),
        ("criteres", "Ce qui compte pour toi", "Quelques questions à cliquer, pour que l'IA note les annonces "
         "comme tu le ferais", "5 min", "/premiers-pas/criteres", saisie("criteres")),
    ]


def _parcours_termine():
    from . import parcours
    try:
        return parcours.termine()
    except Exception:
        return False


# --- Ce que l'interface montre : avancement de la configuration et options -------------

# Fonctions au choix de la personne (page « Fonctions »).
OPTIONS = [
    {"id": "jobroom", "titre": "Preuves de recherche Job-Room",
     "description": "Pour l'ORP : la plateforme prépare la saisie de chaque postulation dans Job-Room et te rappelle les délais."},
]
# Le suivi des objectifs n'a pas d'interrupteur : il suit les objectifs saisis dans « Ma recherche ».
OPTION_OBJECTIFS = "objectifs"

# Ce que chaque fonction demande d'avoir configuré (identifiants de la checklist).
DEBLOCAGES = [
    {"id": "offres", "titre": "Voir les offres trouvées", "demande": ["recherche"],
     "description": "Les annonces trouvées chaque matin sur tes sources, triées par le pré-tri."},
    {"id": "noter", "titre": "La note de chaque annonce", "demande": ["ia", "criteres"],
     "description": "L'IA note chaque annonce d'après ce qui compte pour toi."},
    {"id": "postuler", "titre": "Postuler : CV, lettre et suivi", "demande": ["ia", "identite", "dossier"],
     "description": "Un CV et une lettre préparés pour chaque annonce retenue, puis le suivi des réponses."},
]


def progressif(p=None):
    """Nouvel utilisateur : l'interface s'ouvre au fil de la configuration. Un profil
    d'avant l'accueil (installation de développement) a tout, tout de suite."""
    p = charger() if p is None else p
    return p.get(PREMIERS_PAS) is not None or p.get(ACCUEIL) is not None


def option(nom, p=None):
    if nom == OPTION_OBJECTIFS:                            # actif dès qu'un objectif est saisi
        return any(objectifs_rythme().values())
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
    out[OPTION_OBJECTIFS] = option(OPTION_OBJECTIFS, p)
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
    """Les objectifs seuls (entretien « Ce qui compte pour toi ») : chacun facultatif, vide = aucun."""
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


def poser_comptes_courrier(texte):
    """Les comptes Apple Mail où chercher les réponses des employeurs (page Connecteurs), un par ligne.
    Les mêmes que config/ : retirés du profil, le fichier de config reste maître."""
    vus, comptes = set(), []
    for ligne in str(texte or "").splitlines():
        ligne = re.sub(r"\s+", " ", ligne).strip()
        if ligne and ligne.casefold() not in vus:
            vus.add(ligne.casefold())
            comptes.append(ligne)
    p = copy.deepcopy(charger())
    if _egal_base(COMPTES_COURRIER, comptes):
        _retirer(p, COMPTES_COURRIER)
    else:
        _poser(p, COMPTES_COURRIER, comptes)
    ecrire(p)
    return comptes


def comptes_courrier(p=None):
    return list(effectif(COMPTES_COURRIER, p) or [])


def masquer_premiers_pas():
    p = copy.deepcopy(charger())
    if p.get(PREMIERS_PAS):
        p[PREMIERS_PAS] = "masquee"
        ecrire(p)


def existe():
    return config.chemin_profil().exists()


# Clés qui ne s'appliquent plus : ignorées à la lecture, donc retirées du fichier au prochain enregistrement.
# Règles particulières et pages du CV : l'onglet Dossier n'existe plus ; objectifs : plus d'interrupteur.
OBSOLETES = ["dossier.regles", "dossier.pages_cv", "interface.objectifs"]


@lru_cache
def charger():
    p = config.chemin_profil()
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for cle in OBSOLETES:
        _retirer(d, cle)
    return d


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


def familles_en_motif(mots):
    """Le motif « hors-familles » du pré-tri, d'après `mots_postes` : un mot distinctif, au pluriel ou au
    féminin près quand il est assez long (« produits », « digitales ») ; un poste entier (« chef de
    projet ») exige chacun de ses mots, dans n'importe quel ordre."""
    simples = [m + (_FLEXION if len(m) >= 4 else "") for m in mots if " " not in m]
    entiers = ["".join(r"(?=.*\b%s\b)" % w for w in m.split()) for m in mots if " " in m]
    return "|".join(([r"\b(%s)\b" % "|".join(simples)] if simples else []) + entiers)


def base(cle):
    """La valeur par défaut d'une clé superposée, lue dans config/ (sans le profil)."""
    if cle == POSTES:
        return postes_de_base()
    fichier, cible = SUPERPOSES[cle]
    return lire(config.lire_yaml(FICHIERS_CONFIG[fichier]), cible)


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
        _poser(out, cible, v)
    ps = postes_saisis(p)
    if fichier == "criteres" and ps:
        _poser(out, "sourcing.requetes", intitules(ps))
        _poser(out, "sourcing.linkedin_requetes", [requete_linkedin(x) for x in ps])
        mots = mots_postes(ps)
        out["familles_cibles"] = familles_en_motif(mots) if mots else ""
    if fichier == "reglages" and not lire(out, "indeed.lieux"):
        _poser(out, "indeed.lieux", lieux_indeed(p))
    if fichier == "criteres" and lire(p, "recherche.cantons"):
        _regle_cantons(out, lire(p, "recherche.cantons"))
        _poser(out, "sourcing.jobup_lieux", lieux_cantons(p))      # jobup cherche canton par canton
    if fichier == "criteres" and lieu_linkedin(p):
        _poser(out, "sourcing.linkedin_lieu", lieu_linkedin(p))    # l'adresse du profil
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
    if cle == POSTES:
        return postes(p)
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
    if c["type"] == "criteres":
        return lignes_criteres(criteres_structures(p))
    if c["type"] == "postes":
        return [x["intitule"] + (" (%s)" % ", ".join(x["variantes"]) if x["variantes"] else "") for x in v]
    return v


def manquants(p=None):
    """[(étape, libellé)] des champs obligatoires encore vides."""
    p = charger() if p is None else p
    return [(e["id"], c["libelle"]) for e in etapes() for c in e["champs"]
            if c["requis"] and (effectif(c["cle"], p) in ("", [], None, {}) or
                                c["type"] == "criteres" and not lignes_criteres(criteres_structures(p)))]


# --- Ce que les consignes et les fichiers en tirent ---------------------------------


def prenom():
    return identite().get("prenom") or "la personne"


def nom_complet():
    i = identite()
    return " ".join(x for x in (i.get("prenom"), i.get("nom")) if x) or "la personne"


def criteres_evaluation():
    """[(nom, précision)] : « trajet : 45 minutes au plus » → ("trajet", "45 minutes au plus")."""
    out = []
    for ligne in lignes_criteres(criteres_structures()):
        nom, _, precision = ligne.partition(":")
        out.append((nom.strip(), precision.strip()))
    return out


def interet_evaluation(p=None):
    """Ce qui rend un poste intéressant, pour la consigne : les activités, puis le texte libre."""
    p = charger() if p is None else p
    morceaux = [", ".join(lire(p, "evaluation.activites") or []), lire(p, "evaluation.interet") or ""]
    return " ; ".join(x for x in morceaux if x)


# --- Critères structurés : un champ par élément ------------------------------------
#
# Ce que l'IA vérifie en notant chaque annonce. Chaque critère a son champ : un seul choix (« un »), plusieurs
# (« plusieurs »), un nombre, un texte ou une liste libre. Le profil les garde sous `evaluation.criteres`,
# {clé: valeur}, seulement ceux qui sont remplis ; `lignes_criteres` les écrit en phrases pour la consigne de
# notation. Un profil d'avant (une liste de phrases) se relit avec `criteres_depuis_lignes`.

# Formes de contrat : (libellé de la question, libellé court du critère).
CONTRATS = {
    "cdi": ("CDI, durée indéterminée", "CDI"),
    "cdd-2ans": ("CDD de 2 ans ou plus", "CDD de 2 ans ou plus"),
    "cdd-long": ("CDD de 12 à 24 mois", "CDD de 12 à 24 mois"),
    "cdd-moyen": ("CDD de 6 à 12 mois", "CDD de 6 à 12 mois"),
    "cdd-court": ("CDD de moins de 6 mois", "CDD de moins de 6 mois"),
    "cdd-cdi": ("CDD pouvant déboucher sur un CDI", "CDD pouvant déboucher sur un CDI"),
    "remplacement": ("Remplacement (congé maternité, longue absence)", "remplacement"),
    "mission": ("Mission ou intérim", "mission ou intérim"),
    "freelance": ("Freelance ou mandat", "freelance ou mandat"),
}
LANGUES = [("français", "Français"), ("anglais", "Anglais"), ("allemand", "Allemand"), ("italien", "Italien")]
ORGANISATIONS = [("start-up", "Start-up"), ("PME", "PME"), ("grande entreprise", "Grande entreprise"),
                 ("secteur public", "Secteur public"), ("ONG ou associatif", "ONG ou associatif")]
TRAJETS = [("30", "30 minutes"), ("45", "45 minutes"), ("60", "1 heure"), ("90", "1 h 30")]

# Les critères, par section (« section » : le titre montré avant le premier critère de chaque groupe). Les mêmes
# libellés que les questions de « Ce qui compte pour toi » (ce_qui_compte.QUESTIONS), qui les reprennent.
TELETRAVAIL = [("1", "1 jour par semaine"), ("2", "2 jours par semaine"), ("3", "3 jours par semaine"),
               ("5", "Entièrement à distance")]
ENCADREMENT = [("non", "Pas d'équipe à encadrer"), ("oui", "Une équipe à encadrer")]
DEPLACEMENTS = [("non", "Pas de déplacements"), ("occasionnels", "Déplacements occasionnels")]
CRITERES = [
    {"cle": "trajet", "libelle": "Trajet au plus, porte à porte", "type": "un", "vide": "Peu importe",
     "choix": TRAJETS, "section": "Conditions de travail"},
    {"cle": "trajet_depuis", "libelle": "Trajet depuis", "type": "texte", "exemple": "ta localité"},
    {"cle": "teletravail", "libelle": "Télétravail, au moins", "type": "un", "vide": "Pas d'exigence",
     "choix": TELETRAVAIL},
    {"cle": "taux_min", "libelle": "Taux d'activité, au moins (%)", "type": "nombre", "exemple": "80",
     "aide": "Sous 100 %, le tri du matin garde aussi les annonces à temps partiel."},
    {"cle": "taux_max", "libelle": "Taux d'activité, au plus (%)", "type": "nombre", "exemple": "100"},
    {"cle": "heures_max", "libelle": "Heures par semaine, au plus", "type": "nombre", "exemple": "42"},
    {"cle": "vacances_min", "libelle": "Semaines de vacances, au moins", "type": "nombre", "exemple": "5"},
    {"cle": "deplacements", "libelle": "Déplacements", "type": "un", "vide": "Peu importe", "choix": DEPLACEMENTS},
    {"cle": "contrats", "libelle": "Formes de contrat acceptées", "type": "plusieurs",
     "choix": [(k, v[0]) for k, v in CONTRATS.items()], "section": "Le poste",
     "aide": "Aucune ou toutes cochées : pas de critère. Sans CDD ni mission, le tri du matin les écarte d'office."},
    {"cle": "langues", "libelle": "Langues de travail", "type": "plusieurs", "choix": LANGUES,
     "aide": "Sans l'allemand, le tri du matin écarte les annonces en allemand."},
    {"cle": "encadrement", "libelle": "Encadrement", "type": "un", "vide": "Peu importe", "choix": ENCADREMENT},
    {"cle": "organisations", "libelle": "Types d'organisation", "type": "plusieurs", "choix": ORGANISATIONS,
     "aide": "Aucun ou tous cochés : peu importe."},
    {"cle": "secteurs_exclus", "libelle": "Secteurs exclus", "type": "liste", "virgules": True,
     "exemple": "ajouter des secteurs, séparés par des virgules"},
    {"cle": "a_eviter", "libelle": "À éviter", "type": "texte", "exemple": "trop de réunions, un management distant…",
     "section": "À éviter, et le reste"},
    {"cle": "autres", "libelle": "Autres critères", "type": "liste",
     "exemple": "ajouter un critère ; une précision facultative après « : »"},
]
CRITERE = {c["cle"]: c for c in CRITERES}


def _propre(v, n=300):
    from .parcours import sans_montants
    return sans_montants(re.sub(r"\s+", " ", str(v or "")).strip())[:n]


def valeurs_formulaire(f, nom):
    """Les valeurs d'un nom de formulaire : plusieurs cases (formulaire web) ou une par ligne (dictionnaire)."""
    brut = f.getlist(nom) if hasattr(f, "getlist") else f.get(nom, [])
    brut = [brut] if isinstance(brut, str) else list(brut or [])
    return [x for v in brut for x in str(v).splitlines()]


def ajouts_formulaire(f, nom, virgules=False):
    """Ce que le champ « ajouter » d'une liste à pilules apporte (`nom.ajout`)."""
    return [x for v in valeurs_formulaire(f, nom + ".ajout") for x in re.split(r"[;,]" if virgules else r";", v)]


def criteres_du_formulaire(f):
    """Les champs `critere.<clé>` d'un formulaire → {clé: valeur brute}, à valider par normaliser_criteres."""
    d = {}
    for c in CRITERES:
        nom = "critere." + c["cle"]
        if c["type"] in ("plusieurs", "liste"):
            d[c["cle"]] = valeurs_formulaire(f, nom) + (ajouts_formulaire(f, nom, c.get("virgules"))
                                                         if c["type"] == "liste" else [])
        else:
            d[c["cle"]] = (valeurs_formulaire(f, nom) or [""])[0]
    return d


def normaliser_criteres(d):
    """{clé: valeur} validé : seulement les critères remplis, chacun dans sa forme. Lève ProfilInvalide."""
    out, erreurs = {}, []
    for c in CRITERES:
        k, t, v = c["cle"], c["type"], (d or {}).get(c["cle"])
        if t == "un":
            v = str(v if v is not None else "").strip()
            if v and v not in dict(c["choix"]):
                erreurs.append("%s : choisir dans la liste" % c["libelle"])
            elif v:
                out[k] = v
        elif t == "plusieurs":
            vus = {str(x) for x in (v if isinstance(v, list) else [v] if v else [])}
            if (gardes := [x for x, _ in c["choix"] if x in vus]):
                out[k] = gardes
        elif t == "nombre":
            v = str(v if v is not None else "").strip()
            if v and (not v.isdigit() or int(v) > 500):
                erreurs.append("%s : un nombre entier" % c["libelle"])
            elif v:
                out[k] = int(v)
        elif t == "texte":
            if (v := _propre(v)):
                out[k] = v
        else:                                                  # liste libre
            items = v if isinstance(v, list) else str(v or "").splitlines()
            if (items := [x for x in dict.fromkeys(_propre(x, 200) for x in items) if x][:20]):
                out[k] = items
    if "taux_min" in out or "taux_max" in out:
        mini, maxi = out.get("taux_min"), out.setdefault("taux_max", 100)
        if mini is None:
            erreurs.append("taux d'activité : indique le minimum")
        elif not 10 <= mini <= maxi <= 100:
            erreurs.append("taux d'activité : entre 10 et 100 %, le minimum avant le maximum")
    if erreurs:
        raise ProfilInvalide(" ; ".join(erreurs))
    return out


def lignes_criteres(d, avec_cles=False):
    """Les critères en phrases, pour la consigne de notation : « trajet : 45 minutes au plus depuis … ».
    Un critère qui laisse tout passer (tous les contrats, 100 %) n'en donne pas. `avec_cles` : [(clé, phrase)]."""
    d, out = d or {}, []
    noms = lambda cle: dict(CRITERE[cle]["choix"])
    if d.get("trajet") in noms("trajet"):
        out.append(("trajet", "trajet : %s au plus%s" % (noms("trajet")[d["trajet"]],
                                                         " depuis " + d["trajet_depuis"] if d.get("trajet_depuis") else "")))
    if (t := d.get("teletravail")) in noms("teletravail"):
        out.append(("teletravail", "télétravail : entièrement à distance" if t == "5" else
                    "télétravail : %s au moins" % noms("teletravail")[t]))
    if (mini := d.get("taux_min")) and (mini, d.get("taux_max", 100)) != (100, 100):
        maxi = d.get("taux_max", 100)
        out.append(("taux_min", "taux d'activité : %s" % ("%d %%" % mini if mini == maxi else "%d à %d %%" % (mini, maxi))))
    if (vs := [c for c in CONTRATS if c in (d.get("contrats") or [])]) and len(vs) < len(CONTRATS):
        out.append(("contrats", "contrat : durée indéterminée seulement" if vs == ["cdi"] else
                    "contrat : %s" % ", ".join(CONTRATS[c][1] for c in vs)))
    if d.get("langues"):
        out.append(("langues", "langues de travail : %s" % ", ".join(d["langues"])))
    if d.get("encadrement") in ("non", "oui"):
        out.append(("encadrement", "encadrement : pas d'équipe à encadrer" if d["encadrement"] == "non" else
                    "encadrement : une équipe à encadrer"))
    if (vs := d.get("organisations")) and len(vs) < len(ORGANISATIONS):
        out.append(("organisations", "organisation : %s" % " ou ".join(vs)))
    if d.get("secteurs_exclus"):
        out.append(("secteurs_exclus", "secteurs exclus : %s" % ", ".join(d["secteurs_exclus"])))
    if d.get("heures_max"):
        out.append(("heures_max", "temps de travail : %s heures par semaine au plus" % d["heures_max"]))
    if d.get("vacances_min"):
        out.append(("vacances_min", "vacances : %s semaines au moins" % d["vacances_min"]))
    if d.get("deplacements") in ("non", "occasionnels"):
        out.append(("deplacements", "déplacements : aucun" if d["deplacements"] == "non" else
                    "déplacements : occasionnels seulement"))
    if d.get("a_eviter"):
        out.append(("a_eviter", "à éviter : %s" % d["a_eviter"]))
    out += [("autres", x) for x in d.get("autres") or []]
    return out if avec_cles else [x for _, x in out]


_ORGS = "|".join(re.escape(v) for v, _ in sorted(ORGANISATIONS, key=lambda o: -len(o[0])))


def criteres_depuis_lignes(lignes):
    """Un profil d'avant les champs (une phrase par critère) → {clé: valeur}. Les phrases que les champs ne
    savent pas dire restent telles quelles dans « Autres critères »."""
    d, autres = {}, []
    inverse = lambda cle: {lib: v for v, lib in CRITERE[cle]["choix"]}
    contrats = {v[1]: k for k, v in CONTRATS.items()}
    for ligne in (str(x).strip() for x in lignes or []):
        nom, _, reste = (x.strip() for x in ligne.partition(":"))
        m = None
        if nom == "trajet" and (m := re.fullmatch(r"(.+?) au plus(?: depuis (.+))?", reste)) \
                and m.group(1) in inverse("trajet"):
            d["trajet"] = inverse("trajet")[m.group(1)]
            if m.group(2):
                d["trajet_depuis"] = m.group(2)
        elif nom == "télétravail" and reste == "entièrement à distance":
            d["teletravail"] = "5"
        elif nom == "télétravail" and (m := re.fullmatch(r"([123]) jours? par semaine au moins", reste)):
            d["teletravail"] = m.group(1)
        elif nom == "taux d'activité" and (m := re.fullmatch(r"(\d+)(?: à (\d+))? %", reste)):
            d["taux_min"], d["taux_max"] = int(m.group(1)), int(m.group(2) or m.group(1))
        elif nom == "contrat" and reste == "durée indéterminée seulement":
            d["contrats"] = ["cdi"]
        elif nom == "contrat" and reste and all(x in contrats for x in reste.split(", ")):
            d["contrats"] = [contrats[x] for x in reste.split(", ")]
        elif nom == "langues de travail" and reste and all(x in dict(LANGUES) for x in reste.split(", ")):
            d["langues"] = reste.split(", ")
        elif nom == "encadrement" and reste in ("pas d'équipe à encadrer", "une équipe à encadrer"):
            d["encadrement"] = "non" if reste.startswith("pas") else "oui"
        elif nom == "organisation" and re.fullmatch("(?:%s)(?: ou (?:%s))*" % (_ORGS, _ORGS), reste):
            d["organisations"] = [v for v, _ in ORGANISATIONS if v in re.findall(_ORGS, reste)]
        elif nom == "secteurs exclus" and reste:
            d["secteurs_exclus"] = [x.strip() for x in reste.split(",") if x.strip()]
        elif nom == "temps de travail" and (m := re.fullmatch(r"(\d+) heures par semaine au plus", reste)):
            d["heures_max"] = int(m.group(1))
        elif nom == "vacances" and (m := re.fullmatch(r"(\d+) semaines au moins", reste)):
            d["vacances_min"] = int(m.group(1))
        elif nom == "déplacements" and reste in ("aucun", "occasionnels seulement"):
            d["deplacements"] = "non" if reste == "aucun" else "occasionnels"
        elif nom == "à éviter" and reste:
            d["a_eviter"] = reste
        elif ligne:
            autres.append(ligne)
    if autres:
        d["autres"] = autres
    return d


def criteres_structures(p=None):
    """Les critères du profil, {clé: valeur} ; un profil d'avant les champs est relu phrase par phrase."""
    v = lire(charger() if p is None else p, "evaluation.criteres")
    if isinstance(v, list):
        return criteres_depuis_lignes(v)
    return dict(v) if isinstance(v, dict) else {}


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


# --- Écriture -----------------------------------------------------------------------


def _convertir(c, brut):
    t = c["type"]
    if t == "regles":                              # cases cochées : liste (formulaire) ou une par ligne
        cochees = set(brut if isinstance(brut, list) else str(brut or "").split())
        return [i for i in _ids_au_choix() if i in cochees]
    if t in ("liste", "liste_choix", "cantons", "lignes"):
        vus, out = set(), []
        for ligne in str(brut or "").splitlines():
            ligne = re.sub(r"\s+", " ", ligne).strip()
            if t == "lignes":
                ligne = _ligne_en_colonnes(c, ligne)
            if c["cle"] == "recherche.cantons":
                ligne = ligne.upper()
                if ligne and ligne not in pays.CANTONS:
                    raise ProfilInvalide("%s : « %s » n'est pas une abréviation de canton" % (c["libelle"], ligne))
            elif c["cle"] == "exclusions.mots_titre":
                from .prefilter import plier
                ligne = plier(ligne).replace("|", " ").strip()
            cle = ligne
            if c["cle"] in LISTES_CV.values():       # même certification ou langue, autre date ou niveau : la première reste
                cle = _nom_dans_liste(ligne)
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
    if c["cle"] == "recherche.niveau":
        v = niveau_connu(v) or v
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


# --- Champs en lignes (certifications, langues) : une case par colonne -----------------


def mois_annee(v):
    """« 3/2024 », « 03.2024 », « 2024-03 » → « 03.2024 » ; une année seule reste l'année ; None si ce n'est
    pas une date de ce genre."""
    v = re.sub(r"\s+", "", str(v or ""))
    if not v:
        return ""
    if re.fullmatch(r"(19|20)\d\d", v):
        return v
    m = re.fullmatch(r"(\d{1,2})[./-](\d{4})", v) or re.fullmatch(r"(\d{4})[./-](\d{1,2})", v)
    if not m:
        return None
    mo, a = (m.group(1), m.group(2)) if len(m.group(2)) == 4 else (m.group(2), m.group(1))
    return "%02d.%s" % (int(mo), a) if 1 <= int(mo) <= 12 and 1900 <= int(a) <= 2100 else None


def colonnes_de_ligne(c, ligne):
    """Une ligne enregistrée « a | b | c » → ses cases, autant que de colonnes (la dernière garde le reste)."""
    n = len(c["colonnes"]) or 1
    morceaux = [x.strip() for x in str(ligne or "").split("|", n - 1)]
    return morceaux + [""] * (n - len(morceaux))


def _ligne_en_colonnes(c, ligne):
    """Une ligne remise en forme : cases nettoyées, date en MM.AAAA quand elle se lit, cases vides de fin retirées."""
    cases = colonnes_de_ligne(c, ligne)
    for i, (sous, *_) in enumerate(c["colonnes"]):
        if sous == "date":
            cases[i] = mois_annee(cases[i]) or cases[i]     # import du CV : une date illisible reste telle quelle
        elif sous == "niveau":
            cases[i] = niveau_langue(cases[i])
    while cases and not cases[-1]:
        cases.pop()
    return " | ".join(cases) if cases and cases[0] else ""


def lignes_du_formulaire(f, c):
    """Les lignes d'un champ en lignes : `cle.<colonne>` (formulaire web, une valeur par ligne ; ou un texte, une
    ligne par retour) → [[case, …]], lignes vides retirées. Lève ProfilInvalide pour une ligne sans son premier
    champ ou une date illisible. Sans ces champs, `cle` lui-même : un texte « a | b | c » par ligne."""
    cle = c["cle"]
    if not any((cle + "." + sous) in f for sous, *_ in c["colonnes"]):
        return [colonnes_de_ligne(c, l) for l in str(f.get(cle) or "").splitlines() if l.strip()]
    def valeurs(n):
        if hasattr(f, "getlist"):
            return [str(x) for x in f.getlist(n)]
        v = f.get(n)
        return str(v).split("\n") if isinstance(v, str) else [str(x) for x in v or []]
    cols = [valeurs(cle + "." + sous) for sous, *_ in c["colonnes"]]
    n = max(len(x) for x in cols)
    lignes = []
    for i in range(n):
        cases = [re.sub(r"\s+", " ", (col[i] if i < len(col) else "")).replace("|", "/").strip() for col in cols]
        if not any(cases):
            continue
        if not cases[0]:
            raise ProfilInvalide("%s : une ligne n'a pas de %s" % (c["libelle"], c["colonnes"][0][1].lower()))
        for j, (sous, lib, *_) in enumerate(c["colonnes"]):
            if sous == "date" and cases[j]:
                d = mois_annee(cases[j])
                if d is None:
                    raise ProfilInvalide("%s : « %s » n'est pas une date MM.AAAA" % (c["libelle"], cases[j]))
                cases[j] = d
        lignes.append(cases)
    return lignes


# --- Certifications et langues : tirées du CV importé (parcours) ----------------------

LISTES_CV = {"certification": "dossier.certifications", "langue": "dossier.langues"}
NOMS_LISTES_CV = {"certification": ("certification", "certifications"), "langue": ("langue", "langues")}


def _nom_dans_liste(ligne):
    """Ce qui identifie une ligne « nom | … » : le nom, sans accents, majuscules ni ponctuation."""
    from .prefilter import plier
    return re.sub(r"[^a-z0-9]+", " ", plier(str(ligne).split("|")[0])).strip()


def ligne_du_cv(e):
    """Un élément du parcours (certification ou langue) → sa ligne dans l'onglet du profil."""
    if e["genre"] == "langue":
        morceaux = [e.get("titre"), e.get("detail")]
    else:
        morceaux = [e.get("titre"), e.get("organisation"), e.get("fin") or e.get("debut")]
    while morceaux and not (morceaux[-1] or "").strip():
        morceaux.pop()
    return " | ".join((m or "").strip() for m in morceaux)


def _champ_cle(cle):
    return next(c for e in ETAPES for c in e["champs"] if c["cle"] == cle)


def absents_du_cv(elements, p=None):
    """{genre: [lignes]} des certifications et langues du parcours qui manquent à leur onglet."""
    p = charger() if p is None else p
    out = {}
    for genre, cle in LISTES_CV.items():
        vus = {_nom_dans_liste(x) for x in lire(p, cle) or []}
        for e in elements:
            if e["genre"] == genre and (n := _nom_dans_liste(e["titre"])) and n not in vus:
                vus.add(n)
                out.setdefault(genre, []).append(ligne_du_cv(e))
    return out


def completer_depuis_cv(elements):
    """Ajoute à leur onglet les certifications et langues du CV qui n'y sont pas encore. Ce qui y est déjà
    reste tel quel : corrigé à la main, il fait foi. Renvoie {genre: nombre ajouté}."""
    p = copy.deepcopy(charger())
    ajouts = absents_du_cv(elements, p)
    if not ajouts:
        return {}
    if not existe():
        p[ACCUEIL] = "en_cours"
    for genre, lignes in ajouts.items():
        cle = LISTES_CV[genre]
        _poser(p, cle, _convertir(_champ_cle(cle), "\n".join(list(lire(p, cle) or []) + lignes)))
    ecrire(p)
    return {g: len(l) for g, l in ajouts.items()}


def en_saisie(c, v):
    """La valeur telle qu'elle s'écrit dans le champ : sans le préfixe affiché devant."""
    v = "" if v is None else str(v)
    if c["cle"] == "recherche.niveau":              # « senior » écrit avant la liste : l'option « Senior »
        return niveau_connu(v) or v
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
    return v == base(cle)


def etapes():
    """Les étapes de l'assistant (le parcours vit dans la base, `parcours.py`)."""
    return ETAPES


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
            if c["type"] == "criteres":
                brut = formulaire.get(c["cle"])            # un dictionnaire (questionnaire), sinon les champs
                v = normaliser_criteres(brut if isinstance(brut, dict) else
                                        criteres_depuis_lignes(str(brut).splitlines()) if isinstance(brut, str)
                                        else criteres_du_formulaire(formulaire))
            elif c["type"] == "liste_choix":
                v = _convertir(c, "\n".join(valeurs_formulaire(formulaire, c["cle"])
                                            + ajouts_formulaire(formulaire, c["cle"], c["virgules"])))
            elif c["type"] == "postes":
                v = postes_du_formulaire(formulaire, c["cle"])
            elif c["type"] == "lignes":
                v = _convertir(c, "\n".join(" | ".join(x) for x in lignes_du_formulaire(formulaire, c)))
            elif c["type"] == "cantons":              # pilules cochées (formulaire web), sinon une par ligne
                v = _convertir(c, "\n".join(valeurs_formulaire(formulaire, c["cle"])))
            else:
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
        elif c["requis"] and (v in ("", [], None) or c["type"] == "criteres" and not lignes_criteres(v)):
            erreurs.append("%s : obligatoire" % c["libelle"])
        elif (c["cle"] in SUPERPOSES or c["cle"] == POSTES) and _egal_base(c["cle"], v):
            _retirer(p, c["cle"])               # même valeur que config/ : le fichier de config reste maître
        elif v in ("", None, {}) or (v == [] and c["cle"] not in SUPERPOSES):
            _retirer(p, c["cle"])
        else:
            _poser(p, c["cle"], v)
    if erreurs:
        raise ProfilInvalide(" ; ".join(erreurs))
    if ident == "recherche":
        for cle in ANCIENNES:                   # repris dans les postes, ou déduits d'eux
            _retirer(p, cle)
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
