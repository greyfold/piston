"""Aperçu des documents d'un dossier dans le tableau de bord.

Un PDF s'affiche tel quel dans le navigateur. Un Word est converti en HTML
(mammoth) avec les styles des modèles du coffre (Title, Body Text, Compact) et
présenté comme une page A4 aux couleurs sobres de l'application : c'est un aperçu
fidèle au contenu et à la structure, pas au pixel près. Pour le rendu exact,
le PDF (produit si LibreOffice est installé) fait foi.
"""

import html
import re
import shutil
import subprocess
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


def soffice():
    """LibreOffice, s'il est installé (pour produire les PDF)."""
    return shutil.which("soffice") or next(
        (p for p in ("/Applications/LibreOffice.app/Contents/MacOS/soffice",) if Path(p).exists()), None)


def en_pdf_chrome(docx, langue="fr"):
    """PDF A4 à partir du Word, imprimé par Chrome (déjà installé) via la conversion HTML.
    Mise en page de l'aperçu : une colonne, couleurs sobres."""
    from playwright.sync_api import sync_playwright
    docx = Path(docx)
    pdf = docx.with_suffix(".pdf")
    with sync_playwright() as p:
        navigateur = p.chromium.launch(channel="chrome", headless=True)
        try:
            page = navigateur.new_page()
            page.set_content(docx_en_html(docx, langue), wait_until="networkidle", timeout=30000)
            page.pdf(path=str(pdf), format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            navigateur.close()
    return pdf if pdf.exists() else None


def en_pdf(docx, langue="fr"):
    """PDF à côté du Word : LibreOffice s'il est installé (rendu exact du Word), sinon Chrome."""
    s = soffice()
    if not s:
        try:
            return en_pdf_chrome(docx, langue)
        except Exception:
            return None
    docx = Path(docx)
    subprocess.run([s, "--headless", "--convert-to", "pdf", "--outdir", str(docx.parent), str(docx)],
                   capture_output=True, timeout=120)
    for verrou in docx.parent.glob(".~lock.*"):
        verrou.unlink(missing_ok=True)                                # rien de temporaire ne reste (CQ-15)
    pdf = docx.with_suffix(".pdf")
    return pdf if pdf.exists() else None
