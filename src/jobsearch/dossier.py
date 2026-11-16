"""Dossier de candidature, produit dès le clic sur Go.

Suit la méthode « Candidature ciblée » livrée avec l'application : analyse (angle
proposé en tête, exigences, écarts), CV adapté (titre exact de l'annonce,
puces du CV maître), lettre (langue de l'annonce, quatre paragraphes, au plus
une anecdote, prête à envoyer), fiche d'envoi. Rien d'inventé : un chiffre
vient de Preuves.md ou n'existe pas.

Quatre tâches du moteur IA (Claude Code, abonnement de l'utilisateur), profil
et méthode en contexte fixe, donc en cache. Le dossier est écrit
dans `data/dossiers/`, mesuré (couverture des
mots-clés, `motscles.py`), contrôlé (CQ), puis rendu en Word. Un contrôle qui
échoue ne bloque rien : le dossier passe « à revoir » avec la liste des points.

La méthode, les modèles Word et la mesure sont ceux livrés avec l'application
(`modeles/`, `ats.py`) ; le profil vient de la base Parcours (`parcours.py`).
"""

import datetime as dt
import json
import re
import threading
from pathlib import Path

from . import adresses, evaluation, moteur, profil
from .config import DATA
from .db import Registre, maintenant
from .prefilter import langue

SCHEMA = """
CREATE TABLE IF NOT EXISTS dossiers (
  empreinte TEXT PRIMARY KEY, etat TEXT NOT NULL, debut TEXT, fin TEXT, chemin TEXT,
  couverture INTEGER, controles TEXT, erreur TEXT, etape TEXT
);
"""
MOTS_PAR_PAGE = 430      # constat du 27.10 : CV JTI, 365 et 511 mots sur ses deux premières pages A4
RACCOURCIS_MAX = 2       # demandes de raccourci au plus, après quoi le contrôle CQ-13 signale le dépassement
INTERROMPU_MIN = 30      # un dossier « en cours » plus vieux : sa tâche est morte (tableau de bord redémarré)

FICHIERS = (("analyse", "Analyse.md"), ("cv", "CV.md"), ("lettre", "Lettre.md"), ("envoi", "Envoi.md"))
ETAPES = {"analyse": "analyse de l'annonce", "cv": "CV adapté", "lettre": "lettre", "envoi": "fiche d'envoi",
          "finition": "Word, PDF et contrôles"}
NOM_METHODE = "Candidature ciblée.md"      # nom du document de la méthode, dans le contexte de Claude

CONSIGNE = """Tu prépares le dossier de candidature de %(nom)s pour une annonce à laquelle %(prenom)s a dit Go. Tu suis la méthode « Candidature ciblée » fournie, et ces règles absolues :
- Rien d'inventé : ni chiffre, ni nom, ni date, ni anecdote. Un chiffre vient des preuves ou du parcours (Preuves.md, base Parcours) ou n'existe pas. Un mot-clé absent ne se comble que s'il correspond à quelque chose de vrai ; sinon c'est un écart, traité honnêtement dans la lettre.
- Le titre du CV reprend le titre exact de l'annonce.
- Le CV et la lettre sont dans la langue de l'annonce, rédigés directement dans cette langue (jamais une traduction mot à mot). Les mots-clés viennent du texte original de l'annonce.
- Aucun tiret cadratin (—). Une puce par ligne. Aucun lien [[...]], aucune référence P-xx ou E-n, aucun tag d'angle dans le CV ou la lettre.
- Lettre : une page, au style de « Style d'écriture », jamais « ravi de », au plus une anecdote tirée des preuves ou du parcours et portée par une force de « Forces VIA » qui répond à un signal de l'annonce, sans jamais nommer le test. Prête à envoyer : aucun champ à compléter, salutation neutre si le destinataire est inconnu.
Réponds uniquement par le contenu Markdown demandé, sans préambule."""

# Guides de Harvard (Mignone Center for Career Success), retenus le 27.10 et résumés ici :
# careerservices.fas.harvard.edu/resources/create-a-strong-resume/ (CV et lettre) et
# careerservices.fas.harvard.edu/ai-resumes-and-cover-letters/ (l'IA relit et adapte, elle n'invente pas).
# HARVARD s'applique à chaque écriture du CV : première version, raccourci ou révision.
HARVARD = """Règles du guide de CV de Harvard, à appliquer à chaque puce et chaque section :
- Taillé pour ce poste : tout ne doit pas s'y rapporter directement, mais le CV montre les compétences que cet employeur valorise ; ce qui ne les sert pas se résume ou disparaît.
- Langue précise plutôt que générale, active plutôt que passive, sobre plutôt qu'ampoulée : écrire pour dire, pas pour impressionner.
- Chaque puce commence par un verbe d'action précis (diriger, lancer, concevoir, négocier, optimiser…), au passé pour un poste terminé, au présent pour le poste actuel, sans répéter le même verbe d'une puce à l'autre ; jamais « responsable de », « chargé de », « participé à ».
- Une puce = action + objet + résultat. Le résultat est chiffré si le chiffre vient des preuves, sinon qualifié par son effet concret ; montrer des résultats, pas des tâches.
- Aucun pronom (ni je, ni nous), aucun style narratif, aucune familiarité, aucune abréviation de mot courant. Les termes techniques, outils et verbes de l'annonce s'écrivent exactement sous sa forme, pour les logiciels de tri : sigle si elle écrit le sigle (UX, HTML, API…), toutes lettres suivies du sigle si elle l'écrit en toutes lettres (« Hypertext Markup Language (HTML) ») ; un sigle absent de l'annonce, interne ou rare, s'explique une fois.
- Lisible en quelques secondes, par une personne comme par un logiciel de tri : puces courtes, mots-clés visibles.
- Dans chaque section, ordre chronologique inversé. Aucun trou inexpliqué dans le parcours.
- Profil en deux ou trois lignes au plus, sans « je » ni aucun pronom, au style des puces : ce que la personne apporte à ce poste.
- Pas de photo, d'âge, de genre, d'état civil, de nationalité ni de références : l'en-tête garde l'adresse, le téléphone, l'e-mail, le site et LinkedIn.
- L'IA adapte, elle n'est pas l'auteur : partir des puces du CV maître, des preuves et des réalisations du parcours, les reformuler pour ce poste ; aucune formule générique qui pourrait figurer dans n'importe quel CV.
- Chaque ligne doit pouvoir être défendue en entretien, exemple à l'appui ; ce qui ne le peut pas disparaît.
- Pour les logiciels de tri : compétences listées explicitement, mots-clés de l'annonce placés dans la puce qui les prouve, pas seulement empilés.
- Avant de répondre, relis le CV comme le recruteur de cette annonce : il voit en quelques secondes les points forts pour le poste, sans répétition, sans écart passé sous silence ni affirmation inexacte ; corrige ce qui manque.
- Mise en page et style figés : exactement le format ci-dessous, ligne par ligne ; seul le contenu s'adapte. Trois écarts voulus au CV maître (décidés le 27.10) : l'expérience passe avant les compétences, le profil s'écrit sans « je », la nationalité sort de l'en-tête.

Format du CV.md (le Word et le PDF en dépendent ; ne rien y changer) :
# <Prénom Nom>
## <titre exact de l'annonce, sans mention de genre comme (m/f/d) ou (h/f)>
<adresse> | <téléphone> | <e-mail> | <site> | <LinkedIn>
## <Profil>
<deux ou trois lignes>
## <Expérience>
### <intitulé du poste>
**<employeur>** | <lieu> | MM.AAAA – MM.AAAA
- <puce>
(un poste ancien sans puces garde ses deux lignes ### et **employeur**)
## <Compétences>
- **<bloc> :** <terme>, <terme>, <terme>
## <Formation>
- <diplôme> | <école> | <dates>
## <Certifications>
- <nom> | <organisme> | MM.AAAA
## <Langues>
- <langue> : <niveau>
Intitulés de sections dans la langue de l'annonce ; le gras seulement sur l'employeur et le nom des blocs de compétences ; le séparateur est toujours « | »."""

HARVARD_LETTRE = """Règles des guides de Harvard pour la lettre : adressée à une personne nommée si l'annonce en donne une ; adaptée à cette organisation, avec ce que l'annonce et les données disent d'elle ; concise et factuelle, sans langue ampoulée ; chaque compétence avancée est appuyée par un exemple précis et exact du parcours ou des preuves, relié à une exigence de l'annonce ; « je » sans excès. Avant de répondre, relis-la comme le recruteur : montre-t-elle ce que la personne apporte à l'entreprise ? Est-ce sa voix ? Se lit-elle d'une traite à voix haute ? Corrige ce qui manque."""

PIECES = {
    "analyse": """Écris Analyse.md. Commence exactement par « ## Angle proposé » (%(angles)s, et pourquoi en trois phrases). Puis : « ## Exigences » (explicites, implicites, signaux culturels), « ## Correspondance » (chaque exigence majeure → preuve P-xx, élément E-n du parcours ou poste), « ## Écarts assumés » (et comment la lettre les traite), « ## Mots-clés de l'annonce » (15 à 25 termes, dans la langue de l'annonce, marqués présent / à aligner / écart), « ## Questions en attente » (hypothèses prises faute d'information ; vide si aucune). Tu peux citer P-xx et E-n ici : c'est une note de travail.""",
    "cv": """Écris CV.md, le CV adapté. Première ligne : « # %(nom)s », deuxième : « ## <titre exact de l'annonce> ». Puis les sections usuelles dans la langue de l'annonce, dans cet ordre : profil, expérience, compétences, formation, certifications, langues ; une seule colonne, dates au format MM.AAAA. Puces choisies et réordonnées depuis le CV maître (ou les réalisations de la base Parcours) pour l'angle retenu ; mots-clés de l'annonce repris seulement s'ils sont vrais. Aucune référence P-xx ni E-n, aucun lien, aucun tiret cadratin. Le CV tient sur %(pages_cv)s pages A4 au plus, soit %(mots_cv)s mots environ : les postes anciens ou éloignés de l'annonce gardent leur ligne d'en-tête et perdent leurs puces, les compétences sont resserrées sur l'annonce. Section formation : %(formations)s
Section certifications : %(certifications)s
Section langues : %(langues)s

""" + HARVARD.replace("%", "%%"),
    "lettre": """Écris Lettre.md. Avant la lettre, une note de travail de deux lignes au plus (anecdote choisie et pourquoi), puis une ligne « --- », puis la lettre elle-même dans la langue de l'annonce : la ligne de lieu et date avec la date du jour fournie (« %(ville)s, le 26 octobre 2026 », ou son équivalent dans la langue de l'annonce), objet, salutation, quatre paragraphes (accroche sur le problème de l'entreprise, preuve principale, %(differenciateur)s, clôture avec demande d'entretien), formule finale, « %(nom)s ». Aucun tiret cadratin, aucun champ à compléter.

""" + HARVARD_LETTRE.replace("%", "%%"),
    "envoi": """Écris Envoi.md, la fiche d'envoi, avec exactement ces rubriques : « ## 0. Avant d'envoyer » (alertes : délai, poste peut-être pourvu, signaux relevés), « ## 1. Formulaire » (lien officiel de postulation tel qu'il figure dans les données ; ne jamais en inventer un), « ## 2. Pièces » (CV, lettre, certificats de travail, CFC), « ## 3. Réponses aux questions standard » (dans la langue de l'annonce : disponibilité, taux, langues, formation la plus élevée, motivation en 200 caractères), « ## 4. Prétentions salariales » (fourchette réaliste pour ce poste, appuyée sur l'estimation fournie et son raisonnement ; jamais un plancher). Rien après la rubrique 4. Dans « 0. Avant d'envoyer », signale aussi toute consigne de l'annonce ou de l'employeur sur l'usage de l'IA pour préparer une candidature ; s'il n'y en a pas, n'en dis rien.""",
}


def _valeurs():
    """Ce que les consignes tirent du profil (page Profil)."""
    p = profil.charger()
    angles = profil.lire(p, "evaluation.angles") or []
    diff = profil.lire(p, "dossier.differenciateur")
    return {
        "nom": profil.nom_complet(), "prenom": profil.prenom(),
        "ville": profil.identite().get("ville") or "Localité",
        "angles": "un des %s angles" % profil.nombre_fr(len(angles)) if angles else "un des angles de la méthode",
        "differenciateur": "différenciateur " + diff if diff else "différenciateur",
        "pages_cv": PAGES_CV, "mots_cv": MOTS_PAR_PAGE * PAGES_CV,
        "formations": _formations(profil.lire(p, "dossier.formations") or []),
        "certifications": _certifications(profil.lire(p, "dossier.certifications") or []),
        "langues": _langues(profil.lire(p, "dossier.langues") or []),
    }


def _formations(liste):
    if not liste:
        return "celle du CV maître, chaque diplôme une seule fois."
    return ("les diplômes de la liste ci-dessous, qui fait foi (diplôme | établissement | année d'obtention) et "
            "remplace la formation du CV maître : chacun une seule fois ; ordre chronologique inversé ; un diplôme "
            "sans année est en cours, dis-le sans inventer de date ; titre traduit dans la langue de l'annonce "
            "seulement s'il n'a pas de nom officiel dans cette langue.\n"
            + "".join("- %s\n" % x for x in liste))


def _certifications(liste):
    if not liste:
        return "celles du CV maître, chacune une seule fois."
    return ("celles de la liste ci-dessous, qui fait foi (noms, organismes, dates) et remplace celle du CV maître : "
            "chacune une seule fois, même si le CV maître ou les preuves la citent sous un autre nom ou une autre date ; "
            "les cours d'une même série chez le même organisme tiennent sur une ligne ; ordre chronologique inversé ; "
            "nom traduit dans la langue de l'annonce seulement s'il n'a pas de nom officiel dans cette langue.\n"
            + "".join("- %s\n" % c for c in liste))


def _langues(liste):
    if not liste:
        return "celles du CV maître, chacune une seule fois."
    return ("celles de la liste ci-dessous, qui fait foi (langue | niveau | attestation éventuelle) et remplace celle "
            "du CV maître : chacune une seule fois ; nom de la langue et niveau traduits dans la langue de l'annonce. "
            "Le niveau est une auto-évaluation sur l'échelle européenne : jamais « certifié », « attesté », « diplôme » "
            "ni nom de test ou de certificat qui ne figure pas sur sa ligne ; une attestation donnée se cite telle quelle, "
            "entre parenthèses après le niveau.\n"
            + "".join("- %s\n" % x for x in liste))


PAGES_CV = 2                 # pages A4 au plus : un CV plus long est raccourci par Claude


def lignes_par_page(pdf):
    """Lignes de texte de chaque page du PDF (pypdf), None si le PDF est absent ou illisible."""
    if not pdf or not Path(pdf).exists():
        return None
    try:
        from pypdf import PdfReader
        pages = PdfReader(str(pdf)).pages
        return [sum(1 for l in (p.extract_text(extraction_mode="layout") or "").splitlines() if l.strip())
                for p in pages]
    except Exception:
        return None


def nombre_pages(pdf):
    """Pages d'un PDF (Chrome et LibreOffice écrivent un objet /Type /Page par page), None sans PDF."""
    if not pdf or not Path(pdf).exists():
        return None
    return len(re.findall(rb"/Type\s*/Page\b", Path(pdf).read_bytes())) or None


def consigne():
    return CONSIGNE % _valeurs()


def pieces_a_ecrire():
    v = _valeurs()
    return {k: gabarit % v for k, gabarit in PIECES.items()}


def prefixe_fichier():
    """« Prenom-Nom » sans accents : début des noms de fichiers Word et PDF."""
    i = profil.identite()
    nom = "-".join(x for x in (i.get("prenom"), i.get("nom")) if x) or "Candidature"
    return re.sub(r"[^A-Za-z0-9]+", "-", evaluation_plier(nom)).strip("-")


def _schema(reg):
    reg.cx.executescript(SCHEMA)
    if "etape" not in {r[1] for r in reg.cx.execute("PRAGMA table_info(dossiers)")}:
        reg.cx.execute("ALTER TABLE dossiers ADD COLUMN etape TEXT")      # 27.10 : avancement affiché


def _etape(reg, emp, cle):
    n = list(ETAPES).index(cle) + 1
    reg.cx.execute("UPDATE dossiers SET etape=? WHERE empreinte=?", ("%d/%d · %s" % (n, len(ETAPES), ETAPES[cle]), emp))
    reg.commit()


def contexte_fixe():
    """Méthode (style et forces par défaut compris) + profil (base Parcours) : identique pour
    tous les dossiers (mis en cache)."""
    return methode_de_base() + "\n\n" + evaluation.profil()


def methode_de_base():
    """La méthode « Candidature ciblée » livrée avec l'application (style et forces par défaut
    compris)."""
    from . import modeles
    return "<document nom=\"%s\">\n%s\n</document>" % (NOM_METHODE, modeles.methode())


def nom_dossier(f):
    propre = lambda t, n: re.sub(r"\s+", " ", re.sub(r"[\\/:*?\"<>|]", "-", t or "")).strip()[:n]
    return "%s - %s - %s" % (dt.date.today().isoformat(), propre(f["entreprise"], 40), propre(f["titre"], 60))


def nom_fichier(f, piece):
    court = lambda t: re.sub(r"[^A-Za-z0-9]+", "-", re.sub(r"\b(SA|AG|Sàrl|SARL|GmbH|Ltd)\b", "", evaluation_plier(t))).strip("-")[:40]
    base = prefixe_fichier() + {"cv": "-CV", "lettre": "-Cover-Letter"}[piece]
    return "%s-%s-%s.docx" % (base, court(f["entreprise"]), court(f["titre"]))


def evaluation_plier(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(ch for ch in t if not unicodedata.combining(ch))


# --- Fiche d'envoi : ce qui ne regarde pas l'IA ---------------------------------------

# La réponse à « assignation par l'ORP » et la rubrique Job-Room s'écrivent ici, sans Claude :
# elles diraient à l'IA que la personne est inscrite au chômage, une donnée sensible (nLPD,
# art. 5 : mesures d'aide sociale). Elles ne dépendent que de l'annonce et du registre.
ORP = "- Assignation par l'ORP : non"


def completer_envoi(texte, f):
    """Envoi.md tel que Claude l'a écrit (rubriques 0 à 4), complété de la ligne ORP en
    rubrique 3 et de la rubrique « 5. Job-Room » (raison sociale, adresse, poste exact)."""
    texte = re.split(r"(?m)^## 5\.", texte, maxsplit=1)[0].rstrip()
    if "ORP" not in texte:
        m = re.search(r"(?m)^## 4\.", texte)
        if m:
            texte = texte[:m.start()].rstrip() + "\n" + ORP + "\n\n" + texte[m.start():]
        else:
            texte += "\n" + ORP
    adresse = (f.get("extra") or {}).get("adresse_employeur")
    cellule = lambda t: re.sub(r"\s*\|\s*", " / ", t or "?").strip()
    lignes = ["## 5. Job-Room", "", "À saisir après l'envoi seulement.", "", "| Champ | Valeur |", "|---|---|",
              "| Raison sociale | %s |" % cellule(f.get("entreprise")),
              "| Lieu | %s |" % (cellule(adresses.en_ligne(adresse)) if adresse else
                                 "adresse à compléter" + (" (lieu de l'annonce : %s)" % cellule(f["lieu"]) if f.get("lieu") else "")),
              "| Désignation exacte du poste | %s |" % cellule(f.get("titre"))]
    if adresse and adresse.get("origine"):
        lignes += ["", "Adresse : %s." % adresse["origine"]]
    return texte + "\n\n" + "\n".join(lignes) + "\n"


# --- Production ---------------------------------------------------------------------


def _appel(c, systeme_fixe, consigne_piece, donnees):
    """Une pièce par le moteur `c` : consigne et contexte fixes, texte libre, aucun outil."""
    t = moteur.Tache("dossier", consigne(), contexte=systeme_fixe, modele=evaluation.MODELE,
                     effort="high", delai_s=900)
    r = c.executer(t, donnees + "\n\n<tache>\n" + consigne_piece + "\n</tache>")
    return r.texte.strip() + "\n"


def _donnees(f, ev, pieces):
    blocs = ["Date du jour : %s\n\n<annonce>\nEntreprise : %s\nIntitulé exact : %s\nLieu : %s\nLien de postulation : %s\nLien de l'annonce : %s\n"
             "Langue du texte : %s\n\n%s\n</annonce>" % (
                 dt.date.today().isoformat(), f["entreprise"], f["titre"], f.get("lieu") or "?", f.get("url_postuler") or "(aucun connu)",
                 f.get("url") or "(aucun)", f.get("langue") or "?", f.get("texte") or "")]
    adresse = (f.get("extra") or {}).get("adresse_employeur")
    if adresse:
        blocs.append("<adresse_employeur>\n%s\n</adresse_employeur>" % adresses.en_ligne(adresse))
    if ev:
        blocs.append("<evaluation>\n%s\n</evaluation>" % json.dumps(ev["resultat"], ensure_ascii=False))
    for nom, contenu in pieces.items():
        blocs.append("<%s>\n%s\n</%s>" % (nom, contenu, nom))
    return "\n\n".join(blocs)


def demarrer(reg, emp):
    """Marque le dossier « en cours ». Lève EvaluationImpossible s'il ne peut pas se faire."""
    _schema(reg)
    f = reg.get(emp)
    if not f:
        raise evaluation.EvaluationImpossible("annonce introuvable")
    if not f.get("texte"):
        raise evaluation.EvaluationImpossible("texte de l'annonce absent : un CV ne se construit pas sur un titre")
    dossier = DATA / "dossiers" / nom_dossier(f)
    dossier.mkdir(parents=True, exist_ok=True)
    reg.cx.execute("INSERT INTO dossiers (empreinte, etat, debut, chemin, etape) VALUES (?,?,?,?,?) ON CONFLICT(empreinte) "
                   "DO UPDATE SET etat=excluded.etat, debut=excluded.debut, chemin=excluded.chemin, "
                   "etape=excluded.etape, fin=NULL, erreur=NULL",
                   (emp, "en_cours", maintenant(), str(dossier), "démarrage"))
    reg.commit()
    return f, dossier


def produire(reg, emp, c=None):
    """Produit le dossier complet. Renvoie l'état final ('pret' ou 'a_revoir')."""
    f, dossier = demarrer(reg, emp)
    if adresses.renseigner(reg, emp):           # pour la preuve Job-Room (NPA / Lieu obligatoires)
        f = reg.get(emp)
    try:
        c = c or moteur.ouvrir()
        fixe = contexte_fixe()
        ev = evaluation.lire(reg, emp)
        (dossier / "Annonce.md").write_text("# %s — %s\n\n%s\n\n%s\n" % (
            f["entreprise"], f["titre"], f.get("url") or "", f.get("texte") or ""), encoding="utf-8")
        pieces = {}
        for piece, fichier in FICHIERS:
            _etape(reg, emp, piece)
            pieces[piece] = _appel(c, fixe, pieces_a_ecrire()[piece], _donnees(f, ev, pieces))
            if piece == "envoi":
                pieces[piece] = completer_envoi(pieces[piece], f)
            (dossier / fichier).write_text(pieces[piece], encoding="utf-8")
        return _finaliser(reg, f, dossier, pieces, fixe, c)
    except Exception as e:
        reg.cx.rollback()
        reg.cx.execute("UPDATE dossiers SET etat='echec', fin=?, erreur=? WHERE empreinte=?",
                       (maintenant(), str(e)[:500], emp))
        reg.noter(emp, "dossier en échec · %s" % str(e)[:200])
        reg.commit()
        raise


def finir(reg, emp, c=None):
    """Termine un dossier dont les quatre pièces sont déjà écrites (mesure, Word, contrôles).
    Sans moteur, Claude n'est pas rappelé : un CV trop long est seulement signalé (CQ-13)."""
    _schema(reg)
    f, d = reg.get(emp), etat(reg, emp)
    if not f or not d:
        raise evaluation.EvaluationImpossible("dossier introuvable")
    dossier = Path(d["chemin"])
    pieces = {piece: (dossier / fichier).read_text(encoding="utf-8") for piece, fichier in FICHIERS}
    return _finaliser(reg, f, dossier, pieces, contexte_fixe(), c)


RACCOURCIR = """Le CV ci-dessous, rendu en Word puis en PDF A4, fait %(pages)s pages ; il en faut %(pages_cv)s au plus. %(debord)sIl compte %(mots)s mots : ramène-le à %(cible)s mots au plus. Garde le titre exact, l'en-tête, le profil et les expériences récentes et pertinentes pour l'annonce avec leurs meilleures puces. Condense d'abord ce qui sert le moins l'annonce : postes anciens ou éloignés réduits à leur ligne d'en-tête, sans puces, compétences resserrées, puces plus courtes, détails redondants. Ne rien inventer, ne rien ajouter ; les mots-clés de l'annonce déjà présents restent, les sections certifications et langues restent complètes. Même format : sections, dates MM.AAAA, une puce par ligne, aucun tiret cadratin, aucune référence P-xx.

""" + HARVARD.replace("%", "%%") + """

Réponds uniquement par le nouveau CV.md complet."""


def _raccourcir(c, f, cv, pages, fixe, tour, par_page=None):
    """Une demande de raccourci. Les lignes des pages en trop, si elles se lisent, donnent la coupe :
    une ligne qui déborde se règle en supprimant des lignes entières, pas en raccourcissant des
    phrases (constat JTI, 27.10 : deux raccourcis de mots, la ligne débordait toujours)."""
    mots = len(cv.split())
    if par_page and len(par_page) > PAGES_CV:
        debord = sum(par_page[PAGES_CV:])
        lignes = (debord + 3) * tour                                  # le second tour coupe plus franchement
        texte = ("Les pages en trop portent %d ligne(s) : supprime au moins %d lignes entières du rendu "
                 "(une puce entière, une ligne de compétences, un poste très ancien et sans lien avec l'annonce, "
                 "une puce qui passe sur deux lignes ramenée à une) ; raccourcir des mots à l'intérieur d'une "
                 "ligne ne libère aucune place. " % (debord, lignes))
        coupe = 12 * lignes
    else:
        coupe = mots - int(MOTS_PAR_PAGE * PAGES_CV * (0.95 if tour == 1 else 0.85))
        texte = ""
    cible = mots - max(coupe, 40)
    return _appel(c, fixe, RACCOURCIR % dict(_valeurs(), pages=pages, mots=mots, cible=cible, debord=texte),
                  _donnees(f, None, {"cv_actuel": cv}))


def _finaliser(reg, f, dossier, pieces, fixe, c=None):
    emp = f["empreinte"]
    _etape(reg, emp, "finition")
    from .lettre import date_cible, dater_markdown
    pieces["lettre"] = dater_markdown(pieces["lettre"], date_cible(f), f.get("langue") or "fr")
    (dossier / "Lettre.md").write_text(pieces["lettre"], encoding="utf-8")
    from .apercu import en_pdf

    def rendre(piece):
        docx = dossier / nom_fichier(f, piece)
        rendre_docx(pieces[piece], docx, piece)
        return en_pdf(docx, f.get("langue") or "fr")   # PDF à côté du Word (Chrome de la machine virtuelle)

    rendre("lettre")
    pdf = rendre("cv")
    pages = nombre_pages(pdf)
    tour = 0
    while c and pages and pages > PAGES_CV and tour < RACCOURCIS_MAX:
        tour += 1
        (dossier / ("CV.%d-pages.md" % pages)).write_text(pieces["cv"], encoding="utf-8")   # version trop longue gardée
        avant = pages
        pieces["cv"] = _raccourcir(c, f, pieces["cv"], pages, fixe, tour, lignes_par_page(pdf))
        (dossier / "CV.md").write_text(pieces["cv"], encoding="utf-8")
        pdf = rendre("cv")
        pages = nombre_pages(pdf)
        reg.noter(emp, "CV raccourci · %s → %s pages" % (avant, pages or "?"))
    couverture = mesurer(dossier, f.get("entreprise"))
    certifications = "\n".join((profil.lire(profil.charger(), "dossier.formations") or [])
                               + (profil.lire(profil.charger(), "dossier.certifications") or [])
                               + (profil.lire(profil.charger(), "dossier.langues") or []))
    resultats = controler(f, dossier, pieces, couverture, fixe + "\n" + certifications, pages)
    etat = "pret" if all(r["ok"] for r in resultats) else "a_revoir"
    reg.cx.execute("UPDATE dossiers SET etat=?, fin=?, couverture=?, controles=? WHERE empreinte=?",
                   (etat, maintenant(), couverture, json.dumps(resultats, ensure_ascii=False), emp))
    reg.maj(emp, dossier=dossier.name, cv_genere=True,
            prochaine_action="Relire le dossier et envoyer" if etat == "pret" else "Dossier à revoir avant envoi")
    reg.noter(emp, "dossier produit · %s · couverture %s %%" % ("prêt" if etat == "pret" else "à revoir",
                                                              couverture if couverture is not None else "?"))
    reg.commit()
    return etat


REVISION = """%(prenom)s a relu la couverture des mots-clés de son CV et confirme que les termes ci-dessous de l'annonce correspondent à quelque chose de vrai dans son parcours. Révise CV.md pour les intégrer, dans la langue de l'annonce, là où ils sont justes (profil, compétences, ou la puce d'expérience qui le prouve), sans rien inventer d'autre : pas de nouveau chiffre, pas de nouvel employeur, pas de nouvelle responsabilité. Si un terme ne peut s'intégrer sans mentir sur le profil, laisse-le de côté. Garde tout le reste à l'identique : titre exact, structure, puces, format MM.AAAA, aucun tiret cadratin, aucune référence P-xx.

""" + HARVARD.replace("%", "%%") + """

Réponds uniquement par le nouveau CV.md complet."""


def reviser_cv(reg, emp, termes, precision="", c=None):
    """Intègre au CV les mots-clés cochés, puis remesure et rend à nouveau.
    L'ancienne version est gardée (CV.avant-AAAAMMJJ-HHMM.md)."""
    _schema(reg)
    f, d = reg.get(emp), etat(reg, emp)
    if not f or not d:
        raise evaluation.EvaluationImpossible("dossier introuvable")
    termes = [t.strip() for t in termes if t and t.strip()]
    if not termes:
        raise evaluation.EvaluationImpossible("aucun mot-clé choisi")
    dossier = Path(d["chemin"])
    cv = (dossier / "CV.md").read_text(encoding="utf-8")
    reg.cx.execute("UPDATE dossiers SET etat='en_cours', debut=?, erreur=NULL WHERE empreinte=?", (maintenant(), emp))
    reg.commit()
    try:
        c = c or moteur.ouvrir()
        donnees = _donnees(f, None, {"cv_actuel": cv}) + "\n\n<termes_confirmes>\n%s\n</termes_confirmes>" % "\n".join(
            "- " + t for t in termes)
        if precision.strip():
            donnees += "\n\n<precision>\n%s\n</precision>" % precision.strip()
        nouveau = _appel(c, contexte_fixe(), REVISION % _valeurs(), donnees)
        horodatage = dt.datetime.now().strftime("%Y%m%d-%H%M")
        (dossier / ("CV.avant-%s.md" % horodatage)).write_text(cv, encoding="utf-8")
        (dossier / "CV.md").write_text(nouveau, encoding="utf-8")
        reg.noter(emp, "CV révisé · mots-clés ajoutés : %s" % ", ".join(termes)[:300])
        reg.commit()
        return finir(reg, emp, c)
    except Exception as e:
        reg.cx.rollback()
        reg.cx.execute("UPDATE dossiers SET etat='a_revoir', fin=?, erreur=? WHERE empreinte=?",
                       (maintenant(), "révision du CV : %s" % str(e)[:400], emp))
        reg.commit()
        raise


def reviser_en_tache(emp, termes, precision=""):
    def tache():
        reg = Registre()
        try:
            reviser_cv(reg, emp, termes, precision)
        except Exception:
            pass                         # l'erreur est notée sur le dossier
        try:
            prevenir(reg, emp, " · CV révisé")
        finally:
            reg.fermer()
    threading.Thread(target=tache, name="revision-%s" % emp[:8], daemon=True).start()


def mettre_a_date(reg, emp):
    """Date de la lettre : aujourd'hui tant que la candidature n'est pas partie, la date
    d'envoi ensuite. Réécrit Lettre.md et le Word si la date a changé (sans appel à Claude).
    Renvoie la date de la lettre, ou None si le dossier n'a pas de lettre."""
    from .apercu import en_pdf
    from .lettre import date_cible, dater_markdown
    f, d = reg.get(emp), etat(reg, emp)
    if not f or not d or d["etat"] == "en_cours":
        return None
    dossier = Path(d["chemin"])
    md = dossier / "Lettre.md"
    if not md.exists():
        return None
    cible = date_cible(f)
    avant = md.read_text(encoding="utf-8")
    apres = dater_markdown(avant, cible, f.get("langue") or "fr")
    docx = dossier / nom_fichier(f, "lettre")
    if apres != avant or not docx.exists():
        md.write_text(apres, encoding="utf-8")
        rendre_docx(apres, docx, "lettre")
        en_pdf(docx, f.get("langue") or "fr")
    return cible


def prevenir(reg, emp, suite="", fin=None):
    """Notification de fin de dossier : prêt, à revoir (quels contrôles) ou en échec (pourquoi).
    Un clic ouvre la page du dossier. Ne lève jamais : une notification ratée ne casse rien.
    `fin` : l'état rendu par la production, si la table ne l'a pas (encore) noté."""
    try:
        from . import notify
        f, d = reg.get(emp) or {}, etat(reg, emp) or {}
        fin = d.get("etat") if d.get("etat") not in (None, "en_cours") else fin
        titre = {"pret": "Dossier prêt", "a_revoir": "Dossier à revoir avant envoi", "echec": "Dossier en échec",
                 "interrompu": "Dossier interrompu"}.get(fin)
        if not titre:
            return []
        lignes = ["%s · %s" % (f.get("entreprise"), f.get("titre"))]
        if fin == "a_revoir":
            rates = [c["cq"] for c in d.get("controles") or [] if not c.get("ok")]
            if d.get("erreur"):
                lignes.append(d["erreur"][:200])
            if rates:
                lignes.append("À revoir : " + " ; ".join(rates[:3]) + (" …" if len(rates) > 3 else ""))
        elif fin in ("echec", "interrompu"):
            lignes.append((d.get("erreur") or "tâche arrêtée")[:200])
            lignes.append("Relance-le depuis la page du dossier.")
        return notify.notifier(titre + suite, "\n".join(lignes), urgent=fin in ("echec", "interrompu"),
                               lien="/dossier/%s" % emp)
    except Exception:
        return []


def produire_en_tache(emp, reg=None):
    """Lancé au clic sur Go : le dossier est marqué « en cours » avant la réponse (un dossier
    impossible se dit au clic, EvaluationImpossible), s'écrit en arrière-plan, et une
    notification dit quand il est prêt."""
    r = reg or Registre()
    try:
        demarrer(r, emp)
    finally:
        if reg is None:
            r.fermer()

    def tache():
        reg = Registre()
        try:
            fin = produire(reg, emp)
        except Exception:
            fin = "echec"                # l'échec est noté dans la table dossiers et le journal
        try:
            prevenir(reg, emp, fin=fin)
        finally:
            reg.fermer()
    threading.Thread(target=tache, name="dossier-%s" % emp[:8], daemon=True).start()


# --- Mesure, contrôle, rendu ----------------------------------------------------------


def mesurer(dossier, entreprise=""):
    """Couverture des mots-clés (mesure de l'application, `ats.py`). Le détail
    (présents, absents, format) est gardé dans motscles.json pour la page du dossier."""
    from .motscles import analyser
    cv, annonce = dossier / "CV.md", dossier / "Annonce.md"
    if not cv.exists() or not annonce.exists():
        return None
    r = analyser(cv.read_text(encoding="utf-8"), annonce.read_text(encoding="utf-8"), entreprise)
    if r is None:
        return None
    (dossier / "motscles.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return r["taux"]


_NOMBRE = re.compile(r"(?<![\w.])\d{1,3}(?:[’' ]\d{3})+(?![\w])|(?<![\w.])\d+(?:[.,]\d+)?\s?%|(?<![\w.])\d{2,}(?![\w./-])")


def _chiffres_sans_preuve(texte, fixe):
    """Nombres du CV ou de la lettre absents du profil (hors années et dates)."""
    ref = re.sub(r"[’' ]", "", fixe)
    douteux = []
    for m in _NOMBRE.finditer(texte):
        n = m.group(0)
        brut = re.sub(r"[’' %]", "", n)
        pourcentage = "%" in n
        if not pourcentage and (re.fullmatch(r"(19|20)\d\d", brut) or re.fullmatch(r"\d{1,2}", brut)):
            continue                     # années et petits nombres (« 2 semaines ») : pas des preuves
        if brut not in ref:
            douteux.append(n)
    return sorted(set(douteux))


_GENRE = re.compile(r"\(\s*(?:[a-z]{1,2}\s*/\s*){1,3}[a-z]{1,2}\s*\)", re.I)


def _titre_comparable(t):
    """Titre sans mention de genre ((m/f/d), (h/f)…), sans accents ni casse : le CV n'en porte pas."""
    return re.sub(r"\s+", " ", _GENRE.sub(" ", evaluation_plier(t))).strip().lower()


def controler(f, dossier, pieces, couverture, fixe, pages=None):
    cv, lettre_complete = pieces["cv"], pieces["lettre"]
    lettre = lettre_complete.split("\n---", 1)[-1]
    rendu = cv + "\n" + lettre
    r = []
    ajouter = lambda cq, ok, detail="": r.append({"cq": cq, "ok": bool(ok), "detail": detail})
    marques = re.findall(r"\[\[.*?\]\]|\bP-\d\d\b|\bE-\d+\b|\bTODO\b|\[(?:[A-ZÉ][^\]]{1,30})\]|\{[A-Z][A-Z_]{2,}(?:_\d+)?\}", rendu)
    ajouter("CQ-01 aucun crochet, lien, P-xx, TODO ni balise", not marques, ", ".join(sorted(set(marques)))[:200])
    ajouter("CQ-02 aucun tiret cadratin", "—" not in rendu)
    noms = [p.name for p in dossier.glob("*.docx")]
    ajouter("CQ-03 noms de fichiers conformes, sans « ATS »", noms and not any("ATS" in n for n in noms), ", ".join(noms))
    titre_cv = next((l[3:].strip() for l in cv.splitlines() if l.startswith("## ")), "")
    ajouter("CQ-04 titre du CV = titre de l'annonce", _titre_comparable(titre_cv) == _titre_comparable(f["titre"]), titre_cv)
    l_annonce, l_lettre, l_cv = f.get("langue"), langue(lettre), langue(cv)
    ajouter("CQ-05 lettre et CV dans la langue de l'annonce",
            not l_annonce or (l_lettre in (None, l_annonce) and l_cv in (None, l_annonce)),
            "annonce %s, lettre %s, CV %s" % (l_annonce, l_lettre, l_cv))
    ajouter("CQ-06 couverture ≥ 80 % ou écarts listés",
            (couverture or 0) >= 80 or "## Écarts assumés" in pieces["analyse"],
            "%s %%" % couverture if couverture is not None else "non mesurée")
    sans_preuve = _chiffres_sans_preuve(rendu, fixe)
    ajouter("CQ-07 chiffres rattachés au profil", not sans_preuve, ", ".join(sans_preuve)[:200])
    ajouter("CQ-09 pas de nom du test de caractère", not re.search(r"\bVIA\b", rendu))
    ajouter("CQ-11 fiche d'envoi complète (6 rubriques)",
            len(re.findall(r"^## [0-5]\.", pieces["envoi"], re.M)) >= 6)
    ajouter("CQ-12 l'analyse commence par « Angle proposé »", pieces["analyse"].lstrip().startswith("## Angle proposé"))
    if pages is not None:                                             # sans PDF, rien à compter
        ajouter("CQ-13 CV sur %d pages au plus" % PAGES_CV, pages <= PAGES_CV, "%d pages" % pages)
    return r


def modele_word(piece):
    """Le modèle Word de la pièce, livré avec l'application."""
    from . import modeles
    return modeles.chemin(piece)


def rendre_docx(markdown, cible, piece):
    """Word à partir du modèle (styles conservés, contenu remplacé)."""
    from docx import Document
    doc = Document(str(modele_word(piece)))
    corps = doc.element.body
    for el in list(corps):
        if not el.tag.endswith("sectPr"):
            corps.remove(el)
    texte = markdown.split("\n---", 1)[-1] if piece == "lettre" else markdown
    # Les modèles pandoc nomment leurs styles « Heading 1 » ; python-docx traduit ce nom
    # avant de chercher. On passe donc l'objet style lui-même.
    styles = {st.name: st for st in doc.styles}
    style = lambda nom, repli="Normal": styles.get(nom) or styles.get(repli)
    for ligne in texte.splitlines():
        l = ligne.rstrip()
        if not l.strip():
            continue
        if l.startswith("# "):
            p = doc.add_paragraph(style=style("Title"))
            l = l[2:]
        elif l.startswith("## "):
            p = doc.add_paragraph(style=style("Heading 1"))
            l = l[3:]
        elif l.startswith("### "):
            p = doc.add_paragraph(style=style("Heading 2"))
            l = l[4:]
        elif re.match(r"^\s*[-*] ", l):
            p = doc.add_paragraph(style=style("Compact"))
            l = "• " + re.sub(r"^\s*[-*] ", "", l)
        else:
            p = doc.add_paragraph(style=style("Body Text"))
        for i, morceau in enumerate(re.split(r"\*\*", l)):
            if morceau:
                p.add_run(morceau).bold = bool(i % 2)
    doc.save(str(cible))


def _interrompu(d):
    debut = d.get("debut")
    return d["etat"] == "en_cours" and bool(debut) and dt.datetime.now() - dt.datetime.fromisoformat(debut) \
        > dt.timedelta(minutes=INTERROMPU_MIN)


def etat(reg, emp):
    """L'état du dossier ; « interrompu » pour un « en cours » dont la tâche est morte."""
    _schema(reg)
    r = reg.cx.execute("SELECT * FROM dossiers WHERE empreinte=?", (emp,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["controles"] = json.loads(d["controles"] or "[]")
    if _interrompu(d):
        d["etat"] = "interrompu"
    return d


def libelle(d):
    """Une ligne : où en est le dossier."""
    if d["etat"] == "en_cours":
        return "Dossier en préparation · %s · depuis %s" % (d.get("etape") or "démarrage", (d.get("debut") or "")[11:16])
    return {"pret": "Dossier prêt", "a_revoir": "Dossier à revoir avant envoi",
            "echec": "Dossier en échec : %s" % (d.get("erreur") or "")[:120],
            "interrompu": "Dossier interrompu (tâche arrêtée) : à refaire"}.get(d["etat"], d["etat"])


def a_produire(reg):
    """Retenues sans dossier en cours ni fait : rattrapage au passage du matin."""
    _schema(reg)
    faits = {r["empreinte"] for r in reg.cx.execute("SELECT empreinte, etat, debut FROM dossiers "
                                                    "WHERE etat IN ('en_cours','pret','a_revoir')")
             if not _interrompu(dict(r))}
    return [f for f in reg.liste(["retenue"]) if f["empreinte"] not in faits and not f.get("cv_genere")]
