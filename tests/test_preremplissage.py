import pytest

from conftest import annonce
from jobsearch import preremplissage
from jobsearch.navigateur import valeurs_jobroom

pytestmark = pytest.mark.usefixtures("jobroom_en_service")   # code des preuves Job-Room, coupé en version 1

ENVOI = """## 5. Job-Room

| Champ | Valeur |
|---|---|
| Raison sociale | Association des Communes Genevoises (ACG) |
| Lieu | Esplanade de Pont-Rouge 3, 1212 Grand-Lancy |
| Désignation exacte du poste | Directeur-trice adjoint-e, « Projets et de l'innovation » |
"""


def test_page_de_confirmation():
    from jobsearch.navigateur import candidature_envoyee
    ok = "https://www.jobup.ch/fr/application/ok/4b981fe4-64e0-4f39-acc1-54d24d5dea2b/de5ce2c9-b93d-4926-9ed6-7ec0211a3a06/"
    assert candidature_envoyee(ok, "", True)
    assert candidature_envoyee("https://career5.successfactors.eu/x", "Your application has been submitted", False)
    assert not candidature_envoyee("https://career5.successfactors.eu/x", "Your application has been submitted", True)
    assert not candidature_envoyee("https://www.jobup.ch/fr/application/create/de5ce2c9/", "Candidature simplifiée", True)
    assert not candidature_envoyee("https://www.jobup.ch/fr/emplois/detail/de5ce2c9/", "Merci pour votre intérêt", False)


def test_noter_envoi_une_seule_fois(reg, monkeypatch):
    from jobsearch import notify
    monkeypatch.setattr("jobsearch.db.chemin_db", lambda: reg.chemin)
    monkeypatch.setattr(notify, "notifier", lambda *a, **k: None)
    e = annonce(reg, statut="retenue")
    msg = preremplissage.noter_envoi(e, "https://www.jobup.ch/fr/application/ok/a/b/")
    reg.cx.rollback()
    f = reg.get(e)
    assert f["statut"] == "candidature" and f["canal_envoi"] == "jobup" and "Notée envoyée" in msg
    assert "Déjà notée" in preremplissage.noter_envoi(e, "https://www.jobup.ch/fr/application/ok/a/b/")
    assert preremplissage.canal_depuis("https://career5.successfactors.eu/careers") == "SuccessFactors"


def test_valeurs_jobroom_date_d_envoi_et_adresse_de_l_annonce():
    f = {"entreprise": "ACG", "titre": "Directeur adjoint", "postulee_le": "2026-10-27", "url_postuler": "https://x.ch/apply"}
    v = valeurs_jobroom(f, ENVOI)
    assert v["date"] == "27.10.2026"                          # la date d'envoi, jamais celle du jour
    assert v["entreprise"].startswith("Association des Communes") and v["poste"].startswith("Directeur-trice")
    assert (v["rue"], v["numero"], v["npa"], v["ville"]) == ("Esplanade de Pont-Rouge", "3", "1212", "Grand-Lancy")
    assert v["lien"] == "https://x.ch/apply"


def test_pas_de_preuve_pour_une_candidature_pas_partie(reg):
    e = annonce(reg, statut="retenue")
    with pytest.raises(preremplissage.PreremplissageImpossible):
        preremplissage.jobroom(reg, e)


def test_pas_de_seconde_preuve(reg):
    e = annonce(reg, statut="candidature", postulee_le="2026-10-20", jobroom_saisie_le="2026-10-21")
    with pytest.raises(preremplissage.PreremplissageImpossible, match="déjà saisie"):
        preremplissage.jobroom(reg, e)


def test_jetons_anti_doublon(reg):
    e = annonce(reg, entreprise="Banque Pictet & Cie SA", titre="Transformation Manager",
                statut="candidature", postulee_le="2026-10-20")
    _, jetons = preremplissage.jobroom(reg, e)
    assert jetons == ["pictet"]


def test_formulaire_sans_go_refuse(reg):
    e = annonce(reg, statut="B", url_postuler="https://x.ch")
    with pytest.raises(preremplissage.PreremplissageImpossible):
        preremplissage.employeur(reg, e)


def test_lettre_en_texte_simple():
    from jobsearch.coordonnees import texte_brut
    md = ("Lausanne, le 26 octobre 2026\n\n**Objet : candidature au poste de Directeur-trice adjoint-e**\n\n"
          "Madame, Monsieur,\n\nVoir [mon portfolio](https://www.camille-exemple.ch) et *mes* projets.\n\n\n\n"
          "* une puce\n\nCamille Exemple")
    t = texte_brut(md)
    assert "**" not in t and "*" not in t and "[" not in t
    assert "Objet : candidature au poste de Directeur-trice adjoint-e" in t
    assert "Voir mon portfolio et mes projets." in t and "- une puce" in t and "\n\n\n" not in t
