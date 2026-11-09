"""Couverture des mots-clés d'un CV face à une annonce, sans coffre.

Même interface et même mesure que `4 - Tools/bin/ats.py` du coffre (qui garde la
priorité quand il est là, voir `motscles.py`), avec des mots vides en français,
en anglais et en allemand, les trois langues des annonces suisses :

1. Couverture lexicale : les termes de l'annonce (groupes de un à trois mots,
   sans mots vides) sont cherchés dans le CV, à la racine près (« facilitated »
   compte pour « facilitate »).
2. Pièges de format : sections aux noms exotiques, dates illisibles par une
   machine, sigles jamais développés, coordonnées absentes.

Ne lit et n'écrit aucun fichier : il compte et il alerte.
"""

import collections
import re
import unicodedata

VIDES = set("""
a an and are as at be by for from has have in into is it its of on or that the to with will you your our
we us their they this these those which who whom across able about after all also any been being both can
could do does each more most must not other over own same should so some such than then there under until
up very what when where while would within without work role you'll we're join beyond including etc via
per new well good strong plus may might year years every them how

le la les de des du un une et en pour dans sur avec au aux par est sont ou qui que dont votre vos nous
notre nos vous leur leurs ses son sa ces cet cette tout tous toute toutes etre avoir sera seront ont
avez etes plus tres bien sans sous entre afin ainsi comme chez lors selon aussi encore autre autres
mais donc car faire fait poste postes mission missions profil
entreprise societe offre offrons recherchons cherchons rejoindre sein cadre type taux
idealement egalement notamment niveau bonne bonnes connaissance connaissances capacite
maitrisez disposez possedez souhaitez aimez savez justifiez beneficiez souhaite souhaitee souhaitees
souhaites requis requise requises apprecie appreciee atout minimum ans annee annees
directement differents differentes participer contribuer comprendre nouveau nouvelle nouveaux nouvelles

und der die das den dem des ein eine einer eines einem einen fur mit von zu zur zum auf bei als wir sie
ihr ihre ihren ihrem unsere unser unserer unseren sowie oder ist sind werden wird haben hast hat im in an
aus nach uber durch sich auch noch sehr gute guten gutes bieten suchen deine dein dich dir du ihnen
stelle aufgaben bereich
""".split())

# Compléments de nom admis à l'intérieur d'un terme : « gestion de projet », « esprit d'équipe »,
# « head of product ». Pas « et » ni « la » : « équipe et anglais » ou « piloter la roadmap »
# ne sont pas des termes. Ils ne comptent pas pour la présence dans le CV.
LIAISONS = {"de", "d", "du", "des", "of", "fur", "der"}

SECTIONS_STANDARD = {
    "experience", "professional experience", "work experience", "employment",
    "experience professionnelle", "experiences", "experiences professionnelles", "parcours professionnel",
    "berufserfahrung", "berufliche erfahrung", "werdegang", "beruflicher werdegang",
    "education", "formation", "formations", "ausbildung", "bildung", "aus- und weiterbildung",
    "skills", "core skills", "technical skills", "competences", "kenntnisse", "kompetenzen", "fahigkeiten",
    "certifications", "certificats", "certificates", "zertifikate", "zertifizierungen", "weiterbildung",
    "summary", "professional summary", "profile", "profil", "kurzprofil",
    "languages", "langues", "sprachen", "sprachkenntnisse",
}

FORMES_JURIDIQUES = {"LP", "SA", "AG", "SARL", "SÀRL", "GMBH", "LLC", "LTD", "INC", "PLC", "NV", "BV", "SAS"}

# Sigles qu'un recruteur suisse lit sans dictionnaire : les signaler serait du bruit.
SIGLES_CONNUS = {"CV", "AI", "IA", "KI", "UX", "UI", "IT", "HR", "RH", "CRM", "ERP", "SEO", "SAAS", "B2B",
                 "B2C", "ATS", "EPFL", "ETH", "ETHZ", "HES", "HEC", "CFC", "EFZ", "MBA", "CAS", "DAS", "MAS",
                 "CIO", "CTO", "CDO", "COO", "CEO", "CFO", "KPI", "PDF", "API", "SQL", "HTML", "CSS", "PHP",
                 "AWS", "GCP", "SAP", "PMP", "OKR", "ROI", "PME", "KMU", "NPA", "PLZ", "CH", "EU", "USA"}


def sans_accents(t):
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def mots(texte):
    return re.findall(r"[a-z0-9][a-z0-9\-+/]*", sans_accents(texte.lower()))


def racine(m):
    """Racinisation grossière, suffisante pour comparer un CV et une annonce
    (« facilitated » et « facilitate », « gestion » et « gestions »)."""
    if len(m) > 4 and m.endswith("ies"):
        return m[:-3] + "y"
    for suf in ("ations", "ation", "ings", "ing", "ements", "ement", "ungen", "ung", "ed", "er", "es", "en", "s"):
        if len(m) > len(suf) + 3 and m.endswith(suf):
            m = m[: -len(suf)]
            break
    return m[:-1] if len(m) > 4 and m.endswith("e") else m


def _segments(texte):
    """Morceaux de texte qu'un terme ne traverse pas : phrases, membres d'énumération, lignes."""
    return re.split(r"[.!?;:,](?:\s|$)|[()\[\]\n•·|]", texte)


def _terme(groupe):
    """Un groupe de mots fait un terme s'il commence et finit par un mot porteur et que ses
    mots intérieurs sont porteurs ou de simples liaisons."""
    porteur = lambda x: len(x) >= 3 and x not in VIDES
    return porteur(groupe[0]) and porteur(groupe[-1]) and all(porteur(x) or x in LIAISONS for x in groupe[1:-1])


def ngrammes(texte, n_max=3):
    sortie = collections.Counter()
    for segment in _segments(texte):
        ms = mots(segment)
        for n in range(1, n_max + 1):
            for i in range(len(ms) - n + 1):
                if _terme(ms[i:i + n]):
                    sortie[" ".join(ms[i:i + n])] += 1
    return sortie


def present(terme, cv_racines, cv_texte):
    """Un terme est présent si tous ses mots porteurs le sont, à la racine près."""
    return terme in cv_texte or all(racine(m) in cv_racines for m in terme.split() if m not in LIAISONS)


def texte_annonce(brut):
    """L'annonce seule, sans les notes de veille éventuelles au-dessus."""
    m = re.search(r"^##+ +Texte de l'annonce\s*$", brut, re.M | re.I)
    return brut[m.end():] if m else brut


def a_ignorer(brut):
    """Le nom de l'entreprise (titre « # Entreprise — Poste » d'Annonce.md) ne compte pas :
    il ne figure jamais sur un CV."""
    m = re.search(r"^# +(.+)$", brut, re.M)
    return {x for x in mots(m.group(1).split("—")[0]) if len(x) > 2} if m else set()


def couverture(cv, annonce, combien, ignores=frozenset()):
    """(présents, absents) : listes de (terme, occurrences dans l'annonce)."""
    compte = ngrammes(annonce)
    # Termes répétés ou de plusieurs mots : un mot vu une fois n'est pas une exigence.
    candidats = [(t, n) for t, n in compte.items()
                 if (n >= 2 or (" " in t and n >= 1)) and not any(m in ignores for m in t.split())]
    candidats.sort(key=lambda x: (-x[1], -len(x[0])))
    gardes = []                         # « generative » s'efface devant « generative ai »
    for t, n in candidats:
        if any(t != g and t in g for g, _ in gardes):
            continue
        gardes.append((t, n))
        if len(gardes) >= combien:
            break
    cv_texte = " ".join(mots(cv))
    cv_racines = {racine(m) for m in mots(cv)}
    presents = [(t, n) for t, n in gardes if present(t, cv_racines, cv_texte)]
    absents = [(t, n) for t, n in gardes if not present(t, cv_racines, cv_texte)]
    return presents, absents


def format_cv(cv):
    """Points d'attention de format, triés."""
    alertes = []
    standard = {sans_accents(s) for s in SECTIONS_STANDARD}
    lignes_h2 = re.findall(r"^## +(.+)$", cv, re.M)
    for t in lignes_h2[1:]:            # la première ligne ## est le titre du poste, pas une section
        if sans_accents(t.strip().lower()) not in standard:
            alertes.append("Section « %s » : titre non standard, un logiciel de tri peut ne pas la reconnaître."
                           % t.strip())
    if not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", cv):
        alertes.append("Aucune adresse e-mail trouvée.")
    if not re.search(r"\+\d[\d ]{8,}", cv):
        alertes.append("Aucun numéro de téléphone au format international.")
    if not re.search(r"\b\d{2}\.\d{4}\b", cv):
        alertes.append("Aucune date au format MM.AAAA : les dates d'emploi doivent être lisibles par une machine.")
    for sigle in re.findall(r"\b[A-Z]{2,5}\b", cv):
        if sigle in SIGLES_CONNUS or sigle in FORMES_JURIDIQUES:
            continue
        if not re.search(re.escape(sigle) + r"\s*\(", cv) and not re.search(r"\(\s*" + re.escape(sigle) + r"\s*\)", cv):
            alertes.append("Sigle « %s » jamais développé : l'écrire une fois en toutes lettres." % sigle)
    puces = len(re.findall(r"^- ", cv, re.M))
    if puces > 25:
        alertes.append("%d puces : au-delà de 25, le CV se dilue." % puces)
    return sorted(set(alertes))
