"""Le dossier de candidature se produit avec ce que livre l'application : méthode, modèles Word
et mesure des mots-clés."""

import json
from pathlib import Path

import pytest
from docx import Document

from conftest import annonce
from jobsearch import ats, dossier, evaluation, modeles
from jobsearch.moteur import FauxMoteur

PROFIL = "<document nom=\"Parcours\">\nAlex Exemple. Chef de produit. A formé 300 employés à l'IA.\n</document>"
TEXTE = ("Nous recherchons un Product Manager pour piloter la roadmap produit et la gestion de projet. "
         "Vous maîtrisez la roadmap produit, la gestion de projet agile et l'analyse de données. "
         "Expérience en intelligence artificielle souhaitée. Esprit d'équipe et anglais courant. ") * 3
ANALYSE = "## Angle proposé\nProduit.\n## Exigences\n## Écarts assumés\nAucun.\n## Mots-clés de l'annonce\n"
CV = """# Alex Exemple
## Product Manager
Rue de l'Exemple 1, 1000 Lausanne | +41 79 000 00 00 | alex@example.com
## Profil
Chef de produit, roadmap produit et gestion de projet agile, avec un esprit d'équipe affirmé.
## Expérience
### Chef de produit
**Exemple Services SA** | Lausanne | 01.2022 – 10.2026
- Piloté la roadmap produit de la plateforme et la gestion de projet agile de trois équipes
- Formé 300 employés à l'intelligence artificielle
## Compétences
- **Produit :** roadmap produit, analyse de données, gestion de projet agile
## Langues
- Anglais : courant
"""
LETTRE = ("Note : anecdote de la formation.\n---\nLausanne, le 9 novembre 2026\n\n**Objet** : candidature\n\n"
          "Madame, Monsieur,\n\nVotre roadmap produit demande un pilotage serré.\n\nAlex Exemple\n")
ENVOI = ("## 0. Avant d'envoyer\n## 1. Formulaire\n## 2. Pièces\n## 3. Réponses aux questions standard\n"
         "## 4. Prétentions salariales\n")


@pytest.fixture
def isole(tmp_path, monkeypatch):
    monkeypatch.setattr(dossier, "DATA", tmp_path)
    monkeypatch.setattr(evaluation, "profil", lambda: PROFIL)       # base Parcours (parcours.py)
    monkeypatch.setattr("jobsearch.apercu.en_pdf", lambda docx, langue="fr": None)
    return tmp_path


def test_dossier_de_bout_en_bout(reg, isole):
    e = annonce(reg, entreprise="Exemple SA", titre="Product Manager", statut="retenue", texte=TEXTE, langue="fr")
    c = FauxMoteur([ANALYSE, CV, LETTRE, ENVOI])
    fin = dossier.produire(reg, e, c=c)
    assert fin in ("pret", "a_revoir")
    contexte = c.appels[0][0].contexte
    assert contexte.startswith("<document nom=\"Candidature ciblée.md\">\n# Candidature ciblée (méthode de base")
    assert "Style d'écriture (à défaut" in contexte and contexte.endswith(PROFIL)

    d = dossier.etat(reg, e)
    chemin = Path(d["chemin"])
    mots = json.loads((chemin / "motscles.json").read_text(encoding="utf-8"))
    assert d["couverture"] == mots["taux"] and d["couverture"] >= 80
    assert "roadmap produit" in {t for t, _ in mots["presents"]}
    cq06 = next(x for x in d["controles"] if x["cq"].startswith("CQ-06"))
    assert cq06["ok"] and cq06["detail"] != "non mesurée"

    cv = Document(str(chemin / dossier.nom_fichier(reg.get(e), "cv")))
    s = cv.sections[0]
    assert round(s.page_width.cm, 1) == 21.0 and round(s.page_height.cm, 1) == 29.7
    styles = [(p.style.name, p.text) for p in cv.paragraphs]
    assert styles[:4] == [("Title", "Alex Exemple"), ("Heading 1", "Product Manager"),
                          ("Body Text", "Rue de l'Exemple 1, 1000 Lausanne | +41 79 000 00 00 | alex@example.com"),
                          ("Heading 1", "Profil")]
    assert ("Heading 2", "Chef de produit") in styles
    assert any(n == "Compact" and t.startswith("• Formé 300") for n, t in styles)
    assert cv.styles["Body Text"].font.name == "Arial"
    lettre = Document(str(chemin / dossier.nom_fichier(reg.get(e), "lettre")))
    assert lettre.paragraphs[0].text.startswith("Lausanne, le ") and lettre.paragraphs[0].style.name == "Body Text"


def test_modeles_livres_neutres_et_complets():
    for piece in ("cv", "lettre"):
        doc = Document(str(modeles.chemin(piece)))
        noms = {st.name for st in doc.styles}
        assert set(modeles.STYLES_ATTENDUS) <= noms
        assert not doc.paragraphs and not doc.core_properties.author          # rien de personnel, aucun texte
        assert round(doc.sections[0].page_width.cm, 1) == 21.0
    assert "Candidature ciblée" in modeles.methode()


def test_modeles_refaits_identiques(tmp_path):
    """Les modèles livrés sont ceux que `fabriquer` écrit (mêmes styles, mêmes marges)."""
    for piece in ("cv", "lettre"):
        neuf, livre = Document(str(modeles.fabriquer(tmp_path / "m.docx", piece))), Document(str(modeles.chemin(piece)))
        assert neuf.styles.element.xml == livre.styles.element.xml
        assert neuf.sections[0].left_margin == livre.sections[0].left_margin


def test_modeles_et_methode_de_l_application(monkeypatch):
    monkeypatch.setattr(evaluation, "profil", lambda: PROFIL)
    assert dossier.modele_word("cv") == modeles.chemin("cv") and dossier.modele_word("lettre") == modeles.chemin("lettre")
    fixe = dossier.contexte_fixe()
    assert fixe.startswith("<document nom=\"Candidature ciblée.md\">") and "méthode de base" in fixe
    assert fixe.endswith(PROFIL)


def test_mesure_integree_francais_allemand_anglais():
    presents, absents = ats.couverture("Pilotage de la roadmap produit. Projektleitung. Facilitated workshops.",
                                       "la roadmap produit, la roadmap produit ; Projektleitung und Projektleitung ; "
                                       "facilitate workshops ; gestion budgétaire", 30)
    trouves = {t for t, _ in presents}
    assert {"roadmap produit", "projektleitung", "facilitate workshops"} <= trouves
    assert "gestion budgetaire" in {t for t, _ in absents}
    assert not any(t.split()[0] in ats.VIDES for t in trouves | {t for t, _ in absents})


def test_format_du_cv_mesure():
    alertes = ats.format_cv(CV)
    assert not any("Product Manager" in a or "Profil" in a for a in alertes)    # titre du poste : pas une section
    assert ats.format_cv("# X\n## Poste\n## Mes passions\n") == sorted([
        "Section « Mes passions » : titre non standard, un logiciel de tri peut ne pas la reconnaître.",
        "Aucune adresse e-mail trouvée.", "Aucun numéro de téléphone au format international.",
        "Aucune date au format MM.AAAA : les dates d'emploi doivent être lisibles par une machine."])
    assert any("« OKRS »" in a for a in ats.format_cv("OKRS et Objectives and Key Results")) is True
    assert not any("PMO" in a for a in ats.format_cv("Project Management Office (PMO)"))
