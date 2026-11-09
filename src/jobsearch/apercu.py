"""Aperçu des documents d'un dossier dans le tableau de bord.

Un PDF s'affiche tel quel dans le navigateur. Un Word est converti en HTML
(mammoth) avec les styles des modèles livrés (Title, Body Text, Compact) et
présenté comme une page A4 aux couleurs sobres de l'application : c'est un aperçu
fidèle au contenu et à la structure, pas au pixel près. Le PDF est imprimé par le
Chrome de la machine virtuelle à partir de ce même HTML.
"""

import html
import re
from pathlib import Path

CORRESPONDANCE_STYLES = """
p[style-name='Title'] => h1.titre:fresh
p[style-name='Subtitle'] => p.sous-titre:fresh
p[style-name='Body Text'] => p:fresh
p[style-name='First Paragraph'] => p:fresh
p[style-name='Compact'] => ul > li:fresh
"""

PAGE = """<!doctype html>
<html lang="{langue}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titre}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {{ color-scheme: light; }}
  html {{ background: #e9ecea; }}
  body {{ margin: 0; padding: 24px 12px; font: 10.5pt/1.45 Roboto, -apple-system, sans-serif; color: #2b2f33; }}
  .feuille {{ box-sizing: border-box; background: #fff; max-width: 210mm; min-height: 297mm; margin: 0 auto;
             padding: 18mm 20mm; box-shadow: 0 2px 10px rgba(0,0,0,.12); overflow-wrap: anywhere; }}
  h1, h2, h3 {{ color: #2D3355; font-weight: 700; line-height: 1.25; }}
  h1.titre {{ font-size: 20pt; margin: 0 0 2mm; }}
  h1 {{ font-size: 12.5pt; margin: 6mm 0 2mm; padding-bottom: 1mm; border-bottom: 1px solid #d8dce4; }}
  h2 {{ font-size: 11pt; margin: 4mm 0 1.5mm; }}
  h3 {{ font-size: 10.5pt; margin: 3mm 0 1mm; color: #4D5B63; }}
  p {{ margin: 0 0 2mm; }}
  ul {{ margin: 0 0 2mm; padding-left: 5mm; }}
  li {{ margin: 0 0 .8mm; }}
  a {{ color: #2D3355; }}
  table {{ border-collapse: collapse; }} td {{ vertical-align: top; padding: 0 3mm 1mm 0; }}
  img {{ max-width: 35mm; height: auto; }}
  @media (max-width: 600px) {{ body {{ padding: 0; }} .feuille {{ padding: 8mm 6mm; min-height: auto; box-shadow: none; }} }}
  @page {{ size: A4; margin: 16mm 18mm; }}  /* sur chaque page : une marge sur la feuille n'en mettait qu'en haut de la 1re */
  @media print {{ html {{ background: #fff; }} body {{ padding: 0; }}
    .feuille {{ max-width: none; min-height: auto; box-shadow: none; padding: 0; }}
    h1, h2, h3 {{ break-after: avoid; }} li, p {{ break-inside: avoid; }} }}
</style></head>
<body><main class="feuille">{contenu}</main></body></html>"""


def docx_en_html(chemin, langue="fr"):
    import mammoth
    with open(chemin, "rb") as fh:
        r = mammoth.convert_to_html(fh, style_map=CORRESPONDANCE_STYLES)
    contenu = r.value
    contenu = re.sub(r"<li>\s*[•·]\s*", "<li>", contenu)        # puces écrites en toutes lettres
    # Paragraphes « • … » sans style de liste : une vraie liste.
    contenu = re.sub(r"(?:<p>\s*[•·]\s*.*?</p>)+", lambda m: "<ul>" + re.sub(
        r"<p>\s*[•·]\s*(.*?)</p>", r"<li>\1</li>", m.group(0)) + "</ul>", contenu)
    contenu = contenu.replace("</ul><ul>", "")                       # paragraphes consécutifs
    return PAGE.format(langue=html.escape(langue or "fr"), titre=html.escape(Path(chemin).stem), contenu=contenu)


def en_pdf_chrome(docx, langue="fr"):
    """PDF A4 à partir du Word, imprimé par le Chrome de la machine virtuelle via la conversion
    HTML. Mise en page de l'aperçu : une colonne, couleurs sobres."""
    from .travailleur import client as travailleur
    docx = Path(docx)
    pdf = docx.with_suffix(".pdf")
    pdf.write_bytes(travailleur.Distant.depuis_reglages().pdf(docx_en_html(docx, langue)))
    return pdf if pdf.exists() and pdf.stat().st_size else None


def en_pdf(docx, langue="fr"):
    """PDF à côté du Word, par le Chrome de la machine virtuelle ; None si elle ne répond pas
    (le Word reste, le PDF se refait au prochain rendu)."""
    try:
        return en_pdf_chrome(docx, langue)
    except Exception:
        return None
