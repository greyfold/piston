"""Mise en forme du texte d'une annonce pour la lecture.

Trois formes coexistent au registre :
- le texte des plateformes, une ligne par paragraphe, puces « - » ou « • »,
  intertitres seuls sur leur ligne (« Responsibilities », « **Profil** ») ;
- les résumés en un seul paragraphe, avec des rubriques en capitales (« ROLE : … ; … ; … ») ;
- un mélange des deux.

On en tire des blocs (intertitre, paragraphe, liste), puis du HTML échappé.
Rien n'est réécrit au registre : seule la présentation change. Avant la lecture, `nettoyer` retire ce qui
n'est pas du texte : le Markdown échappé de certaines plateformes (« \\* », « \\[ \\](\\< \\>) »), les
logos (« ![Entreprise]( ) »), les liens sans adresse et les marqueurs de suivi (« jpid…jm jpit…jm »).
"""

import html
import re

from markupsafe import Markup

_PUCE = re.compile(r"^\s*(?:[-*•·▪◦○✓➢►–—]|\d{1,2}[.)])\s+(.*)$")
_TITRE_MD = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")
_GRAS_SEUL = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*$")
_ETIQUETTE = re.compile(r"^([^:.;!?]{2,45}?)\s?:\s+(\S.*)$")
# Rubrique en capitales au fil d'un résumé : « ROLE : », « PROFIL REQUIS : ».
_RUBRIQUE = re.compile(r"(?:(?<=^)|(?<=[.;]\s))([A-ZÀÂÉÈÊÎÔÛÇ][A-ZÀÂÉÈÊÎÔÛÇ'’ /&-]{2,40})\s?:\s")
# Ni guillemet ni accent grave dans l'URL : ils sortiraient de l'attribut `href` du lien.
_URL = re.compile(r"https?://[^\s<>()\"'`]+[^\s<>().,;:!?\"'`]")

# Ce que `nettoyer` retire ou rétablit.
_ECHAPPE = re.compile(r"\\+([\\`*_{}\[\]()#+\-.!|<>~])")          # « \\* », « \\\\- » : Markdown échappé
_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")                    # logo : « ![Entreprise]( ) »
_LIEN_VIDE = re.compile(r"\[([^\]\n]*)\]\(\s*<?\s*>?\s*(?:\"[^\"\n]*\")?\s*\)")   # « [Postuler](< >) »
_SUIVI = re.compile(r"\bjpi[a-z][0-9a-z]{1,12}jm\b|#LI-[A-Za-z0-9]+\b")   # suivi : « jpid…jm jpit…jm », « #LI-JF1 »
_PUCE_EN_LIGNE = re.compile(r"(?<=\S)[ \t]+(?:\\+\*|•)[ \t]+(?=\S)")  # « … texte \\* puce \\* puce » sur une ligne
_TITRE_EN_LIGNE = re.compile(r"(?<=\S)[ \t]+(#{2,6}[ \t]+)(?=\S)")
_GRAS_EN_LIGNE = re.compile(r"[ \t]*\\*\*\\*\*([A-ZÀÂÉÈÊÎÔÛÇ][^*\n]{1,60}?)\\*\*\\*\*[ \t]*")  # intertitre gras aplati


def nettoyer(texte):
    """Le texte d'une annonce sans ce qui n'est pas du texte : Markdown échappé rétabli, logos, liens sans
    adresse (leur libellé reste), marqueurs de suivi ; les puces et intertitres aplatis sur une seule ligne
    retrouvent chacun la leur. Les espaces superflus laissés par ces retraits disparaissent."""
    lignes = []
    for l in (texte or "").split("\n"):
        # Puces aplaties : seulement les étoiles échappées (« \\* ») ou les « • », deux au moins par ligne ;
        # une étoile ordinaire dans une phrase (« 5 * 8 heures ») reste du texte.
        if len(_PUCE_EN_LIGNE.findall(l)) >= 2:
            l = _PUCE_EN_LIGNE.sub("\n- ", l)
            l = _GRAS_EN_LIGNE.sub(lambda m: "\n\n**" + m.group(1) + "**\n", l)
        l = _ECHAPPE.sub(r"\1", l)
        l = _SUIVI.sub("", _LIEN_VIDE.sub(lambda m: m.group(1).strip(), _IMAGE.sub("", l)))
        l = _TITRE_EN_LIGNE.sub(lambda m: "\n" + m.group(1), l)
        lignes.extend(x.rstrip() for x in l.split("\n"))
    lignes = [re.sub(r"[ \t]+[*•]$", "", re.sub(r"[ \t]{2,}", " ", l)) for l in lignes]
    lignes = [l for l in lignes if l.strip() not in ("-", "*", "•")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lignes)).strip()


def _intertitre(ligne, suivante):
    """Ligne courte, sans ponctuation finale, qui annonce un vrai contenu :
    une puce ou une phrase complète (pas une autre ligne courte)."""
    l, s = ligne.strip(), suivante.strip()
    if not l or not s or len(l) > 70 or len(l.split()) > 8:
        return False
    if not (_PUCE.match(s) or len(s) > 70 or s[-1] in ".!?:") or s[0].islower():
        return False
    if l[-1] in ".,;!?" or _PUCE.match(l) or _URL.search(l):
        return False
    if l.endswith(":"):
        return True
    return l[0].isupper() and not re.search(r"\b(et|and|de|of|la|le|the)$", l, re.I)


def _eclater_resume(par):
    """Un long paragraphe à rubriques : intertitres et puces séparées par « ; »."""
    morceaux = _RUBRIQUE.split(par)
    if len(morceaux) < 3:
        return None
    blocs = []
    if morceaux[0].strip():
        blocs.append(("p", morceaux[0].strip()))
    for titre, corps in zip(morceaux[1::2], morceaux[2::2]):
        blocs.append(("h", titre.strip().capitalize()))
        corps = corps.strip()
        items = [i.strip(" .") for i in corps.split(" ; ") if i.strip(" .")]
        blocs.append(("ul", items) if len(items) >= 3 else ("p", corps))
    return blocs


def blocs(texte):
    t = nettoyer(html.unescape(texte or "").replace("\r\n", "\n").replace("\xa0", " "))
    # Gras coupé par un retour à la ligne (HTML de jobup) : « **STAT19\n\n** ».
    t = re.sub(r"\*\*([^*\n]+?)\s*\n\s*\n?\s*\*\*", r"**\1**", t)
    lignes = []
    for l in t.split("\n"):
        l = l.rstrip()
        if l.strip() == "**":
            continue
        if l.count("**") % 2:
            l = l.replace("**", "")
        lignes.append(l)
    out, liste, par = [], [], []

    def vider_par():
        if par:
            p = " ".join(par)
            eclate = _eclater_resume(p) if len(p) > 250 else None
            out.extend(eclate or [("p", p)])
            par.clear()

    def vider_liste():
        if liste:
            out.append(("ul", list(liste)))
            liste.clear()

    for i, ligne in enumerate(lignes):
        suivante = next((l for l in lignes[i + 1:] if l.strip()), "")
        if not ligne.strip():
            vider_par()
            if not _PUCE.match(suivante):     # une ligne vide entre deux puces ne coupe pas la liste
                vider_liste()
            continue
        m = _TITRE_MD.match(ligne) or _GRAS_SEUL.match(ligne)
        if m:
            vider_par(); vider_liste()
            out.append(("h", m.group(1).strip().rstrip(":")))
            continue
        m = _PUCE.match(ligne)
        if m:
            vider_par()
            liste.append(m.group(1).strip())
            continue
        if liste and ligne.startswith(("  ", "\t")):
            liste[-1] += " " + ligne.strip()          # suite d'une puce
            continue
        vider_liste()
        if _intertitre(ligne, suivante):
            vider_par()
            out.append(("h", ligne.strip().rstrip(":").strip()))
            continue
        # Ligne coupée au milieu d'une phrase : on recolle. Sinon, un paragraphe par ligne.
        if par and not re.search(r"[.!?:;»\")]$", par[-1]) and ligne.strip()[0].islower():
            par.append(ligne.strip())
        else:
            vider_par()
            par.append(ligne.strip())
    vider_par()
    vider_liste()
    return out


def _en_ligne(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    # `t` est déjà échappé (« & » → « &amp; ») : le motif ne contient plus de guillemet, donc
    # l'URL peut entrer telle quelle dans `href="…"`, sans second échappement de « & ».
    t = _URL.sub(lambda m: '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
                 % (m.group(0), m.group(0) if len(m.group(0)) < 60 else m.group(0)[:57] + "…"), t)
    return t


def _paragraphe(t):
    """« Responsabilités : bla » → étiquette en gras."""
    m = _ETIQUETTE.match(t)
    if m and len(m.group(1).split()) <= 6 and not _URL.search(m.group(1)):
        return "<strong>%s :</strong> %s" % (_en_ligne(m.group(1)), _en_ligne(m.group(2)))
    return _en_ligne(t)


def _meme(a, b):
    n = lambda x: re.sub(r"\W+", " ", (x or "").lower()).strip()
    return n(a) == n(b)


def annonce_html(texte, titre=None):
    morceaux = []
    bl = blocs(texte)
    if bl and titre and bl[0][0] in ("h", "p") and _meme(bl[0][1], titre):
        bl = bl[1:]                      # le titre est déjà en tête de page
    for genre, contenu in bl:
        if genre == "h":
            morceaux.append("<h3>%s</h3>" % _en_ligne(contenu))
        elif genre == "ul":
            morceaux.append("<ul>%s</ul>" % "".join("<li>%s</li>" % _paragraphe(i) for i in contenu))
        else:
            morceaux.append("<p>%s</p>" % _paragraphe(contenu))
    return Markup("\n".join(morceaux))
