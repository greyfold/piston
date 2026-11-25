"""Registre : résolution d'un préfixe d'empreinte et index du schéma."""

from conftest import annonce


def test_resoudre_n_interprete_pas_les_jokers_like(reg):
    e = annonce(reg, entreprise="Exemple SA", titre="Product Manager")
    assert reg.resoudre(e[:6]) == e
    assert reg.resoudre("%") is None          # un joker ne vaut pas « tout »
    assert reg.resoudre("_" * 6) is None
    assert reg.resoudre(e[:3] + "%") is None


def test_index_cree_le_et_run_etapes(reg):
    noms = {r[1] for t in ("annonces", "run_etapes") for r in reg.cx.execute("PRAGMA index_list(%s)" % t)}
    assert {"annonces_cree_le", "run_etapes_run"} <= noms


def test_maj_et_liste_valident_les_colonnes(reg):
    import pytest
    e = annonce(reg, entreprise="Exemple SA", titre="Product Manager")
    with pytest.raises(ValueError):
        reg.maj(e, colonne_inconnue="x")
    with pytest.raises(ValueError):
        reg.liste(ordre="vue_le DESC; DROP TABLE annonces")
    assert len(reg.liste(ordre="echeance IS NULL, echeance, entreprise")) == 1
