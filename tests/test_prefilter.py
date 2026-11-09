import pytest

from conftest import annonce
from jobsearch.prefilter import evaluer, langue, pre_trier

FR = ("Nous recherchons pour notre équipe à Lausanne une personne pour le poste. Vous serez "
      "responsable de la stratégie produit et de la découverte avec les équipes et la direction. "
      "Vous avez une expérience dans le domaine et vous êtes à l'aise avec les outils. ") * 3
DE = ("Wir suchen für unser Team in Bern eine Person. Sie sind verantwortlich für die Strategie und "
      "die Zusammenarbeit mit den Teams und der Geschäftsleitung. Sie haben Erfahrung und sind "
      "mit den Werkzeugen vertraut, die wir bei uns nutzen werden. ") * 3


def regle(f):
    r = evaluer(f)
    return r["id"] if r else None


def test_langue():
    assert langue(FR) == "fr" and langue(DE) == "de" and langue("trop court") is None


@pytest.mark.parametrize("titre,attendu", [
    ("AI Engineer", "titre-ingenierie"),
    ("Senior Software Developer", "titre-ingenierie"),
    ("Data Scientist", "titre-data-recherche"),
    ("Key Account Manager AI Solutions", "titre-vente"),
    ("Chef de projet construction", "titre-gestion-projet-pure"),
    ("Chef de projet digital", None),          # faux ami : à lire avant de jeter
    ("Head of Design (m/w/d)", "titre-allemand"),
    ("Website & UX Capability Manager (m/f/d)", None),      # anglais, malgré (m/f/d)
    ("Projektleiter Digital (m/f/d)", "titre-allemand"),
    ("Stagiaire UX", "niveau-junior"),
    ("AI Product Manager", None),
    ("Innovation Manager", None),
    ("Product Owner", None),
])
def test_titres(titre, attendu):
    assert regle({"titre": titre, "entreprise": "Exemple SA"}) == attendu


def test_regie_est_limite():
    r = evaluer({"titre": "Product Owner", "entreprise": "Michael Page"})
    assert r["id"] == "employeur-regie" and r["limite"]


def test_texte_allemand_exige_et_atout():
    assert regle({"titre": "Product Manager", "texte": FR + " Allemand courant indispensable."}) == "allemand-exige"
    assert regle({"titre": "Product Manager", "texte": FR + " L'allemand est un atout."}) is None      # 28.10
    assert regle({"titre": "Consultant AI Transformation", "texte": "French and English; German on top is a strong plus."}) is None
    assert regle({"titre": "Product Manager", "texte": FR + " Anglais courant."}) is None


def test_contrat_taux_canton():
    assert regle({"titre": "Product Manager", "texte": FR + " CDD de 12 mois."}) == "contrat-cdd"
    assert regle({"titre": "Product Manager", "permanent": False}) == "contrat-api-duree-determinee"
    # La plateforme dit « durée déterminée » sans la durée : les deux ans et plus se lisent dans le texte (06.11).
    assert regle({"titre": "UX Lead", "permanent": False, "texte": "Initial fixed-term appointment "
                  "of two years, renewable."}) is None
    assert regle({"titre": "Product Manager", "permanent": False, "texte": FR + " CDD de 2 ans."}) is None
    assert regle({"titre": "Product Manager", "permanent": False, "texte": FR + " CDD de 12 mois."}) \
        == "contrat-api-duree-determinee"
    assert regle({"titre": "Product Manager", "permanent": False, "texte": FR + " 3 ans d'expérience."}) \
        == "contrat-api-duree-determinee"
    assert regle({"titre": "Strategy Consultant, Geneva, 12 months (remote)"}) == "contrat-cdd"
    assert regle({"titre": "Advisor, Strategy - GL D - Defined Duration until December 2029"}) == "contrat-cdd"
    assert regle({"titre": "Product Manager", "texte": FR + " CDD de 2 ans, renouvelable."}) is None
    assert regle({"titre": "Product Manager", "texte": FR + " Contrat de 24 mois."}) is None
    assert regle({"titre": "UX Lead", "texte": "Contract Duration - 2 years. This post is a limited-term "
                  "fixed-term post. Initial fixed-term appointment of two years."}) is None      # OMPI, 28.10
    assert regle({"titre": "UX Lead", "texte": "Fixed-term contract of 6 months. At least 3 years of experience."}) == "contrat-cdd"
    assert regle({"titre": "Product Manager", "taux_max": 80}) == "taux"
    assert regle({"titre": "Product Manager", "taux_max": 100}) is None
    assert regle({"titre": "Product Manager", "canton": "ZH"}) == "canton"
    assert regle({"titre": "Product Manager", "canton": "VD"}) is None


def test_annonce_redigee_en_allemand():
    assert regle({"titre": "Product Manager", "langue": "de"}) == "langue-allemand"


@pytest.mark.parametrize("titre,attendu", [
    ("Opérateur incinération 2x8", "hors-familles"),           # cas réels du 24.10
    ("Formation contrôleur-euse de la circulation aérienne", "hors-familles"),
    ("Store Manager", "hors-familles"),
    ("Head of Information Management and Governance", None),    # classée B par l'ancienne chaîne
    ("Application Support & Adoption Specialist", None),
    ("Master's Thesis - AI & Automation for Customised Reporting", "niveau-junior"),
    ("Responsable Transformation Digitale", None),
    ("Customer Experience Lead", None),
])
def test_intitule_hors_familles(titre, attendu):
    assert regle({"titre": titre, "entreprise": "Exemple SA"}) == attendu


def test_pre_trier_ecrit_passe_et_motif(reg):
    e1 = annonce(reg, titre="AI Engineer")
    e2 = annonce(reg, titre="AI Product Manager", texte=FR)
    res = pre_trier(reg)
    assert [f["empreinte"] for f, _ in res] == [e1]
    f = reg.get(e1)
    assert f["statut"] == "C" and f["passe_ecart"] == 0 and "ingénierie" in f["motif"]
    assert reg.get(e2)["statut"] == "nouvelle" and reg.get(e2)["langue"] == "fr"


def test_pre_trier_essai_n_ecrit_rien(reg):
    e = annonce(reg, titre="AI Engineer")
    assert len(pre_trier(reg, essai=True)) == 1
    assert reg.get(e)["statut"] == "nouvelle"
