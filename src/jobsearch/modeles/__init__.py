"""Modèles livrés avec l'application : ce que la production du dossier prend quand il n'y a
pas de coffre (installation neuve de l'application Mac).

- `CV.docx` et `Lettre.docx` : modèles Word neutres (A4, Arial, aucune couleur de marque),
  avec les styles que `dossier.rendre_docx` attend : Title, Heading 1, Heading 2, Compact,
  Body Text. Ils se refont avec `fabriquer()` (`uv run python -m jobsearch.modeles`).
- `methode.md` : la méthode de base « Candidature ciblée », mise dans le contexte fixe du
  dossier quand le coffre n'en fournit pas.

Le coffre, s'il est configuré, garde la priorité (modèles, méthode, mesure ATS).
"""

from pathlib import Path

ICI = Path(__file__).resolve().parent
STYLES_ATTENDUS = ("Title", "Heading 1", "Heading 2", "Compact", "Body Text")
POLICE = "Arial"            # présente partout (macOS, Windows ; Liberation Sans dans LibreOffice)
ENCRE = "1F2933"            # texte
TITRES = "1F3A5F"           # titres : bleu nuit sobre


def chemin(piece):
    """Le modèle Word livré pour la pièce (« cv » ou « lettre »)."""
    return ICI / ("CV.docx" if piece == "cv" else "Lettre.docx")


def methode():
    return (ICI / "methode.md").read_text(encoding="utf-8").strip()


# --- Fabrication des modèles ---------------------------------------------------------


def _police(style, taille=None, gras=None, couleur=None):
    """Police explicite : les attributs de thème (asciiTheme…) du modèle de python-docx
    l'emporteraient sinon sur le nom de police."""
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    f = style.font
    f.name = POLICE
    rpr = style.element.get_or_add_rPr()
    polices = rpr.find(qn("w:rFonts"))
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        polices.attrib.pop(qn(attr), None)
    for attr in ("w:eastAsia", "w:cs"):
        polices.set(qn(attr), POLICE)
    if taille:
        f.size = Pt(taille)
    if gras is not None:
        f.bold = gras
    f.italic = False
    f.color.rgb = RGBColor.from_string(couleur or ENCRE)


def _paragraphe(style, avant=0, apres=0, interligne=1.0, suivant=False):
    from docx.shared import Pt
    pf = style.paragraph_format
    pf.space_before, pf.space_after, pf.line_spacing = Pt(avant), Pt(apres), interligne
    pf.keep_with_next = suivant


def _sans_bordure(style):
    from docx.oxml.ns import qn
    ppr = style.element.get_or_add_pPr()
    for el in ppr.findall(qn("w:pBdr")):
        ppr.remove(el)


def _seul(style):
    """Sans style de caractère lié (« Heading1Char »…, aux polices de thème du modèle de
    python-docx) ni espacement de lettres : certains lecteurs (Aperçu, Pages) les suivent."""
    from docx.oxml.ns import qn
    for el in style.element.findall(qn("w:link")):
        style.element.remove(el)
    rpr = style.element.get_or_add_rPr()
    for el in rpr.findall(qn("w:spacing")) + rpr.findall(qn("w:szCs")):
        rpr.remove(el)


def fabriquer(cible, piece):
    """Écrit un modèle Word neutre pour « cv » ou « lettre »."""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Cm, Pt
    cv = piece == "cv"
    doc = Document()
    s = doc.sections[0]
    s.page_width, s.page_height = Cm(21), Cm(29.7)
    marge = Cm(2) if cv else Cm(2.5)
    s.left_margin = s.right_margin = marge
    s.top_margin, s.bottom_margin = (Cm(1.8), Cm(1.8)) if cv else (Cm(2.5), Cm(2.2))
    styles = doc.styles
    taille = 10 if cv else 11

    normal = styles["Normal"]
    _police(normal, taille)
    _paragraphe(normal, 0, 0, 1.0)

    corps = styles["Body Text"]
    _police(corps, taille)
    _paragraphe(corps, 0, 3 if cv else 9, 1.1 if cv else 1.2)

    if "Compact" in [st.name for st in styles]:
        compact = styles["Compact"]
    else:
        compact = styles.add_style("Compact", WD_STYLE_TYPE.PARAGRAPH)
    compact.base_style = corps
    _police(compact, taille)
    _paragraphe(compact, 0, 1.5, 1.1)
    compact.paragraph_format.left_indent = Cm(0.45)            # « • texte » : la suite s'aligne sous le texte
    compact.paragraph_format.first_line_indent = Cm(-0.3)

    titre = styles["Title"]
    _police(titre, 20 if cv else 16, True, TITRES)
    _paragraphe(titre, 0, 2, 1.0, suivant=True)
    _sans_bordure(titre)

    h1 = styles["Heading 1"]
    _police(h1, 12 if cv else 12, True, TITRES)
    _paragraphe(h1, 10, 3, 1.0, suivant=True)

    h2 = styles["Heading 2"]
    _police(h2, 10.5 if cv else 11, True, ENCRE)
    _paragraphe(h2, 6, 1, 1.0, suivant=True)

    for st in (titre, h1, h2):
        st.font.all_caps = False
        st.font.size = st.font.size or Pt(taille)
        _seul(st)
    for nom in ("Title Char", "Heading 1 Char", "Heading 2 Char"):       # plus liés à rien
        lie = next((st for st in styles if st.name == nom), None)
        if lie is not None:
            lie.element.getparent().remove(lie.element)
    corps_doc = doc.element.body
    for el in list(corps_doc):
        if not el.tag.endswith("sectPr"):
            corps_doc.remove(el)
    doc.core_properties.author = ""
    doc.core_properties.title = ""
    doc.core_properties.comments = ""
    doc.save(str(cible))
    return cible


def refaire():
    """Réécrit les deux modèles livrés (après un changement de mise en page ci-dessus)."""
    return [fabriquer(chemin(p), p) for p in ("cv", "lettre")]
