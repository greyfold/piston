"""Confidentialité : les coordonnées de la personne ne partent jamais vers l'IA.

Avant chaque tâche du moteur (évaluation, dossier, lecture d'une page), tout ce qui
identifie ou permet de joindre la personne est remplacé par une balise : {NOM_COMPLET},
{PRENOM}, {NOM}, {ADRESSE}, {RUE}, {TELEPHONE}, {EMAIL}, {SITE}, {LINKEDIN},
{DATE_NAISSANCE}, {AVS}. Claude écrit le CV et la lettre avec ces balises ; elles
reprennent leur vraie valeur ici, sur l'ordinateur, à la réception (`Masque.demasquer`) :
celle du profil quand il la connaît, sinon celle du document.

Les valeurs viennent du profil (page Profil), qui fait foi pour l'en-tête du CV, puis des
lignes d'identité des documents du profil (« | Téléphone | … | » sous un titre
« Identité », « Coordonnées » ou « Contact »). Dans le contexte (profil, méthode), toute
autre adresse e-mail ou tout autre numéro de téléphone, ceux d'un tiers cité dans les
preuves par exemple, reçoit aussi sa balise numérotée ({EMAIL_2}), rendue telle quelle.
Un numéro AVS est masqué partout où il apparaît.

Ce que l'IA reçoit encore, parce qu'elle en a besoin pour écrire : le parcours, les
compétences, les preuves, les critères, la ville (trajets, lettre) et la nationalité
(droit de travailler, postes publics). Rien de ce qui ne passe pas par le moteur n'est
couvert ici : les tâches Chrome (formulaires, preuves Job-Room), qui saisissent les vraies
valeurs à la demande de la personne, et Gmail, dont le connecteur lit les messages.
"""

import re
import unicodedata
from dataclasses import dataclass, field

# Ordre d'application : le plus englobant d'abord (le domaine d'un e-mail ressemble à un site,
# le nom complet contient le prénom).
ORDRE = ["AVS", "EMAIL", "LINKEDIN", "SITE", "ADRESSE", "TELEPHONE", "DATE_NAISSANCE", "NOM_COMPLET",
         "RUE", "NOM", "PRENOM"]
LIBELLES = {
    "NOM_COMPLET": "nom et prénom", "PRENOM": "prénom", "NOM": "nom de famille", "ADRESSE": "adresse",
    "RUE": "rue et numéro", "TELEPHONE": "téléphone", "EMAIL": "adresse e-mail", "SITE": "site web",
    "LINKEDIN": "profil LinkedIn", "DATE_NAISSANCE": "date de naissance", "AVS": "numéro AVS",
}

# Les libellés des lignes d'identité d'un document (« | Téléphone | +41… | », « E-mail : … »).
CHAMPS_DOCUMENT = [
    (r"(?:pr[ée]nom|first\s*name|vorname)", "prenom"),
    (r"(?:nom(?:\s+complet)?|name|full\s*name|nachname)", "nom"),
    (r"(?:adresse|address|domicile|wohnadresse)", "adresse"),
    (r"(?:t[ée]l[ée]phone|t[ée]l\.?|mobile|natel|portable|phone|handy|telefon)", "telephone"),
    (r"(?:e-?mail|courriel|mail)", "email"),
    (r"(?:site(?:\s+web)?|website|web)", "site"),
    (r"linkedin", "linkedin"),
    (r"(?:date\s+de\s+naissance|naissance|n[ée]e?\s+le|date\s+of\s+birth|birth(?:day|date)?|geburtsdatum)",
     "date_naissance"),
    (r"(?:n[°o]?\s*avs|num[ée]ro\s+avs|avs|ahv(?:-?nr\.?)?)", "avs"),
]
SECTIONS_IDENTITE = re.compile(r"identit|coordonn|contact|personnel|personal|kontakt|persönlich", re.I)

# Une balise écrite dans un texte reçu de l'extérieur : ses accolades sont remplacées par ces signes pendant
# le passage par l'IA, puis rendues telles quelles (elle n'est pas rétablie en vraie valeur).
ABRI_OUVRANT, ABRI_FERMANT = "\u2983", "\u2984"
BALISE = re.compile(r"\{\{?\s*([A-Z][A-Z_]*?(?:_\d+)?)\s*\}?\}", re.I)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TELEPHONE = re.compile(r"(?<![\w+])(?:(?:\+|00)\d{1,3}[\s.\-/]?(?:\(0\)[\s.\-/]?)?\d{1,4}(?:[\s.\-/]?\d{2,4}){2,4}"
                       r"|\(?0\d{2}\)?[\s.\-/]?\d{3}[\s.\-/]?\d{2}[\s.\-/]?\d{2})(?!\d)")
AVS = re.compile(r"(?<!\d)756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}(?!\d)")

MOIS = {
    1: ["janvier", "janv", "january", "jan", "januar", "jänner"],
    2: ["février", "fevrier", "févr", "fevr", "february", "feb", "februar"],
    3: ["mars", "march", "mar", "märz", "marz"],
    4: ["avril", "avr", "april", "apr"],
    5: ["mai", "may"],
    6: ["juin", "june", "jun", "juni"],
    7: ["juillet", "juil", "july", "jul", "juli"],
    8: ["août", "aout", "august", "aug"],
    9: ["septembre", "sept", "september", "sep"],
    10: ["octobre", "oct", "october", "oktober", "okt"],
    11: ["novembre", "nov", "november"],
    12: ["décembre", "decembre", "déc", "dec", "december", "dezember", "dez"],
}
ACCENTS = {"a": "aàâäáãå", "e": "eéèêë", "i": "iîïíì", "o": "oôöóòõ", "u": "uûüúù", "c": "cç", "n": "nñ", "y": "yÿ"}


def plier(t):
    return "".join(c for c in unicodedata.normalize("NFKD", t or "") if not unicodedata.combining(c))


def _souple(texte):
    """Motif d'un texte qui accepte les accents en plus ou en moins et toute suite d'espaces
    (dans la casse de chaque lettre : un motif compilé avec re.I les accepte toutes)."""
    out = []
    for c in texte.strip():
        if c.isspace():
            if not out or out[-1] != r"\s+":
                out.append(r"\s+")
            continue
        base = plier(c).lower()
        if c.isalpha() and base in ACCENTS:
            out.append("[%s]" % (ACCENTS[base].upper() if c.isupper() else ACCENTS[base]))
        else:
            out.append(re.escape(c))
    return "".join(out)


def _mot(motif):
    """Un motif qui ne mord ni dans un mot, ni dans une adresse, ni dans une balise ; il prend
    le nom dans un nom de fichier (« CV-Prénom-Nom.pdf »)."""
    return r"(?<![\w@.{])(?:%s)(?![\w@}])" % motif


# --- Motifs par champ ------------------------------------------------------------------


def _motifs_email(v):
    return [re.compile(r"(?<![\w.+-])%s(?![\w-])" % re.escape(v.strip()), re.I)]


def _coeur_url(v):
    v = re.sub(r"^https?://", "", v.strip(), flags=re.I)
    return re.sub(r"^www\.", "", v, flags=re.I).rstrip("/")


def _motifs_url(v):
    coeur = _coeur_url(v)
    if len(coeur) < 4:
        return []
    return [re.compile(r"(?<![\w@./-])(?:https?://)?(?:www\.)?%s/?(?![\w/-]|\.\w)" % re.escape(coeur), re.I)]


def _motifs_telephone(v):
    from . import pays as pays_mod, telephone
    chiffres = re.sub(r"\D", "", v)
    if len(chiffres) < 7:
        return []
    p = telephone.pays_de(v, pays_mod.code_telephone_defaut()) if v.strip().startswith(("+", "00")) \
        else pays_mod.code_telephone_defaut()
    code = telephone.INDICATIFS.get(p, "")
    national = telephone.national(v, p) or chiffres
    sep = r"[\s.\-/)(]{0,2}"             # « (079) 123 45 67 » : l'indicatif régional entre parenthèses
    corps = sep.join(national)
    prefixe = r"(?:(?:\+|00)%s%s(?:\(0\)%s)?|0%s)?" % (code, sep, sep, sep) if code else r"(?:0%s)?" % sep
    return [re.compile(r"(?<![\w+])\(?%s%s(?!\d)" % (prefixe, corps))]


def _date(v):
    """(jour, mois, année) d'une date de naissance écrite à la main, sinon None."""
    t = plier(v).lower().strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    m = re.search(r"(?<!\d)(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})(?!\d)", t)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    for n, noms in MOIS.items():
        for nom in noms:
            nom = plier(nom)
            m = re.search(r"(\d{1,2})(?:er|st|nd|rd|th)?\.?\s+%s\.?\s+(\d{4})" % nom, t) or \
                re.search(r"%s\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})" % nom, t)
            if m:
                return int(m.group(1)), n, int(m.group(2))
    return None


def _motifs_date(v):
    d = _date(v)
    if not d:
        return [re.compile(_souple(v), re.I)] if len(v.strip()) >= 6 else []
    j, m, a = d
    jj = r"0?%d" % j
    noms = "|".join(_souple(x) for x in sorted(MOIS[m], key=len, reverse=True))
    # Une année sur deux chiffres (« 12.03.85 ») : 1985 ou 2085 en toutes lettres, et « 85 » en numérique.
    annees = ["%d" % a] if a >= 100 else ["19%02d" % a, "20%02d" % a]
    annee = "(?:%s)" % "|".join(annees)
    court = "%02d" % (a % 100)
    motifs = [r"(?<!\d)%s\s*[./-]\s*0?%d\s*[./-]\s*(?:%s|%s)(?!\d)" % (jj, m, "|".join(annees), court),
              r"(?<!\d)%s(?:er|st|nd|rd|th)?\.?\s+(?:%s)\.?\s+%s(?!\d)" % (jj, noms, annee),
              r"(?<![\w])(?:%s)\.?\s+%s(?:st|nd|rd|th)?,?\s+%s(?!\d)" % (noms, jj, annee)]
    if a >= 100:
        motifs.append(r"(?<!\d)%d-%02d-%02d(?!\d)" % (a, m, j))
    return [re.compile(p, re.I) for p in motifs]


def _variantes_rue(rue):
    """Motifs d'une rue et de son numéro, dans les deux ordres : « Rue du Lac 12 » et « 12, rue du Lac »."""
    rue = (rue or "").strip()
    out = [_souple(rue)]
    m = re.match(r"^(?P<nom>.+?)[,\s]+(?P<num>\d+\s?[a-zA-Z]?)$", rue) or \
        re.match(r"^(?P<num>\d+\s?[a-zA-Z]?)[,\s]+(?P<nom>[^\d].*)$", rue)
    if m:
        nom, num = _souple(m.group("nom").strip(" ,")), _souple(m.group("num"))
        out += [r"%s\s*,?\s*%s" % (nom, num), r"%s\s*,?\s*%s" % (num, nom)]
    return out


def _motifs_adresse(rue, npa, ville):
    motifs = []
    if rue and npa and ville:
        for r in _variantes_rue(rue):
            motifs.append(re.compile(r"%s\s*[,·|]?\s*%s\s+%s" % (r, re.escape(npa), _souple(ville)), re.I))
    return motifs


def _motifs_rue(rue):
    if not rue or len(rue.strip()) < 4:
        return []
    return [re.compile(_mot(r), re.I) for r in _variantes_rue(rue)]


def _motifs_nom_complet(prenom, nom):
    if not (prenom and nom):
        return []
    # « Prénom Nom » seulement : « Nom, Prénom » ou « Prénom-Nom » passent par les balises du prénom et du
    # nom, qui rendent la ponctuation telle quelle.
    return [re.compile(_mot(r"%s\s+%s" % (_souple(prenom), _souple(nom))), re.I)]


CIVILITES = ("M.", "Mr", "Mme", "Mlle", "Me", "Dr", "Dre", "Pr", "Prof.", "Mrs", "Ms",
             "Monsieur", "Madame", "Mademoiselle", "Docteur", "Maître", "Professeur")


def _motifs_seul(mot, autre="", balise_autre=""):
    """Prénom ou nom seul : à la casse près (un nom comme « Rose » ou « Bell » est aussi un mot courant),
    ou tout en capitales. Un nom de moins de trois lettres (« Li », mais aussi « Le », « Ma ») est un mot
    courant : il n'est pris seul que précédé d'une civilité (« M. Li ») ou accolé à l'autre nom
    (« Jean Li », « Li Jean ») ; jamais comme mot isolé. Les formes jointes (« jean-li ») passent par
    les motifs composés."""
    mot = (mot or "").strip()
    if len(mot) < 2:
        return []
    if len(mot) > 2:
        return [re.compile(_mot(_souple(mot))), re.compile(_mot(_souple(mot.upper())))]
    mot = mot[0].upper() + mot[1:].lower()
    coeur = "(?:%s|%s)" % (_souple(mot), _souple(mot.upper()))
    conditions = [r"(?<=\b(?i:%s)\s)" % re.escape(c) for c in CIVILITES]
    if (autre or "").strip():
        voisin = _souple(autre)
        conditions.append(r"(?<=\b(?i:%s)\s)" % voisin.replace(r"\s+", r"\s"))
        conditions.append(r"(?=%s\s+(?i:%s)(?![\w@}]))" % (coeur, voisin))
        # « Jean-Li » : l'autre nom, déjà remplacé par sa balise ou pas encore, joint par - _ ou .
        for autre_forme in (voisin, re.escape("{%s}" % balise_autre)):
            conditions.append(r"(?<=(?i:%s)[-_.])" % autre_forme)
        conditions.append(r"(?=[-_.](?:%s|(?i:%s)(?![\w@}])))" % (re.escape("{%s}" % balise_autre), voisin))
    return [re.compile(r"(?<![\w@.{])(?:%s)%s(?![\w@}])" % ("|".join(conditions), coeur))]


def _motifs_composes(prenom, nom):
    """« jean-li », « jean.li », « li_jean » : le prénom et le nom joints par - _ ou ., quelle que soit la casse.
    Chaque motif rend (premier mot, séparateur, second mot) ; Masque.masquer remet les balises de chacun."""
    if not (prenom and nom):
        return []
    p, n = _souple(prenom), _souple(nom)
    return [re.compile(r"(?<![\w@.{])(%s)([-_.])(%s)(?![\w@}])" % (a, b), re.I) for a, b in ((p, n), (n, p))]


def _mots_nom(valeur):
    """Les mots d'un nom, sans accents, en minuscules (au moins deux lettres)."""
    return [w for w in re.split(r"[^a-z]+", plier(valeur).lower()) if len(w) >= 2]


def _local_contient_nom(local, noms):
    """Le début d'une adresse e-mail (avant @) est-il bâti sur un nom ? Mot entier, ou, pour un nom de
    quatre lettres ou plus, n'importe où (« cfontaine »)."""
    local = plier(local).lower()
    mots = [w for w in re.split(r"[^a-z]+", local) if w]
    return any(n in mots or (len(n) >= 4 and n in local) for n in noms)


# --- Valeurs ---------------------------------------------------------------------------


def lignes_identite(texte):
    """{champ: [valeurs]} des lignes d'identité d'un texte : un tableau « | Libellé | Valeur | »
    ou une ligne « Libellé : valeur », sous un titre d'identité ou de coordonnées."""
    trouve, dedans = {}, False
    for ligne in (texte or "").splitlines():
        titre = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", ligne)
        if titre:
            dedans = bool(SECTIONS_IDENTITE.search(titre.group(1)))
            continue
        if not dedans:
            continue
        m = re.match(r"^\s*\|\s*\**([^|*]+?)\**\s*\|\s*([^|]+?)\s*\|\s*$", ligne) or \
            re.match(r"^\s*[-*]?\s*\**([^:*|]{2,30}?)\**\s*:\s*(.+?)\s*$", ligne)
        if not m or set(m.group(2)) <= set("-: "):
            continue
        libelle, valeur = m.group(1).strip(), m.group(2).strip("* \t")
        for motif, champ in CHAMPS_DOCUMENT:
            if re.fullmatch(motif, plier(libelle).lower()) or re.fullmatch(motif, libelle.lower()):
                trouve.setdefault(champ, []).append(valeur)
                break
    return trouve


def _adresse_document(v):
    m = re.match(r"^(?P<rue>.+?)\s*[,·]\s*(?P<npa>\d{4,5})\s+(?P<ville>.+)$", v.strip())
    return m.groupdict() if m else None


@dataclass
class Masque:
    """Table balise → valeur rendue, et les motifs qui trouvent chaque valeur dans un texte."""
    valeurs: dict = field(default_factory=dict)        # balise → ce qu'elle rend
    motifs: list = field(default_factory=list)         # [(balise, motif)] dans l'ordre d'application
    numeros: dict = field(default_factory=dict)        # valeur détectée → balise numérotée
    noms: list = field(default_factory=list)           # mots du prénom et du nom (e-mails bâtis dessus)
    composes: list = field(default_factory=list)       # [(balise du 1er motif, balise du 2d, motif)] : « jean-li »

    def ajouter(self, balise, rendu, motifs):
        if not rendu or not motifs:
            return
        self.valeurs.setdefault(balise, rendu)
        self.motifs.extend((balise, m) for m in motifs)

    def trier(self):
        self.motifs.sort(key=lambda bm: ORDRE.index(bm[0]) if bm[0] in ORDRE else len(ORDRE))
        return self

    def copie(self):
        return Masque(dict(self.valeurs), list(self.motifs), dict(self.numeros), list(self.noms), list(self.composes))

    def _numerotee(self, base, valeur):
        cle = (base, re.sub(r"\s+", " ", valeur.strip()).lower())
        if cle not in self.numeros:
            n = 1 + sum(1 for b in self.valeurs if b == base or b.startswith(base + "_"))
            balise = base if n == 1 else "%s_%d" % (base, n)
            self.numeros[cle] = balise
            self.valeurs[balise] = valeur.strip()
        return "{%s}" % self.numeros[cle]

    def masquer(self, texte, contexte=False, neutraliser=False):
        """Le texte, coordonnées remplacées par leurs balises. `contexte` : toute autre adresse
        e-mail et tout autre numéro de téléphone reçoivent aussi la leur (profil, méthode).
        `neutraliser` : les balises déjà écrites dans ce texte (annonce, contenu non fiable) sont
        mises à l'abri, pour que `demasquer` ne les remplace pas par une vraie valeur."""
        if not texte:
            return texte
        if neutraliser:
            texte = BALISE.sub(lambda m: m.group(0).replace("{", ABRI_OUVRANT).replace("}", ABRI_FERMANT), texte)
        for balise, motif in (bm for bm in self.motifs if bm[0] in ("AVS", "EMAIL")):
            texte = motif.sub("{%s}" % balise, texte)
        if self.noms:               # une autre adresse bâtie sur le nom (« j.li@autre.ch ») : balise numérotée
            texte = EMAIL.sub(lambda m: self._numerotee("EMAIL", m.group(0))
                              if _local_contient_nom(m.group(0).rsplit("@", 1)[0], self.noms) else m.group(0), texte)
        for balise, motif in (bm for bm in self.motifs if bm[0] not in ("AVS", "EMAIL")):
            texte = motif.sub("{%s}" % balise, texte)
        for premier, second, motif in self.composes:      # ce que la casse a laissé : « jean-li »
            texte = motif.sub(lambda m, p=premier, d=second: "{%s}%s{%s}" % (p, m.group(2), d), texte)
        texte = AVS.sub(lambda m: self._numerotee("AVS", m.group(0)), texte)
        if contexte:
            texte = EMAIL.sub(lambda m: self._numerotee("EMAIL", m.group(0)), texte)
            texte = TELEPHONE.sub(lambda m: self._numerotee("TELEPHONE", m.group(0)), texte)
        return texte

    def demasquer(self, texte):
        """Chaque balise connue reprend sa valeur ; une balise inconnue reste telle quelle."""
        if not isinstance(texte, str) or ("{" not in texte and ABRI_OUVRANT not in texte):
            return texte
        if "{" in texte:
            texte = BALISE.sub(lambda m: self.valeurs.get(m.group(1).upper(), m.group(0)), texte)
        return texte.replace(ABRI_OUVRANT, "{").replace(ABRI_FERMANT, "}")

    def demasquer_objet(self, o):
        """Même chose dans une réponse structurée (dictionnaires, listes, textes)."""
        if isinstance(o, str):
            return self.demasquer(o)
        if isinstance(o, list):
            return [self.demasquer_objet(x) for x in o]
        if isinstance(o, dict):
            return {k: self.demasquer_objet(v) for k, v in o.items()}
        return o

    def balises_dans(self, texte):
        """Les balises de base (sans numéro) présentes dans un texte masqué, dans l'ordre."""
        vues = {m.group(1) for m in BALISE.finditer(texte or "")}
        return [b for b in ORDRE if b in vues]


def construire(identite=None, documents=""):
    """Le masque des coordonnées : celles du profil (qui font foi pour ce qui est rendu),
    puis celles que les lignes d'identité des `documents` ajoutent."""
    if identite is None:
        from . import profil
        identite = profil.identite()
    i = {k: str(v).strip() for k, v in (identite or {}).items() if v not in (None, "")}
    doc = lignes_identite(documents)
    m = Masque()

    prenom, nom = i.get("prenom", ""), i.get("nom", "")
    complets = [v for v in doc.get("nom", []) if len(v.split()) >= 2]
    if not (prenom and nom) and complets:
        mots = complets[0].split()
        prenom, nom = prenom or mots[0], nom or " ".join(mots[1:])
    if prenom and nom:
        m.ajouter("NOM_COMPLET", "%s %s" % (prenom, nom), _motifs_nom_complet(prenom, nom))
    for v in complets:
        mots = v.split()
        m.ajouter("NOM_COMPLET", v, _motifs_nom_complet(mots[0], " ".join(mots[1:])))
    m.ajouter("PRENOM", prenom, _motifs_seul(prenom, nom, "NOM"))
    m.ajouter("NOM", nom, _motifs_seul(nom, prenom, "PRENOM"))
    for v in doc.get("prenom", []):
        m.ajouter("PRENOM", v, _motifs_seul(v, nom, "NOM"))
    for v in doc.get("nom", []):
        if len(v.split()) == 1:
            m.ajouter("NOM", v, _motifs_seul(v, prenom, "PRENOM"))
    if prenom and nom and "PRENOM" in m.valeurs and "NOM" in m.valeurs:
        m.composes = [("PRENOM", "NOM", _motifs_composes(prenom, nom)[0]),
                      ("NOM", "PRENOM", _motifs_composes(prenom, nom)[1])]
    m.noms = sorted({w for v in (prenom, nom, *doc.get("prenom", []), *doc.get("nom", [])) for w in _mots_nom(v)})

    rue, npa, ville = i.get("adresse", ""), i.get("npa", ""), i.get("ville", "")
    adresses_doc = [a for a in map(_adresse_document, doc.get("adresse", [])) if a]
    if rue and npa and ville:
        m.ajouter("ADRESSE", "%s, %s %s" % (rue, npa, ville), _motifs_adresse(rue, npa, ville))
    for a in adresses_doc:
        m.ajouter("ADRESSE", "%s, %s %s" % (a["rue"], a["npa"], a["ville"]),
                  _motifs_adresse(a["rue"], a["npa"], a["ville"]))
    for r in [rue] + [a["rue"] for a in adresses_doc]:
        m.ajouter("RUE", rue or r, _motifs_rue(r))

    for champ, balise, fabrique in (("email", "EMAIL", _motifs_email), ("telephone", "TELEPHONE", _motifs_telephone),
                                    ("date_naissance", "DATE_NAISSANCE", _motifs_date), ("avs", "AVS", None)):
        valeurs = ([i[champ]] if i.get(champ) else []) + doc.get(champ, [])
        for v in valeurs:
            m.ajouter(balise, valeurs[0], fabrique(v) if fabrique else [re.compile(re.escape(v))])

    for champ, balise in (("site", "SITE"), ("linkedin", "LINKEDIN")):
        valeurs = ([i[champ]] if i.get(champ) else []) + doc.get(champ, [])
        if not valeurs:
            continue
        # Le site s'écrit dans le CV comme dans le document (« www.exemple.ch »), si c'est le même.
        rendu = next((v for v in doc.get(champ, []) if _coeur_url(v).lower() == _coeur_url(valeurs[0]).lower()),
                     valeurs[0])
        for v in valeurs:
            m.ajouter(balise, rendu, _motifs_url(v))
    return m.trier()


CONSIGNE = """Confidentialité : les coordonnées de la personne ont été remplacées par des balises entre accolades (%s). Écris-les telles quelles, accolades comprises, là où la coordonnée doit figurer (en-tête du CV, signature de la lettre, champ d'un formulaire) : elles reprennent leur vraie valeur à la réception. N'en invente aucune, ne les traduis pas, ne les commente pas et ne signale pas qu'une coordonnée manque."""


def consigne(balises):
    return CONSIGNE % ", ".join("{%s} : %s" % (b, LIBELLES.get(b, b.lower())) for b in balises)


def apercu(texte, identite=None):
    """(texte masqué, [(balise, libellé)]) : ce que l'IA reçoit d'un texte, pour la page Confidentialité."""
    m = construire(identite, texte)
    masque = m.masquer(texte, contexte=True)
    return masque, [(b, LIBELLES.get(b, b)) for b in m.balises_dans(masque)]
