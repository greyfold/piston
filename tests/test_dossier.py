from pathlib import Path

import pytest

from conftest import annonce
from jobsearch import dossier
from jobsearch.moteur import FauxMoteur

ANALYSE = "## Angle proposé\nProduit et IA.\n## Écarts assumés\nAucun.\n"
CV = "# Camille Exemple\n## AI Product Manager\n- Led AI adoption for 300 employees\n- Launched e-commerce in 2 weeks\n"
LETTRE = "Note : anecdote P-03.\n---\nLausanne, le 24 octobre 2026\n\nDear Hiring Team,\n\nYour AI roadmap needs a product lead who can frame problems and drive adoption across teams.\n\nKind regards,\nCamille Exemple\n"
ENVOI = "## 0. Avant d'envoyer\n## 1. Formulaire\n## 2. Pièces\n## 3. Réponses aux questions standard\n## 4. Prétentions salariales\n## 5. Job-Room\n"


FluxFaux = FauxMoteur          # rend les pièces dans l'ordre, garde (tâche, message)


@pytest.fixture
def isole(tmp_path, monkeypatch):
    monkeypatch.setattr(dossier, "DATA", tmp_path)
    monkeypatch.setattr(dossier, "contexte_fixe", lambda: "Preuves : 300 employés formés.")
    monkeypatch.setattr("jobsearch.apercu.en_pdf", lambda docx, langue="fr": None)   # Chrome : test à part
    return tmp_path


def test_dossier_complet(reg, isole):
    e = annonce(reg, entreprise="Exemple SA", titre="AI Product Manager", statut="retenue",
                texte="We are looking for an AI Product Manager to own the roadmap. " * 5, langue="en")
    c = FluxFaux([ANALYSE, CV, LETTRE, ENVOI])
    etat = dossier.produire(reg, e, c=c)
    d = dossier.etat(reg, e)
    noms = {p.name for p in (isole / "dossiers").iterdir().__next__().iterdir()}
    assert {"Annonce.md", "Analyse.md", "CV.md", "Lettre.md", "Envoi.md"} <= noms
    assert any(n.startswith("Camille-Exemple-CV-Exemple") and n.endswith(".docx") for n in noms)
    assert any(n.startswith("Camille-Exemple-Cover-Letter-") for n in noms)
    controles = {x["cq"][:5]: x for x in d["controles"]}
    assert controles["CQ-04"]["ok"] and controles["CQ-01"]["ok"] and controles["CQ-11"]["ok"]
    assert controles["CQ-07"]["ok"]                                   # « 300 » figure dans le profil
    f = reg.get(e)
    assert f["cv_genere"] and f["statut"] == "retenue"                                     # le dossier n'envoie rien
    assert c.appels[0][0].modele == "claude-opus-5-5" and c.appels[1][1].count("<analyse>") == 1
    assert c.appels[0][0].outils == [] and c.appels[0][0].contexte == "Preuves : 300 employés formés."
    assert etat in ("pret", "a_revoir")


def test_avancement_etape_par_etape(reg, isole):
    e = annonce(reg, statut="retenue", texte="We are looking for an AI Product Manager. " * 5, langue="en")
    vues = []
    c = FluxFaux([ANALYSE, CV, LETTRE, ENVOI])
    executer = c.executer
    c.executer = lambda t, m: (vues.append(dossier.etat(reg, e)["etape"]), executer(t, m))[1]
    dossier.produire(reg, e, c=c)
    assert vues == ["1/5 · analyse de l'annonce", "2/5 · CV adapté", "3/5 · lettre", "4/5 · fiche d'envoi"]
    assert dossier.etat(reg, e)["etape"] == "5/5 · Word, PDF et contrôles"
    assert dossier.libelle(dossier.etat(reg, e)).startswith("Dossier ")


def test_en_cours_trop_vieux_est_interrompu_et_rattrape(reg, isole):
    e = annonce(reg, statut="retenue", texte="x")
    dossier.demarrer(reg, e)
    d = dossier.etat(reg, e)
    assert d["etat"] == "en_cours" and dossier.libelle(d).startswith("Dossier en préparation · démarrage · depuis ")
    assert dossier.a_produire(reg) == []
    reg.cx.execute("UPDATE dossiers SET debut='2026-01-01 08:00' WHERE empreinte=?", (e,))
    assert dossier.etat(reg, e)["etat"] == "interrompu"
    assert [f["empreinte"] for f in dossier.a_produire(reg)] == [e]


def test_go_sans_texte_se_dit_au_clic(reg, isole):
    e = annonce(reg, statut="retenue", texte="")
    with pytest.raises(dossier.evaluation.EvaluationImpossible):
        dossier.produire_en_tache(e, reg)
    assert dossier.etat(reg, e) is None


def test_notification_quand_le_dossier_est_fait(reg, isole, monkeypatch, tmp_path):
    import threading
    from jobsearch import notify
    from jobsearch.db import Registre
    e = annonce(reg, entreprise="JTI", titre="UX Manager", statut="retenue", texte="x")
    reg.commit()
    envoyees = []
    monkeypatch.setattr(dossier, "Registre", lambda: Registre(tmp_path / "test.db"))
    monkeypatch.setattr(dossier, "produire", lambda r, emp: "a_revoir")
    monkeypatch.setattr(notify, "notifier", lambda titre, texte, urgent=False, **k: envoyees.append((titre, texte)))
    dossier.produire_en_tache(e, reg)
    for t in threading.enumerate():
        if t.name.startswith("dossier-"):
            t.join(5)
    assert envoyees == [("Dossier à revoir avant envoi", "JTI · UX Manager")]


def test_cv_trop_long_raccourci_jusqu_a_deux_pages(reg, isole, monkeypatch):
    e = annonce(reg, statut="retenue", texte="We are looking for an AI Product Manager. " * 5, langue="en")
    pages = [3, 2]                                          # rendu du CV : 3 pages, puis 2 après raccourci
    monkeypatch.setattr("jobsearch.apercu.en_pdf", lambda docx, langue="fr": docx)
    monkeypatch.setattr(dossier, "nombre_pages", lambda pdf: pages.pop(0))
    monkeypatch.setattr(dossier, "lignes_par_page", lambda pdf: [52, 55, 1])
    court = CV.replace("- Launched e-commerce in 2 weeks\n", "")
    c = FluxFaux([ANALYSE, CV, LETTRE, ENVOI, court])
    dossier.produire(reg, e, c=c)
    d = dossier.etat(reg, e)
    chemin = isole / "dossiers" / next((isole / "dossiers").iterdir()).name
    assert (chemin / "CV.md").read_text() == court and (chemin / "CV.3-pages.md").read_text() == CV
    assert {x["cq"]: x for x in d["controles"]}["CQ-13 CV sur 2 pages au plus"] == {
        "cq": "CQ-13 CV sur 2 pages au plus", "ok": True, "detail": "2 pages"}
    consigne = c.appels[4][1]
    assert "fait 3 pages" in consigne and "guide de CV de Harvard" in consigne
    assert "portent 1 ligne(s) : supprime au moins 4 lignes entières" in consigne   # une ligne déborde
    assert any("CV raccourci · 3 → 2 pages" in j["texte"] for j in reg.journal(e))


def test_cv_toujours_trop_long_signale(reg, isole, monkeypatch):
    e = annonce(reg, statut="retenue", texte="We are looking for an AI Product Manager. " * 5, langue="en")
    monkeypatch.setattr("jobsearch.apercu.en_pdf", lambda docx, langue="fr": docx)
    monkeypatch.setattr(dossier, "nombre_pages", lambda pdf: 3)
    c = FluxFaux([ANALYSE, CV, LETTRE, ENVOI, CV, CV])
    assert dossier.produire(reg, e, c=c) == "a_revoir"
    assert len(c.appels) == 4 + dossier.RACCOURCIS_MAX                # deux raccourcis, pas davantage
    cq = {x["cq"][:5]: x for x in dossier.etat(reg, e)["controles"]}["CQ-13"]
    assert not cq["ok"] and cq["detail"] == "3 pages"


def test_harvard_dans_chaque_ecriture_du_cv():
    v = dossier._valeurs()
    for consigne in (dossier.pieces_a_ecrire()["cv"], dossier.RACCOURCIR % dict(v, pages=3, mots=1000, cible=800, debord=""),
                     dossier.REVISION % v):
        assert "guide de CV de Harvard" in consigne and "verbe d'action" in consigne
    assert "2 pages A4 au plus" in dossier.pieces_a_ecrire()["cv"]
    cv = dossier.pieces_a_ecrire()["cv"]
    assert "profil, expérience, compétences" in cv and "sans « je »" in cv and "de nationalité" in cv
    assert "Règles des guides de Harvard pour la lettre" in dossier.pieces_a_ecrire()["lettre"]
    assert "usage de l'IA" in dossier.pieces_a_ecrire()["envoi"]


def test_titre_sans_mention_de_genre():
    f = {"titre": "Website & UX Capability Manager (m/f/d)", "langue": "en"}
    pieces = {"cv": "# X\n## Website & UX Capability Manager\n", "lettre": "", "analyse": "## Angle proposé", "envoi": ""}
    cq = {x["cq"][:5]: x for x in dossier.controler(f, Path("."), pieces, 90, "")}
    assert cq["CQ-04"]["ok"]
    assert not dossier._titre_comparable("AI Lead") == dossier._titre_comparable("Data/AI Lead")


def test_format_du_cv_fige():
    cv = dossier.pieces_a_ecrire()["cv"]
    assert "**<employeur>** | <lieu> | MM.AAAA – MM.AAAA" in cv and "le séparateur est toujours « | »" in cv
    assert "toutes lettres suivies du sigle si elle l'écrit en toutes lettres" in cv


def test_nombre_pages(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"<< /Type /Pages /Count 2 >> << /Type /Page >> << /Type/Page >>")
    assert dossier.nombre_pages(pdf) == 2 and dossier.nombre_pages(None) is None


def test_controle_tiret_cadratin_et_titre(reg, isole):
    e = annonce(reg, entreprise="Exemple SA", titre="Head of Product", statut="retenue", texte="Texte. " * 50)
    dossier.produire(reg, e, c=FluxFaux([ANALYSE, CV.replace("AI Product Manager", "Product Lead — AI"), LETTRE, ENVOI]))
    controles = {x["cq"][:5]: x for x in dossier.etat(reg, e)["controles"]}
    assert not controles["CQ-02"]["ok"] and not controles["CQ-04"]["ok"]
    assert dossier.etat(reg, e)["etat"] == "a_revoir"


def test_sans_texte_pas_de_dossier(reg, isole):
    e = annonce(reg, statut="retenue", texte="")
    with pytest.raises(Exception):
        dossier.produire(reg, e, c=FluxFaux([]))


def test_chiffres_sans_preuve():
    assert dossier._chiffres_sans_preuve("Formé 300 collègues, 45 % de gain, en 2024", "300 collègues") == ["45 %"]
    lettre = "Service des ressources humaines\nRue de l'Exemple 1\n1400 Exempleville\n\nJ'ai géré 1400 tickets."
    assert dossier._chiffres_sans_preuve(lettre, "Profil.") == ["1400"]      # l'adresse oui, la phrase non


def test_cq07_accepte_un_chiffre_de_l_annonce(tmp_path):
    """« plus de 300 professionnels » vient de l'annonce : pas inventé. Un chiffre absent des deux l'est."""
    f = {"titre": "Chef de projet", "entreprise": "Exemple SA", "langue": "fr",
         "texte": "Notre équipe de plus de 300 professionnels."}
    pieces = {"cv": "## Chef de projet\n", "lettre": "Votre équipe de plus de 300 professionnels, et 45 % de gain.",
              "analyse": "## Angle proposé\n", "envoi": ""}
    cq = {x["cq"][:5]: x for x in dossier.controler(f, tmp_path, pieces, 90, "Profil sans chiffre.")}
    assert not cq["CQ-07"]["ok"] and cq["CQ-07"]["detail"] == "45 %"


def test_a_produire(reg, isole):
    e = annonce(reg, statut="retenue", texte="x")
    annonce(reg, entreprise="Autre", statut="retenue", texte="x", cv_genere=True)
    assert [f["empreinte"] for f in dossier.a_produire(reg)] == [e]


def test_revision_du_cv_avec_mots_coches(reg, isole):
    e = annonce(reg, entreprise="Exemple SA", titre="AI Product Manager", statut="retenue",
                texte="We need machine learning and stakeholder management skills. " * 5, langue="en")
    dossier.produire(reg, e, c=FluxFaux([ANALYSE, CV, LETTRE, ENVOI]))
    nouveau = CV + "- Stakeholder management across business units\n"
    c = FluxFaux([nouveau])
    assert dossier.reviser_cv(reg, e, ["stakeholder management"], "vrai chez Eaton", c=c) in ("pret", "a_revoir")
    chemin = dossier.etat(reg, e)["chemin"]
    import os
    assert "Stakeholder management" in open(os.path.join(chemin, "CV.md")).read()
    assert any(n.startswith("CV.avant-") for n in os.listdir(chemin))           # l'ancienne version reste
    envoye = c.appels[0][1]
    assert "stakeholder management" in envoye and "vrai chez Eaton" in envoye
    with pytest.raises(Exception):
        dossier.reviser_cv(reg, e, [], c=FluxFaux([]))


def test_apercu_word_en_page_a4(isole):
    from jobsearch import apercu
    cible = isole / "cv.docx"
    dossier.rendre_docx(CV, cible, "cv")
    h = apercu.docx_en_html(cible, "en")
    assert 'class="feuille"' in h and "AI Product Manager" in h and "<li>" in h and "• " not in h
    assert '<html lang="en">' in h


def test_motscles_ignore_marque_et_pronoms():
    from jobsearch.motscles import a_ignorer
    ig = a_ignorer("Banque Cantonale de Fribourg (BCF)")
    assert {"bcf", "fribourg", "vous", "vos missions"} <= ig


def test_lettre_datee_du_jour_puis_figee_a_l_envoi(reg, isole):
    import datetime as dt
    from jobsearch import actions
    from jobsearch.lettre import date_du_docx
    e = annonce(reg, entreprise="Exemple SA", titre="AI Product Manager", statut="retenue",
                texte="Texte de l'annonce. " * 20, langue="fr")
    lettre = "Note.\n---\n\nLausanne, novembre 2026\n\n**Objet** : candidature\n\nMadame, Monsieur,\n"
    dossier.produire(reg, e, c=FluxFaux([ANALYSE, CV, lettre, ENVOI]))
    chemin = dossier.etat(reg, e)["chemin"]
    import os
    docx = os.path.join(chemin, dossier.nom_fichier(reg.get(e), "lettre"))
    auj = dt.date.today()
    assert "Lausanne, le %s" % (auj.day if auj.day > 1 else "1er") in open(os.path.join(chemin, "Lettre.md")).read()
    assert date_du_docx(docx).startswith("Lausanne, le ")
    actions.appliquer(reg, e, "candidature", {"date": "2026-10-20", "canal": "portail"})
    assert dossier.mettre_a_date(reg, e) == dt.date(2026, 10, 20)
    assert date_du_docx(docx) == "Lausanne, le 20 octobre 2026"
    assert dossier.mettre_a_date(reg, e) == dt.date(2026, 10, 20)        # figée : ne bouge plus


def test_lettres_datees_d_avance_et_pdf_rate_refait(reg, isole, monkeypatch):
    """L'échéance du jour date les lettres (l'onglet Dossier n'attend plus le PDF de la VM) ; un PDF que
    la VM n'a pas pu faire est refait à la lecture suivante, même si la date n'a pas bougé."""
    e = annonce(reg, entreprise="Exemple SA", titre="AI Product Manager", statut="retenue",
                texte="Texte de l'annonce. " * 20, langue="fr")
    lettre = "Note.\n---\n\nLausanne, novembre 2026\n\n**Objet** : candidature\n\nMadame, Monsieur,\n"
    dossier.produire(reg, e, c=FluxFaux([ANALYSE, CV, lettre, ENVOI]))
    docx = Path(dossier.etat(reg, e)["chemin"]) / dossier.nom_fichier(reg.get(e), "lettre")
    assert dossier.pdf_en_retard(docx)                                   # VM éteinte : pas de PDF
    assert dossier.dater_lettres(reg) == {"lettres": 1, "pdf_en_retard": 1, "erreurs": 0}
    faits = []
    monkeypatch.setattr("jobsearch.apercu.en_pdf", lambda d, langue="fr": faits.append(d) or d.with_suffix(".pdf").write_bytes(b"%PDF"))
    assert dossier.dater_lettres(reg) == {"lettres": 1, "pdf_en_retard": 0, "erreurs": 0}
    assert faits == [docx]
    dossier.dater_lettres(reg)
    assert faits == [docx]                                               # à jour : rien à refaire
