"""Rechargement du texte original : seule une annonce retirée (404, 410) est « introuvable »."""

import httpx
import pytest
from conftest import annonce

from jobsearch import originaux
from jobsearch.sources.base import RouteCassee


class _Source:
    nom = "jobup"

    def __init__(self, erreur):
        self.erreur = erreur

    def detail(self, a):
        raise self.erreur


def _fiche(reg):
    return annonce(reg, statut="B", texte="résumé", texte_origine="extrait", ids_source=["jobup:9"])


@pytest.mark.parametrize("code", [404, 410])
def test_annonce_retiree_est_introuvable(reg, code):
    e = _fiche(reg)
    assert not originaux.recharger(reg, reg.get(e), {"jobup": _Source(RouteCassee("GET x → HTTP %d" % code))})
    assert reg.get(e)["texte_origine"] == "introuvable"


@pytest.mark.parametrize("erreur", [
    RouteCassee("GET x → HTTP 429"), RouteCassee("GET x → HTTP 503"), RouteCassee("GET x → HTTP 403"),
    httpx.ReadTimeout("délai"), httpx.ConnectError("connexion")])
def test_panne_passagere_laisse_la_fiche_a_reessayer(reg, erreur):
    e = _fiche(reg)
    assert not originaux.recharger(reg, reg.get(e), {"jobup": _Source(erreur)})
    assert reg.get(e)["texte_origine"] == "extrait"
    assert [f["empreinte"] for f in originaux.a_recharger(reg)] == [e]
