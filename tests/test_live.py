"""Interroge les vrais sites : `uv run pytest -m live`. Hors de la suite normale."""

import pytest

from jobsearch.sources.base import Client
from jobsearch.sources.jobroom import JobRoom
from jobsearch.sources.jobup import Jobup

pytestmark = pytest.mark.live


def test_routes_en_service():
    c = Client(pause=0.5)
    assert JobRoom(c).tester() >= 0
    assert Jobup(c).tester() >= 0


def test_une_recherche_par_source():
    c = Client(pause=0.5)
    jr = JobRoom(c).chercher("Product Manager", ["VD", "GE"], 14)
    ju = Jobup(c).chercher("Product Manager", "Vaud", 14)
    assert all(a.entreprise and a.titre for a in jr + ju)


def test_pages_carriere():
    from jobsearch.sources.employeurs import connecteurs
    for nom, src in connecteurs(Client(pause=0.5)).items():
        offres = src.offres()
        assert all(a.entreprise and a.titre and a.url for a in offres), nom
