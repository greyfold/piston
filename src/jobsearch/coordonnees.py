"""Ce qui sert à pré-remplir un formulaire : coordonnées de la personne, réponses
d'Envoi.md, documents du dossier.

Les coordonnées viennent du profil (`data/profil.yaml`, page Profil du tableau de
bord). Les réponses viennent d'Envoi.md du dossier ; le salaire est la valeur
« à un seul chiffre » de la rubrique 4, jamais un plancher.
"""

import re
from pathlib import Path

from . import profil


def _depuis_cv(cv):
    """« # Prénom Nom » puis « Rue 1, 1000 Ville · +41 79 … · x@y · www… · linkedin… · Nationalité suisse »."""
    lignes = cv.splitlines()
    entete = next((l for l in lignes if "@" in l and ("·" in l or "," in l)), "")
    morceaux = [m.strip() for m in re.split(r"\s+·\s+", entete) if m.strip()]
    titre = next((l[2:].split() for l in lignes if l.startswith("# ")), [])
    c = {"prenom": titre[0], "nom": " ".join(titre[1:])} if titre else {}
    for m in morceaux:
        if "@" in m:
            c["email"] = m
        elif re.match(r"^\+?\d[\d\s]{7,}$", m):
            c["telephone"] = m
        elif "linkedin" in m.lower():
            c["linkedin"] = m if m.startswith("http") else "https://www." + m.removeprefix("www.")
        elif m.lower().startswith(("www.", "http")):
            c["site"] = m if m.startswith("http") else "https://" + m
        elif m.lower().startswith("nationalit"):
            c["nationalite"] = m.split()[-1].capitalize()
        else:
            a = re.match(r"^(?P<rue>.+?),\s*(?P<npa>\d{4})\s+(?P<ville>.+)$", m)
            if a:
                c.update(adresse=a.group("rue"), npa=a.group("npa"), ville=a.group("ville"))
    return c


def coordonnees():
    return profil.identite()


def reponses_envoi(texte):
    """Rubrique 3 (tableau question | réponse) et valeur salariale « à un seul chiffre »."""
    r = {}
    for l in texte.splitlines():
        m = re.match(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$", l.strip())
        if m and not set(m.group(1)) <= set("-: ") and m.group(1).lower() != "question":
            r[m.group(1).strip("* ").lower()] = m.group(2).strip()
    sal = re.search(r"un seul chiffre[^\n]*?(\d{2,3}[’' ]?\d{3})", texte, re.I)
    if sal:
        r["salaire"] = re.sub(r"\D", "", sal.group(1))
    return r


def texte_brut(md):
    """Markdown → texte simple pour un champ de formulaire : sans gras, italique, titres
    ni liens ; paragraphes séparés par une ligne vide, puces gardées en « - »."""
    t = md or ""
    t = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", t)           # [texte](lien) → texte
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t, flags=re.S)           # gras
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)   # italique
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", t)                    # titres
    t = re.sub(r"(?m)^\s*[*+]\s+", "- ", t)                         # puces
    t = re.sub(r"(?m)^\s*>\s?", "", t)                              # citations
    t = t.replace("**", "").replace("`", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def valeurs_formulaire(f, envoi_md, lettre_texte=""):
    """Le dictionnaire clé → valeur passé au remplisseur du navigateur."""
    c = coordonnees()
    rep = reponses_envoi(envoi_md or "")
    trouve = lambda *cles: next((v for k, v in rep.items() for cle in cles if cle in k), "")
    v = {
        "civilite": c.get("civilite", ""), "date_naissance": c.get("date_naissance", ""),
        "prenom": c.get("prenom", ""), "nom": c.get("nom", ""),
        "nom_complet": ("%s %s" % (c.get("prenom", ""), c.get("nom", ""))).strip(),
        "email": c.get("email", ""), "telephone": c.get("telephone", ""),
        "adresse": c.get("adresse", ""), "npa": c.get("npa", ""), "ville": c.get("ville", ""),
        "pays": c.get("pays", "Suisse"), "nationalite": c.get("nationalite", ""),
        "linkedin": c.get("linkedin", ""), "site": c.get("site", ""),
        "disponibilite": texte_brut(trouve("disponibilit")), "salaire": rep.get("salaire", ""),
        "motivation": texte_brut(trouve("motivation")),
        "lettre": texte_brut(lettre_texte),
    }
    return {k: x for k, x in v.items() if x}


def documents(dossier_chemin, f, est_coffre=False):
    """{"cv": {"docx": …, "pdf": …}, "lettre": {...}} : le Word d'abord (mieux lu par les logiciels
    de tri), le PDF pour les champs qui n'acceptent que ce format."""
    cv, lettre = _documents_pdf(dossier_chemin, f, est_coffre)
    d = Path(dossier_chemin)
    formats = {}
    for genre, pdf, motifs in (("cv", cv, ("-CV-",)), ("lettre", lettre, ("Cover-Letter", "Lettre", "Letter"))):
        if not pdf:
            continue
        docx = pdf.with_suffix(".docx")
        if not docx.exists():
            docx = next((p for p in sorted(d.iterdir()) if p.suffix == ".docx" and any(m in p.name for m in motifs)), None)
        formats[genre] = {k: v for k, v in (("docx", docx), ("pdf", pdf if pdf.suffix == ".pdf" else None)) if v}
    return formats


def _documents_pdf(dossier_chemin, f, est_coffre=False):
    """(CV, lettre) en PDF : la plupart des portails n'acceptent que ce format (jobup
    ignore un Word sans le dire). Le PDF est produit s'il manque ; la lettre est datée
    (date du jour, ou date d'envoi)."""
    from . import dossier as dmod
    from .apercu import en_pdf
    d = Path(dossier_chemin)
    langue = f.get("langue") or "fr"
    cvs = sorted(p for p in d.iterdir() if "-CV-" in p.name and p.suffix in (".pdf", ".docx"))
    cv = next((p for p in cvs if p.suffix == ".pdf"), cvs[0] if cvs else None)
    if cv and cv.suffix == ".docx":
        cv = en_pdf(cv, langue) or cv
    lettres = sorted(p for p in d.iterdir() if p.suffix == ".docx"
                     and any(m in p.name for m in ("Cover-Letter", "Lettre", "Letter")))
    lettre = lettres[0] if lettres else None
    if lettre and est_coffre:
        lettre = dmod.lettre_coffre_datee(f, d, lettre.name)
    if lettre:
        pdf = lettre.with_suffix(".pdf")
        if not pdf.exists() or pdf.stat().st_mtime < lettre.stat().st_mtime:
            pdf = en_pdf(lettre, langue)
        lettre = pdf or lettre
    return cv, lettre
