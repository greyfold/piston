"""Postes recherchés proposés par l'IA d'après le parcours : l'appel, son nettoyage, le champ du profil."""

import pytest
from fastapi.testclient import TestClient

from jobsearch import moteur, parcours
from jobsearch import postes_proposes as pp


@pytest.fixture
def base(profil_de_test):
    r = parcours.ouvrir()
    yield r
    r.cx.close()


def _remplir(reg):
    parcours.ajouter(reg, "poste", "Responsable UX", organisation="Exemple SA")


def _proposition():
    return pp.Proposition(postes=[
        pp.Poste(intitule="UX Lead", variantes=["Lead UX"], appui="déjà recherché"),          # déjà dans config/
        pp.Poste(intitule="  Chef de projet digital ", variantes=["Digital Project Manager", "chef de projet digital",
                                                                    "Responsable de projet digital"],
                 appui="pilotage d'une refonte, budget de 120k"),
        pp.Poste(intitule="Responsable digital", variantes=["Digital Project Manager", "PO"]),       # variante déjà prise
    ])


def test_proposer_lit_le_parcours_et_nettoie_la_reponse(base):
    _remplir(base)
    faux = moteur.FauxMoteur([_proposition()])
    postes = pp.proposer(base, c=faux)
    tache, message = faux.appels[0]
    assert "Responsable UX" in tache.contexte and "UX Lead" in message           # la base, et ce qui est déjà cherché
    assert postes == [
        {"intitule": "Chef de projet digital", "variantes": ["Digital Project Manager", "Responsable de projet digital"],
         "appui": "pilotage d'une refonte, budget de [montant retiré]"},
        {"intitule": "Responsable digital", "variantes": ["PO"], "appui": ""}]
    e = pp.etat(base)
    assert e["propose"] and e["parcours"] and not e["perime"] and e["postes"] == postes
    parcours.ajouter(base, "formation", "Master en design")
    assert pp.etat(base)["perime"]                                              # le parcours a changé depuis


def test_un_poste_repris_n_est_plus_propose(base):
    from jobsearch import profil
    _remplir(base)
    pp.proposer(base, c=moteur.FauxMoteur([_proposition()]))
    form = {"recherche.postes.intitule": ["Responsable digital"], "recherche.postes.variantes": [""],
            "recherche.cantons": ["VD"]}
    profil.enregistrer("recherche", form)
    assert [x["intitule"] for x in pp.etat(base)["postes"]] == ["Chef de projet digital"]


def test_sans_parcours_rien_a_proposer(base):
    faux = moteur.FauxMoteur([])
    with pytest.raises(pp.ParcoursVide):
        pp.proposer(base, c=faux)
    assert not faux.appels and pp.etat(base) == {"parcours": False, "postes": [], "propose": False, "perime": False}


def test_le_champ_du_profil_propose_et_reprend(base, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "ia_connectee", lambda: True)
    web = TestClient(appmod.app)
    assert "Importe ton CV dans" in web.get("/profil/recherche").text
    _remplir(base)
    page = web.get("/profil/recherche").text
    assert "Proposer des postes d&#39;après mon CV" in page or "Proposer des postes d'après mon CV" in page
    monkeypatch.setattr(moteur, "ouvrir", lambda: moteur.FauxMoteur([_proposition()]))
    d = web.post("/profil/postes/proposer").json()
    assert d["ok"] and 'data-intitule="Chef de projet digital"' in d["html"] and "Proposés par l" in d["html"]
    assert 'data-variantes="Digital Project Manager, Responsable de projet digital"' in d["html"]
    assert 'data-intitule="Chef de projet digital"' in web.get("/profil/recherche").text   # gardé d'une visite à l'autre
    monkeypatch.setattr(moteur, "ouvrir", lambda: moteur.FauxMoteur([moteur.MoteurImpossible("Claude déconnecté")]))
    assert web.post("/profil/postes/proposer").json() == {"ok": False, "erreur": "Claude déconnecté"}
